from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory

ROLE_MENTION = "<@&1347629718438154251>"
UTILITY_ARROW = "<:utilityarrow:1277891423735255051>"
TOPIC_ARROW = "<a:arrowright:1436104391781388430>"
SEPARATOR = "═══════ ☆ ═══════"

DAY_HEADINGS = (
    "<a:animatedarrowblue:1330113135511736330> MONTAG",
    "<a:animatedarroworange:1330113117354463318> DIENSTAG",
    "<a:animatedarrowgreen:1330113110979379321> MITTWOCH",
    "<a:animatedarrowred:1330113130625503252> DONNERSTAG",
    "<a:animatedarrowpink2:1330113132831703120> FREITAG",
    "<a:animatedarrowwhite:1330113124359209010> SAMSTAG",
    "<a:animatedarrowyellow:1330113113843830834> SONNTAG",
)
DAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

FULL_CLOCKS = ("🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚")
HALF_CLOCKS = ("🕧", "🕜", "🕝", "🕞", "🕟", "🕠", "🕡", "🕢", "🕣", "🕤", "🕥", "🕦")


def _week_monday(value: str | None) -> date:
    if not value:
        current = datetime.now().astimezone().date()
    else:
        parsed: date | None = None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(value.strip(), fmt).date()
                break
            except ValueError:
                pass
        if parsed is None:
            raise ValueError("Datum muss YYYY-MM-DD oder DD.MM.YYYY sein")
        current = parsed
    return current - timedelta(days=current.weekday())


def _day_index(value: str) -> int:
    raw = value.strip().lower().replace(".", "")
    aliases = {
        "montag": 0,
        "mo": 0,
        "dienstag": 1,
        "di": 1,
        "mittwoch": 2,
        "mi": 2,
        "donnerstag": 3,
        "do": 3,
        "freitag": 4,
        "fr": 4,
        "samstag": 5,
        "sa": 5,
        "sonntag": 6,
        "so": 6,
    }
    if raw not in aliases:
        raise ValueError("Tag muss Montag–Sonntag sein")
    return aliases[raw]


def _first_time(value: str) -> tuple[int, int]:
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", value)
    if not match:
        raise ValueError("Zeit muss mindestens eine Uhrzeit wie 20:00 enthalten")
    return int(match.group(1)), int(match.group(2))


def _clock_for(hour: int, minute: int) -> str:
    index = hour % 12
    return HALF_CLOCKS[index] if minute >= 30 else FULL_CLOCKS[index]


