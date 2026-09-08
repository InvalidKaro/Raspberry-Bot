from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory

ROLE_MENTION = "<@&1347629718438154251>"
UTILITY_ARROW = "<:utilityarrow:1277891423735255051>"
TOPIC_ARROW = "<a:arrowright:1436104391781388430>"
SEPARATOR = "═══════ ☆ ═══════"
STANDARD_RP_DAYS = (2, 5)  # Mittwoch, Samstag
STANDARD_RP_TIME = "22:30"

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

MD_SCHEMA = """
CREATE TABLE IF NOT EXISTS md_weekly_drafts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    week_start TEXT NOT NULL,
    channel_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT,
    UNIQUE(guild_id, owner_id)
);
CREATE TABLE IF NOT EXISTS md_weekly_entries(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    day_index INTEGER NOT NULL,
    start_sort TEXT NOT NULL,
    time_text TEXT NOT NULL,
    kind TEXT NOT NULL,
    teachers TEXT,
    topic TEXT,
    title TEXT,
    owner_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(draft_id) REFERENCES md_weekly_drafts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_md_weekly_entries_draft_day
    ON md_weekly_entries(draft_id, day_index, start_sort, id);
"""


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
        "montag": 0, "mo": 0,
        "dienstag": 1, "di": 1,
        "mittwoch": 2, "mi": 2,
        "donnerstag": 3, "do": 3,
        "freitag": 4, "fr": 4,
        "samstag": 5, "sa": 5,
        "sonntag": 6, "so": 6,
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
    return HALF_CLOCKS[hour % 12] if minute >= 30 else FULL_CLOCKS[hour % 12]


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
    clean = (kind or "Termin").strip() or "Termin"
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
        emoji = "<:1State:1448298845208580138>" if day_index == 2 else "<:OS_RP:1448296240058990672>"
        return f"{emoji} RP mit Staatsfraktionen", False
    if "wochenbesprech" in low or "besprechung" in low:
        return f"📡 {clean}", False
    return clean, False


