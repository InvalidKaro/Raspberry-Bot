from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks


def _embed(title: str, description: str, color: int = 0x5865F2) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(UTC))
    e.set_footer(text="Raspberry-Bot · Utility Suite")
    return e


def _parse_when(raw: str) -> datetime:
    value = raw.strip().lower()
    now = datetime.now().astimezone()
    m = re.fullmatch(r"in\s+(\d+)\s*([mhd])", value)
    if m:
        amount = int(m.group(1))
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[m.group(2)]
        return (now + delta).astimezone(UTC)
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=now.tzinfo).astimezone(UTC)
        except ValueError:
            pass
    raise ValueError("Nutze `in 30m`, `in 2h`, `in 3d`, `YYYY-MM-DD HH:MM` oder `DD.MM.YYYY HH:MM`.")


class PaletteView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=180)

    @discord.ui.button(label="Workspace", emoji="🗂️", style=discord.ButtonStyle.primary)
    async def workspace(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=_embed("Workspace", "`/workspace` · Tasks, Events, Wiki, Trainings und Planner."), ephemeral=True)

    @discord.ui.button(label="Arcade", emoji="🎮", style=discord.ButtonStyle.success)
    async def arcade(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=_embed("Arcade", "Öffne `/arcade menu` für Games, Duelle und Community-Minigames.", 0x57F287), ephemeral=True)

    @discord.ui.button(label="System", emoji="🧠", style=discord.ButtonStyle.secondary)
    async def system(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=_embed("Smart Ops", "`/ops healthcheck` · `/ops diagnose` · `/ops pulse` · `/ops insights`"), ephemeral=True)

    @discord.ui.button(label="Creator", emoji="✨", style=discord.ButtonStyle.secondary)
    async def creator(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=_embed("Creator", "`/creator` enthält Announcement-, Embed-, Form- und Panel-Tools."), ephemeral=True)


