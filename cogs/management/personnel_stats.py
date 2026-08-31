from __future__ import annotations

import json
import math
import re
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.personnel_charts import render_personnel_chart, summarize


_NAME_RE = re.compile(r"[^a-z0-9_-]+")


def _split_labels(raw: str) -> list[str]:
    separator = ";" if ";" in raw else ","
    labels = [part.strip() for part in raw.split(separator)]
    labels = [item for item in labels if item]
    if not labels:
        raise ValueError("No X-axis labels were found.")
    if len(labels) > 24:
        raise ValueError("A chart may contain at most 24 X-axis values.")
    return labels


def _parse_values(raw: str) -> list[float]:
    # Semicolons are preferred because they allow German decimal commas:
    # 1,5;2,0;3,25
    separator = ";" if ";" in raw else ","
    values: list[float] = []
    for part in raw.split(separator):
        item = part.strip()
        if not item:
            continue
        if separator == ";":
            item = item.replace(",", ".")
        try:
            value = float(item)
        except ValueError as exc:
            raise ValueError(f"`{part.strip()}` is not a valid number.") from exc
        if not math.isfinite(value):
            raise ValueError("Values must be normal finite numbers.")
        values.append(value)
    if not values:
        raise ValueError("No Y-axis values were found.")
    if len(values) > 24:
        raise ValueError("A chart may contain at most 24 Y-axis values.")
    return values


def _clean_saved_name(value: str) -> str:
    clean = _NAME_RE.sub("-", value.strip().lower().replace(" ", "-")).strip("-_")
    if not clean:
        raise ValueError("The save name must contain letters or numbers.")
    return clean[:50]


def _fmt(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=1e-9):
        return f"{int(round(value)):,}".replace(",", ".")
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").rstrip("0").rstrip(",")


def _stats_text(values: Sequence[float]) -> str:
    data = summarize(values)
    change = "—"
    if data.change is not None:
        sign = "+" if data.change > 0 else ""
        change = f"{sign}{_fmt(data.change)}"
        if data.change_percent is not None:
            pct_sign = "+" if data.change_percent > 0 else ""
            change += f" ({pct_sign}{data.change_percent:.1f}%)"
    return (
        f"Summe: **{_fmt(data.total)}**\n"
        f"Ø: **{_fmt(data.average)}** • Median: **{_fmt(data.median)}**\n"
        f"Min: **{_fmt(data.minimum)}** • Max: **{_fmt(data.maximum)}**\n"
        f"Erster → letzter Wert: **{change}**"
    )


def _validate_weekly_counts(values: Sequence[float], *, field_name: str) -> None:
    for value in values:
        if value < 0:
            raise ValueError(f"{field_name} dürfen nicht negativ sein.")
        if not math.isclose(value, round(value), abs_tol=1e-9):
            raise ValueError(f"{field_name} müssen ganze Zahlen sein.")


def _weekly_kpi_text(labels: Sequence[str], applications: Sequence[float], inductions: Sequence[float]) -> str:
    total_applications = float(sum(applications))
    total_inductions = float(sum(inductions))
    weeks = max(len(labels), 1)
    rate = None if total_applications <= 0 else total_inductions / total_applications * 100.0

    best_application_index = max(range(len(applications)), key=lambda index: applications[index])
    best_induction_index = max(range(len(inductions)), key=lambda index: inductions[index])

    rate_text = "—" if rate is None else f"{rate:.1f}%"
    return (
        f"Bewerbungen gesamt: **{_fmt(total_applications)}**\n"
        f"Einweisungen gesamt: **{_fmt(total_inductions)}**\n"
        f"Ø Bewerbungen/Woche: **{_fmt(total_applications / weeks)}**\n"
        f"Ø Einweisungen/Woche: **{_fmt(total_inductions / weeks)}**\n"
        f"Einweisungen ÷ Bewerbungen: **{rate_text}**\n"
        f"Meiste Bewerbungen: **{labels[best_application_index]}** ({_fmt(applications[best_application_index])})\n"
        f"Meiste Einweisungen: **{labels[best_induction_index]}** ({_fmt(inductions[best_induction_index])})"
    )


