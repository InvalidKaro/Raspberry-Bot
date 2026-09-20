"""Owner-only Discord controls for the dashboard-owned, application-scoped VPN proxy."""
from __future__ import annotations

from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from services.homepi_vpn_proxy import VPNError, request_controller

HOMEPI_GUILD = discord.Object(id=1162733312226361454)


@app_commands.guilds(HOMEPI_GUILD)
class HomePiVpn(commands.GroupCog, group_name="vpn", group_description="HomePi VPN-App-Proxy verwalten"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != HOMEPI_GUILD.id or interaction.user.id not in self.bot.settings.owner_ids:
            await interaction.response.send_message("Nicht autorisiert.", ephemeral=True)
            return False
        return True

    async def _operation(self, interaction: discord.Interaction, action: str, profile: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await request_controller(Path.cwd(), action, profile)
            if not data.get("ok"):
                raise VPNError(str(data.get("message", "VPN-Controller unavailable.")))
            output = (
                f"Profil: **{data.get('active_profile') or 'kein aktives Profil'}**\n"
                f"Proxy: `{data.get('proxy') or 'aus'}`\n"
                f"Status: {'lokal erreichbar' if data.get('proxy_ready') else 'inaktiv'}\n"
                f"Profile: {', '.join(data.get('profiles', [])) or 'keine'}\n"
                "Hinweis: Nur explizit konfigurierte Apps nutzen den Proxy; ein Ziel-Land ist nicht verifiziert."
            )
        except (OSError, VPNError, TimeoutError, ValueError) as exc:
            output = f"VPN-Controller nicht verfügbar: {exc}"
        await interaction.followup.send(output[:1900], ephemeral=True)

    @app_commands.command(name="status", description="VPN-App-Proxy-Status anzeigen")
    async def vpn_status(self, interaction: discord.Interaction) -> None:
        await self._operation(interaction, "status")

    @app_commands.command(name="locations", description="Lokal hinterlegte VPN-Profile anzeigen")
    async def vpn_locations(self, interaction: discord.Interaction) -> None:
        await self._operation(interaction, "status")

    @app_commands.command(name="connect", description="WireGuard-App-Proxy mit einem vorhandenen Profil starten")
    @app_commands.describe(profile="Dateiname ohne .conf aus dem privaten Profilverzeichnis")
    async def vpn_connect(self, interaction: discord.Interaction, profile: str) -> None:
        await self._operation(interaction, "connect", profile)

    @app_commands.command(name="disconnect", description="Den VPN-App-Proxy stoppen")
    async def vpn_disconnect(self, interaction: discord.Interaction) -> None:
        await self._operation(interaction, "disconnect")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HomePiVpn(bot))