def _time_line(raw: str) -> tuple[str, str]:
    hour, minute = _first_time(raw)
    value = raw.strip().replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+Uhr\s*$", "", value, flags=re.IGNORECASE)
    if re.match(r"^ab\s+", value, flags=re.IGNORECASE):
        shown = f"ab {hour:02d}:{minute:02d} Uhr"
    else:
        times = re.findall(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", value)
        if len(times) >= 2:
            h2, m2 = int(times[1][0]), int(times[1][1])
            shown = f"{hour:02d}:{minute:02d} - {h2:02d}:{m2:02d} Uhr"
        else:
            shown = f"{hour:02d}:{minute:02d} Uhr"
    return f"{hour:02d}:{minute:02d}", f"{_clock_for(hour, minute)} {shown}"


def _kind_line(kind: str, day_index: int) -> tuple[str, bool]:
    clean = kind.strip()
    low = clean.lower()
    if "theorie" in low or low.startswith("modul"):
        return "📚 Theorieunterricht", True
    if "medizin" in low and "grund" in low:
        return "<:faction_ems:1338914975392993280> Medizinische Grundlagen", False
    if "rtw" in low:
        return "<a:ambulance:1421666005695860856> RTW - Schulung", False
    if "notaufnahme" in low:
        return f"🏥 {clean}", False
    if "reha" in low or "psychiatr" in low:
        return "🤸‍♂️ Reha-Schulung / 🧠 Psychiatrie", False
    if low in {"rp", "staatsfraktionen", "rp mit staatsfraktionen"} or "staatsfraktion" in low:
        emoji = "<:1State:1448298845208580138>" if day_index == 3 else "<:OS_RP:1448296240058990672>"
        return f"{emoji} RP mit Staatsfraktionen", False
    if "wochenbesprech" in low or "besprechung" in low:
        return f"📡 {clean}", False
    return clean, False


def _teacher_lines(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = []
    for part in re.split(r"[\n|]", raw):
        item = part.strip()
        if not item:
            continue
        if item.startswith(("👨", "👩", "🧑", "📍", "🏫", "<@", "<:")) or "Schulungsraum" in item:
            shown = item
        else:
            shown = f"👨‍🏫 {item}"
        values.append(f"{UTILITY_ARROW} {shown}")
    return values


def _header() -> str:
    return (
        "<a:sparklesgold:1425406930104356875>  MD Bell WOCHENPLANER <a:sparklesgold:1425406930104356875>\n"
        f"-# {ROLE_MENTION}\n\n"
        "<a:attention:1228352251832176640> Gemeinsam lernen & wachsen\n"
        "<a:attention:1228352251832176640> Bitte pünktlich zu allen Terminen erscheinen.\n"
        "═══════  ☆  ═══════"
    )


def _footer(last_updated: str) -> str:
    return (
        "<:OneStateGear:1428141698260664362> Änderungen oder Zusatztermine werden rechtzeitig bekannt gegeben.\n\n"
        f"Letzter Stand: {last_updated}"
    )


def _entry_text(row) -> str:
    _, time_line = _time_line(str(row["time_text"]))
    kind_line, theory = _kind_line(str(row["kind"]), int(row["day_index"]))
    lines = [time_line, kind_line]
    lines.extend(_teacher_lines(str(row["teachers"] or "")))
    topic = str(row["topic"] or "").strip()
    if topic:
        if theory:
            lines.append(f"➡️ {topic}")
        else:
            lines.append(f"{TOPIC_ARROW} {topic}")
    return "\n".join(lines)


def _split_chunks(header: str, day_blocks: list[str], footer: str, limit: int = 1950) -> list[str]:
    chunks: list[str] = []
    current = header
    for block in day_blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) > limit and current != header:
            chunks.append(current)
            current = block
        elif len(candidate) > limit:
            chunks.append(current)
            current = block
        else:
            current = candidate
    candidate = f"{current}\n\n{footer}"
    if len(candidate) > limit:
        chunks.append(current)
        current = footer
    else:
        current = candidate
    chunks.append(current)
    return chunks


class PlannerEntryModal(discord.ui.Modal, title="Termin zum Wochenplan hinzufügen"):
    day = discord.ui.TextInput(
        label="Tag",
        placeholder="Montag",
        max_length=12,
    )
    time_text = discord.ui.TextInput(
        label="Zeit",
        placeholder="20:00 - 21:00 oder ab 22:30",
        max_length=40,
    )
    kind = discord.ui.TextInput(
        label="Art / Bereich",
        placeholder="Theorieunterricht, RTW-Schulung, Notaufnahme ...",
        max_length=100,
    )
    teachers = discord.ui.TextInput(
        label="Lehrer / Ort (optional)",
        placeholder="Jerome Sanchez | Sven Bracht oder Schulungsraum F2",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )
    topic = discord.ui.TextInput(
        label="Thema / Modul (optional)",
        placeholder="Modul 3 / Behandlung von Suchtkranken / Thema: offen",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=700,
    )

    def __init__(self, bot: commands.Bot, draft_id: int, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.draft_id = int(draft_id)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Das ist nicht dein Wochenplan-Entwurf.", ephemeral=True)
            return
        try:
            day_index = _day_index(str(self.day.value))
            sort_time, _ = _time_line(str(self.time_text.value))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        draft = await self.bot.database.fetchone(
            "SELECT id FROM md_weekly_drafts WHERE id=? AND guild_id=? AND owner_id=?",
            (self.draft_id, interaction.guild_id, self.owner_id),
        )
        if not draft:
            await interaction.response.send_message("Entwurf nicht mehr gefunden. Nutze `/mdplan start`.", ephemeral=True)
            return
        await self.bot.database.execute(
            """
            INSERT INTO md_weekly_entries(draft_id,day_index,start_sort,time_text,kind,teachers,topic)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                self.draft_id,
                day_index,
                sort_time,
                str(self.time_text.value).strip(),
                str(self.kind.value).strip(),
                str(self.teachers.value or "").strip(),
                str(self.topic.value or "").strip(),
            ),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Termin hinzugefügt",
                description=f"**{DAY_NAMES[day_index]}** · {str(self.time_text.value).strip()} · **{str(self.kind.value).strip()}**",
            ),
            view=PlannerBuilderView(self.bot, self.draft_id, self.owner_id),
            ephemeral=True,
        )


class PlannerBuilderView(discord.ui.View):
    def __init__(self, bot: commands.Bot, draft_id: int, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.draft_id = int(draft_id)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Das ist nicht dein Wochenplan-Builder.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Termin hinzufügen", emoji="➕", style=discord.ButtonStyle.primary)
    async def add_entry(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(PlannerEntryModal(self.bot, self.draft_id, self.owner_id))

    @discord.ui.button(label="Vorschau", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        chunks = await _render_draft(self.bot, self.draft_id, interaction.guild_id, self.owner_id)
        if not chunks:
            await interaction.response.send_message("Entwurf nicht gefunden.", ephemeral=True)
            return
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @discord.ui.button(label="Letzten löschen", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def remove_last(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        row = await self.bot.database.fetchone(
            "SELECT id FROM md_weekly_entries WHERE draft_id=? ORDER BY id DESC LIMIT 1",
            (self.draft_id,),
        )
        if not row:
            await interaction.response.send_message("Noch kein Termin vorhanden.", ephemeral=True)
            return
        await self.bot.database.execute(
            "DELETE FROM md_weekly_entries WHERE id=? AND draft_id=?",
            (int(row["id"]), self.draft_id),
        )
        await interaction.response.send_message(f"Termin `#{row['id']}` entfernt.", ephemeral=True)

    @discord.ui.button(label="Veröffentlichen", emoji="📨", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        draft = await self.bot.database.fetchone(
            "SELECT * FROM md_weekly_drafts WHERE id=? AND guild_id=? AND owner_id=?",
            (self.draft_id, interaction.guild_id, self.owner_id),
        )
        if not draft:
            await interaction.response.send_message("Entwurf nicht gefunden.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(int(draft["channel_id"])) if draft["channel_id"] else interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message("Zielkanal nicht gefunden.", ephemeral=True)
            return
        chunks = await _render_draft(self.bot, self.draft_id, interaction.guild_id, self.owner_id)
        for chunk in chunks:
            await channel.send(chunk, allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False))
        await self.bot.database.execute(
            "UPDATE md_weekly_drafts SET published_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (self.draft_id,),
        )
        await interaction.response.send_message(f"Wochenplan in {channel.mention} veröffentlicht.", ephemeral=True)

    @discord.ui.button(label="Hilfe", emoji="❓", style=discord.ButtonStyle.secondary)
    async def help_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "**Beispiele für die Abfrage**\n"
            "Tag: `Dienstag`\n"
            "Zeit: `20:00 - 20:45` oder `ab 22:30`\n"
            "Art: `RTW-Schulung`, `Theorieunterricht`, `Medizinische Grundlagen`, `Notaufnahme – Innere Medizin`, `Reha-Schulung`, `Wochenbesprechung`\n"
            "Lehrer: `Melvin Crawley | 👩🏼‍🏫 Nancy Crawley`\n"
            "Thema: `Patientenübergabe an die Notaufnahme & kleines Funktraining`\n\n"
            "Donnerstag und Samstag wird bei einem neuen Entwurf standardmäßig RP mit Staatsfraktionen angelegt.",
            ephemeral=True,
        )


async def _render_draft(bot: commands.Bot, draft_id: int, guild_id: int | None, owner_id: int) -> list[str]:
    if guild_id is None:
        return []
    draft = await bot.database.fetchone(
        "SELECT * FROM md_weekly_drafts WHERE id=? AND guild_id=? AND owner_id=?",
        (draft_id, guild_id, owner_id),
    )
    if not draft:
        return []
    rows = await bot.database.fetchall(
        "SELECT * FROM md_weekly_entries WHERE draft_id=? ORDER BY day_index,start_sort,id",
        (draft_id,),
    )
    grouped: dict[int, list] = {i: [] for i in range(7)}
    for row in rows:
        grouped[int(row["day_index"])].append(row)

    day_blocks: list[str] = []
    for day_index in range(7):
        parts = [DAY_HEADINGS[day_index]]
        for row in grouped[day_index]:
            parts.append(_entry_text(row))
        parts.append(SEPARATOR)
        day_blocks.append("\n\n".join(parts))

    updated = datetime.now().astimezone().strftime("%d.%m.%Y")
    return _split_chunks(_header(), day_blocks, _footer(updated))


class MDBellWeeklyPlanner(
    commands.GroupCog,
    group_name="mdplan",
    group_description="Interaktiver MD Bell Wochenplaner",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS md_weekly_drafts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                channel_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                published_at TEXT,
                UNIQUE(guild_id,owner_id)
            )
            """
        )
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS md_weekly_entries(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                day_index INTEGER NOT NULL,
                start_sort TEXT NOT NULL,
                time_text TEXT NOT NULL,
                kind TEXT NOT NULL,
                teachers TEXT,
                topic TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(draft_id) REFERENCES md_weekly_drafts(id) ON DELETE CASCADE
            )
            """
        )
        await self.bot.database.execute(
            "CREATE INDEX IF NOT EXISTS idx_md_weekly_entries_draft_day ON md_weekly_entries(draft_id,day_index,start_sort,id)"
        )

    async def _draft(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            return None
        return await self.bot.database.fetchone(
            "SELECT * FROM md_weekly_drafts WHERE guild_id=? AND owner_id=?",
            (interaction.guild_id, interaction.user.id),
        )

    @app_commands.command(name="start", description="Neuen interaktiven MD-Bell-Wochenplan beginnen.")
    @app_commands.default_permissions(manage_messages=True)
    async def start(
        self,
        interaction: discord.Interaction,
        woche: str | None = None,
        kanal: discord.TextChannel | None = None,
        standard_rp: bool = True,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        try:
            monday = _week_monday(woche)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        old = await self._draft(interaction)
        if old:
            await self.bot.database.execute("DELETE FROM md_weekly_entries WHERE draft_id=?", (int(old["id"]),))
            await self.bot.database.execute(
                "UPDATE md_weekly_drafts SET week_start=?,channel_id=?,updated_at=CURRENT_TIMESTAMP,published_at=NULL WHERE id=?",
                (monday.isoformat(), (kanal or interaction.channel).id, int(old["id"])),
            )
            draft_id = int(old["id"])
        else:
            draft_id = await self.bot.database.execute(
                "INSERT INTO md_weekly_drafts(guild_id,owner_id,week_start,channel_id) VALUES(?,?,?,?)",
                (interaction.guild_id, interaction.user.id, monday.isoformat(), (kanal or interaction.channel).id),
            )

        if standard_rp:
            await self.bot.database.execute(
                "INSERT INTO md_weekly_entries(draft_id,day_index,start_sort,time_text,kind,teachers,topic) VALUES(?,3,'22:30','ab 22:30','RP mit Staatsfraktionen','','')",
                (draft_id,),
            )
            await self.bot.database.execute(
                "INSERT INTO md_weekly_entries(draft_id,day_index,start_sort,time_text,kind,teachers,topic) VALUES(?,5,'22:30','ab 22:30','RP mit Staatsfraktionen','','')",
                (draft_id,),
            )

        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="MD Bell Wochenplaner gestartet",
                description=(
                    f"Woche ab **{monday.strftime('%d.%m.%Y')}**.\n"
                    f"Zielkanal: {(kanal or interaction.channel).mention}\n\n"
                    "Drücke **Termin hinzufügen**. Der Bot fragt dann Tag, Zeit, Art/Bereich, Lehrer/Ort und Thema/Modul ab."
                ),
            ),
            view=PlannerBuilderView(self.bot, draft_id, interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="builder", description="Vorhandenen Wochenplan-Builder wieder öffnen.")
    @app_commands.default_permissions(manage_messages=True)
    async def builder(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf. Starte mit `/mdplan start`.", ephemeral=True)
            return
        count = await self.bot.database.fetchone(
            "SELECT COUNT(*) AS n FROM md_weekly_entries WHERE draft_id=?",
            (int(draft["id"]),),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="MD Bell Wochenplaner",
                description=f"Aktiver Entwurf mit **{int(count['n'])} Terminen**.",
            ),
            view=PlannerBuilderView(self.bot, int(draft["id"]), interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="add", description="Termin über ein Abfragefenster hinzufügen.")
    @app_commands.default_permissions(manage_messages=True)
    async def add(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf. Starte mit `/mdplan start`.", ephemeral=True)
            return
        await interaction.response.send_modal(
            PlannerEntryModal(self.bot, int(draft["id"]), interaction.user.id)
        )

    @app_commands.command(name="preview", description="Fertigen Discord-Wochenplan als Vorschau anzeigen.")
    @app_commands.default_permissions(manage_messages=True)
    async def preview(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        chunks = await _render_draft(self.bot, int(draft["id"]), interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @app_commands.command(name="publish", description="Wochenplan im Zielkanal veröffentlichen.")
    @app_commands.default_permissions(manage_messages=True)
    async def publish(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel | None = None,
    ) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        target = kanal or interaction.guild.get_channel(int(draft["channel_id"])) if draft["channel_id"] else interaction.channel
        if not isinstance(target, discord.abc.Messageable):
            await interaction.response.send_message("Zielkanal nicht gefunden.", ephemeral=True)
            return
        chunks = await _render_draft(self.bot, int(draft["id"]), interaction.guild_id, interaction.user.id)
        for chunk in chunks:
            await target.send(chunk, allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False))
        await interaction.response.send_message(f"Wochenplan in {target.mention} veröffentlicht.", ephemeral=True)

    @app_commands.command(name="list", description="Termine im aktuellen Entwurf mit IDs anzeigen.")
    @app_commands.default_permissions(manage_messages=True)
    async def list_entries(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        rows = await self.bot.database.fetchall(
            "SELECT * FROM md_weekly_entries WHERE draft_id=? ORDER BY day_index,start_sort,id",
            (int(draft["id"]),),
        )
        lines = [
            f"`#{r['id']}` **{DAY_NAMES[int(r['day_index'])]}** · {r['time_text']} · {r['kind']}"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Wochenplan-Termine", description="\n".join(lines) or "Noch keine Termine."),
            ephemeral=True,
        )

    @app_commands.command(name="remove", description="Termin anhand seiner ID löschen.")
    @app_commands.default_permissions(manage_messages=True)
    async def remove(self, interaction: discord.Interaction, termin_id: int) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        row = await self.bot.database.fetchone(
            "SELECT id FROM md_weekly_entries WHERE id=? AND draft_id=?",
            (termin_id, int(draft["id"])),
        )
        if not row:
            await interaction.response.send_message("Termin nicht gefunden.", ephemeral=True)
            return
        await self.bot.database.execute(
            "DELETE FROM md_weekly_entries WHERE id=? AND draft_id=?",
            (termin_id, int(draft["id"])),
        )
        await interaction.response.send_message(f"Termin `#{termin_id}` gelöscht.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MDBellWeeklyPlanner(bot))