class DropView(discord.ui.View):
    def __init__(self, bot: commands.Bot, drop_id: int, limit: int, role_id: int | None) -> None:
        super().__init__(timeout=86400)
        self.bot, self.drop_id, self.limit, self.role_id = bot, drop_id, limit, role_id

    @discord.ui.button(label="CLAIM", emoji="⚡", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        old = await self.bot.database.fetchone("SELECT 1 FROM utility_drop_claims WHERE drop_id=? AND user_id=?", (self.drop_id, interaction.user.id))
        if old:
            await interaction.response.send_message("Du hast diesen Drop bereits geclaimt.", ephemeral=True)
            return
        row = await self.bot.database.fetchone("SELECT COUNT(*) AS c FROM utility_drop_claims WHERE drop_id=?", (self.drop_id,))
        count = int(row["c"] if row else 0)
        if count >= self.limit:
            button.disabled = True
            await interaction.response.edit_message(view=self)
            return
        await self.bot.database.execute("INSERT INTO utility_drop_claims(drop_id,user_id) VALUES(?,?)", (self.drop_id, interaction.user.id))
        role_text = ""
        if self.role_id and isinstance(interaction.user, discord.Member):
            role = interaction.guild.get_role(self.role_id)
            if role:
                try:
                    await interaction.user.add_roles(role, reason="Utility Drop claim")
                    role_text = f"\nRolle erhalten: {role.mention}"
                except discord.HTTPException:
                    role_text = "\nDie Rolle konnte nicht automatisch vergeben werden."
        count += 1
        if count >= self.limit:
            button.disabled = True
        await interaction.response.send_message(f"⚡ Claim **{count}/{self.limit}** erfolgreich.{role_text}", ephemeral=True)
        if button.disabled and interaction.message:
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass


class SecretVoteView(discord.ui.View):
    def __init__(self, bot: commands.Bot, vote_id: int, a: str, b: str) -> None:
        super().__init__(timeout=86400)
        self.bot, self.vote_id = bot, vote_id
        self.a.label = a[:80]
        self.b.label = b[:80]

    async def _vote(self, interaction: discord.Interaction, choice: str) -> None:
        await self.bot.database.execute(
            """INSERT INTO utility_secret_vote_choices(vote_id,user_id,choice) VALUES(?,?,?)
            ON CONFLICT(vote_id,user_id) DO UPDATE SET choice=excluded.choice,updated_at=CURRENT_TIMESTAMP""",
            (self.vote_id, interaction.user.id, choice),
        )
        await interaction.response.send_message("🔒 Deine geheime Stimme wurde gespeichert.", ephemeral=True)

    @discord.ui.button(label="A", style=discord.ButtonStyle.primary)
    async def a(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._vote(interaction, "A")

    @discord.ui.button(label="B", style=discord.ButtonStyle.secondary)
    async def b(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._vote(interaction, "B")


class UtilityPlus(commands.GroupCog, group_name="utilityplus", group_description="Zeitkapseln, Macros, Secret Votes, Drops und Community-Tools"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scheduler.start()

    async def cog_load(self) -> None:
        for sql in (
            """CREATE TABLE IF NOT EXISTS utility_scheduled(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,guild_id INTEGER,channel_id INTEGER NOT NULL,message_id INTEGER,user_id INTEGER NOT NULL,payload TEXT NOT NULL,due_at TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS utility_macros(guild_id INTEGER NOT NULL,name TEXT NOT NULL,steps TEXT NOT NULL,created_by INTEGER NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,name))""",
            """CREATE TABLE IF NOT EXISTS utility_drops(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,message_id INTEGER,title TEXT NOT NULL,claim_limit INTEGER NOT NULL,role_id INTEGER,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS utility_drop_claims(drop_id INTEGER NOT NULL,user_id INTEGER NOT NULL,claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(drop_id,user_id))""",
            """CREATE TABLE IF NOT EXISTS utility_secret_votes(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,message_id INTEGER,question TEXT NOT NULL,option_a TEXT NOT NULL,option_b TEXT NOT NULL,closes_at TEXT NOT NULL,closed INTEGER NOT NULL DEFAULT 0,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS utility_secret_vote_choices(vote_id INTEGER NOT NULL,user_id INTEGER NOT NULL,choice TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(vote_id,user_id))""",
        ):
            await self.bot.database.execute(sql)

    async def cog_unload(self) -> None:
        self.scheduler.cancel()

    @tasks.loop(seconds=20)
    async def scheduler(self) -> None:
        now = datetime.now(UTC).isoformat()
        rows = await self.bot.database.fetchall("SELECT * FROM utility_scheduled WHERE done=0 AND due_at<=? ORDER BY due_at LIMIT 25", (now,))
        for row in rows:
            try:
                channel = self.bot.get_channel(int(row["channel_id"])) or await self.bot.fetch_channel(int(row["channel_id"]))
                if row["kind"] == "timecapsule" and isinstance(channel, discord.abc.Messageable):
                    await channel.send(embed=_embed("⏳ Time Capsule geöffnet", f"<@{row['user_id']}> hat diese Nachricht für später versiegelt:\n\n{row['payload']}", 0x9B59B6), allowed_mentions=discord.AllowedMentions(users=True))
                elif row["kind"] == "deadman" and row["message_id"]:
                    try:
                        msg = await channel.fetch_message(int(row["message_id"]))
                        await msg.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                await self.bot.database.execute("UPDATE utility_scheduled SET done=1 WHERE id=?", (row["id"],))
            except Exception:
                continue
        votes = await self.bot.database.fetchall("SELECT * FROM utility_secret_votes WHERE closed=0 AND closes_at<=? ORDER BY closes_at LIMIT 20", (now,))
        for vote in votes:
            counts = await self.bot.database.fetchall("SELECT choice,COUNT(*) AS c FROM utility_secret_vote_choices WHERE vote_id=? GROUP BY choice", (vote["id"],))
            score = {"A": 0, "B": 0}
            for r in counts:
                score[str(r["choice"])] = int(r["c"])
            total = score["A"] + score["B"]
            desc = f"**{vote['question']}**\n\n🅰️ **{vote['option_a']}** — {score['A']} Stimmen\n🅱️ **{vote['option_b']}** — {score['B']} Stimmen\n\nGesamt: **{total}** · Stimmen waren bis zum Ende geheim."
            try:
                channel = self.bot.get_channel(int(vote["channel_id"])) or await self.bot.fetch_channel(int(vote["channel_id"]))
                msg = await channel.fetch_message(int(vote["message_id"]))
                await msg.edit(embed=_embed("🗳️ Secret Vote · Ergebnis", desc), view=None)
            except Exception:
                pass
            await self.bot.database.execute("UPDATE utility_secret_votes SET closed=1 WHERE id=?", (vote["id"],))

    @scheduler.before_loop
    async def before_scheduler(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="timecapsule", description="Versiegelt eine Nachricht bis zu einem späteren Zeitpunkt.")
    async def timecapsule(self, interaction: discord.Interaction, wann: str, nachricht: str) -> None:
        if interaction.channel_id is None:
            return
        try:
            due = _parse_when(wann)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if due <= datetime.now(UTC):
            await interaction.response.send_message("Der Zeitpunkt muss in der Zukunft liegen.", ephemeral=True)
            return
        await self.bot.database.execute("INSERT INTO utility_scheduled(kind,guild_id,channel_id,user_id,payload,due_at) VALUES('timecapsule',?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, interaction.user.id, nachricht[:1800], due.isoformat()))
        await interaction.response.send_message(embed=_embed("⏳ Time Capsule versiegelt", f"Öffnet {discord.utils.format_dt(due, style='R')}.\nDer Inhalt bleibt bis dahin verborgen.", 0x9B59B6))

    @app_commands.command(name="deadman", description="Sendet eine temporäre Nachricht, die später automatisch gelöscht wird.")
    async def deadman(self, interaction: discord.Interaction, minuten: app_commands.Range[int, 1, 10080], text: str) -> None:
        if interaction.channel_id is None:
            return
        await interaction.response.send_message(embed=_embed("💨 Temporäre Nachricht", text[:3500], 0x95A5A6))
        msg = await interaction.original_response()
        due = datetime.now(UTC) + timedelta(minutes=int(minuten))
        await self.bot.database.execute("INSERT INTO utility_scheduled(kind,guild_id,channel_id,message_id,user_id,payload,due_at) VALUES('deadman',?,?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, msg.id, interaction.user.id, "", due.isoformat()))

    @app_commands.command(name="handover", description="Erstellt eine kompakte Übergabe aus Tasks, Events und Planner.")
    @app_commands.guild_only()
    async def handover(self, interaction: discord.Interaction) -> None:
        gid = int(interaction.guild_id or 0)
        tasks_rows = await self.bot.database.fetchall("SELECT title,status,due_at FROM workspace_tasks WHERE guild_id=? AND status NOT IN('done','closed') ORDER BY due_at IS NULL,due_at LIMIT 8", (gid,))
        event_rows = await self.bot.database.fetchall("SELECT title,starts_at FROM workspace_events WHERE guild_id=? ORDER BY starts_at LIMIT 6", (gid,))
        planner = await self.bot.database.fetchall("SELECT event_date,start_time,title FROM planner_entries WHERE guild_id=? ORDER BY event_date,start_time LIMIT 6", (gid,))
        t = "\n".join(f"• **{r['title']}** · `{r['status']}`" for r in tasks_rows) or "—"
        e = "\n".join(f"• **{r['title']}** · {r['starts_at']}" for r in event_rows) or "—"
        p = "\n".join(f"• `{r['event_date']} {r['start_time']}` · {r['title']}" for r in planner) or "—"
        emb = _embed("📋 Handover", "Automatisch aus deinem Workspace zusammengestellt.", 0x3498DB)
        emb.add_field(name="Offene Tasks", value=t[:1024], inline=False); emb.add_field(name="Events", value=e[:1024], inline=False); emb.add_field(name="Planner", value=p[:1024], inline=False)
        await interaction.response.send_message(embed=emb)

    @app_commands.command(name="commandpalette", description="Öffnet eine kompakte interaktive Bot-Befehlszentrale.")
    async def commandpalette(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_embed("⌘ Command Palette", "Eine kompakte Zentrale für die wichtigsten Bereiche.\n\nWähle unten einen Bereich."), view=PaletteView(), ephemeral=True)

    @app_commands.command(name="macro", description="Speichert oder führt sichere Multi-Step-Abläufe aus.")
    @app_commands.default_permissions(manage_guild=True)
    async def macro(self, interaction: discord.Interaction, aktion: str, name: str = "", schritte: str = "") -> None:
        if interaction.guild_id is None:
            return
        action = aktion.lower().strip()
        if action == "list":
            rows = await self.bot.database.fetchall("SELECT name,steps FROM utility_macros WHERE guild_id=? ORDER BY name LIMIT 30", (interaction.guild_id,))
            await interaction.response.send_message(embed=_embed("⚙️ Macros", "\n".join(f"• `{r['name']}` — {str(r['steps'])[:100]}" for r in rows) or "Noch keine Macros."), ephemeral=True)
            return
        if not name.strip():
            await interaction.response.send_message("Ein Name ist erforderlich.", ephemeral=True); return
        if action == "save":
            await self.bot.database.execute("""INSERT INTO utility_macros(guild_id,name,steps,created_by) VALUES(?,?,?,?) ON CONFLICT(guild_id,name) DO UPDATE SET steps=excluded.steps,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP""", (interaction.guild_id, name.strip().lower(), schritte[:3500], interaction.user.id))
            await interaction.response.send_message(f"Macro `{name}` gespeichert.", ephemeral=True); return
        if action == "delete":
            await self.bot.database.execute("DELETE FROM utility_macros WHERE guild_id=? AND name=?", (interaction.guild_id, name.strip().lower()))
            await interaction.response.send_message(f"Macro `{name}` gelöscht.", ephemeral=True); return
        if action != "run":
            await interaction.response.send_message("Aktion muss `save`, `run`, `list` oder `delete` sein.", ephemeral=True); return
        row = await self.bot.database.fetchone("SELECT steps FROM utility_macros WHERE guild_id=? AND name=?", (interaction.guild_id, name.strip().lower()))
        if not row:
            await interaction.response.send_message("Macro nicht gefunden.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        executed = []
        for raw in str(row["steps"]).split(";")[:10]:
            step = raw.strip()
            if step.startswith("say:") and interaction.channel:
                await interaction.channel.send(step[4:].strip()[:1900]); executed.append("say")
            elif step.startswith("task:"):
                title = step[5:].strip()
                if title:
                    await self.bot.database.execute("INSERT INTO workspace_tasks(guild_id,title,status,created_by) VALUES(?,?,'open',?)", (interaction.guild_id, title[:250], interaction.user.id)); executed.append("task")
            elif step.startswith("reminder:"):
                try:
                    _, mins, text = step.split(":", 2); due = datetime.now(UTC) + timedelta(minutes=max(1, int(mins)))
                    await self.bot.database.execute("INSERT INTO reminders(guild_id,channel_id,user_id,message,due_at) VALUES(?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, interaction.user.id, text[:1500], due.isoformat())); executed.append("reminder")
                except (ValueError, TypeError):
                    pass
            elif step.startswith("embed:") and interaction.channel:
                title, _, text = step[6:].partition("|"); await interaction.channel.send(embed=_embed(title[:250] or "Macro", text[:3500])); executed.append("embed")
        await interaction.followup.send(f"Macro ausgeführt: **{len(executed)}** Schritte · `{', '.join(executed) or 'none'}`", ephemeral=True)

    @app_commands.command(name="timeline", description="Baut eine kompakte Timeline aus Audit, Tasks und Events.")
    @app_commands.guild_only()
    async def timeline(self, interaction: discord.Interaction) -> None:
        gid = int(interaction.guild_id or 0)
        audit = await self.bot.database.fetchall("SELECT action,created_at FROM bot_audit_log WHERE guild_id=? ORDER BY id DESC LIMIT 8", (gid,))
        tasks_rows = await self.bot.database.fetchall("SELECT title,updated_at FROM workspace_tasks WHERE guild_id=? ORDER BY id DESC LIMIT 6", (gid,))
        events = [(str(r["created_at"]), f"Audit · {r['action']}") for r in audit] + [(str(r["updated_at"]), f"Task · {r['title']}") for r in tasks_rows]
        events.sort(key=lambda x: x[0], reverse=True)
        await interaction.response.send_message(embed=_embed("🕒 Server Timeline", "\n".join(f"`{ts[:16]}`  {label}" for ts, label in events[:12]) or "Keine Timeline-Daten vorhanden.", 0x2ECC71))

    @app_commands.command(name="linkhub", description="Erstellt ein hochwertiges Link-Panel.")
    @app_commands.default_permissions(manage_guild=True)
    async def linkhub(self, interaction: discord.Interaction, titel: str, links: str) -> None:
        view = discord.ui.View(timeout=None); valid = 0
        for item in links.split(",")[:5]:
            label, sep, url = item.strip().partition("|")
            if sep and url.startswith(("https://", "http://")):
                view.add_item(discord.ui.Button(label=label.strip()[:80] or "Link", url=url.strip())); valid += 1
        if not valid:
            await interaction.response.send_message("Format: `Website|https://example.com, Docs|https://...`", ephemeral=True); return
        await interaction.response.send_message(embed=_embed(f"🔗 {titel[:240]}", "Alle wichtigen Links an einem Ort.", 0x1ABC9C), view=view)

    @app_commands.command(name="drop", description="Erstellt einen limitierten First-Come-First-Serve Drop.")
    @app_commands.default_permissions(manage_guild=True)
    async def drop(self, interaction: discord.Interaction, titel: str, anzahl: app_commands.Range[int, 1, 100] = 1, rolle: discord.Role | None = None) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            return
        drop_id = await self.bot.database.execute("INSERT INTO utility_drops(guild_id,channel_id,title,claim_limit,role_id,created_by) VALUES(?,?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, titel[:250], int(anzahl), rolle.id if rolle else None, interaction.user.id))
        view = DropView(self.bot, drop_id, int(anzahl), rolle.id if rolle else None)
        await interaction.response.send_message(embed=_embed("⚡ LIMITED DROP", f"**{titel}**\n\nDie ersten **{anzahl}** Personen können claimen." + (f"\nReward: {rolle.mention}" if rolle else ""), 0xF1C40F), view=view)
        msg = await interaction.original_response(); await self.bot.database.execute("UPDATE utility_drops SET message_id=? WHERE id=?", (msg.id, drop_id))

    @app_commands.command(name="secretvote", description="Startet eine Abstimmung ohne sichtbare Zwischenergebnisse.")
    @app_commands.default_permissions(manage_messages=True)
    async def secretvote(self, interaction: discord.Interaction, frage: str, option_a: str, option_b: str, minuten: app_commands.Range[int, 1, 1440] = 10) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            return
        closes = datetime.now(UTC) + timedelta(minutes=int(minuten))
        vote_id = await self.bot.database.execute("INSERT INTO utility_secret_votes(guild_id,channel_id,question,option_a,option_b,closes_at,created_by) VALUES(?,?,?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, frage[:500], option_a[:200], option_b[:200], closes.isoformat(), interaction.user.id))
        view = SecretVoteView(self.bot, vote_id, option_a, option_b)
        await interaction.response.send_message(embed=_embed("🗳️ Secret Vote", f"**{frage}**\n\nDie Zwischenergebnisse bleiben geheim.\nEnde {discord.utils.format_dt(closes, style='R')}."), view=view)
        msg = await interaction.original_response(); await self.bot.database.execute("UPDATE utility_secret_votes SET message_id=? WHERE id=?", (msg.id, vote_id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityPlus(bot))
