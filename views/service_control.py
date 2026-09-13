from __future__ import annotations

import logging

import discord

from helpers.embeds import EmbedFactory
from services.health_checks import HEALTH_SERVICES, HealthResult, check_systemd_service
from services.process_runner import run_process

logger = logging.getLogger(__name__)

HELPER = "/usr/local/sbin/homepi-systemctl"
SERVICE_LABELS: dict[str, str] = {
    "bot": "Discord Bot",
    "dashboard": "Dashboard",
    "radar": "Flight Radar",
    "mesh": "Meshtastic",
    "display": "Display 1",
    "display2": "Display 2",
    "intelligence": "Intelligence",
    "pihole": "Pi-hole",
}


def _status_embed(result: HealthResult) -> discord.Embed:
    if result.status == "online":
        embed = EmbedFactory.success(title=f"{SERVICE_LABELS.get(result.name, result.name)} · Online")
    elif result.status == "degraded":
        embed = EmbedFactory.warning(title=f"{SERVICE_LABELS.get(result.name, result.name)} · Eingeschränkt")
    else:
        embed = EmbedFactory.error(title=f"{SERVICE_LABELS.get(result.name, result.name)} · Offline")
    embed.description = result.message
    embed.add_field(name="Latenz", value=f"{result.latency_ms:.1f} ms", inline=True)
    embed.add_field(name="Unit", value=f"`{HEALTH_SERVICES[result.name]}.service`", inline=True)
    return embed


class ServiceSelect(discord.ui.Select):
    def __init__(self, parent: "ServiceControlView") -> None:
        options = [
            discord.SelectOption(label=SERVICE_LABELS.get(key, key), value=key, description=unit)
            for key, unit in HEALTH_SERVICES.items()
        ]
        super().__init__(placeholder="Service auswählen …", min_values=1, max_values=1, options=options[:25])
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected = self.values[0]
        await self.parent_view.show_selected(interaction)


class RestartConfirmView(discord.ui.View):
    def __init__(self, owner_id: int, service_key: str) -> None:
        super().__init__(timeout=45)
        self.owner_id = owner_id
        self.service_key = service_key

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Diese Bestätigung gehört zu einer anderen Session.", ephemeral=True)
            return False
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Für Neustarts brauchst du **Manage Server**.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Restart bestätigen", emoji="♻️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        unit = HEALTH_SERVICES[self.service_key]
        await interaction.response.defer(ephemeral=True)
        try:
            result = await run_process(["sudo", "-n", HELPER, "restart", unit], timeout=30)
            health = await check_systemd_service(self.service_key, unit)
            if result.ok and health.ok:
                embed = EmbedFactory.success(
                    title=f"{SERVICE_LABELS.get(self.service_key, self.service_key)} neu gestartet",
                    description=health.message,
                )
            else:
                detail = (result.stderr or result.stdout or health.message).strip()[-700:]
                embed = EmbedFactory.error(
                    title="Service-Neustart fehlgeschlagen",
                    description=f"`{detail or 'Unbekannter Fehler'}`",
                )
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception as exc:
            logger.exception("Service restart failed for %s", unit)
            await interaction.edit_original_response(
                embed=EmbedFactory.error(
                    title="Service-Neustart fehlgeschlagen",
                    description=f"`{type(exc).__name__}` · technische Details wurden geloggt.",
                ),
                view=None,
            )

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Neustart abgebrochen.", embed=None, view=None)


class ServiceControlView(discord.ui.View):
    def __init__(self, owner_id: int, selected: str = "bot") -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.selected = selected if selected in HEALTH_SERVICES else "bot"
        self.add_item(ServiceSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Dieses Service-Control gehört zu einer anderen Session.", ephemeral=True)
        return False

    async def show_selected(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        result = await check_systemd_service(self.selected, HEALTH_SERVICES[self.selected])
        await interaction.edit_original_response(embed=_status_embed(result), view=self)

    @discord.ui.button(label="Status aktualisieren", emoji="🔄", style=discord.ButtonStyle.primary, row=2)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.show_selected(interaction)

    @discord.ui.button(label="Restart", emoji="♻️", style=discord.ButtonStyle.danger, row=2)
    async def restart(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Für Neustarts brauchst du **Manage Server**.", ephemeral=True)
            return
        label = SERVICE_LABELS.get(self.selected, self.selected)
        await interaction.response.send_message(
            embed=EmbedFactory.warning(
                title=f"{label} neu starten?",
                description="Der Service wird kurz unterbrochen. Diese Aktion läuft über die feste HomePi-Systemd-Allowlist.",
            ),
            view=RestartConfirmView(self.owner_id, self.selected),
            ephemeral=True,
        )


async def open_service_control(interaction: discord.Interaction) -> None:
    view = ServiceControlView(interaction.user.id)
    result = await check_systemd_service("bot", HEALTH_SERVICES["bot"])
    await interaction.response.send_message(embed=_status_embed(result), view=view, ephemeral=True)
