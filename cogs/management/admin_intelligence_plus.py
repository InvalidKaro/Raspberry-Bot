from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

ACCENT = 0x6366F1
GREEN = 0x22C55E
YELLOW = 0xEAB308
RED = 0xEF4444


def card(title: str, text: str, color: int = ACCENT) -> discord.Embed:
    e = discord.Embed(title=title, description=text, color=color)
    e.set_footer(text="Raspberry-Bot · Intelligence Suite")
    return e


def shorten(value: object, limit: int = 180) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def service_state(name: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec("systemctl", "is-active", name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        return stdout.decode().strip() or "unknown"
    except (OSError, asyncio.TimeoutError):
        return "unknown"


class Macro(commands.GroupCog, group_name="macro", group_description="Sichere Bot-Abläufe aus mehreren Aktionen"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="create", description="Speichert einen sicheren Ablauf aus announce/task/reminder-Schritten.")
    @app_commands.default_permissions(manage_guild=True)
    async def create(self, interaction: discord.Interaction, name: str, schritte: str) -> None:
        if interaction.guild_id is None:
            return
        parsed: list[dict[str, object]] = []
        for raw in schritte.split(";"):
            raw = raw.strip()
            if not raw or ":" not in raw:
                continue
            kind, value = raw.split(":", 1)
            kind = kind.strip().lower()
            value = value.strip()
            if kind == "announce" and value:
                parsed.append({"type": "announce", "text": value[:1500]})
            elif kind == "task" and value:
                parsed.append({"type": "task", "title": value[:160]})
            elif kind == "reminder" and "|" in value:
                minutes, text = value.split("|", 1)
                try:
                    mins = max(1, min(10080, int(minutes)))
                except ValueError:
                    continue
                parsed.append({"type": "reminder", "minutes": mins, "text": text[:1000]})
        if not parsed or len(parsed) > 10:
            await interaction.response.send_message("Format z. B. `announce:Start;task:Protokoll;reminder:30|Nachfassen` (max. 10 Schritte).", ephemeral=True)
            return
        await self.bot.database.execute("""INSERT INTO safe_macros(guild_id,name,steps_json,created_by) VALUES(?,?,?,?)
            ON CONFLICT(guild_id,name) DO UPDATE SET steps_json=excluded.steps_json,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP""", (interaction.guild_id, name.lower().strip(), json.dumps(parsed, ensure_ascii=False), interaction.user.id))
        await interaction.response.send_message(embed=card("⚙️ Macro gespeichert", f"`{name}` · **{len(parsed)} Schritte**"), ephemeral=True)

    @app_commands.command(name="run", description="Führt ein gespeichertes Macro aus.")
    @app_commands.default_permissions(manage_guild=True)
    async def run(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            return
        row = await self.bot.database.fetchone("SELECT steps_json FROM safe_macros WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        if not row:
            await interaction.response.send_message("Macro nicht gefunden.", ephemeral=True)
            return
        steps = json.loads(str(row["steps_json"]))
        await interaction.response.defer(ephemeral=True)
        results: list[str] = []
        for step in steps:
            if step.get("type") == "announce":
                if interaction.channel:
                    await interaction.channel.send(embed=card("📣 Macro Announcement", str(step["text"])))
                    results.append("✅ announcement")
            elif step.get("type") == "task":
                await self.bot.database.execute("INSERT INTO workspace_tasks(guild_id,title,status,created_by) VALUES(?,?,?,?)", (interaction.guild_id, str(step["title"]), "open", interaction.user.id))
                results.append("✅ task")
            elif step.get("type") == "reminder":
                due = datetime.now() + timedelta(minutes=int(step["minutes"]))
                await self.bot.database.execute("INSERT INTO reminders(guild_id,channel_id,user_id,message,due_at) VALUES(?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, interaction.user.id, str(step["text"]), due.strftime("%Y-%m-%d %H:%M:%S")))
                results.append("✅ reminder")
        await interaction.followup.send(embed=card("⚙️ Macro ausgeführt", "\n".join(results) or "Keine Schritte ausgeführt."), ephemeral=True)

    @app_commands.command(name="list", description="Listet gespeicherte Macros.")
    async def list_macros(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT name,steps_json FROM safe_macros WHERE guild_id=? ORDER BY name LIMIT 50", (interaction.guild_id,))
        text = "\n".join(f"`{r['name']}` · {len(json.loads(str(r['steps_json'])))} steps" for r in rows) or "Keine Macros."
        await interaction.response.send_message(embed=card("⚙️ Macros", text), ephemeral=True)


class RestorePoint(commands.GroupCog, group_name="restorepoint", group_description="Sichere Snapshots wichtiger Server-Konfiguration"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def capture(self, guild_id: int) -> dict[str, object]:
        settings = await self.bot.database.fetchone("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,))
        staff = await self.bot.database.fetchall("SELECT role_id,permission_level FROM ticket_staff_roles WHERE guild_id=?", (guild_id,))
        access = await self.bot.database.fetchall("SELECT role_id,level FROM bot_access_roles WHERE guild_id=?", (guild_id,))
        monitor = await self.bot.database.fetchone("SELECT * FROM system_monitor_config WHERE guild_id=?", (guild_id,))
        return {"guild_settings": dict(settings) if settings else None, "ticket_staff_roles": [dict(x) for x in staff], "bot_access_roles": [dict(x) for x in access], "system_monitor_config": dict(monitor) if monitor else None}

    @app_commands.command(name="create", description="Erstellt einen Konfigurations-Restorepoint.")
    @app_commands.default_permissions(administrator=True)
    async def create(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None:
            return
        snapshot = await self.capture(interaction.guild_id)
        await self.bot.database.execute("INSERT INTO config_restorepoints(guild_id,name,snapshot_json,created_by) VALUES(?,?,?,?)", (interaction.guild_id, name[:80], json.dumps(snapshot, ensure_ascii=False), interaction.user.id))
        await interaction.response.send_message(embed=card("💾 Restorepoint erstellt", f"**{name}**\nGuild-Settings, Access-Rollen, Ticket-Rollen und Monitor-Konfiguration gespeichert.", GREEN), ephemeral=True)

    @app_commands.command(name="list", description="Listet Restorepoints.")
    @app_commands.default_permissions(administrator=True)
    async def list_points(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT id,name,created_at FROM config_restorepoints WHERE guild_id=? ORDER BY id DESC LIMIT 20", (interaction.guild_id,))
        await interaction.response.send_message(embed=card("💾 Restorepoints", "\n".join(f"`#{r['id']}` · **{r['name']}** · {r['created_at']}" for r in rows) or "Keine Restorepoints."), ephemeral=True)

    @app_commands.command(name="restore", description="Stellt einen Restorepoint kontrolliert wieder her.")
    @app_commands.default_permissions(administrator=True)
    async def restore(self, interaction: discord.Interaction, id: int, bestaetigen: bool = False) -> None:
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone("SELECT snapshot_json,name FROM config_restorepoints WHERE guild_id=? AND id=?", (interaction.guild_id, id))
        if not row:
            await interaction.response.send_message("Restorepoint nicht gefunden.", ephemeral=True)
            return
        if not bestaetigen:
            await interaction.response.send_message(f"Restorepoint **{row['name']}** gefunden. Wiederhole mit `bestaetigen:true`. Vor dem Restore wird automatisch ein neuer Sicherungspunkt angelegt.", ephemeral=True)
            return
        before = await self.capture(interaction.guild_id)
        await self.bot.database.execute("INSERT INTO config_restorepoints(guild_id,name,snapshot_json,created_by) VALUES(?,?,?,?)", (interaction.guild_id, f"auto-before-restore-{id}", json.dumps(before, ensure_ascii=False), interaction.user.id))
        snap = json.loads(str(row["snapshot_json"]))
        gs = snap.get("guild_settings")
        if gs:
            await self.bot.database.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (interaction.guild_id,))
            for col in ("embed_color","ticket_category_id","ticket_log_channel_id","welcome_channel_id","suggestion_channel_id","general_log_channel_id","auto_role_id","welcome_message"):
                if col in gs:
                    await self.bot.database.execute(f"UPDATE guild_settings SET {col}=? WHERE guild_id=?", (gs[col], interaction.guild_id))
        await self.bot.database.execute("DELETE FROM ticket_staff_roles WHERE guild_id=?", (interaction.guild_id,))
        for item in snap.get("ticket_staff_roles", []):
            await self.bot.database.execute("INSERT INTO ticket_staff_roles(guild_id,role_id,permission_level) VALUES(?,?,?)", (interaction.guild_id, item["role_id"], item["permission_level"]))
        await self.bot.database.execute("DELETE FROM bot_access_roles WHERE guild_id=?", (interaction.guild_id,))
        for item in snap.get("bot_access_roles", []):
            await self.bot.database.execute("INSERT INTO bot_access_roles(guild_id,role_id,level,created_by) VALUES(?,?,?,?)", (interaction.guild_id, item["role_id"], item["level"], interaction.user.id))
        await interaction.response.send_message(embed=card("♻️ Restore abgeschlossen", f"**{row['name']}** wurde wiederhergestellt.\nAutomatischer Pre-Restore-Snapshot wurde angelegt.", GREEN), ephemeral=True)


class AdminIntelligencePlus(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.database.execute("""CREATE TABLE IF NOT EXISTS safe_macros(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,steps_json TEXT NOT NULL,created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name))""")
        await self.bot.database.execute("""CREATE TABLE IF NOT EXISTS config_restorepoints(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,snapshot_json TEXT NOT NULL,created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    @app_commands.command(name="handover", description="Erstellt eine kompakte Übergabe aus offenen Workspace-Daten.")
    async def handover(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        tasks = await self.bot.database.fetchall("SELECT title,status,due_at FROM workspace_tasks WHERE guild_id=? AND status NOT IN ('done','closed') ORDER BY due_at IS NULL,due_at LIMIT 8", (interaction.guild_id,))
        events = await self.bot.database.fetchall("SELECT title,starts_at FROM workspace_events WHERE guild_id=? AND starts_at>=datetime('now') ORDER BY starts_at LIMIT 6", (interaction.guild_id,))
        tickets = await self.bot.database.fetchall("SELECT id,subject,priority,status FROM tickets WHERE guild_id=? AND status!='closed' ORDER BY id DESC LIMIT 6", (interaction.guild_id,))
        planner = await self.bot.database.fetchall("SELECT event_date,start_time,title FROM planner_entries WHERE guild_id=? AND event_date>=date('now') ORDER BY event_date,start_time LIMIT 6", (interaction.guild_id,))
        sections = ["**OFFENE TASKS**"]
        sections += [f"• {shorten(x['title'],80)} · `{x['status']}`" + (f" · {x['due_at']}" if x['due_at'] else "") for x in tasks] or ["• keine"]
        sections += ["\n**NÄCHSTE EVENTS**"] + ([f"• {x['starts_at']} · {shorten(x['title'],80)}" for x in events] or ["• keine"])
        sections += ["\n**OFFENE TICKETS**"] + ([f"• `#{x['id']}` {shorten(x['subject'],70)} · {x['priority']}" for x in tickets] or ["• keine"])
        sections += ["\n**PLANER**"] + ([f"• {x['event_date']} {x['start_time']} · {shorten(x['title'],70)}" for x in planner] or ["• keine"])
        await interaction.response.send_message(embed=card("📋 Handover", "\n".join(sections), ACCENT))

    @app_commands.command(name="permissionmap", description="Zeigt effektive Rechte eines Users oder einer Rolle in einem Channel.")
    @app_commands.default_permissions(manage_guild=True)
    async def permissionmap(self, interaction: discord.Interaction, mitglied: discord.Member | None = None, rolle: discord.Role | None = None, kanal: discord.TextChannel | None = None) -> None:
        if interaction.guild is None:
            return
        if bool(mitglied) == bool(rolle):
            await interaction.response.send_message("Bitte genau **Mitglied oder Rolle** angeben.", ephemeral=True)
            return
        channel = kanal or (interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None)
        if mitglied:
            perms = channel.permissions_for(mitglied) if channel else mitglied.guild_permissions
            name = mitglied.mention
        else:
            assert rolle is not None
            perms = rolle.permissions
            if channel:
                overwrite = channel.overwrites_for(rolle)
                allow, deny = overwrite.pair()
                perms = (perms | allow) & ~deny
            name = rolle.mention
        keys = ["administrator","manage_guild","manage_roles","manage_channels","manage_webhooks","manage_messages","kick_members","ban_members","moderate_members","view_audit_log","send_messages","view_channel","mention_everyone"]
        lines = [f"{'✅' if getattr(perms,k,False) else '—'} `{k}`" for k in keys]
        await interaction.response.send_message(embed=card("🗺️ Permission Map", f"Ziel: {name}\nChannel: {channel.mention if channel else 'Serverweit'}\n\n" + "\n".join(lines)), ephemeral=True)

    @app_commands.command(name="roleaudit", description="Findet riskante, leere oder problematisch platzierte Rollen.")
    @app_commands.default_permissions(manage_roles=True)
    async def roleaudit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        guild = interaction.guild
        me = guild.me
        risky, empty, above = [], [], []
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            p = role.permissions
            flags = [name for name in ("administrator","manage_guild","manage_roles","manage_webhooks","ban_members") if getattr(p,name)]
            if flags:
                risky.append(f"{role.mention} · `{', '.join(flags)}`")
            if not role.members:
                empty.append(role.name)
            if me and role >= me.top_role:
                above.append(role.name)
        everyone = guild.default_role.permissions
        warnings = []
        if everyone.mention_everyone:
            warnings.append("@everyone kann everyone/here erwähnen")
        if everyone.manage_messages:
            warnings.append("@everyone kann Nachrichten verwalten")
        text = "**Riskante Rollen**\n" + ("\n".join(risky[:12]) or "keine")
        text += "\n\n**Über/auf Bot-Hierarchie**\n" + (", ".join(above[:15]) or "keine")
        text += "\n\n**Leere Rollen**\n" + (", ".join(empty[:20]) or "keine")
        if warnings:
            text += "\n\n**@everyone Warnungen**\n" + "\n".join("⚠️ " + x for x in warnings)
        await interaction.response.send_message(embed=card("🛡️ Role Audit", text, YELLOW if risky or warnings else GREEN), ephemeral=True)

    async def health_data(self, guild_id: int) -> dict[str, object]:
        db_ok = True
        try:
            await self.bot.database.fetchone("SELECT 1 ok")
        except Exception:
            db_ok = False
        db_path = Path(self.bot.settings.database_path)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        disk = shutil.disk_usage(db_path.parent if db_path.parent.exists() else Path.cwd())
        latest = await self.bot.database.fetchone("SELECT pihole_ok,tailscale_ok,temperature,ram_percent,cpu_percent FROM system_snapshots_v4 WHERE guild_id=? ORDER BY id DESC LIMIT 1", (guild_id,))
        return {"db": db_ok, "db_writable": os.access(db_path.parent, os.W_OK), "latency_ms": round(self.bot.latency * 1000), "disk_percent": round((disk.used / max(1,disk.total))*100,1), "bot_service": await service_state("raspberry-bot"), "dashboard_service": await service_state("raspberry-dashboard"), "snapshot": dict(latest) if latest else None}

    @app_commands.command(name="healthcheck", description="Vollständiger Selbsttest für Bot, DB, Systemd und letzte Systemmetriken.")
    @app_commands.default_permissions(manage_guild=True)
    async def healthcheck(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True)
        data = await self.health_data(interaction.guild_id)
        snap = data["snapshot"] or {}
        lines = [f"{'✅' if data['db'] else '❌'} SQLite connection", f"{'✅' if data['db_writable'] else '❌'} DB directory writable", f"{'✅' if data['bot_service']=='active' else '⚠️'} raspberry-bot · `{data['bot_service']}`", f"{'✅' if data['dashboard_service']=='active' else '⚠️'} raspberry-dashboard · `{data['dashboard_service']}`", f"📡 Discord latency · **{data['latency_ms']} ms**", f"💽 Disk · **{data['disk_percent']}%**"]
        if snap:
            lines += [f"🌡️ Temp · **{snap.get('temperature')}°C**", f"🧠 RAM · **{snap.get('ram_percent')}%**", f"🧱 Pi-hole · **{'OK' if snap.get('pihole_ok') else 'check'}**", f"🔐 Tailscale · **{'OK' if snap.get('tailscale_ok') else 'check'}**"]
        await interaction.followup.send(embed=card("🩺 Healthcheck", "\n".join(lines), GREEN if data['db'] and data['disk_percent'] < 90 else YELLOW), ephemeral=True)

    @app_commands.command(name="diagnose", description="Analysiert typische Bot-Probleme und nennt wahrscheinliche Ursachen.")
    @app_commands.default_permissions(manage_guild=True)
    async def diagnose(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True)
        data = await self.health_data(interaction.guild_id)
        errors = await self.bot.database.fetchone("SELECT COUNT(*) c FROM command_analytics WHERE guild_id=? AND success=0 AND created_at>=datetime('now','-24 hours')", (interaction.guild_id,))
        pending = await self.bot.database.fetchone("SELECT COUNT(*) c FROM dashboard_commands WHERE status='pending' AND created_at<datetime('now','-2 minutes')")
        open_tickets = await self.bot.database.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND status!='closed'", (interaction.guild_id,))
        issues: list[str] = []
        if not data["db"]:
            issues.append("❌ SQLite nicht erreichbar → Bot-DB/Dateipfad prüfen.")
        if not data["db_writable"]:
            issues.append("❌ DB-Verzeichnis nicht beschreibbar → Rechte/Owner prüfen.")
        if data["disk_percent"] >= 90:
            issues.append(f"❌ Datenträger bei {data['disk_percent']}% → Logs/Backups prüfen.")
        elif data["disk_percent"] >= 80:
            issues.append(f"⚠️ Datenträger bei {data['disk_percent']}%.")
        if data["latency_ms"] > 500:
            issues.append(f"⚠️ Discord-Latenz hoch ({data['latency_ms']} ms) → Netzwerk prüfen.")
        if int(errors["c"]) >= 10:
            issues.append(f"⚠️ {errors['c']} fehlgeschlagene Commands in 24h → `/insights` prüfen.")
        if int(pending["c"]) > 0:
            issues.append(f"⚠️ {pending['c']} Dashboard-Kommandos hängen >2 Minuten → Bot-Worker prüfen.")
        if not issues:
            issues.append("✅ Keine offensichtliche Ursache gefunden. Kernsysteme sehen gesund aus.")
        issues.append(f"ℹ️ Offene Tickets: **{open_tickets['c']}**")
        await interaction.followup.send(embed=card("🔬 Diagnose", "\n".join(issues), GREEN if issues[0].startswith('✅') else YELLOW), ephemeral=True)

    @app_commands.command(name="configdiff", description="Vergleicht zwei Konfigurations-Restorepoints.")
    @app_commands.default_permissions(administrator=True)
    async def configdiff(self, interaction: discord.Interaction, restorepoint_a: int, restorepoint_b: int) -> None:
        if interaction.guild_id is None:
            return
        rows = []
        for rid in (restorepoint_a, restorepoint_b):
            row = await self.bot.database.fetchone("SELECT name,snapshot_json FROM config_restorepoints WHERE guild_id=? AND id=?", (interaction.guild_id, rid))
            if not row:
                await interaction.response.send_message(f"Restorepoint #{rid} nicht gefunden.", ephemeral=True)
                return
            rows.append((str(row['name']), json.loads(str(row['snapshot_json']))))
        a, b = rows[0][1], rows[1][1]
        diffs = [f"• **{section}** geändert" for section in sorted(set(a) | set(b)) if a.get(section) != b.get(section)]
        await interaction.response.send_message(embed=card("🧬 Config Diff", f"`#{restorepoint_a}` **{rows[0][0]}** ↔ `#{restorepoint_b}` **{rows[1][0]}**\n\n" + ("\n".join(diffs) or "✅ Keine Unterschiede.")), ephemeral=True)

    @app_commands.command(name="pulse", description="Kompakte Live-Ansicht zu Aktivität, Games, Tickets, Tasks und Events.")
    async def pulse(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        gid = interaction.guild_id
        commands_1h = await self.bot.database.fetchone("SELECT COUNT(*) c,COUNT(DISTINCT user_id) u FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-1 hour')", (gid,))
        tickets = await self.bot.database.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND status!='closed'", (gid,))
        tasks = await self.bot.database.fetchone("SELECT COUNT(*) c FROM workspace_tasks WHERE guild_id=? AND status NOT IN ('done','closed')", (gid,))
        events = await self.bot.database.fetchone("SELECT COUNT(*) c FROM workspace_events WHERE guild_id=? AND starts_at>=datetime('now') AND starts_at<datetime('now','+7 days')", (gid,))
        games = await self.bot.database.fetchone("SELECT COUNT(*) c FROM game_match_history WHERE guild_id=? AND played_at>=datetime('now','-1 hour')", (gid,))
        text = f"⌘ Commands 1h · **{commands_1h['c']}** von **{commands_1h['u']} Usern**\n🎮 Matches 1h · **{games['c']}**\n🎫 Offene Tickets · **{tickets['c']}**\n✅ Offene Tasks · **{tasks['c']}**\n📅 Events nächste 7 Tage · **{events['c']}**\n📡 Discord · **{self.bot.latency*1000:.0f} ms**"
        await interaction.response.send_message(embed=card("💓 Server Pulse", text, GREEN))

    @app_commands.command(name="anomaly", description="Sucht automatisch nach ungewöhnlicher Aktivität oder Fehlerspitzen.")
    @app_commands.default_permissions(manage_guild=True)
    async def anomaly(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        gid = interaction.guild_id
        now = await self.bot.database.fetchone("SELECT COUNT(*) c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-1 hour')", (gid,))
        before = await self.bot.database.fetchone("SELECT COUNT(*) c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-7 hours') AND created_at<datetime('now','-1 hour')", (gid,))
        errors = await self.bot.database.fetchone("SELECT COUNT(*) total,SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) bad FROM command_analytics WHERE guild_id=? AND created_at>=datetime('now','-1 hour')", (gid,))
        new_tickets = await self.bot.database.fetchone("SELECT COUNT(*) c FROM tickets WHERE guild_id=? AND created_at>=datetime('now','-1 hour')", (gid,))
        baseline = float(before['c']) / 6 if before else 0
        findings: list[str] = []
        if baseline >= 2 and int(now['c']) > baseline * 3:
            findings.append(f"📈 Command-Spike: **{now['c']}** vs Ø **{baseline:.1f}/h**")
        total = int(errors['total'] or 0)
        bad = int(errors['bad'] or 0)
        if total >= 5 and bad / total >= .25:
            findings.append(f"⚠️ Error-Rate: **{bad}/{total} ({bad/total:.0%})**")
        if int(new_tickets['c']) >= 5:
            findings.append(f"🎫 Ticket-Spike: **{new_tickets['c']}** neue Tickets in 1h")
        await interaction.response.send_message(embed=card("📡 Anomaly Scan", "\n".join(findings) if findings else "✅ Keine auffälligen Muster im aktuellen Zeitfenster.", YELLOW if findings else GREEN), ephemeral=True)

    @app_commands.command(name="insights", description="Analysiert Nutzung, Stoßzeiten, Fehler und kaum genutzte Features.")
    @app_commands.default_permissions(manage_guild=True)
    async def insights(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        gid = interaction.guild_id
        top = await self.bot.database.fetchall("SELECT command_name,COUNT(*) c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-30 days') GROUP BY command_name ORDER BY c DESC LIMIT 5", (gid,))
        low = await self.bot.database.fetchall("SELECT command_name,COUNT(*) c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-30 days') GROUP BY command_name HAVING c<=2 ORDER BY c,command_name LIMIT 8", (gid,))
        hour = await self.bot.database.fetchone("SELECT strftime('%H',created_at) h,COUNT(*) c FROM command_usage WHERE guild_id=? AND created_at>=datetime('now','-30 days') GROUP BY h ORDER BY c DESC LIMIT 1", (gid,))
        errors = await self.bot.database.fetchall("SELECT COALESCE(error_type,'unknown') e,COUNT(*) c FROM command_analytics WHERE guild_id=? AND success=0 AND created_at>=datetime('now','-30 days') GROUP BY e ORDER BY c DESC LIMIT 5", (gid,))
        text = "**Top Commands**\n" + ("\n".join(f"• `{x['command_name']}` · {x['c']}" for x in top) or "keine")
        text += "\n\n**Kaum genutzt**\n" + (", ".join(f"`{x['command_name']}`" for x in low) or "keine")
        text += f"\n\n**Peak Hour**\n{hour['h']}:00 UTC · {hour['c']} Commands" if hour else "\n\n**Peak Hour**\nkeine Daten"
        text += "\n\n**Häufige Fehler**\n" + ("\n".join(f"• `{x['e']}` · {x['c']}" for x in errors) or "keine")
        await interaction.response.send_message(embed=card("🧠 Bot Insights · 30 Tage", text), ephemeral=True)

    @app_commands.command(name="timeline", description="Baut eine chronologische Timeline aus Audit, Tasks, Events und Tickets.")
    async def timeline(self, interaction: discord.Interaction, limit: app_commands.Range[int, 5, 30] = 15) -> None:
        if interaction.guild_id is None:
            return
        gid = interaction.guild_id
        entries: list[tuple[str,str,str]] = []
        audit = await self.bot.database.fetchall("SELECT created_at,action,target_type FROM bot_audit_log WHERE guild_id=? ORDER BY id DESC LIMIT ?", (gid, int(limit)))
        for x in audit:
            entries.append((str(x['created_at']), "AUDIT", f"{x['action']} · {x['target_type'] or '-'}"))
        tickets = await self.bot.database.fetchall("SELECT created_at,id,subject FROM tickets WHERE guild_id=? ORDER BY id DESC LIMIT ?", (gid, int(limit)//2))
        for x in tickets:
            entries.append((str(x['created_at']), "TICKET", f"#{x['id']} {shorten(x['subject'],90)}"))
        tasks = await self.bot.database.fetchall("SELECT created_at,title FROM workspace_tasks WHERE guild_id=? ORDER BY id DESC LIMIT ?", (gid, int(limit)//2))
        for x in tasks:
            entries.append((str(x['created_at']), "TASK", shorten(x['title'],100)))
        events = await self.bot.database.fetchall("SELECT created_at,title FROM workspace_events WHERE guild_id=? ORDER BY id DESC LIMIT ?", (gid, int(limit)//2))
        for x in events:
            entries.append((str(x['created_at']), "EVENT", shorten(x['title'],100)))
        entries.sort(key=lambda x: x[0], reverse=True)
        text = "\n".join(f"`{ts}` **{kind}** · {desc}" for ts,kind,desc in entries[:int(limit)]) or "Keine Timeline-Daten."
        await interaction.response.send_message(embed=card("🕓 Server Timeline", text))


async def setup(bot: commands.Bot) -> None:
    suite = AdminIntelligencePlus(bot)
    await bot.add_cog(suite)
    await bot.add_cog(Macro(bot))
    await bot.add_cog(RestorePoint(bot))
