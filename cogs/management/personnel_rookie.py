"""Rookie subteam and read-only live database commands."""
from __future__ import annotations

import asyncio
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

from services.personnel_rookie import RookieService
from services.personnel_v2 import PersonnelService
from services.personnel_export import render_personnel_png


class RookieManagement(commands.GroupCog, group_name="rookie", group_description="Perso • Rookie Team"):
    def __init__(self, bot):
        self.bot = bot
        self.rookie = RookieService(bot.database)
        self.personnel = PersonnelService(bot)

    async def _autocomplete(self, interaction: discord.Interaction, current: str):
        rows = await self.personnel.list_members(interaction.guild_id or 0)
        return [
            app_commands.Choice(name=str(row["display_name"])[:100], value=str(row["display_name"])[:100])
            for row in rows if current.casefold() in str(row["display_name"]).casefold()
        ][:25]

    @app_commands.command(name="add", description="Perso-Mitglied dem Rookie Team zuordnen")
    @app_commands.autocomplete(person=_autocomplete)
    @app_commands.default_permissions(manage_messages=True)
    async def add(self, interaction: discord.Interaction, person: str):
        row = await self.personnel.get_by_name(interaction.guild_id or 0, person)
        if row is None:
            await interaction.response.send_message("Person nicht gefunden.", ephemeral=True)
            return
        changed = await self.rookie.join(int(row["id"]), interaction.user.id)
        if changed and hasattr(self.bot, "audit"):
            await self.bot.audit.record(
                "perso.rookie.add", guild_id=interaction.guild_id,
                actor_id=interaction.user.id, target_type="personnel",
                target_id=int(row["id"]),
            )
        await interaction.response.send_message(
            "Rookie hinzugefügt." if changed else "Bereits im Rookie Team.", ephemeral=True
        )

    @app_commands.command(name="remove", description="Rookie-Zuordnung beenden; Statistik behalten")
    @app_commands.autocomplete(person=_autocomplete)
    @app_commands.default_permissions(manage_messages=True)
    async def remove(self, interaction: discord.Interaction, person: str):
        row = await self.personnel.get_by_name(interaction.guild_id or 0, person)
        if row is None:
            await interaction.response.send_message("Person nicht gefunden.", ephemeral=True)
            return
        changed = await self.rookie.leave(int(row["id"]))
        if changed and hasattr(self.bot, "audit"):
            await self.bot.audit.record(
                "perso.rookie.remove", guild_id=interaction.guild_id,
                actor_id=interaction.user.id, target_type="personnel",
                target_id=int(row["id"]),
            )
        await interaction.response.send_message(
            "Rookie-Zuordnung beendet." if changed else "Keine aktive Zuordnung.", ephemeral=True
        )

    @app_commands.command(name="members", description="Aktuelle Rookie-Team-Mitglieder")
    @app_commands.default_permissions(manage_messages=True)
    async def members(self, interaction: discord.Interaction):
        rows = await self.rookie.members()
        lines = [f"{r['display_name']} (seit {r['joined_on']})" for r in rows]
        await interaction.response.send_message("\n".join(lines)[:1900] or "Keine Rookie-Mitglieder.", ephemeral=True)

    @app_commands.command(name="stats", description="Rookie-BWG und Einweisungen als PNG")
    @app_commands.choices(period=[
        app_commands.Choice(name="Woche", value="week"),
        app_commands.Choice(name="Monat", value="month"),
        app_commands.Choice(name="Gesamt", value="all"),
    ])
    @app_commands.default_permissions(manage_messages=True)
    async def stats(self, interaction: discord.Interaction, period: app_commands.Choice[str] | None = None):
        await interaction.response.defer(ephemeral=True)
        rows = await self.rookie.statistics(period=period.value if period else "week", rookie_only=True)
        bwg = sum(int(r["bwg"]) for r in rows)
        einweisungen = sum(int(r["inductions"]) for r in rows)
        png = await asyncio.to_thread(render_personnel_png, "Personalabteilung • Rookie Team", rows)
        await interaction.followup.send(
            content=f"Rookie Team: **{bwg} BWG**, **{einweisungen} Einweisungen**. Diese zählen auch zur Perso-Gesamtstatistik.",
            file=discord.File(BytesIO(png), filename="rookie-statistik.png"),
            ephemeral=True,
        )

    @app_commands.command(name="db", description="Live-DB: Personaleinträge mit Filter und Seiten")
    @app_commands.default_permissions(manage_messages=True)
    async def db(
        self, interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 1000] = 1,
        rookie_only: bool = False,
    ):
        rows = await self.rookie.records(offset=(page - 1) * 15, limit=15, rookie_only=rookie_only)
        lines = [
            f"#{r['id']} · {r['record_date']} · {r['display_name']} · BWG {r['bwg']} · E {r['inductions']}"
            for r in rows
        ]
        await interaction.response.send_message(
            f"Perso Live-DB · Seite {page}\n" + ("\n".join(lines)[:1800] or "Keine Einträge."),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(RookieManagement(bot))