def _teacher_lines(raw: str | None) -> list[str]:
    if not raw:
        return []
    values: list[str] = []
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


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _entry_text(row: Any) -> str:
    _, time_line = _time_line(str(_row_value(row, "time_text", "00:00")))
    day_index = int(_row_value(row, "day_index", 0) or 0)
    kind_line, theory = _kind_line(str(_row_value(row, "kind", "Termin")), day_index)
    lines = [time_line, kind_line]
    lines.extend(_teacher_lines(str(_row_value(row, "teachers", "") or "")))
    topic = str(_row_value(row, "topic", "") or "").strip()
    if topic:
        lines.append(f"➡️ {topic}" if theory else f"{TOPIC_ARROW} {topic}")
    return "\n".join(lines)


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        pieces = [paragraph]
        if len(paragraph) > limit:
            pieces = []
            line_buf = ""
            for line in paragraph.splitlines() or [paragraph]:
                if len(line) > limit:
                    if line_buf:
                        pieces.append(line_buf)
                        line_buf = ""
                    pieces.extend(line[i:i + limit] for i in range(0, len(line), limit))
                    continue
                candidate = f"{line_buf}\n{line}" if line_buf else line
                if len(candidate) > limit:
                    pieces.append(line_buf)
                    line_buf = line
                else:
                    line_buf = candidate
            if line_buf:
                pieces.append(line_buf)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) > limit:
                if current:
                    chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _split_chunks(header: str, day_blocks: list[str], footer: str, limit: int = 1950) -> list[str]:
    chunks: list[str] = []
    current = ""
    for section in [header, *day_blocks, footer]:
        for piece in _split_text(section, limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) > limit:
                if current:
                    chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _short(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


async def _table_columns(database: Any, table: str) -> set[str]:
    cursor = await database.connection.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    await cursor.close()
    return {str(row["name"]) for row in rows}


async def ensure_md_schema(database: Any) -> None:
    """Create the current schema and repair old MD-plan database layouts in place."""
    await database.connection.executescript(MD_SCHEMA)
    await database.connection.commit()

    entry_columns = await _table_columns(database, "md_weekly_entries")
    additions = {
        "day_index": "INTEGER NOT NULL DEFAULT 0",
        "start_sort": "TEXT NOT NULL DEFAULT '00:00'",
        "time_text": "TEXT NOT NULL DEFAULT '00:00'",
        "kind": "TEXT NOT NULL DEFAULT 'Termin'",
        "teachers": "TEXT",
        "topic": "TEXT",
        # Compatibility columns used by Dashboard Pro's calendar reader.
        "title": "TEXT",
        "owner_text": "TEXT",
        "created_at": "TEXT",
    }
    for column, definition in additions.items():
        if column not in entry_columns:
            await database.connection.execute(
                f"ALTER TABLE md_weekly_entries ADD COLUMN {column} {definition}"
            )
            entry_columns.add(column)

    draft_columns = await _table_columns(database, "md_weekly_drafts")
    draft_additions = {
        "channel_id": "INTEGER",
        "updated_at": "TEXT",
        "published_at": "TEXT",
    }
    for column, definition in draft_additions.items():
        if column not in draft_columns:
            await database.connection.execute(
                f"ALTER TABLE md_weekly_drafts ADD COLUMN {column} {definition}"
            )
            draft_columns.add(column)

    if "start_time" in entry_columns:
        await database.connection.execute(
            "UPDATE md_weekly_entries SET start_sort=COALESCE(NULLIF(start_sort,''),start_time,'00:00'), "
            "time_text=COALESCE(NULLIF(time_text,''),start_time,'00:00')"
        )
    await database.connection.execute(
        "UPDATE md_weekly_entries SET "
        "kind=COALESCE(NULLIF(kind,''),NULLIF(title,''),'Termin'), "
        "teachers=COALESCE(NULLIF(teachers,''),owner_text,''), "
        "topic=COALESCE(topic,''), "
        "title=CASE WHEN TRIM(COALESCE(topic,''))<>'' THEN topic ELSE COALESCE(NULLIF(kind,''),'Termin') END, "
        "owner_text=COALESCE(teachers,'')"
    )
    await database.connection.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_md_weekly_entries_compat_insert
        AFTER INSERT ON md_weekly_entries
        BEGIN
            UPDATE md_weekly_entries
            SET title=CASE WHEN TRIM(COALESCE(NEW.topic,''))<>'' THEN NEW.topic ELSE NEW.kind END,
                owner_text=COALESCE(NEW.teachers,'')
            WHERE id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS trg_md_weekly_entries_compat_update
        AFTER UPDATE OF kind,teachers,topic ON md_weekly_entries
        BEGIN
            UPDATE md_weekly_entries
            SET title=CASE WHEN TRIM(COALESCE(NEW.topic,''))<>'' THEN NEW.topic ELSE NEW.kind END,
                owner_text=COALESCE(NEW.teachers,'')
            WHERE id=NEW.id;
        END;
        """
    )
    await database.connection.commit()


async def _touch_draft(bot: commands.Bot, draft_id: int) -> None:
    await bot.database.execute(
        "UPDATE md_weekly_drafts SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(draft_id),),
    )


async def _insert_entry(
    bot: commands.Bot,
    draft_id: int,
    day_index: int,
    time_text: str,
    kind: str,
    teachers: str = "",
    topic: str = "",
) -> int:
    sort_time, _ = _time_line(time_text)
    return await bot.database.execute(
        """
        INSERT INTO md_weekly_entries(
            draft_id,day_index,start_sort,time_text,kind,teachers,topic,title,owner_text
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            int(draft_id), int(day_index), sort_time, time_text.strip(), kind.strip(),
            teachers.strip(), topic.strip(), topic.strip() or kind.strip(), teachers.strip(),
        ),
    )


