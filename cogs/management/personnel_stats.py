from __future__ import annotations

import csv
from datetime import date, timedelta
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.personnel_export import render_personnel_chart, render_personnel_png
from services.personnel_v2 import PersonnelService


class Personnel(
    commands.GroupCog,
    group_name="perso",
    group_description="MD Personalabteilung • Mitarbeiter & Statistiken",
):
    def __init__(self, bot):
        self.bot = bot
        self.service = PersonnelService(bot)

    async def _name_auto(self, interaction, current):
        if not interaction.guild_id:
            return []
        rows = await self.service.list_members(interaction.guild_id)
        cur = current.lower()
        return [
            app_commands.Choice(
                name=str(row["display_name"])[:100],
                value=str(row["display_name"])[:100],
            )
            for row in rows
            if cur in str(row["display_name"]).lower()
        ][:25]

    async def _period_auto(self, interaction, current):
        """Suggest actually stored Perso periods for Discord autocomplete."""
        del interaction
        rows = await self.bot.database.fetchall(
            """
            SELECT
                period_key,
                MIN(record_date) AS first_date,
                MAX(record_date) AS last_date,
                COUNT(*) AS records
            FROM personnel_records
            WHERE period_key IS NOT NULL AND trim(period_key) <> ''
            GROUP BY period_key
            ORDER BY MAX(record_date) DESC, period_key DESC
            LIMIT 50
            """
        )
        needle = str(current or "").strip().casefold()
        choices = []
        for row in rows:
            key = str(row["period_key"] or "").strip()
            if not key:
                continue

            first_raw = str(row["first_date"] or "").strip()
            last_raw = str(row["last_date"] or "").strip()
            label = key
            try:
                first = date.fromisoformat(first_raw)
                last = date.fromisoformat(last_raw)
                if first == last:
                    date_text = first.strftime("%d.%m.%Y")
                else:
                    date_text = f"{first.strftime('%d.%m.')}–{last.strftime('%d.%m.%Y')}"
                label = f"{key} · {date_text}"
            except ValueError:
                pass

            if needle and needle not in key.casefold() and needle not in label.casefold():
                continue
            choices.append(app_commands.Choice(name=label[:100], value=key[:100]))
            if len(choices) >= 25:
                break
        return choices

    async def _member(self, interaction, person):
        row = await self.service.get_by_name(interaction.guild_id, person)
        if not row:
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title="Nicht gefunden",
                    description="Person nicht in der Perso-Datenbank.",
                ),
                ephemeral=True,
            )
        return row

    @app_commands.command(name="add", description="Mitarbeiter zur Perso-Datenbank hinzufügen.")
    @app_commands.default_permissions(manage_messages=True)
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        mitglied: discord.Member | None = None,
        rang: str | None = None,
        abteilung: str | None = None,
    ):
        row = await self.service.add(
            interaction.guild_id,
            name,
            interaction.user.id,
            user_id=mitglied.id if mitglied else None,
            rank=rang,
            department=abteilung,
        )
        if hasattr(self.bot, "audit"):
            await self.bot.audit.record(
                "perso.member.add",
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                target_type="personnel",
                target_id=row["id"],
                after=dict(row),
            )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Mitarbeiter gespeichert",
                description=f"**{row['display_name']}** ist gespeichert.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="edit", description="Name, Rang, Abteilung oder Discord-Verknüpfung ändern.")
    @app_commands.autocomplete(person=_name_auto)
    @app_commands.default_permissions(manage_messages=True)
    async def edit(
        self,
        interaction: discord.Interaction,
        person: str,
        name: str | None = None,
        rang: str | None = None,
        abteilung: str | None = None,
        mitglied: discord.Member | None = None,
    ):
        row = await self._member(interaction, person)
        if not row:
            return
        updated = await self.service.edit(
            interaction.guild_id,
            int(row["id"]),
            interaction.user.id,
            name=name,
            rank=rang,
            department=abteilung,
            user_id=mitglied.id if mitglied else None,
        )
        if hasattr(self.bot, "audit"):
            await self.bot.audit.record(
                "perso.member.edit",
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                target_type="personnel",
                target_id=row["id"],
                before=dict(row),
                after=dict(updated),
            )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Personalakte aktualisiert",
                description=f"**{updated['display_name']}** wurde aktualisiert.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="archive", description="Mitarbeiter archivieren, ohne Historie zu löschen.")
    @app_commands.autocomplete(person=_name_auto)
    @app_commands.default_permissions(manage_messages=True)
    async def archive(self, interaction: discord.Interaction, person: str):
        row = await self._member(interaction, person)
        if not row:
            return
        await self.service.archive(interaction.guild_id, int(row["id"]))
        if hasattr(self.bot, "audit"):
            await self.bot.audit.record(
                "perso.member.archive",
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                target_type="personnel",
                target_id=row["id"],
                before=dict(row),
            )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Archiviert",
                description=f"**{row['display_name']}** wurde archiviert. Daten bleiben erhalten.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="record", description="Einweisungen/BWG eintragen.")
    @app_commands.autocomplete(person=_name_auto)
    @app_commands.default_permissions(manage_messages=True)
    async def record(
        self,
        interaction: discord.Interaction,
        person: str,
        einweisungen: app_commands.Range[int, 0, 999] = 0,
        bwg: app_commands.Range[int, 0, 999] = 0,
        zeitraum: str | None = None,
        datum: str | None = None,
        notiz: str | None = None,
    ):
        row = await self._member(interaction, person)
        if not row:
            return
        try:
            d = datum or date.today().isoformat()
            date.fromisoformat(d)
        except ValueError:
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title="Datum ungültig",
                    description="Bitte `YYYY-MM-DD` verwenden.",
                ),
                ephemeral=True,
            )
            return
        rid = await self.service.record(
            interaction.guild_id,
            int(row["id"]),
            interaction.user.id,
            inductions=einweisungen,
            bwg=bwg,
            record_date=d,
            period_key=zeitraum,
            note=notiz,
        )
        if hasattr(self.bot, "audit"):
            await self.bot.audit.record(
                "perso.record.add",
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                target_type="personnel_record",
                target_id=rid,
                after={
                    "person": row["display_name"],
                    "einweisungen": einweisungen,
                    "bwg": bwg,
                    "datum": d,
                    "zeitraum": zeitraum,
                },
            )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Perso-Daten eingetragen",
                description=f"**{row['display_name']}** · E **{einweisungen}** · BWG **{bwg}** · `{d}`",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="bulkrecord", description="Gleiche Aktivität für mehrere gespeicherte Personen eintragen.")
    @app_commands.default_permissions(manage_messages=True)
    async def bulkrecord(
        self,
        interaction: discord.Interaction,
        personen: str,
        einweisungen: app_commands.Range[int, 0, 999] = 0,
        bwg: app_commands.Range[int, 0, 999] = 0,
        zeitraum: str | None = None,
    ):
        names = [x.strip() for x in personen.split(",") if x.strip()][:25]
        ok = []
        missing = []
        for name in names:
            row = await self.service.get_by_name(interaction.guild_id, name)
            if not row:
                missing.append(name)
                continue
            await self.service.record(
                interaction.guild_id,
                int(row["id"]),
                interaction.user.id,
                inductions=einweisungen,
                bwg=bwg,
                period_key=zeitraum,
            )
            ok.append(row["display_name"])
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Bulk-Eintrag",
                description=f"Gespeichert: **{len(ok)}**\nNicht gefunden: {', '.join(missing) if missing else '—'}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="clear", description="Setzt alle Perso-Aktivitätswerte einer Woche zurück, Namen bleiben erhalten.")
    @app_commands.describe(
        bestaetigen="Muss True sein, damit wirklich gelöscht wird",
        datum="Ein Datum aus der Woche im Format YYYY-MM-DD; leer = aktuelle Woche",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def clear(
        self,
        interaction: discord.Interaction,
        bestaetigen: bool,
        datum: str | None = None,
    ):
        try:
            target = date.fromisoformat(datum) if datum else date.today()
        except ValueError:
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title="Datum ungültig",
                    description="Bitte `YYYY-MM-DD` verwenden.",
                ),
                ephemeral=True,
            )
            return
        start = target - timedelta(days=target.weekday())
        end = start + timedelta(days=6)
        if not bestaetigen:
            await interaction.response.send_message(
                embed=EmbedFactory.info(
                    title="Perso-Wochenreset nicht ausgeführt",
                    description=(
                        f"Woche **{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}** würde zurückgesetzt.\n\n"
                        "Führe den Command mit `bestaetigen: True` aus. Namen, Personalakten, Notizen, "
                        "Qualifikationen und Ränge bleiben erhalten."
                    ),
                ),
                ephemeral=True,
            )
            return
        summary = await self.bot.database.fetchone(
            "SELECT COUNT(*) AS records,COALESCE(SUM(inductions),0) AS inductions,COALESCE(SUM(bwg),0) AS bwg "
            "FROM personnel_records WHERE record_date>=? AND record_date<=?",
            (start.isoformat(), end.isoformat()),
        )
        records = int(summary["records"] or 0) if summary else 0
        inductions = int(summary["inductions"] or 0) if summary else 0
        bwg = int(summary["bwg"] or 0) if summary else 0
        await self.bot.database.execute(
            "DELETE FROM personnel_records WHERE record_date>=? AND record_date<=?",
            (start.isoformat(), end.isoformat()),
        )
        if hasattr(self.bot, "audit"):
            await self.bot.audit.record(
                "perso.week.clear",
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                target_type="personnel_week",
                target_id=f"{start.isoformat()}_{end.isoformat()}",
                before={
                    "records": records,
                    "einweisungen": inductions,
                    "bwg": bwg,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                after={"records": 0, "einweisungen": 0, "bwg": 0},
            )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Perso-Woche zurückgesetzt",
                description=(
                    f"**{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}**\n\n"
                    f"Gelöscht: **{records}** Aktivitätseinträge\n"
                    f"Einweisungen: **{inductions} → 0**\n"
                    f"BWG: **{bwg} → 0**\n\n"
                    "✅ Namen und Personalakten wurden **nicht** gelöscht."
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="note", description="Interne Notiz zur Personalakte hinzufügen.")
    @app_commands.autocomplete(person=_name_auto)
    @app_commands.default_permissions(manage_messages=True)
    async def note(self, interaction: discord.Interaction, person: str, notiz: str):
        row = await self._member(interaction, person)
        if not row:
            return
        await self.service.add_note(interaction.guild_id, int(row["id"]), interaction.user.id, notiz)
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Notiz gespeichert",
                description=f"Interne Notiz für **{row['display_name']}** gespeichert.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="qualification", description="Qualifikation/Modul einer Person setzen.")
    @app_commands.autocomplete(person=_name_auto)
    @app_commands.default_permissions(manage_messages=True)
    async def qualification(
        self,
        interaction: discord.Interaction,
        person: str,
        qualifikation: str,
        status: str = "bestanden",
    ):
        row = await self._member(interaction, person)
        if not row:
            return
        await self.service.set_qualification(
            interaction.guild_id,
            int(row["id"]),
            interaction.user.id,
            qualifikation,
            status,
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Qualifikation aktualisiert",
                description=f"**{row['display_name']}** · {qualifikation}: **{status}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="goal", description="Zielwert für eine Person und einen Zeitraum setzen.")
    @app_commands.autocomplete(person=_name_auto)
    @app_commands.choices(
        typ=[
            app_commands.Choice(name="Gesamt", value="activity"),
            app_commands.Choice(name="Einweisungen", value="inductions"),
            app_commands.Choice(name="BWG", value="bwg"),
        ]
    )
    @app_commands.default_permissions(manage_messages=True)
    async def goal(
        self,
        interaction: discord.Interaction,
        person: str,
        typ: app_commands.Choice[str],
        ziel: app_commands.Range[int, 1, 9999],
        zeitraum: str,
    ):
        row = await self._member(interaction, person)
        if not row:
            return
        await self.service.set_goal(
            interaction.guild_id,
            int(row["id"]),
            interaction.user.id,
            typ.value,
            int(ziel),
            zeitraum,
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Ziel gespeichert",
                description=f"**{row['display_name']}** · {typ.name} **{ziel}** · `{zeitraum}`",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="overview", description="Gesamtübersicht aller aktiven Angestellten.")
    async def overview(self, interaction: discord.Interaction, zeitraum: str | None = None):
        rows = await self.service.totals(interaction.guild_id, period_like=zeitraum)
        if not rows:
            await interaction.response.send_message(
                embed=EmbedFactory.info(title="Keine Perso-Daten", description="Noch keine Daten."),
                ephemeral=True,
            )
            return
        lines = [
            f"**{i}. {r['display_name']}** — E **{r['inductions']}** · BWG **{r['bwg']}** · Gesamt **{r['activity']}**"
            for i, r in enumerate(rows[:20], 1)
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="MD Perso • Übersicht", description="\n".join(lines))
        )

    @app_commands.command(name="leaderboard", description="Ranking nach Einweisungen, BWG oder Gesamtaktivität.")
    @app_commands.choices(
        metric=[
            app_commands.Choice(name="Gesamt", value="activity"),
            app_commands.Choice(name="Einweisungen", value="inductions"),
            app_commands.Choice(name="BWG", value="bwg"),
        ]
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        metric: app_commands.Choice[str],
        zeitraum: str | None = None,
    ):
        rows = await self.service.totals(interaction.guild_id, period_like=zeitraum)
        ordered = sorted(rows, key=lambda row: int(row[metric.value]), reverse=True)
        lines = [
            f"**{i}. {row['display_name']}** — **{int(row[metric.value])}**"
            for i, row in enumerate(ordered[:15], 1)
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title=f"Perso • Leaderboard • {metric.name}",
                description="\n".join(lines) or "Keine Daten.",
            )
        )

    @app_commands.command(name="activity", description="Letzte Perso-Einträge chronologisch anzeigen.")
    async def activity(self, interaction: discord.Interaction):
        rows = await self.service.activity_feed(interaction.guild_id, 20)
        lines = [
            f"`{row['record_date']}` **{row['display_name']}** · E {row['inductions']} · BWG {row['bwg']}"
            for row in rows
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Perso • Activity Feed",
                description="\n".join(lines) or "Keine Einträge.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="trend", description="Entwicklung nach gespeichertem Zeitraum anzeigen.")
    async def trend(self, interaction: discord.Interaction):
        rows = list(reversed(await self.service.trend(interaction.guild_id, 12)))
        lines = [
            f"`{row['period_key']}` · E **{row['inductions']}** · BWG **{row['bwg']}** · Gesamt **{row['activity']}**"
            for row in rows
        ]
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Perso • Trend",
                description="\n".join(lines) or "Keine Daten.",
            )
        )

    @app_commands.command(name="person", description="Personalakte mit Historie, Notizen, Qualifikationen und Ranghistorie.")
    @app_commands.autocomplete(person=_name_auto)
    async def person(self, interaction: discord.Interaction, person: str):
        row = await self._member(interaction, person)
        if not row:
            return
        pid = int(row["id"])
        history = await self.service.history(interaction.guild_id, pid, 30)
        notes = await self.service.notes(interaction.guild_id, pid, 5)
        quals = await self.service.qualifications(interaction.guild_id, pid)
        ranks = await self.service.rank_history(interaction.guild_id, pid, 5)
        te = sum(int(item["inductions"]) for item in history)
        tb = sum(int(item["bwg"]) for item in history)
        embed = EmbedFactory.info(
            title=f"Personalakte • {row['display_name']}",
            description=(
                f"Rang: **{row['rank_name'] or '—'}** · Abteilung: **{row['department'] or '—'}**\n"
                f"Einweisungen **{te}** · BWG **{tb}**"
            ),
        )
        if quals:
            embed.add_field(
                name="Qualifikationen",
                value="\n".join(f"• {q['name']} — **{q['status']}**" for q in quals[:10]),
                inline=False,
            )
        if notes:
            embed.add_field(
                name="Interne Notizen",
                value="\n".join(f"• {n['content'][:180]}" for n in notes),
                inline=False,
            )
        if ranks:
            embed.add_field(
                name="Ranghistorie",
                value="\n".join(
                    f"• {rank['old_rank'] or '—'} → **{rank['new_rank'] or '—'}**" for rank in ranks
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="compare", description="Zwei gespeicherte Zeiträume vergleichen.")
    @app_commands.describe(
        zeitraum_a="Ersten gespeicherten Zeitraum auswählen",
        zeitraum_b="Zweiten gespeicherten Zeitraum auswählen",
    )
    @app_commands.autocomplete(zeitraum_a=_period_auto, zeitraum_b=_period_auto)
    async def compare(
        self,
        interaction: discord.Interaction,
        zeitraum_a: str,
        zeitraum_b: str,
    ):
        a = await self.service.totals(interaction.guild_id, period_like=zeitraum_a)
        b = await self.service.totals(interaction.guild_id, period_like=zeitraum_b)
        ma = {row["display_name"]: row for row in a}
        mb = {row["display_name"]: row for row in b}
        names = sorted(set(ma) | set(mb))
        lines = []
        for name in names[:20]:
            av = int(ma.get(name, {"activity": 0})["activity"])
            bv = int(mb.get(name, {"activity": 0})["activity"])
            delta = bv - av
            lines.append(
                f"**{name}** · {av} → {bv} (`{'+' if delta >= 0 else ''}{delta}`)"
            )
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title=f"Perso • Vergleich · {zeitraum_a} → {zeitraum_b}",
                description="\n".join(lines) or "Keine Daten.",
            )
        )

    @app_commands.command(name="report", description="Kompletten Perso-Bericht mit Übersicht und Diagramm erstellen.")
    async def report(self, interaction: discord.Interaction, zeitraum: str | None = None):
        await interaction.response.defer(ephemeral=True)
        rows = await self.service.totals(interaction.guild_id, period_like=zeitraum)
        if not rows:
            await interaction.followup.send("Keine Daten.", ephemeral=True)
            return
        overview = render_personnel_png("MD Personalabteilung • Statistik", rows)
        chart = render_personnel_chart("MD Personalabteilung • Aktivitätsdiagramm", rows)
        await interaction.followup.send(
            embed=EmbedFactory.info(
                title="Perso • Bericht",
                description=f"**{len(rows)} Personen** · Gesamt **{sum(int(r['activity']) for r in rows)}**",
            ),
            files=[
                discord.File(BytesIO(overview), filename="perso-statistik.png"),
                discord.File(BytesIO(chart), filename="perso-diagramm.png"),
            ],
            ephemeral=True,
        )

    @app_commands.command(name="export", description="Perso-Daten als Übersicht, Diagramm oder CSV exportieren.")
    @app_commands.choices(
        format=[
            app_commands.Choice(name="Übersicht PNG", value="png"),
            app_commands.Choice(name="Diagramm PNG", value="chart"),
            app_commands.Choice(name="CSV", value="csv"),
        ]
    )
    async def export(
        self,
        interaction: discord.Interaction,
        format: app_commands.Choice[str],
        zeitraum: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        rows = await self.service.totals(interaction.guild_id, period_like=zeitraum)
        if format.value == "csv":
            data = self.service.csv_bytes(rows)
            name = "perso-statistik.csv"
        elif format.value == "chart":
            data = render_personnel_chart("MD Personalabteilung • Aktivitätsdiagramm", rows)
            name = "perso-diagramm.png"
        else:
            data = render_personnel_png("MD Personalabteilung • Statistik", rows)
            name = "perso-statistik.png"
        await interaction.followup.send(
            file=discord.File(BytesIO(data), filename=name),
            ephemeral=True,
        )

    @app_commands.command(name="import", description="CSV mit Name;Einweisungen;BWG importieren.")
    @app_commands.default_permissions(manage_messages=True)
    async def import_csv(
        self,
        interaction: discord.Interaction,
        datei: discord.Attachment,
        zeitraum: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if datei.size > 1_000_000:
            await interaction.followup.send("CSV ist zu groß.", ephemeral=True)
            return
        raw = (await datei.read()).decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(raw.splitlines(), delimiter=";")
        count = 0
        for item in reader:
            name = (item.get("Name") or item.get("name") or "").strip()
            if not name:
                continue
            row = await self.service.add(interaction.guild_id, name, interaction.user.id)
            try:
                e = int(item.get("Einweisungen") or item.get("einweisungen") or 0)
                b = int(item.get("BWG") or item.get("bwg") or 0)
            except ValueError:
                continue
            if e or b:
                await self.service.record(
                    interaction.guild_id,
                    int(row["id"]),
                    interaction.user.id,
                    inductions=max(0, e),
                    bwg=max(0, b),
                    period_key=zeitraum,
                )
            count += 1
        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="CSV importiert",
                description=f"**{count}** Zeilen verarbeitet.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="stats", description="Übersicht der Perso-Funktionen.")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Perso 2.3",
                description=(
                    "Profile, Einträge, Wochenreset, Zeitraum-Autocomplete, Bulk-Import, Notizen, "
                    "Qualifikationen, Ziele, Ranghistorie, Leaderboard, Trends, Reports, PNG/CSV und "
                    "Archivierung sind aktiv."
                ),
            ),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Personnel(bot))
