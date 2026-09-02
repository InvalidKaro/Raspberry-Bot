from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


def _embed(title: str, description: str = "", color: int = 0x5865F2) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(UTC))
    e.set_footer(text="Raspberry-Bot · Smart Ops")
    return e


def _yes(value: bool) -> str:
    return "✅" if value else "❌"


def _fmt_perms(perms: discord.Permissions) -> list[tuple[str, bool]]:
    return [
        ("Administrator", perms.administrator), ("Manage Server", perms.manage_guild),
        ("Manage Roles", perms.manage_roles), ("Manage Channels", perms.manage_channels),
        ("Manage Messages", perms.manage_messages), ("Moderate Members", perms.moderate_members),
        ("Kick Members", perms.kick_members), ("Ban Members", perms.ban_members),
        ("Manage Webhooks", perms.manage_webhooks), ("View Audit Log", perms.view_audit_log),
        ("Mention Everyone", perms.mention_everyone),
    ]


class SmartOps(commands.GroupCog, group_name="ops", group_description="Diagnose, Audit, Restorepoints, Server Pulse und intelligente Insights"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.database.execute("""CREATE TABLE IF NOT EXISTS ops_restorepoints(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,snapshot_json TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    @app_commands.command(name="permissionmap", description="Zeigt effektive Channel-Rechte für Mitglied oder Rolle.")
    @app_commands.guild_only()
    async def permissionmap(self, interaction: discord.Interaction, mitglied: discord.Member | None = None, rolle: discord.Role | None = None, kanal: discord.TextChannel | None = None) -> None:
        channel = kanal or (interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None)
        if channel is None:
            await interaction.response.send_message("Wähle einen Textkanal.", ephemeral=True); return
        target = mitglied or rolle or interaction.user
        perms = channel.permissions_for(target)
        rows = _fmt_perms(perms)
        enabled = "\n".join(f"✅ {name}" for name, value in rows if value) or "—"
        disabled = "\n".join(f"❌ {name}" for name, value in rows if not value) or "—"
        e = _embed("🗺️ Permission Map", f"**Target:** {getattr(target, 'mention', str(target))}\n**Channel:** {channel.mention}\n\nEffektive Berechtigungen nach Rollen + Overwrites.", 0x3498DB)
        e.add_field(name="Erlaubt", value=enabled[:1024], inline=True); e.add_field(name="Nicht erlaubt", value=disabled[:1024], inline=True)
        e.add_field(name="Basis", value=f"View: {_yes(perms.view_channel)}\nSend: {_yes(perms.send_messages)}\nHistory: {_yes(perms.read_message_history)}\nThreads: {_yes(perms.create_public_threads)}", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="roleaudit", description="Findet riskante, ungenutzte und problematische Rollen.")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def roleaudit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        risky, unused, hierarchy = [], [], []
        bot_member = guild.me
        for role in reversed(guild.roles):
            if role.is_default() or role.managed:
                continue
            flags = [name for name, val in _fmt_perms(role.permissions) if val and name in {"Administrator","Manage Server","Manage Roles","Ban Members","Manage Webhooks","Mention Everyone"}]
            if flags:
                risky.append(f"{role.mention} — {', '.join(flags)}")
            if not role.members:
                unused.append(f"{role.mention} · Pos {role.position}")
            if bot_member and role >= bot_member.top_role:
                hierarchy.append(role.mention)
        e = _embed("🛡️ Role Audit", f"Analysiert **{len(guild.roles)} Rollen**. Hinweis-System, kein automatischer Eingriff.", 0xE67E22)
        e.add_field(name=f"⚠️ Sensible Rechte ({len(risky)})", value="\n".join(risky[:12])[:1024] or "Keine auffälligen Rollen.", inline=False)
        e.add_field(name=f"🫥 Ungenutzt ({len(unused)})", value="\n".join(unused[:12])[:1024] or "Keine.", inline=False)
        e.add_field(name=f"↕️ Über/gleich Bot ({len(hierarchy)})", value=", ".join(hierarchy[:15])[:1024] or "Keine problematische Hierarchie.", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="healthcheck", description="Führt einen vollständigen Bot-/Pi-Selbsttest aus.")
    @app_commands.default_permissions(manage_guild=True)
    async def healthcheck(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        checks = []
        try:
            row = await self.bot.database.fetchone("SELECT 1 AS ok"); checks.append(("SQLite", bool(row and int(row["ok"]) == 1), "Query + Connection"))
        except Exception as exc:
            checks.append(("SQLite", False, type(exc).__name__))
        checks.append(("Discord Gateway", self.bot.is_ready(), f"{round(self.bot.latency*1000)} ms"))
        try:
            usage = shutil.disk_usage("/"); free_gb = usage.free / 1024**3; checks.append(("Disk", free_gb > 1.0, f"{free_gb:.1f} GB frei"))
        except OSError:
            checks.append(("Disk", False, "nicht lesbar"))
        try:
            path = self.bot.database.path; checks.append(("DB-Datei", path.exists(), str(path)))
        except Exception:
            checks.append(("DB-Datei", False, "unbekannt"))
        for service in ("raspberry-bot", "raspberry-dashboard", "pihole-FTL"):
            try:
                proc = await asyncio.create_subprocess_exec("systemctl", "is-active", service, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=3); state = out.decode().strip(); checks.append((service, state == "active", state or "unknown"))
            except Exception:
                checks.append((service, False, "check unavailable"))
        ok_count = sum(1 for _, ok, _ in checks if ok); color = 0x2ECC71 if ok_count == len(checks) else 0xF1C40F if ok_count >= len(checks)-2 else 0xE74C3C
        e = _embed("🩺 Healthcheck", f"**{ok_count}/{len(checks)}** Checks erfolgreich.\n\n" + "\n".join(f"{_yes(ok)} **{name}** · {detail}" for name, ok, detail in checks), color)
        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="diagnose", description="Sucht typische Ursachen für Bot-/Server-Probleme.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def diagnose(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True); findings = []
        me = guild.me
        if me:
            gp = me.guild_permissions
            if not gp.manage_messages: findings.append(("⚠️", "Bot hat kein **Manage Messages**."))
            if not gp.manage_roles: findings.append(("⚠️", "Bot hat kein **Manage Roles**. Drops/Button-Rollen können eingeschränkt sein."))
        errors = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM command_analytics WHERE success=0 AND created_at>=datetime('now','-1 hour')")
        err_count = int(errors["c"] if errors else 0)
        if err_count: findings.append(("⚠️", f"**{err_count} Command-Fehler** in der letzten Stunde."))
        metrics = await self.bot.database.fetchone("SELECT cpu_percent,ram_percent,temperature,disk_percent FROM system_snapshots_v4 ORDER BY id DESC LIMIT 1")
        if metrics:
            if float(metrics["ram_percent"] or 0) >= 85: findings.append(("🔴", f"RAM zuletzt bei **{float(metrics['ram_percent']):.1f}%**."))
            if float(metrics["disk_percent"] or 0) >= 90: findings.append(("🔴", f"Datenträger zuletzt bei **{float(metrics['disk_percent']):.1f}%**."))
            if metrics["temperature"] is not None and float(metrics["temperature"]) >= 75: findings.append(("🔴", f"CPU-Temperatur **{float(metrics['temperature']):.1f}°C**."))
        pending = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM dashboard_commands WHERE status='pending'"); pcount = int(pending["c"] if pending else 0)
        if pcount > 20: findings.append(("⚠️", f"Dashboard-Queue enthält **{pcount}** offene Einträge."))
        if not findings: findings.append(("✅", "Keine typischen Probleme erkannt."))
        await interaction.followup.send(embed=_embed("🔬 Diagnose", "\n\n".join(f"{icon} {text}" for icon, text in findings), 0x2ECC71 if findings[0][0] == "✅" else 0xE67E22), ephemeral=True)

    async def _snapshot(self, guild_id: int) -> dict[str, Any]:
        tables = {"guild_settings":"guild_id=?","ticket_staff_roles":"guild_id=?","system_monitor_config":"guild_id=?","bot_access_roles":"guild_id=?","onboarding_rules":"guild_id=?"}
        result = {}
        for table, where in tables.items():
            rows = await self.bot.database.fetchall(f"SELECT * FROM {table} WHERE {where}", (guild_id,)); result[table] = [{k: row[k] for k in row.keys()} for row in rows]
        return result

    @app_commands.command(name="restorepoint", description="Erstellt, listet oder restauriert Server-Konfigurations-Snapshots.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def restorepoint(self, interaction: discord.Interaction, aktion: str, name: str = "manual", id: int | None = None) -> None:
        gid = int(interaction.guild_id or 0); action = aktion.lower().strip()
        if action == "list":
            rows = await self.bot.database.fetchall("SELECT id,name,created_at,created_by FROM ops_restorepoints WHERE guild_id=? ORDER BY id DESC LIMIT 20", (gid,))
            text = "\n".join(f"`#{r['id']}` **{r['name']}** · {r['created_at']} · <@{r['created_by']}>" for r in rows) or "Keine Restorepoints."
            await interaction.response.send_message(embed=_embed("🧊 Restorepoints", text), ephemeral=True); return
        if action == "create":
            rid = await self.bot.database.execute("INSERT INTO ops_restorepoints(guild_id,name,snapshot_json,created_by) VALUES(?,?,?,?)", (gid, name[:100], json.dumps(await self._snapshot(gid), ensure_ascii=False), interaction.user.id))
            await interaction.response.send_message(f"✅ Restorepoint `#{rid}` **{name}** erstellt.", ephemeral=True); return
        if action != "restore" or id is None:
            await interaction.response.send_message("Nutze `create`, `list` oder `restore` (mit ID).", ephemeral=True); return
        row = await self.bot.database.fetchone("SELECT snapshot_json,name FROM ops_restorepoints WHERE guild_id=? AND id=?", (gid, id))
        if not row:
            await interaction.response.send_message("Restorepoint nicht gefunden.", ephemeral=True); return
        current = await self._snapshot(gid)
        await self.bot.database.execute("INSERT INTO ops_restorepoints(guild_id,name,snapshot_json,created_by) VALUES(?,?,?,?)", (gid, f"auto-before-restore-{id}", json.dumps(current, ensure_ascii=False), interaction.user.id))
        snap = json.loads(str(row["snapshot_json"])); allow = ("guild_settings","ticket_staff_roles","system_monitor_config","bot_access_roles","onboarding_rules")
        await interaction.response.defer(ephemeral=True)
        for table in allow:
            await self.bot.database.execute(f"DELETE FROM {table} WHERE guild_id=?", (gid,))
            for item in snap.get(table, []):
                if not isinstance(item, dict) or not item: continue
                cols = list(item.keys()); placeholders = ",".join("?" for _ in cols)
                await self.bot.database.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({placeholders})", tuple(item[c] for c in cols))
        await interaction.followup.send(f"✅ Restorepoint `#{id}` **{row['name']}** wiederhergestellt. Vorher wurde automatisch ein Safety-Snapshot erstellt.", ephemeral=True)

    @app_commands.command(name="configdiff", description="Vergleicht Restorepoints oder Snapshot mit aktuellem Zustand.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def configdiff(self, interaction: discord.Interaction, id_a: int, id_b: int | None = None) -> None:
        gid = int(interaction.guild_id or 0)
        a = await self.bot.database.fetchone("SELECT snapshot_json,name FROM ops_restorepoints WHERE guild_id=? AND id=?", (gid, id_a))
        if not a:
            await interaction.response.send_message("Erster Restorepoint nicht gefunden.", ephemeral=True); return
        left = json.loads(str(a["snapshot_json"]))
        if id_b is None:
            right, right_name = await self._snapshot(gid), "CURRENT"
        else:
            b = await self.bot.database.fetchone("SELECT snapshot_json,name FROM ops_restorepoints WHERE guild_id=? AND id=?", (gid, id_b))
            if not b:
                await interaction.response.send_message("Zweiter Restorepoint nicht gefunden.", ephemeral=True); return
            right, right_name = json.loads(str(b["snapshot_json"])), str(b["name"])
        lines = []
        for table in sorted(set(left) | set(right)):
            same = json.dumps(left.get(table, []), sort_keys=True, ensure_ascii=False) == json.dumps(right.get(table, []), sort_keys=True, ensure_ascii=False)
            lines.append(f"{'🟢' if same else '🟠'} `{table}` — {'identisch' if same else 'geändert'}")
        await interaction.response.send_message(embed=_embed(f"🧬 Config Diff · {a['name']} ↔ {right_name}", "\n".join(lines), 0x9B59B6), ephemeral=True)

    @app_commands.command(name="pulse", description="Zeigt, was gerade auf Bot und Server passiert.")
    @app_commands.guild_only()
    async def pulse(self, interaction: discord.Interaction) -> None:
        gid = int(interaction.guild_id or 0)
        queries = {
            "commands": ("SELECT COUNT(*) AS c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-24 hour')", (gid,)),
            "errors": ("SELECT COUNT(*) AS c FROM command_analytics WHERE guild_id=? AND success=0 AND created_at>=datetime('now','-24 hour')", (gid,)),
            "tickets": ("SELECT COUNT(*) AS c FROM tickets WHERE guild_id=? AND status='open'", (gid,)),
            "tasks": ("SELECT COUNT(*) AS c FROM workspace_tasks WHERE guild_id=? AND status NOT IN('done','closed')", (gid,)),
            "events": ("SELECT COUNT(*) AS c FROM workspace_events WHERE guild_id=? AND starts_at>=datetime('now')", (gid,)),
        }
        values = {}
        for key, (q, params) in queries.items():
            row = await self.bot.database.fetchone(q, params); values[key] = int(row["c"] if row else 0)
        arcade = self.bot.get_cog("ArcadeSuite"); active_sessions = len(getattr(arcade, "sessions", {})) if arcade else 0
        e = _embed("💓 Server Pulse", f"Live-Snapshot für **{interaction.guild.name if interaction.guild else 'Server'}**.", 0xE91E63)
        for name, value in (("Commands · 24h", values["commands"]),("Command Errors", values["errors"]),("Open Tickets", values["tickets"]),("Open Tasks", values["tasks"]),("Upcoming Events", values["events"]),("Arcade Sessions", active_sessions),("Gateway", f"{round(self.bot.latency*1000)} ms"),("Guilds",len(self.bot.guilds)),("Users cached",len(self.bot.users))):
            e.add_field(name=name, value=f"**{value}**", inline=True)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="anomaly", description="Sucht ungewöhnliche Aktivitäts- und Fehler-Spikes.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def anomaly(self, interaction: discord.Interaction) -> None:
        gid = int(interaction.guild_id or 0)
        hour = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-1 hour')", (gid,))
        day = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-25 hour') AND created_at<datetime('now','-1 hour')", (gid,))
        errors = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM command_analytics WHERE guild_id=? AND success=0 AND created_at>=datetime('now','-1 hour')", (gid,))
        tickets = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM tickets WHERE guild_id=? AND created_at>=datetime('now','-1 hour')", (gid,))
        current = int(hour["c"] if hour else 0); baseline = int(day["c"] if day else 0) / 24; err = int(errors["c"] if errors else 0); ticket = int(tickets["c"] if tickets else 0)
        flags = []
        if current >= max(10, baseline * 3): flags.append(f"📈 Command-Spike: **{current}/h** vs. Ø **{baseline:.1f}/h**")
        if err >= 5: flags.append(f"🧯 Fehler-Spike: **{err}** Fehler in 1h")
        if ticket >= 5: flags.append(f"🎫 Ticket-Spike: **{ticket}** neue Tickets in 1h")
        latest = await self.bot.database.fetchone("SELECT cpu_percent,ram_percent FROM system_snapshots_v4 WHERE guild_id=? ORDER BY id DESC LIMIT 1", (gid,))
        if latest and float(latest["ram_percent"] or 0) >= 90: flags.append(f"🧠 RAM auffällig: **{float(latest['ram_percent']):.1f}%**")
        if latest and float(latest["cpu_percent"] or 0) >= 90: flags.append(f"🔥 CPU auffällig: **{float(latest['cpu_percent']):.1f}%**")
        await interaction.response.send_message(embed=_embed("📡 Anomaly Scanner", "\n".join(flags) if flags else "✅ Keine deutlichen Anomalien erkannt.", 0xE74C3C if flags else 0x2ECC71), ephemeral=True)

    @app_commands.command(name="insights", description="Analysiert Nutzung, Fehler und Bot-Funktionsmuster.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def insights(self, interaction: discord.Interaction) -> None:
        gid = int(interaction.guild_id or 0)
        top = await self.bot.database.fetchall("SELECT command_name,COUNT(*) AS c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-30 day') GROUP BY command_name ORDER BY c DESC LIMIT 8", (gid,))
        errors = await self.bot.database.fetchall("SELECT command_name,COUNT(*) AS c FROM command_analytics WHERE guild_id=? AND success=0 AND created_at>=datetime('now','-30 day') GROUP BY command_name ORDER BY c DESC LIMIT 6", (gid,))
        overall = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-30 day')", (gid,)); total = int(overall["c"] if overall else 0)
        e = _embed("🧠 Bot Insights · 30 Tage", f"**{total} Commands** analysiert.", 0x8E44AD)
        e.add_field(name="Meistgenutzt", value="\n".join(f"`/{r['command_name']}` — **{r['c']}**" for r in top)[:1024] or "—", inline=True)
        e.add_field(name="Fehler-Schwerpunkte", value="\n".join(f"`/{r['command_name']}` — **{r['c']} Fehler**" for r in errors)[:1024] or "Keine Fehler.", inline=True)
        if top:
            e.add_field(name="Pattern", value=f"`/{top[0]['command_name']}` macht **{int(top[0]['c'])/max(1,total)*100:.1f}%** der Nutzung aus.", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SmartOps(bot))