class PersonnelStats(commands.GroupCog, group_name="perso", group_description="MD Personalabteilung statistics and graphs"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _dataset_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        pattern = f"%{current.strip().lower()}%"
        rows = await self.bot.database.fetchall(
            "SELECT name, title FROM personnel_datasets WHERE guild_id = ? AND lower(name) LIKE ? ORDER BY updated_at DESC LIMIT 20",
            (interaction.guild_id, pattern),
        )
        return [
            app_commands.Choice(name=f"{row['name']} — {str(row['title'])[:55]}", value=str(row["name"]))
            for row in rows[:20]
        ]

    async def _render_and_send(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        labels: list[str],
        values: list[float],
        x_label: str,
        y_label: str,
        series_name: str,
        chart_type: str,
        second_values: list[float] | None,
        second_series_name: str | None,
        private: bool,
        saved_name: str | None = None,
        extra_fields: Sequence[tuple[str, str, bool]] | None = None,
    ) -> None:
        if len(labels) != len(values):
            raise ValueError(f"X-axis has {len(labels)} entries, but Y-axis has {len(values)} values.")
        if second_values is not None and len(second_values) != len(values):
            raise ValueError(
                f"The comparison series has {len(second_values)} values; expected {len(values)}."
            )

        await interaction.response.defer(ephemeral=private, thinking=True)
        image = await render_personnel_chart(
            labels=labels,
            values=values,
            title=title,
            x_label=x_label,
            y_label=y_label,
            series_name=series_name,
            chart_type=chart_type,
            second_values=second_values,
            second_series_name=second_series_name,
            author_label=interaction.user.display_name,
        )
        filename = "md-perso-statistik.png"
        file = discord.File(image, filename=filename)
        embed = EmbedFactory.info(
            title=f"MD Personalabteilung • {title[:80]}",
            description=(
                f"**Diagramm:** {'Balken' if chart_type == 'bar' else 'Linie'}\n"
                f"**X-Achse:** {x_label} • **Y-Achse:** {y_label}\n"
                f"**Datenpunkte:** {len(labels)}"
                + (f"\n**Gespeichert als:** `{saved_name}`" if saved_name else "")
            ),
        )
        embed.add_field(name=series_name[:256], value=_stats_text(values), inline=True)
        if second_values is not None:
            embed.add_field(
                name=(second_series_name or "Vergleich")[:256],
                value=_stats_text(second_values),
                inline=True,
            )
        if extra_fields:
            for field_name, field_value, inline in extra_fields:
                embed.add_field(name=field_name[:256], value=field_value[:1024], inline=inline)
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"MD Personalabteilung • erstellt von {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, file=file, ephemeral=private)

    async def _save_dataset(
        self,
        interaction: discord.Interaction,
        *,
        saved_name: str,
        title: str,
        chart_type: str,
        x_label: str,
        y_label: str,
        labels: Sequence[str],
        values: Sequence[float],
        series_name: str,
        second_values: Sequence[float] | None = None,
        second_series_name: str | None = None,
    ) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute(
            "INSERT INTO personnel_datasets "
            "(guild_id, name, title, chart_type, x_label, y_label, labels_json, values_json, series_name, second_values_json, second_series_name, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, name) DO UPDATE SET "
            "title=excluded.title, chart_type=excluded.chart_type, x_label=excluded.x_label, y_label=excluded.y_label, "
            "labels_json=excluded.labels_json, values_json=excluded.values_json, series_name=excluded.series_name, "
            "second_values_json=excluded.second_values_json, second_series_name=excluded.second_series_name, "
            "created_by=excluded.created_by, updated_at=CURRENT_TIMESTAMP",
            (
                interaction.guild_id,
                saved_name,
                title[:120],
                chart_type,
                x_label[:50],
                y_label[:50],
                json.dumps(list(labels), ensure_ascii=False),
                json.dumps(list(values)),
                series_name[:50],
                json.dumps(list(second_values)) if second_values is not None else None,
                second_series_name[:50] if second_series_name else None,
                interaction.user.id,
            ),
        )

    @app_commands.command(name="weekly", description="Create the MD weekly personnel chart for applications and inductions.")
    @app_commands.describe(
        wochen="Kalenderwochen mit ; trennen, z. B. KW35;KW36;KW37",
        bewerbungen="Bewerbungen je Woche mit ; trennen, z. B. 12;17;14",
        einweisungen="Einweisungen je Woche mit ; trennen, z. B. 5;8;6",
        titel="Optionaler Titel der Wochenstatistik",
        diagramm="Balken- oder Liniendiagramm",
        speichern_als="Optionaler Name zum späteren Wiederverwenden",
        privat="Statistik nur für dich anzeigen",
    )
    @app_commands.choices(
        diagramm=[
            app_commands.Choice(name="Balkendiagramm", value="bar"),
            app_commands.Choice(name="Liniendiagramm", value="line"),
        ]
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def weekly(
        self,
        interaction: discord.Interaction,
        wochen: str,
        bewerbungen: str,
        einweisungen: str,
        titel: str = "Wochenstatistik",
        diagramm: app_commands.Choice[str] | None = None,
        speichern_als: str | None = None,
        privat: bool = False,
    ) -> None:
        if interaction.guild_id is None:
            return

        try:
            labels = _split_labels(wochen)
            applications = _parse_values(bewerbungen)
            inductions = _parse_values(einweisungen)
            _validate_weekly_counts(applications, field_name="Bewerbungen")
            _validate_weekly_counts(inductions, field_name="Einweisungen")
            if len(labels) != len(applications):
                raise ValueError(
                    f"Du hast {len(labels)} Wochen angegeben, aber {len(applications)} Bewerbungswerte."
                )
            if len(labels) != len(inductions):
                raise ValueError(
                    f"Du hast {len(labels)} Wochen angegeben, aber {len(inductions)} Einweisungswerte."
                )
            chart = diagramm.value if diagramm else "bar"
            saved_name = _clean_saved_name(speichern_als) if speichern_als else None
        except ValueError as exc:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Ungültige Wochenstatistik", description=str(exc)),
                ephemeral=True,
            )
            return

        title = titel.strip()[:120] or "Wochenstatistik"
        if saved_name:
            await self._save_dataset(
                interaction,
                saved_name=saved_name,
                title=title,
                chart_type=chart,
                x_label="Kalenderwoche",
                y_label="Anzahl",
                labels=labels,
                values=applications,
                series_name="Bewerbungen",
                second_values=inductions,
                second_series_name="Einweisungen",
            )

        await self._render_and_send(
            interaction,
            title=title,
            labels=labels,
            values=applications,
            x_label="Kalenderwoche",
            y_label="Anzahl",
            series_name="Bewerbungen",
            chart_type=chart,
            second_values=inductions,
            second_series_name="Einweisungen",
            private=privat,
            saved_name=saved_name,
            extra_fields=[(
                "Wochen-KPIs",
                _weekly_kpi_text(labels, applications, inductions),
                False,
            )],
        )

    @app_commands.command(name="graph", description="Create an MD personnel graph from values you enter yourself.")
    @app_commands.describe(
        title="Heading of the statistic, e.g. Bewerbungen pro Woche",
        x_values="X-axis labels separated with ; e.g. KW31;KW32;KW33",
        y_values="Y values separated with ; e.g. 12;18;21",
        x_label="Name of the X axis",
        y_label="Name of the Y axis",
        series_name="Name of the first data series",
        chart_type="Bar or line graph",
        second_values="Optional comparison values, e.g. 8;13;17",
        second_series_name="Name of the optional comparison series",
        save_as="Optional name for saving/reusing this dataset",
        private="Only show the result to you",
    )
    @app_commands.choices(
        chart_type=[
            app_commands.Choice(name="Balkendiagramm", value="bar"),
            app_commands.Choice(name="Liniendiagramm", value="line"),
        ]
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def graph(
        self,
        interaction: discord.Interaction,
        title: str,
        x_values: str,
        y_values: str,
        x_label: str = "Zeitraum",
        y_label: str = "Anzahl",
        series_name: str = "Wert",
        chart_type: app_commands.Choice[str] | None = None,
        second_values: str | None = None,
        second_series_name: str | None = None,
        save_as: str | None = None,
        private: bool = False,
    ) -> None:
        if interaction.guild_id is None:
            return
        try:
            labels = _split_labels(x_values)
            values = _parse_values(y_values)
            compare = _parse_values(second_values) if second_values else None
            chart = chart_type.value if chart_type else "bar"
            saved_name = _clean_saved_name(save_as) if save_as else None
            if len(labels) != len(values):
                raise ValueError(f"X-axis has {len(labels)} labels, but Y-axis has {len(values)} values.")
            if compare is not None and len(compare) != len(values):
                raise ValueError(f"The comparison series needs exactly {len(values)} values.")
        except ValueError as exc:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Invalid personnel statistics data", description=str(exc)),
                ephemeral=True,
            )
            return

        if saved_name:
            await self._save_dataset(
                interaction,
                saved_name=saved_name,
                title=title,
                chart_type=chart,
                x_label=x_label,
                y_label=y_label,
                labels=labels,
                values=values,
                series_name=series_name,
                second_values=compare,
                second_series_name=second_series_name,
            )

        try:
            await self._render_and_send(
                interaction,
                title=title[:120],
                labels=labels,
                values=values,
                x_label=x_label[:50],
                y_label=y_label[:50],
                series_name=series_name[:50],
                chart_type=chart,
                second_values=compare,
                second_series_name=second_series_name[:50] if second_series_name else None,
                private=private,
                saved_name=saved_name,
            )
        except ValueError as exc:
            if interaction.response.is_done():
                await interaction.followup.send(embed=EmbedFactory.error(title="Graph error", description=str(exc)), ephemeral=True)
            else:
                await interaction.response.send_message(embed=EmbedFactory.error(title="Graph error", description=str(exc)), ephemeral=True)

    @app_commands.command(name="list", description="List saved MD personnel datasets for this server.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def list_saved(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        rows = await self.bot.database.fetchall(
            "SELECT name, title, chart_type, updated_at FROM personnel_datasets WHERE guild_id = ? ORDER BY updated_at DESC LIMIT 25",
            (interaction.guild_id,),
        )
        if not rows:
            description = "No saved personnel datasets yet. Add `save_as` when using `/perso graph`."
        else:
            description = "\n".join(
                f"• `{row['name']}` — **{row['title']}** • {row['chart_type']} • {row['updated_at']}"
                for row in rows
            )
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Saved MD personnel statistics", description=description[:3900]),
            ephemeral=True,
        )

    @app_commands.command(name="render", description="Render a previously saved MD personnel dataset again.")
    @app_commands.describe(name="Saved dataset name from /perso list", private="Only show the result to you")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def render_saved(self, interaction: discord.Interaction, name: str, private: bool = False) -> None:
        if interaction.guild_id is None:
            return
        try:
            clean = _clean_saved_name(name)
        except ValueError as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Invalid name", description=str(exc)), ephemeral=True)
            return
        row = await self.bot.database.fetchone(
            "SELECT * FROM personnel_datasets WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, clean),
        )
        if row is None:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Dataset not found", description=f"No saved statistic named `{clean}` exists."),
                ephemeral=True,
            )
            return
        data = dict(row)
        try:
            labels = [str(value) for value in json.loads(str(data["labels_json"]))]
            values = [float(value) for value in json.loads(str(data["values_json"]))]
            second = (
                [float(value) for value in json.loads(str(data["second_values_json"]))]
                if data.get("second_values_json")
                else None
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Saved dataset is invalid", description="The stored data could not be decoded."),
                ephemeral=True,
            )
            return
        await self._render_and_send(
            interaction,
            title=str(data["title"]),
            labels=labels,
            values=values,
            x_label=str(data["x_label"]),
            y_label=str(data["y_label"]),
            series_name=str(data["series_name"]),
            chart_type=str(data["chart_type"]),
            second_values=second,
            second_series_name=str(data["second_series_name"]) if data.get("second_series_name") else None,
            private=private,
            saved_name=clean,
        )

    @render_saved.autocomplete("name")
    async def render_saved_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self._dataset_autocomplete(interaction, current)

    @app_commands.command(name="delete", description="Delete one saved MD personnel dataset.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def delete_saved(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild_id is None:
            return
        try:
            clean = _clean_saved_name(name)
        except ValueError as exc:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Invalid name", description=str(exc)), ephemeral=True)
            return
        row = await self.bot.database.fetchone(
            "SELECT id FROM personnel_datasets WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, clean),
        )
        if row is None:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Dataset not found", description=f"`{clean}` does not exist."),
                ephemeral=True,
            )
            return
        await self.bot.database.execute(
            "DELETE FROM personnel_datasets WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, clean),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Personnel dataset deleted", description=f"Deleted `{clean}`."),
            ephemeral=True,
        )

    @delete_saved.autocomplete("name")
    async def delete_saved_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self._dataset_autocomplete(interaction, current)

    @app_commands.command(name="help", description="Show examples for creating MD personnel charts.")
    @app_commands.guild_only()
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = EmbedFactory.info(
            title="MD Personalabteilung • Statistik Hilfe",
            description=(
                "Für die fertige MD-Wochenstatistik nutze `/perso weekly`: Wochen, Bewerbungen und Einweisungen eingeben, fertig.\n\n"
                "Für freie Diagramme nutze `/perso graph` und gib eigene X/Y-Daten ein. Trenne Einträge mit **Semikolons**. "
                "That also supports German decimal commas.\n\n"
                "**Example**\n"
                "Title: `Bewerbungen pro Woche`\n"
                "X values: `KW31;KW32;KW33;KW34`\n"
                "Y values: `12;18;15;23`\n"
                "X label: `Kalenderwoche`\n"
                "Y label: `Bewerbungen`\n"
                "Series: `Eingegangen`\n\n"
                "**Weekly example**\n"
                "Wochen: `KW35;KW36;KW37;KW38`\n"
                "Bewerbungen: `12;17;14;21`\n"
                "Einweisungen: `5;8;6;11`\n\n"
                "For a free comparison, add e.g. second values `8;13;11;19` and name it `Eingestellt`. "
                "Set `save_as` to reuse the statistic later with `/perso render`."
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PersonnelStats(bot))
