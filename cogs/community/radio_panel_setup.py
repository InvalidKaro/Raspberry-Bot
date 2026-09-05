from __future__ import annotations

import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands


RADIO_PANEL_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS radio_panel_config(
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _clean_channel_name(value: str) -> str:
    name = value.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9äöüß_-]", "", name)
    name = re.sub(r"-+", "-", name).strip("-_")
    return (name or "radio-panel")[:100]


class RadioPanelSetup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self.bot.database.connection.executescript(RADIO_PANEL_CONFIG_SCHEMA)
        await self.bot.database.connection.commit()

    @app_commands.command(
        name="radiopanelcreate",
        description="Erstellt und konfiguriert einen dedizierten Kanal für das Live-Radio-Panel.",
    )
    @app_commands.describe(
        name="Name des neuen Textkanals",
        kategorie="Optional: Kategorie, in der der Kanal erstellt werden soll",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def radiopanelcreate(
        self,
        interaction: discord.Interaction,
        name: str = "radio-panel",
        kategorie: discord.CategoryChannel | None = None,
    ) -> None:
        guild = interaction.guild
        guild_id = interaction.guild_id
        if guild is None or guild_id is None:
            return

        member = guild.me
        if member is None and self.bot.user is not None:
            member = guild.get_member(self.bot.user.id)
        if member is None:
            await interaction.response.send_message(
                "Bot-Mitglied konnte nicht aufgelöst werden.",
                ephemeral=True,
            )
            return

        bot_permissions = guild.me.guild_permissions if guild.me is not None else member.guild_permissions
        if not bot_permissions.manage_channels:
            await interaction.response.send_message(
                "Mir fehlt die Server-Berechtigung **Kanäle verwalten**.",
                ephemeral=True,
            )
            return

        existing = await self.bot.database.fetchone(
            "SELECT channel_id,message_id FROM radio_panel_config WHERE guild_id=?",
            (guild_id,),
        )
        if existing is not None:
            existing_channel = guild.get_channel(int(existing["channel_id"]))
            if isinstance(existing_channel, discord.TextChannel):
                await interaction.response.send_message(
                    f"Es ist bereits {existing_channel.mention} als Radio-Panel-Kanal konfiguriert. "
                    "Nutze **/media radiopanelchannel**, wenn du stattdessen einen anderen vorhandenen Kanal verwenden willst.",
                    ephemeral=True,
                )
                return

        channel_name = _clean_channel_name(name)
        reason = f"Radio-Panel-Kanal erstellt von {interaction.user} ({interaction.user.id})"

        await interaction.response.defer(ephemeral=True)
        try:
            channel = await guild.create_text_channel(
                channel_name,
                category=kategorie,
                topic="Automatisches HomePi Live-Radio-Panel. Das Panel erscheint nur während einer aktiven Radio-Session.",
                reason=reason,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Ich darf auf diesem Server keinen Textkanal erstellen.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Der Radio-Panel-Kanal konnte nicht erstellt werden: `{exc}`",
                ephemeral=True,
            )
            return

        permissions = channel.permissions_for(member)
        missing: list[str] = []
        if not permissions.view_channel:
            missing.append("Kanal ansehen")
        if not permissions.send_messages:
            missing.append("Nachrichten senden")
        if not permissions.embed_links:
            missing.append("Links einbetten")
        if not permissions.attach_files:
            missing.append("Dateien anhängen")
        if not permissions.read_message_history:
            missing.append("Nachrichtenverlauf lesen")

        if missing:
            try:
                await channel.delete(reason="Radio-Panel-Kanal wegen fehlender Bot-Berechtigungen zurückgerollt")
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                "Der Kanal wurde erstellt, aber wieder entfernt, weil mir dort Berechtigungen fehlen: "
                f"**{', '.join(missing)}**.",
                ephemeral=True,
            )
            return

        old_message_id = None
        if existing is not None and existing["message_id"] is not None:
            old_message_id = int(existing["message_id"])

        runtime = self.bot.get_cog("RadioMetadataRuntime")
        if runtime is not None and existing is not None:
            old_channel_id = int(existing["channel_id"])
            try:
                await runtime._delete_panel_message(guild, old_channel_id, old_message_id)
            except (AttributeError, discord.HTTPException):
                pass

        await self.bot.database.execute(
            """
            INSERT INTO radio_panel_config(guild_id,channel_id,message_id,updated_at)
            VALUES(?,?,NULL,CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id=excluded.channel_id,
                message_id=NULL,
                updated_at=CURRENT_TIMESTAMP
            """,
            (guild_id, channel.id),
        )

        await interaction.followup.send(
            f"📻 {channel.mention} wurde erstellt und als automatischer Radio-Panel-Kanal gespeichert. "
            "Beim Start eines Radiosenders erscheint dort das Panel; nach Stop oder Disconnect wird es wieder gelöscht.",
            ephemeral=True,
        )

        if runtime is not None:
            try:
                asyncio.create_task(runtime._reconcile_panel(guild))
            except AttributeError:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RadioPanelSetup(bot))
