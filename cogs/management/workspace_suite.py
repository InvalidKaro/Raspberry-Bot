from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.smart_search import autocomplete as smart_autocomplete
from services.smart_search import search as smart_search_rows


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = _parse_iso(value)
        return f"<t:{int(dt.timestamp())}:f>"
    except ValueError:
        return str(value)


class WorkspaceSuite(
    commands.GroupCog,
    group_name="workspace",
    group_description="Planung, Aufgaben, Wissen, Schulungen und interne Tools",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _knowledge_set(
        self,
        interaction: discord.Interaction,
        *,
        kind: str,
        key: str,
        title: str,
        content: str,
        tags: str | None = None,
    ) -> None:
        await self.bot.database.execute(
            """
            INSERT INTO knowledge_entries(guild_id,kind,entry_key,title,content,tags,created_by)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,kind,entry_key) DO UPDATE SET
                title=excluded.title,
                content=excluded.content,
                tags=excluded.tags,
                updated_at=CURRENT_TIMESTAMP,
                created_by=excluded.created_by
            """,
            (
                interaction.guild_id,
                kind,
                key.strip().lower(),
                title.strip(),
                content.strip(),
                (tags or "").strip(),
                interaction.user.id,
            ),
        )

    async def _knowledge_get(
        self,
        interaction: discord.Interaction,
        *,
        kind: str,
        key: str,
        label: str,
    ) -> None:
        row = await self.bot.database.fetchone(
            "SELECT * FROM knowledge_entries WHERE guild_id=? AND kind=? AND lower(entry_key)=lower(?) LIMIT 1",
            (interaction.guild_id, kind, key.strip()),
        )
        if not row:
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title=f"{label} nicht gefunden",
                    description=f"`{key}` ist noch nicht gespeichert.",
                ),
                ephemeral=True,
            )
            return
        embed = EmbedFactory.info(title=str(row["title"]), description=str(row["content"])[:3900])
        if row["tags"]:
            embed.set_footer(text=f"Tags: {row['tags']}")
        await interaction.response.send_message(embed=embed)

    async def _choices(
        self,
        interaction: discord.Interaction,
        current: str,
        *,
        kinds: set[str] | None = None,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        items = await smart_autocomplete(self.bot, interaction.guild_id, current, kinds=kinds)
        return [app_commands.Choice(name=label, value=value) for label, value in items[:25]]

    @app_commands.command(name="planner_add", description="Termin zum Wochenplan hinzufügen.")
    @app_commands.default_permissions(manage_messages=True)
    async def planner_add(
        self,
        interaction: discord.Interaction,
        datum: str,
        uhrzeit: str,
        titel: str,
        verantwortliche: str | None = None,
        kategorie: str = "Termin",
    ) -> None:
        try:
            datetime.fromisoformat(datum)
            datetime.strptime(uhrzeit, "%H:%M")
        except ValueError:
            await interaction.response.send_message(
                "Bitte Datum `YYYY-MM-DD` und Uhrzeit `HH:MM` verwenden.", ephemeral=True
            )
            return
        await self.bot.database.execute(
            "INSERT INTO planner_entries(guild_id,event_date,start_time,title,owner_text,category,created_by) VALUES(?,?,?,?,?,?,?)",
            (
                interaction.guild_id,
                datum,
                uhrzeit,
                titel.strip(),
                (verantwortliche or "").strip(),
                kategorie.strip(),
                interaction.user.id,
            ),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Wochenplan aktualisiert",
                description=f"`{datum}` `{uhrzeit}` · **{titel}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="planner_show", description="Wochenplan als Discord-Text erzeugen.")
    async def planner_show(
        self,
        interaction: discord.Interaction,
        ab_datum: str | None = None,
        tage: app_commands.Range[int, 1, 31] = 7,
    ) -> None:
        start = ab_datum or _now_utc().date().isoformat()
        try:
            start_date = datetime.fromisoformat(start).date()
        except ValueError:
            await interaction.response.send_message("Datum bitte als `YYYY-MM-DD`.", ephemeral=True)
            return
        end = (start_date + timedelta(days=int(tage))).isoformat()
        rows = await self.bot.database.fetchall(
            "SELECT * FROM planner_entries WHERE guild_id=? AND event_date>=? AND event_date<? ORDER BY event_date,start_time,id",
            (interaction.guild_id, start_date.isoformat(), end),
        )
        if not rows:
            await interaction.response.send_message("Keine Termine im Zeitraum.", ephemeral=True)
            return
        by_date: dict[str, list] = {}
        for row in rows:
            by_date.setdefault(str(row["event_date"]), []).append(row)
        blocks: list[str] = []
        weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        for day, items in by_date.items():
            d = datetime.fromisoformat(day).date()
            blocks.append(f"## {weekdays[d.weekday()]} · {d.strftime('%d.%m.%Y')}")
            for row in items:
                owner = f" · {row['owner_text']}" if row["owner_text"] else ""
                blocks.append(f"**{row['start_time']}** · {row['title']} — *{row['category']}*{owner}")
        text = "\n".join(blocks)
        await interaction.response.send_message(
            text[:1950] if len(text) <= 1950 else "Der Plan ist zu lang:\n" + text[:1900]
        )

    @app_commands.command(name="planner_clear", description="Alte Wochenplan-Termine entfernen.")
    @app_commands.default_permissions(manage_messages=True)
    async def planner_clear(self, interaction: discord.Interaction, bis_datum: str) -> None:
        try:
            datetime.fromisoformat(bis_datum)
        except ValueError:
            await interaction.response.send_message("Datum bitte als `YYYY-MM-DD`.", ephemeral=True)
            return
        await self.bot.database.execute(
            "DELETE FROM planner_entries WHERE guild_id=? AND event_date<?",
            (interaction.guild_id, bis_datum),
        )
        await interaction.response.send_message("Alte Plan-Termine entfernt.", ephemeral=True)

    @app_commands.command(name="task_add", description="Aufgabe zum internen Aufgabenboard hinzufügen.")
    @app_commands.default_permissions(manage_messages=True)
    async def task_add(
        self,
        interaction: discord.Interaction,
        titel: str,
        details: str | None = None,
        zustaendig: discord.Member | None = None,
        faellig: str | None = None,
    ) -> None:
        if faellig:
            try:
                _parse_iso(faellig)
            except ValueError:
                await interaction.response.send_message(
                    "Fälligkeit bitte ISO, z. B. `2026-09-05T20:00`.", ephemeral=True
                )
                return
        task_id = await self.bot.database.execute(
            "INSERT INTO workspace_tasks(guild_id,title,details,status,assigned_to,due_at,created_by) VALUES(?,?,?,'open',?,?,?)",
            (
                interaction.guild_id,
                titel.strip(),
                (details or "").strip(),
                zustaendig.id if zustaendig else None,
                faellig,
                interaction.user.id,
            ),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title=f"Aufgabe #{task_id}", description=f"**{titel}** wurde zum Board hinzugefügt."
            ),
            ephemeral=True,
        )

    @app_commands.command(name="task_list", description="Aufgabenboard anzeigen.")
    async def task_list(self, interaction: discord.Interaction, status: str = "open") -> None:
        if status not in {"open", "doing", "done", "all"}:
            await interaction.response.send_message("`status`: open, doing, done oder all.", ephemeral=True)
            return
        if status == "all":
            rows = await self.bot.database.fetchall(
                "SELECT * FROM workspace_tasks WHERE guild_id=? ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,due_at IS NULL,due_at,id DESC LIMIT 30",
                (interaction.guild_id,),
            )
        else:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM workspace_tasks WHERE guild_id=? AND status=? ORDER BY due_at IS NULL,due_at,id DESC LIMIT 30",
                (interaction.guild_id, status),
            )
        symbols = {"open": "○", "doing": "◐", "done": "●"}
        lines = []
        for row in rows:
            assigned = f" · <@{row['assigned_to']}>" if row["assigned_to"] else ""
            due = f" · {_fmt_dt(row['due_at'])}" if row["due_at"] else ""
            lines.append(f"{symbols.get(str(row['status']), '•')} `#{row['id']}` **{row['title']}**{assigned}{due}")
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Aufgabenboard", description="\n".join(lines) or "Keine Aufgaben.")
        )

    @app_commands.command(name="task_status", description="Status einer Aufgabe ändern.")
    @app_commands.default_permissions(manage_messages=True)
    async def task_status(self, interaction: discord.Interaction, task_id: int, status: str) -> None:
        if status not in {"open", "doing", "done"}:
            await interaction.response.send_message("`status`: open, doing oder done.", ephemeral=True)
            return
        await self.bot.database.execute(
            "UPDATE workspace_tasks SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?",
            (status, task_id, interaction.guild_id),
        )
        await interaction.response.send_message(f"Aufgabe `#{task_id}` → **{status}**.", ephemeral=True)

    @app_commands.command(name="event_add", description="Event/Kalendertermin erstellen.")
    @app_commands.default_permissions(manage_messages=True)
    async def event_add(
        self,
        interaction: discord.Interaction,
        titel: str,
        start: str,
        beschreibung: str | None = None,
        kanal: discord.TextChannel | None = None,
    ) -> None:
        try:
            dt = _parse_iso(start)
        except ValueError:
            await interaction.response.send_message(
                "Start bitte ISO, z. B. `2026-09-05T20:00`.", ephemeral=True
            )
            return
        event_id = await self.bot.database.execute(
            "INSERT INTO workspace_events(guild_id,title,description,starts_at,channel_id,created_by) VALUES(?,?,?,?,?,?)",
            (
                interaction.guild_id,
                titel.strip(),
                (beschreibung or "").strip(),
                dt.isoformat(),
                kanal.id if kanal else interaction.channel_id,
                interaction.user.id,
            ),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title=f"Event #{event_id} erstellt",
                description=f"**{titel}** · <t:{int(dt.timestamp())}:F>",
            )
        )

    @app_commands.command(name="event_list", description="Kommende Events anzeigen.")
    async def event_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT e.*,SUM(CASE WHEN r.status='yes' THEN 1 ELSE 0 END) AS yes_count,SUM(CASE WHEN r.status='maybe' THEN 1 ELSE 0 END) AS maybe_count FROM workspace_events e LEFT JOIN event_rsvps r ON r.event_id=e.id WHERE e.guild_id=? AND e.starts_at>=? GROUP BY e.id ORDER BY e.starts_at LIMIT 20",
            (interaction.guild_id, _now_utc().isoformat()),
        )
        lines = [
            f"`#{r['id']}` **{r['title']}** · {_fmt_dt(r['starts_at'])} · ✅ {r['yes_count'] or 0} · ❔ {r['maybe_count'] or 0}"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Kommende Events", description="\n".join(lines) or "Keine kommenden Events.")
        )

    @app_commands.command(name="event_rsvp", description="Bei einem Event zu-/absagen.")
    async def event_rsvp(self, interaction: discord.Interaction, event_id: int, status: str) -> None:
        if status not in {"yes", "maybe", "no"}:
            await interaction.response.send_message("`status`: yes, maybe oder no.", ephemeral=True)
            return
        event = await self.bot.database.fetchone(
            "SELECT id FROM workspace_events WHERE id=? AND guild_id=?",
            (event_id, interaction.guild_id),
        )
        if not event:
            await interaction.response.send_message("Event nicht gefunden.", ephemeral=True)
            return
        await self.bot.database.execute(
            "INSERT INTO event_rsvps(event_id,user_id,status) VALUES(?,?,?) ON CONFLICT(event_id,user_id) DO UPDATE SET status=excluded.status,updated_at=CURRENT_TIMESTAMP",
            (event_id, interaction.user.id, status),
        )
        await interaction.response.send_message(f"Event `#{event_id}` → **{status}**.", ephemeral=True)

    @app_commands.command(name="calendar", description="Planer, Events und Erinnerungen zusammen anzeigen.")
    async def calendar(
        self, interaction: discord.Interaction, tage: app_commands.Range[int, 1, 31] = 14
    ) -> None:
        now = _now_utc()
        until = now + timedelta(days=int(tage))
        planner = await self.bot.database.fetchall(
            "SELECT event_date,start_time,title,'Plan' source FROM planner_entries WHERE guild_id=? AND event_date>=? AND event_date<=? ORDER BY event_date,start_time LIMIT 20",
            (interaction.guild_id, now.date().isoformat(), until.date().isoformat()),
        )
        events = await self.bot.database.fetchall(
            "SELECT starts_at,title,'Event' source FROM workspace_events WHERE guild_id=? AND starts_at>=? AND starts_at<=? ORDER BY starts_at LIMIT 20",
            (interaction.guild_id, now.isoformat(), until.isoformat()),
        )
        items: list[tuple[str, str]] = []
        for row in planner:
            items.append(
                (
                    f"{row['event_date']}T{row['start_time']}:00",
                    f"🗓️ **{row['title']}** · `{row['event_date']} {row['start_time']}`",
                )
            )
        for row in events:
            items.append((str(row["starts_at"]), f"📅 **{row['title']}** · {_fmt_dt(row['starts_at'])}"))
        items.sort(key=lambda item: item[0])
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title=f"Kalender · nächste {tage} Tage",
                description="\n".join(text for _, text in items[:30]) or "Keine Termine.",
            )
        )

    @app_commands.command(name="training_add", description="Eintrag zur Schulungsbibliothek hinzufügen.")
    @app_commands.default_permissions(manage_messages=True)
    async def training_add(
        self,
        interaction: discord.Interaction,
        titel: str,
        inhalt: str,
        kategorie: str = "Allgemein",
        link: str | None = None,
    ) -> None:
        await self.bot.database.execute(
            "INSERT INTO training_library(guild_id,title,category,content,source_url,created_by) VALUES(?,?,?,?,?,?)",
            (interaction.guild_id, titel.strip(), kategorie.strip(), inhalt.strip(), link, interaction.user.id),
        )
        await interaction.response.send_message("Schulung gespeichert.", ephemeral=True)

    @app_commands.command(name="training_get", description="Schulung aus der Bibliothek öffnen.")
    async def training_get(self, interaction: discord.Interaction, suche: str) -> None:
        ranked = await smart_search_rows(
            self.bot, interaction.guild_id, suche, limit=1, kinds={"training"}
        )
        if not ranked:
            await interaction.response.send_message("Keine passende Schulung.", ephemeral=True)
            return
        row = await self.bot.database.fetchone(
            "SELECT * FROM training_library WHERE guild_id=? AND id=?",
            (interaction.guild_id, int(ranked[0]["key"])),
        )
        if not row:
            await interaction.response.send_message("Keine passende Schulung.", ephemeral=True)
            return
        embed = EmbedFactory.info(title=str(row["title"]), description=str(row["content"])[:3900])
        embed.add_field(name="Kategorie", value=str(row["category"]), inline=True)
        if row["source_url"]:
            embed.add_field(name="Material", value=str(row["source_url"])[:1000], inline=False)
        await interaction.response.send_message(embed=embed)

    @training_get.autocomplete("suche")
    async def training_get_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current, kinds={"training"})

    @app_commands.command(name="training_list", description="Schulungsbibliothek auflisten.")
    async def training_list(self, interaction: discord.Interaction, kategorie: str | None = None) -> None:
        if kategorie:
            rows = await self.bot.database.fetchall(
                "SELECT id,title,category FROM training_library WHERE guild_id=? AND lower(category)=lower(?) ORDER BY title COLLATE NOCASE LIMIT 30",
                (interaction.guild_id, kategorie),
            )
        else:
            rows = await self.bot.database.fetchall(
                "SELECT id,title,category FROM training_library WHERE guild_id=? ORDER BY category,title COLLATE NOCASE LIMIT 30",
                (interaction.guild_id,),
            )
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Schulungsbibliothek",
                description="\n".join(f"`#{r['id']}` **{r['title']}** · {r['category']}" for r in rows)
                or "Noch leer.",
            )
        )

    @app_commands.command(name="wiki_set", description="Wiki-Seite speichern/aktualisieren.")
    @app_commands.default_permissions(manage_messages=True)
    async def wiki_set(
        self,
        interaction: discord.Interaction,
        key: str,
        titel: str,
        inhalt: str,
        tags: str | None = None,
    ) -> None:
        await self._knowledge_set(
            interaction, kind="wiki", key=key, title=titel, content=inhalt, tags=tags
        )
        await interaction.response.send_message("Wiki-Seite gespeichert.", ephemeral=True)

    @app_commands.command(name="wiki_get", description="Wiki-Seite öffnen.")
    async def wiki_get(self, interaction: discord.Interaction, key: str) -> None:
        await self._knowledge_get(interaction, kind="wiki", key=key, label="Wiki-Seite")

    @wiki_get.autocomplete("key")
    async def wiki_get_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current, kinds={"wiki"})

    @app_commands.command(name="faq_set", description="FAQ-Eintrag speichern/aktualisieren.")
    @app_commands.default_permissions(manage_messages=True)
    async def faq_set(
        self, interaction: discord.Interaction, frage: str, antwort: str, key: str
    ) -> None:
        await self._knowledge_set(
            interaction, kind="faq", key=key, title=frage, content=antwort
        )
        await interaction.response.send_message("FAQ gespeichert.", ephemeral=True)

    @app_commands.command(name="faq_get", description="FAQ durchsuchen.")
    async def faq_get(self, interaction: discord.Interaction, suche: str) -> None:
        ranked = await smart_search_rows(
            self.bot, interaction.guild_id, suche, limit=1, kinds={"faq"}
        )
        if not ranked:
            await interaction.response.send_message("Keine passende FAQ.", ephemeral=True)
            return
        await self._knowledge_get(
            interaction, kind="faq", key=str(ranked[0]["key"]), label="FAQ"
        )

    @faq_get.autocomplete("suche")
    async def faq_get_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current, kinds={"faq"})

    @app_commands.command(name="med_set", description="Medikament/Wissenseintrag speichern.")
    @app_commands.default_permissions(manage_messages=True)
    async def med_set(
        self, interaction: discord.Interaction, name: str, inhalt: str, tags: str | None = None
    ) -> None:
        await self._knowledge_set(
            interaction, kind="med", key=name, title=name, content=inhalt, tags=tags
        )
        await interaction.response.send_message("Wissenseintrag gespeichert.", ephemeral=True)

    @app_commands.command(name="med_get", description="Medikament/Wissenseintrag öffnen.")
    async def med_get(self, interaction: discord.Interaction, name: str) -> None:
        await self._knowledge_get(interaction, kind="med", key=name, label="Wissenseintrag")

    @med_get.autocomplete("name")
    async def med_get_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current, kinds={"med"})

    @app_commands.command(name="quiz_add", description="Prüfungsfrage zum Fragenpool hinzufügen.")
    @app_commands.default_permissions(manage_messages=True)
    async def quiz_add(
        self,
        interaction: discord.Interaction,
        frage: str,
        antwort: str,
        kategorie: str = "Allgemein",
        erklaerung: str | None = None,
    ) -> None:
        await self.bot.database.execute(
            "INSERT INTO quiz_questions(guild_id,category,question,answer,explanation,created_by) VALUES(?,?,?,?,?,?)",
            (
                interaction.guild_id,
                kategorie.strip(),
                frage.strip(),
                antwort.strip(),
                (erklaerung or "").strip(),
                interaction.user.id,
            ),
        )
        await interaction.response.send_message("Prüfungsfrage gespeichert.", ephemeral=True)

    @app_commands.command(name="quiz_random", description="Zufällige Prüfungsfrage ziehen.")
    async def quiz_random(self, interaction: discord.Interaction, kategorie: str | None = None) -> None:
        if kategorie:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM quiz_questions WHERE guild_id=? AND lower(category)=lower(?)",
                (interaction.guild_id, kategorie),
            )
        else:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM quiz_questions WHERE guild_id=?", (interaction.guild_id,)
            )
        if not rows:
            await interaction.response.send_message("Keine Fragen vorhanden.", ephemeral=True)
            return
        row = random.choice(rows)
        embed = EmbedFactory.info(
            title=f"Prüfungsfrage · {row['category']}", description=str(row["question"])
        )
        embed.add_field(name="Antwort", value=f"||{str(row['answer'])[:1000]}||", inline=False)
        if row["explanation"]:
            embed.add_field(
                name="Erklärung", value=f"||{str(row['explanation'])[:1000]}||", inline=False
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="search",
        description="Smart Search: Wiki, FAQ, Wissen, Schulungen, Quiz, Templates, Forms und Commands.",
    )
    async def smart_search(self, interaction: discord.Interaction, suche: str) -> None:
        query = suche.strip()
        if len(query) < 2:
            await interaction.response.send_message(
                "Bitte mindestens 2 Zeichen eingeben oder einen Auto-Fill-Vorschlag auswählen.",
                ephemeral=True,
            )
            return
        rows = await smart_search_rows(self.bot, interaction.guild_id, query, limit=15)
        if not rows:
            await interaction.response.send_message(
                embed=EmbedFactory.info(
                    title=f"Smart Search · {query}",
                    description="Keine Treffer. Tipp: Teilwort, Abkürzung oder Tag probieren.",
                ),
                ephemeral=True,
            )
            return
        labels = {
            "wiki": "Wiki",
            "faq": "FAQ",
            "med": "Wissen",
            "training": "Schulung",
            "quiz": "Quiz",
            "template": "Template",
            "form": "Formular",
            "command": "Command",
        }
        lines = []
        for index, row in enumerate(rows, 1):
            kind = str(row.get("kind") or "item")
            preview = " ".join(str(row.get("content") or "").split())[:180]
            category = f" · {row['category']}" if row.get("category") else ""
            lines.append(
                f"**{index}. [{labels.get(kind, kind)}] {row['title']}**{category}\n"
                f"`{row['key']}` · Relevanz {row['score']:.0f}\n{preview}"
            )
        embed = EmbedFactory.info(
            title=f"Smart Search · {query}", description="\n\n".join(lines)[:3900]
        )
        embed.set_footer(text="Auto-Fill ist beim Suchfeld aktiv · exakte Keys/Titel werden priorisiert")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @smart_search.autocomplete("suche")
    async def smart_search_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current)

    @app_commands.command(name="reminders", description="Eigene offenen Reminder als Hub anzeigen.")
    async def reminders(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.database.fetchall(
            "SELECT id,message,due_at FROM reminders WHERE user_id=? AND delivered=0 ORDER BY due_at LIMIT 20",
            (interaction.user.id,),
        )
        lines = [
            f"`#{r['id']}` **{str(r['message'])[:100]}** · {_fmt_dt(r['due_at'])}" for r in rows
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Reminder Hub", description="\n".join(lines) or "Keine offenen Reminder."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WorkspaceSuite(bot))
