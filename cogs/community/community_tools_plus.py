from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

ACCENT = 0x8B5CF6
GREEN = 0x22C55E
RED = 0xEF4444
GOLD = 0xF59E0B


def embed(title: str, text: str, color: int = ACCENT) -> discord.Embed:
    e = discord.Embed(title=title, description=text, color=color)
    e.set_footer(text="Raspberry-Bot · Community Tools")
    return e


def parse_local_time(raw: str) -> datetime | None:
    value = raw.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


class CommandPaletteView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=600)

    async def show(self, interaction: discord.Interaction, title: str, text: str) -> None:
        await interaction.response.send_message(embed=embed(title, text), ephemeral=True)

    @discord.ui.button(label="Arcade", emoji="🕹️", style=discord.ButtonStyle.primary)
    async def arcade(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.show(interaction, "🕹️ Arcade", "`/arcade` · `/battleship` · `/cipherduel` · `/blackjack duel` · `/territory` · `/wordchain` · `/reactionbattle` · `/escape` · `/bossfight` · `/heist`")

    @discord.ui.button(label="Workspace", emoji="🧠", style=discord.ButtonStyle.secondary)
    async def workspace(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.show(interaction, "🧠 Workspace", "`/workspace` · `/creator` · `/mdplan` · `/handover` · `/timeline` · `/macro`")

    @discord.ui.button(label="Community", emoji="👥", style=discord.ButtonStyle.secondary)
    async def community(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.show(interaction, "👥 Community", "`/timecapsule` · `/deadman` · `/linkhub` · `/drop` · `/secretvote` · `/story` · `/blindrank` · `/wouldyourather`")

    @discord.ui.button(label="System", emoji="🖥️", style=discord.ButtonStyle.secondary)
    async def system(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.show(interaction, "🖥️ System", "`/healthcheck` · `/diagnose` · `/pulse` · `/insights` · `/anomaly` · `/permissionmap` · `/roleaudit`")


class LinkHubView(discord.ui.View):
    def __init__(self, links: list[dict[str, str]]) -> None:
        super().__init__(timeout=None)
        for item in links[:25]:
            self.add_item(discord.ui.Button(label=item["label"][:80], url=item["url"], emoji="🔗"))


class DropView(discord.ui.View):
    def __init__(self, cog: "CommunityToolsPlus", guild_id: int, creator_id: int, limit: int, seconds: int, role: discord.Role | None) -> None:
        super().__init__(timeout=seconds)
        self.cog = cog
        self.guild_id = guild_id
        self.creator_id = creator_id
        self.limit = limit
        self.role = role
        self.claimed: set[int] = set()
        self.ends_at = datetime.now() + timedelta(seconds=seconds)

    @discord.ui.button(label="CLAIM", emoji="⚡", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if datetime.now() >= self.ends_at:
            await interaction.response.send_message("Dieser Drop ist bereits beendet.", ephemeral=True)
            return
        if interaction.user.id in self.claimed:
            await interaction.response.send_message("Du hast bereits geclaimt.", ephemeral=True)
            return
        if len(self.claimed) >= self.limit:
            button.disabled = True
            await interaction.response.edit_message(view=self)
            return
        self.claimed.add(interaction.user.id)
        await self.cog.bot.database.execute("INSERT OR IGNORE INTO community_drop_claims(guild_id,user_id,creator_id,claimed_at) VALUES(?,?,?,CURRENT_TIMESTAMP)", (self.guild_id, interaction.user.id, self.creator_id))
        role_note = ""
        if self.role and isinstance(interaction.user, discord.Member):
            try:
                await interaction.user.add_roles(self.role, reason="Community Drop claim")
                role_note = f" · Rolle **{self.role.name}** erhalten"
            except discord.HTTPException:
                role_note = " · Rolle konnte nicht vergeben werden"
        if len(self.claimed) >= self.limit:
            button.disabled = True
        await interaction.response.send_message(f"⚡ Claim **{len(self.claimed)}/{self.limit}** erfolgreich{role_note}.", ephemeral=True)
        if interaction.message:
            await interaction.message.edit(embed=embed("⚡ DROP", f"Claims: **{len(self.claimed)}/{self.limit}**\nEnde: <t:{int(self.ends_at.timestamp())}:R>", GOLD), view=self)


class SecretVoteSelect(discord.ui.Select):
    def __init__(self, view_ref: "SecretVoteView", options: list[str]) -> None:
        self.game = view_ref
        super().__init__(placeholder="Geheim abstimmen…", min_values=1, max_values=1, options=[discord.SelectOption(label=x[:100], value=str(i)) for i, x in enumerate(options[:25])])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.game.closed:
            await interaction.response.send_message("Abstimmung beendet.", ephemeral=True)
            return
        self.game.votes[interaction.user.id] = int(self.values[0])
        await interaction.response.send_message("🔒 Stimme gespeichert. Zwischenergebnisse bleiben geheim.", ephemeral=True)


class SecretVoteView(discord.ui.View):
    def __init__(self, question: str, options: list[str], seconds: int) -> None:
        super().__init__(timeout=seconds + 30)
        self.question = question
        self.options_text = options[:25]
        self.votes: dict[int, int] = {}
        self.closed = False
        self.message: discord.Message | None = None
        self.add_item(SecretVoteSelect(self, self.options_text))
        self.seconds = seconds

    async def reveal_after(self) -> None:
        await asyncio.sleep(self.seconds)
        self.closed = True
        for item in self.children:
            item.disabled = True
        counts = [0] * len(self.options_text)
        for idx in self.votes.values():
            if 0 <= idx < len(counts):
                counts[idx] += 1
        total = max(1, sum(counts))
        lines = [f"**{label}** · {count} · {count/total:.0%}" for label, count in zip(self.options_text, counts)]
        if self.message:
            try:
                await self.message.edit(embed=embed("🗳️ Secret Vote · Ergebnis", self.question + "\n\n" + "\n".join(lines), GREEN), view=self)
            except discord.HTTPException:
                pass


class LinkHub(commands.GroupCog, group_name="linkhub", group_description="Persistente Link-Panels"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="create", description="Erstellt oder überschreibt einen LinkHub.")
    @app_commands.default_permissions(manage_guild=True)
    async def create(self, interaction: discord.Interaction, name: str, titel: str, links: str) -> None:
        if interaction.guild_id is None:
            return
        parsed: list[dict[str, str]] = []
        for part in links.split(";"):
            if "|" not in part:
                continue
            label, url = [x.strip() for x in part.split("|", 1)]
            if label and url.startswith(("https://", "http://")):
                parsed.append({"label": label, "url": url})
        if not parsed:
            await interaction.response.send_message("Format: `GitHub|https://...;Wiki|https://...`", ephemeral=True)
            return
        await self.bot.database.execute("""INSERT INTO community_linkhubs(guild_id,name,title,links_json,created_by) VALUES(?,?,?,?,?)
               ON CONFLICT(guild_id,name) DO UPDATE SET title=excluded.title,links_json=excluded.links_json,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP""", (interaction.guild_id, name.lower().strip(), titel, json.dumps(parsed, ensure_ascii=False), interaction.user.id))
        await interaction.response.send_message(embed=embed("🔗 LinkHub gespeichert", f"`{name}` · **{len(parsed)} Links**"), ephemeral=True)

    @app_commands.command(name="show", description="Veröffentlicht einen gespeicherten LinkHub.")
    async def show(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone("SELECT title,links_json FROM community_linkhubs WHERE guild_id=? AND lower(name)=lower(?)", (interaction.guild_id, name.strip()))
        if not row:
            await interaction.response.send_message("LinkHub nicht gefunden.", ephemeral=True)
            return
        links = json.loads(str(row["links_json"]))
        await interaction.response.send_message(embed=embed("🔗 " + str(row["title"]), "Zentrale Links und Ressourcen."), view=LinkHubView(links))

    @app_commands.command(name="list", description="Listet LinkHubs.")
    async def list_hubs(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall("SELECT name,title FROM community_linkhubs WHERE guild_id=? ORDER BY name", (interaction.guild_id,))
        await interaction.response.send_message(embed=embed("🔗 LinkHubs", "\n".join(f"`{r['name']}` · {r['title']}" for r in rows) or "Keine LinkHubs."), ephemeral=True)


class CommunityToolsPlus(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.delivery_loop.start()

    async def cog_load(self) -> None:
        await self.bot.database.execute("""CREATE TABLE IF NOT EXISTS delayed_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,channel_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,message TEXT NOT NULL,due_at TEXT NOT NULL,source_message_id INTEGER,delivered INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        await self.bot.database.execute("""CREATE TABLE IF NOT EXISTS community_linkhubs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,title TEXT NOT NULL,
            links_json TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name))""")
        await self.bot.database.execute("""CREATE TABLE IF NOT EXISTS community_drop_claims(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,creator_id INTEGER NOT NULL,
            claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def cog_unload(self) -> None:
        self.delivery_loop.cancel()

    @tasks.loop(seconds=30)
    async def delivery_loop(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = await self.bot.database.fetchall("SELECT id,channel_id,user_id,kind,message,source_message_id FROM delayed_messages WHERE delivered=0 AND due_at<=? ORDER BY due_at LIMIT 25", (now,))
        for row in rows:
            channel = self.bot.get_channel(int(row["channel_id"]))
            try:
                if row["kind"] == "timecapsule" and isinstance(channel, discord.abc.Messageable):
                    await channel.send(embed=embed("⏳ Time Capsule geöffnet", f"Von <@{row['user_id']}>:\n\n{row['message']}", GOLD))
                elif row["kind"] == "deadman" and channel and row["source_message_id"]:
                    try:
                        msg = await channel.fetch_message(int(row["source_message_id"]))
                        await msg.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                await self.bot.database.execute("UPDATE delayed_messages SET delivered=1 WHERE id=?", (row["id"],))
            except discord.HTTPException:
                continue

    @delivery_loop.before_loop
    async def before_delivery(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="timecapsule", description="Speichert eine Nachricht bis zu einem zukünftigen Zeitpunkt.")
    async def timecapsule(self, interaction: discord.Interaction, wann: str, nachricht: str) -> None:
        if interaction.channel_id is None:
            return
        due = parse_local_time(wann)
        if due is None or due <= datetime.now():
            await interaction.response.send_message("Zeit bitte z. B. als `2026-09-10 20:00` oder `10.09.2026 20:00` angeben.", ephemeral=True)
            return
        if due - datetime.now() > timedelta(days=365):
            await interaction.response.send_message("Time Capsules sind auf maximal 365 Tage begrenzt.", ephemeral=True)
            return
        await self.bot.database.execute("INSERT INTO delayed_messages(guild_id,channel_id,user_id,kind,message,due_at) VALUES(?,?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, interaction.user.id, "timecapsule", nachricht, due.strftime("%Y-%m-%d %H:%M:%S")))
        await interaction.response.send_message(embed=embed("⏳ Time Capsule versiegelt", f"Öffnet <t:{int(due.timestamp())}:F>.\nInhalt bleibt bis dahin verborgen."), ephemeral=True)

    @app_commands.command(name="deadman", description="Sendet eine Nachricht, die nach X Minuten automatisch verschwindet.")
    async def deadman(self, interaction: discord.Interaction, minuten: app_commands.Range[int, 1, 1440], nachricht: str) -> None:
        if interaction.channel_id is None:
            return
        await interaction.response.send_message(embed=embed("💨 Temporäre Nachricht", nachricht, RED))
        msg = await interaction.original_response()
        due = datetime.now() + timedelta(minutes=int(minuten))
        await self.bot.database.execute("INSERT INTO delayed_messages(guild_id,channel_id,user_id,kind,message,due_at,source_message_id) VALUES(?,?,?,?,?,?,?)", (interaction.guild_id, interaction.channel_id, interaction.user.id, "deadman", nachricht, due.strftime("%Y-%m-%d %H:%M:%S"), msg.id))

    @app_commands.command(name="commandpalette", description="Öffnet eine interaktive Zentrale für die wichtigsten Bot-Funktionen.")
    async def commandpalette(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=embed("⌘ Command Palette", "Wähle einen Bereich. Du bekommst die passenden Commands kompakt angezeigt."), view=CommandPaletteView(), ephemeral=True)

    @app_commands.command(name="drop", description="Zeitlich begrenzter First-Come-Drop mit optionaler Rolle.")
    @app_commands.default_permissions(manage_guild=True)
    async def drop(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 5, dauer_minuten: app_commands.Range[int, 1, 60] = 10, rolle: discord.Role | None = None) -> None:
        if interaction.guild_id is None:
            return
        if rolle and interaction.guild and interaction.guild.me and rolle >= interaction.guild.me.top_role:
            await interaction.response.send_message("Diese Rolle liegt über/auf meiner höchsten Rolle.", ephemeral=True)
            return
        view = DropView(self, interaction.guild_id, interaction.user.id, int(limit), int(dauer_minuten) * 60, rolle)
        await interaction.response.send_message(embed=embed("⚡ DROP", f"Die ersten **{limit}** Personen gewinnen." + (f"\nReward: {rolle.mention}" if rolle else "\nReward: **Claim/Achievement-Eintrag**") + f"\nEnde: <t:{int(view.ends_at.timestamp())}:R>", GOLD), view=view)

    @app_commands.command(name="secretvote", description="Geheime Abstimmung ohne sichtbare Zwischenergebnisse.")
    async def secretvote(self, interaction: discord.Interaction, frage: str, optionen: str, dauer_minuten: app_commands.Range[int, 1, 60] = 5) -> None:
        options = [x.strip() for x in optionen.split("|") if x.strip()]
        if not 2 <= len(options) <= 10:
            await interaction.response.send_message("Bitte 2–10 Optionen mit `|` trennen.", ephemeral=True)
            return
        view = SecretVoteView(frage, options, int(dauer_minuten) * 60)
        await interaction.response.send_message(embed=embed("🗳️ Secret Vote", frage + f"\n\n**{len(options)} Optionen** · Ergebnis <t:{int((datetime.now()+timedelta(minutes=int(dauer_minuten))).timestamp())}:R>\nZwischenstände sind unsichtbar."), view=view)
        view.message = await interaction.original_response()
        asyncio.create_task(view.reveal_after())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommunityToolsPlus(bot))
    await bot.add_cog(LinkHub(bot))
