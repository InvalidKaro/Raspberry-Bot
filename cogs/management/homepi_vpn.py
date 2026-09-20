"""Owner-only, guild-scoped read-only WireGuard status commands."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from services.homepi_vpn import status

HOMEPI_GUILD = discord.Object(id=1162733312226361454)


@app_commands.guilds(HOMEPI_GUILD)
class HomePiVpn(commands.GroupCog, group_name="vpn", group_description="HomePi VPN status"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner_ids = {int(value) for value in self.bot.settings.owner_ids}
        if interaction.guild_id != HOMEPI_GUILD.id or interaction.user.id not in owner_ids:
            await interaction.response.send_message("Nicht autorisiert.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="status", description="WireGuard-Status anzeigen")
    async def vpn_status(self, interaction: discord.Interaction) -> None:
        data = await status()
        await interaction.response.send_message(
            "WireGuard: " + (", ".join(data["active_interfaces"]) or "nicht aktiv")
            + "\nProfile: " + (", ".join(data["profiles"]) or "keine")
            + "\n" + data["message"],
            ephemeral=True,
        )

    @app_commands.command(name="locations", description="Vorhandene VPN-Profile anzeigen")
    async def vpn_locations(self, interaction: discord.Interaction) -> None:
        data = await status()
        await interaction.response.send_message(
            "Lokale Profile: " + (", ".join(data["profiles"]) or "keine vorhanden")
            + "\nEin Profilname bestätigt keinen tatsächlichen VPN-Standort.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HomePiVpn(bot))