async def _insert_standard_rp(bot: commands.Bot, draft_id: int) -> None:
    for day_index in STANDARD_RP_DAYS:
        await _insert_entry(
            bot,
            draft_id,
            day_index,
            f"ab {STANDARD_RP_TIME}",
            "RP mit Staatsfraktionen",
        )


async def _render_draft(
    bot: commands.Bot,
    draft_id: int,
    guild_id: int | None,
    owner_id: int,
) -> list[str]:
    if guild_id is None:
        return []
    draft = await bot.database.fetchone(
        "SELECT * FROM md_weekly_drafts WHERE id=? AND guild_id=? AND owner_id=?",
        (int(draft_id), int(guild_id), int(owner_id)),
    )
    if not draft:
        return []
    rows = await bot.database.fetchall(
        "SELECT * FROM md_weekly_entries WHERE draft_id=? ORDER BY day_index,start_sort,id",
        (int(draft_id),),
    )
    grouped: dict[int, list[Any]] = {i: [] for i in range(7)}
    for row in rows:
        day = max(0, min(6, int(_row_value(row, "day_index", 0) or 0)))
        grouped[day].append(row)

    day_blocks: list[str] = []
    for day_index in range(7):
        parts = [DAY_HEADINGS[day_index]]
        parts.extend(_entry_text(row) for row in grouped[day_index])
        parts.append(SEPARATOR)
        day_blocks.append("\n\n".join(parts))

    updated = datetime.now().astimezone().strftime("%d.%m.%Y")
    return _split_chunks(_header(), day_blocks, _footer(updated))


async def _entry_rows(bot: commands.Bot, draft_id: int) -> list[Any]:
    return await bot.database.fetchall(
        "SELECT id,day_index,time_text,kind,teachers,topic FROM md_weekly_entries "
        "WHERE draft_id=? ORDER BY day_index,start_sort,id",
        (int(draft_id),),
    )


async def _send_preview(
    interaction: discord.Interaction,
    bot: commands.Bot,
    draft_id: int,
    owner_id: int,
) -> None:
    chunks = await _render_draft(bot, draft_id, interaction.guild_id, owner_id)
    if not chunks:
        await interaction.response.send_message("Entwurf nicht gefunden.", ephemeral=True)
        return
    await interaction.response.send_message(chunks[0], ephemeral=True)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True)


class PlannerEntryModal(discord.ui.Modal, title="Termin hinzufügen"):
    day = discord.ui.TextInput(label="Tag", placeholder="Montag", max_length=12)
    time_text = discord.ui.TextInput(
        label="Zeit", placeholder="20:00 - 21:00 oder ab 22:30", max_length=40
    )
    kind = discord.ui.TextInput(
        label="Art / Bereich", placeholder="Theorie, RTW, Notaufnahme, Reha ...", max_length=100
    )
    teachers = discord.ui.TextInput(
        label="Lehrer / Ort (optional)",
        placeholder="Jerome Sanchez | Schulungsraum F2",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )
    topic = discord.ui.TextInput(
        label="Thema / Modul (optional)",
        placeholder="Modul 3 / Patientenübergabe / Thema offen",
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
            _time_line(str(self.time_text.value))
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
        await _insert_entry(
            self.bot,
            self.draft_id,
            day_index,
            str(self.time_text.value),
            str(self.kind.value),
            str(self.teachers.value or ""),
            str(self.topic.value or ""),
        )
        await _touch_draft(self.bot, self.draft_id)
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Termin hinzugefügt",
                description=(
                    f"**{DAY_NAMES[day_index]}** · {str(self.time_text.value).strip()} · "
                    f"**{str(self.kind.value).strip()}**"
                ),
            ),
            view=PlannerBuilderView(self.bot, self.draft_id, self.owner_id),
            ephemeral=True,
        )


class RemoveEntrySelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, draft_id: int, owner_id: int, rows: list[Any]) -> None:
        self.bot = bot
        self.draft_id = int(draft_id)
        self.owner_id = int(owner_id)
        options: list[discord.SelectOption] = []
        for row in rows[:25]:
            entry_id = int(_row_value(row, "id", 0) or 0)
            day = int(_row_value(row, "day_index", 0) or 0)
            label = _short(
                f"{DAY_NAMES[max(0, min(6, day))]} · {_row_value(row, 'time_text', '')} · {_row_value(row, 'kind', 'Termin')}",
                100,
            )
            detail = _short(_row_value(row, "topic", "") or _row_value(row, "teachers", "") or f"Termin #{entry_id}", 100)
            options.append(discord.SelectOption(label=label, description=detail, value=str(entry_id)))
        super().__init__(
            placeholder="Welchen Termin entfernen?",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"mdplan:remove:{draft_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        entry_id = int(self.values[0])
        row = await self.bot.database.fetchone(
            "SELECT id FROM md_weekly_entries WHERE id=? AND draft_id=?",
            (entry_id, self.draft_id),
        )
        if not row:
            await interaction.response.send_message("Dieser Termin existiert nicht mehr.", ephemeral=True)
            return
        await self.bot.database.execute(
            "DELETE FROM md_weekly_entries WHERE id=? AND draft_id=?",
            (entry_id, self.draft_id),
        )
        await _touch_draft(self.bot, self.draft_id)
        await interaction.response.send_message(
            f"Termin `#{entry_id}` entfernt.",
            view=PlannerBuilderView(self.bot, self.draft_id, self.owner_id),
            ephemeral=True,
        )


class RemoveEntryView(discord.ui.View):
    def __init__(self, bot: commands.Bot, draft_id: int, owner_id: int, rows: list[Any]) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.add_item(RemoveEntrySelect(bot, draft_id, owner_id, rows))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Das ist nicht dein Wochenplan.", ephemeral=True)
            return False
        return True


class PlannerBuilderView(discord.ui.View):
    def __init__(self, bot: commands.Bot, draft_id: int, owner_id: int) -> None:
        super().__init__(timeout=1800)
        self.bot = bot
        self.draft_id = int(draft_id)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Das ist nicht dein Wochenplan-Builder.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Hinzufügen", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add_entry(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(PlannerEntryModal(self.bot, self.draft_id, self.owner_id))

    @discord.ui.button(label="Entfernen", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove_entry(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await _entry_rows(self.bot, self.draft_id)
        if not rows:
            await interaction.response.send_message("Noch keine Termine vorhanden.", ephemeral=True)
            return
        note = "Wähle den Termin aus, den du entfernen möchtest."
        if len(rows) > 25:
            note += " Es werden die ersten 25 Termine angezeigt; weitere kannst du mit `/mdplan remove` löschen."
        await interaction.response.send_message(
            note,
            view=RemoveEntryView(self.bot, self.draft_id, self.owner_id, rows),
            ephemeral=True,
        )

    @discord.ui.button(label="Vorschau", emoji="👁️", style=discord.ButtonStyle.secondary, row=0)
    async def preview(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await _send_preview(interaction, self.bot, self.draft_id, self.owner_id)

    @discord.ui.button(label="Termine", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def entries(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        rows = await _entry_rows(self.bot, self.draft_id)
        lines = [
            f"`#{int(_row_value(row, 'id', 0))}` **{DAY_NAMES[int(_row_value(row, 'day_index', 0))]}** · "
            f"{_row_value(row, 'time_text', '')} · {_row_value(row, 'kind', 'Termin')}"
            for row in rows
        ]
        text = "**Aktuelle Termine**\n" + ("\n".join(lines) if lines else "Noch keine Termine.")
        chunks = _split_text(text, 1900)
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @discord.ui.button(label="Veröffentlichen", emoji="📨", style=discord.ButtonStyle.success, row=1)
    async def publish(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
            return
        draft = await self.bot.database.fetchone(
            "SELECT * FROM md_weekly_drafts WHERE id=? AND guild_id=? AND owner_id=?",
            (self.draft_id, interaction.guild_id, self.owner_id),
        )
        if not draft:
            await interaction.response.send_message("Entwurf nicht gefunden.", ephemeral=True)
            return
        channel = (
            interaction.guild.get_channel(int(draft["channel_id"]))
            if draft["channel_id"]
            else interaction.channel
        )
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message("Zielkanal nicht gefunden.", ephemeral=True)
            return
        chunks = await _render_draft(self.bot, self.draft_id, interaction.guild_id, self.owner_id)
        if not chunks:
            await interaction.response.send_message("Entwurf nicht gefunden.", ephemeral=True)
            return
        for chunk in chunks:
            await channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
        await self.bot.database.execute(
            "UPDATE md_weekly_drafts SET published_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (self.draft_id,),
        )
        await interaction.response.send_message(f"Wochenplan in {channel.mention} veröffentlicht.", ephemeral=True)

    @discord.ui.button(label="Hilfe", emoji="❓", style=discord.ButtonStyle.secondary, row=1)
    async def help_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "**MD-Plan Bedienung**\n"
            "➕ **Hinzufügen** – Termin per Formular eintragen\n"
            "🗑️ **Entfernen** – Termin direkt aus einer Liste auswählen\n"
            "👁️ **Vorschau** – fertigen Wochenplan ansehen\n"
            "📋 **Termine** – alle Einträge mit IDs anzeigen\n"
            "📨 **Veröffentlichen** – Wochenplan in den Zielkanal senden\n\n"
            "Neue Entwürfe enthalten standardmäßig **Mittwoch und Samstag ab 22:30 Uhr RP mit Staatsfraktionen**.",
            ephemeral=True,
        )


class MDBellWeeklyPlanner(
    commands.GroupCog,
    group_name="mdplan",
    group_description="Interaktiver MD Bell Wochenplaner",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await ensure_md_schema(self.bot.database)

    async def _draft(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            return None
        return await self.bot.database.fetchone(
            "SELECT * FROM md_weekly_drafts WHERE guild_id=? AND owner_id=? ORDER BY id DESC LIMIT 1",
            (interaction.guild_id, interaction.user.id),
        )

    @app_commands.command(name="start", description="Neuen MD-Bell-Wochenplan starten und Button-Menü öffnen.")
    @app_commands.describe(
        woche="Optional: Datum innerhalb der gewünschten Woche",
        kanal="Zielkanal für die spätere Veröffentlichung",
        standard_rp="Mittwoch und Samstag ab 22:30 automatisch eintragen",
    )
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
        target = kanal or interaction.channel
        if target is None or not hasattr(target, "id"):
            await interaction.response.send_message("Zielkanal konnte nicht bestimmt werden.", ephemeral=True)
            return
        try:
            monday = _week_monday(woche)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        old = await self._draft(interaction)
        if old:
            draft_id = int(old["id"])
            await self.bot.database.execute("DELETE FROM md_weekly_entries WHERE draft_id=?", (draft_id,))
            await self.bot.database.execute(
                "UPDATE md_weekly_drafts SET week_start=?,channel_id=?,updated_at=CURRENT_TIMESTAMP,published_at=NULL WHERE id=?",
                (monday.isoformat(), int(target.id), draft_id),
            )
        else:
            draft_id = await self.bot.database.execute(
                "INSERT INTO md_weekly_drafts(guild_id,owner_id,week_start,channel_id) VALUES(?,?,?,?)",
                (interaction.guild_id, interaction.user.id, monday.isoformat(), int(target.id)),
            )

        if standard_rp:
            await _insert_standard_rp(self.bot, draft_id)
        await _touch_draft(self.bot, draft_id)

        count = len(await _entry_rows(self.bot, draft_id))
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="MD Bell Wochenplaner",
                description=(
                    f"Woche ab **{monday.strftime('%d.%m.%Y')}**\n"
                    f"Zielkanal: {target.mention}\n"
                    f"Aktuell: **{count} Termine**\n\n"
                    "Ab jetzt brauchst du fast nur noch die Buttons unten."
                ),
            ),
            view=PlannerBuilderView(self.bot, draft_id, interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="builder", description="Button-Menü für den aktuellen Wochenplan wieder öffnen.")
    @app_commands.default_permissions(manage_messages=True)
    async def builder(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf. Starte mit `/mdplan start`.", ephemeral=True)
            return
        rows = await _entry_rows(self.bot, int(draft["id"]))
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="MD Bell Wochenplaner",
                description=f"Aktiver Entwurf mit **{len(rows)} Terminen**. Nutze die Buttons unten.",
            ),
            view=PlannerBuilderView(self.bot, int(draft["id"]), interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="add", description="Termin direkt per Formular hinzufügen.")
    @app_commands.default_permissions(manage_messages=True)
    async def add(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf. Starte mit `/mdplan start`.", ephemeral=True)
            return
        await interaction.response.send_modal(PlannerEntryModal(self.bot, int(draft["id"]), interaction.user.id))

    @app_commands.command(name="preview", description="Fertigen Discord-Wochenplan als Vorschau anzeigen.")
    @app_commands.default_permissions(manage_messages=True)
    async def preview(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        await _send_preview(interaction, self.bot, int(draft["id"]), interaction.user.id)

    @app_commands.command(name="publish", description="Wochenplan im Zielkanal veröffentlichen.")
    @app_commands.default_permissions(manage_messages=True)
    async def publish(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel | None = None,
    ) -> None:
        draft = await self._draft(interaction)
        if not draft or interaction.guild is None:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        target = kanal or (
            interaction.guild.get_channel(int(draft["channel_id"]))
            if draft["channel_id"]
            else interaction.channel
        )
        if not isinstance(target, discord.abc.Messageable):
            await interaction.response.send_message("Zielkanal nicht gefunden.", ephemeral=True)
            return
        chunks = await _render_draft(self.bot, int(draft["id"]), interaction.guild_id, interaction.user.id)
        if not chunks:
            await interaction.response.send_message("Entwurf nicht gefunden.", ephemeral=True)
            return
        for chunk in chunks:
            await target.send(
                chunk,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
        await self.bot.database.execute(
            "UPDATE md_weekly_drafts SET published_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(draft["id"]),),
        )
        await interaction.response.send_message(f"Wochenplan in {target.mention} veröffentlicht.", ephemeral=True)

    @app_commands.command(name="list", description="Termine im aktuellen Entwurf mit IDs anzeigen.")
    @app_commands.default_permissions(manage_messages=True)
    async def list_entries(self, interaction: discord.Interaction) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        rows = await _entry_rows(self.bot, int(draft["id"]))
        lines = [
            f"`#{int(_row_value(row, 'id', 0))}` **{DAY_NAMES[int(_row_value(row, 'day_index', 0))]}** · "
            f"{_row_value(row, 'time_text', '')} · {_row_value(row, 'kind', 'Termin')}"
            for row in rows
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Wochenplan-Termine",
                description="\n".join(lines)[:3900] if lines else "Noch keine Termine.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="remove", description="Termin anhand seiner ID löschen.")
    @app_commands.default_permissions(manage_messages=True)
    async def remove(self, interaction: discord.Interaction, termin_id: int) -> None:
        draft = await self._draft(interaction)
        if not draft:
            await interaction.response.send_message("Kein Entwurf.", ephemeral=True)
            return
        draft_id = int(draft["id"])
        row = await self.bot.database.fetchone(
            "SELECT id FROM md_weekly_entries WHERE id=? AND draft_id=?",
            (termin_id, draft_id),
        )
        if not row:
            await interaction.response.send_message("Termin nicht gefunden.", ephemeral=True)
            return
        await self.bot.database.execute(
            "DELETE FROM md_weekly_entries WHERE id=? AND draft_id=?",
            (termin_id, draft_id),
        )
        await _touch_draft(self.bot, draft_id)
        await interaction.response.send_message(
            f"Termin `#{termin_id}` gelöscht.",
            view=PlannerBuilderView(self.bot, draft_id, interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MDBellWeeklyPlanner(bot))
