from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.universal_devices import (
    ControlState,
    UniversalDevice,
    UniversalDeviceError,
    UniversalDeviceService,
)

logger = logging.getLogger(__name__)

SMART_HOME_GUILD_ID = 1162733312226361454
SMART_HOME_GUILD = discord.Object(id=SMART_HOME_GUILD_ID)

SCAN_MODES = [
    app_commands.Choice(name="Schnell", value="quick"),
    app_commands.Choice(name="Tiefenscan", value="deep"),
]

POWER_STATES = [
    app_commands.Choice(name="An", value="on"),
    app_commands.Choice(name="Aus", value="off"),
]


@app_commands.guilds(SMART_HOME_GUILD)
class UniversalDevices(
    commands.GroupCog,
    group_name="devices",
    group_description="Herstellerunabhängige lokale Gerätesuche und Steuerung",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        smart_home = bot.get_cog("SmartHome")
        govee_service = getattr(smart_home, "service", None) if smart_home is not None else None
        self.service = UniversalDeviceService(govee_service=govee_service)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != SMART_HOME_GUILD_ID:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Diese Gerätesteuerung ist auf diesem Server nicht verfügbar.",
                    ephemeral=True,
                )
            return False
        return True

    @app_commands.command(
        name="scan",
        description="Sucht lokale WLAN/LAN- und Bluetooth-Geräte und prüft Steuerbarkeit.",
    )
    @app_commands.describe(mode="Schnellscan oder zusätzlicher lokaler Subnetzscan")
    @app_commands.choices(mode=SCAN_MODES)
    async def scan(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        deep = mode is not None and mode.value == "deep"
        try:
            result = await self.service.scan(deep=deep, include_ble=True)
        except Exception as exc:
            logger.exception("Universal device scan failed")
            await interaction.followup.send(
                embed=EmbedFactory.error(
                    title="Gerätescan fehlgeschlagen",
                    description=(str(exc).strip() or type(exc).__name__)[:4000],
                ),
                ephemeral=True,
            )
            return

        controllable = sum(1 for device in result.devices if device.state == ControlState.CONTROLLABLE)
        auth_required = sum(1 for device in result.devices if device.state == ControlState.AUTH_REQUIRED)
        pairing_required = sum(1 for device in result.devices if device.state == ControlState.PAIRING_REQUIRED)
        detected_only = sum(1 for device in result.devices if device.state == ControlState.DETECTED_ONLY)

        embed = EmbedFactory.system(
            title="Universal Device Discovery",
            description=(
                f"**{len(result.devices)}** benannte/klassifizierte Geräte gefunden. "
                f"Davon **{controllable} lokal steuerbar**.\n"
                f"Scan: **{'Tiefenscan' if deep else 'Schnell'}** · "
                f"{result.scanned_hosts} LAN-Kandidaten geprüft."
            ),
        )
        embed.add_field(
            name="Steuerbarkeit",
            value=(
                f"Direkt steuerbar: **{controllable}**\n"
                f"Zugangsdaten nötig: **{auth_required}**\n"
                f"Pairing nötig: **{pairing_required}**\n"
                f"Nur erkannt: **{detected_only}**"
            ),
            inline=True,
        )
        if result.unnamed_ble_count:
            embed.add_field(
                name="Bluetooth",
                value=(
                    f"Zusätzlich **{result.unnamed_ble_count}** anonyme BLE-Sender gesehen. "
                    "Sie werden absichtlich nicht als steuerbare Geräte behandelt."
                ),
                inline=True,
            )

        if result.devices:
            lines = [self._device_line(device) for device in result.devices[:18]]
            embed.add_field(
                name="Gefundene Geräte",
                value="\n".join(lines)[:1024],
                inline=False,
            )
            if len(result.devices) > 18:
                embed.set_footer(
                    text=f"Weitere {len(result.devices) - 18} Geräte: /devices list"
                )
        else:
            embed.add_field(
                name="Ergebnis",
                value=(
                    "Keine klassifizierbaren Geräte gefunden. Ein Tiefenscan prüft zusätzlich "
                    "das lokale /24-Segment auf bekannte lokale APIs."
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="list",
        description="Zeigt alle zuletzt gefundenen Geräte und deren Steuerbarkeit.",
    )
    async def list_devices(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        devices = self.service.list_devices()
        if not devices:
            await interaction.followup.send(
                embed=EmbedFactory.warning(
                    title="Noch kein Gerätescan",
                    description="Führe zuerst `/devices scan` aus.",
                ),
                ephemeral=True,
            )
            return

        embed = EmbedFactory.system(
            title="Lokale Geräte",
            description=(
                f"**{len(devices)}** Geräte im aktuellen Registry-Cache · "
                f"**{len(self.service.controllable_devices())}** direkt steuerbar"
            ),
        )
        chunks = [devices[index:index + 10] for index in range(0, min(len(devices), 30), 10)]
        for index, chunk in enumerate(chunks, start=1):
            embed.add_field(
                name=f"Geräte {((index - 1) * 10) + 1}–{((index - 1) * 10) + len(chunk)}",
                value="\n".join(self._device_line(device) for device in chunk)[:1024],
                inline=False,
            )
        if len(devices) > 30:
            embed.set_footer(text=f"{len(devices) - 30} weitere Geräte nicht eingeblendet.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="inspect",
        description="Zeigt warum ein Gerät steuerbar, gesperrt oder nur erkannt ist.",
    )
    @app_commands.describe(device="Gerät aus dem letzten Scan")
    async def inspect(self, interaction: discord.Interaction, device: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            target = self.service.resolve(device)
        except LookupError as exc:
            await interaction.followup.send(
                embed=EmbedFactory.warning(title="Gerät nicht gefunden", description=str(exc)),
                ephemeral=True,
            )
            return

        state_labels = {
            ControlState.CONTROLLABLE: "Direkt lokal steuerbar",
            ControlState.AUTH_REQUIRED: "Zugangsdaten erforderlich",
            ControlState.PAIRING_REQUIRED: "Pairing/Controller erforderlich",
            ControlState.DETECTED_ONLY: "Erkannt, aktuell nicht schreibbar",
        }
        embed = EmbedFactory.system(
            title=target.display_name,
            description=target.reason or "Keine zusätzliche Diagnose verfügbar.",
        )
        embed.add_field(name="Status", value=f"**{state_labels[target.state]}**", inline=False)
        embed.add_field(name="Hersteller", value=target.vendor or "Unbekannt", inline=True)
        embed.add_field(name="Modell", value=target.model or "Unbekannt", inline=True)
        embed.add_field(name="Protokoll", value=target.protocol, inline=True)
        embed.add_field(name="Transport", value=target.transport, inline=True)
        embed.add_field(name="Adresse", value=f"`{target.address}`", inline=True)
        embed.add_field(
            name="Capabilities",
            value=", ".join(target.capabilities) if target.capabilities else "Keine freigegeben",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @inspect.autocomplete("device")
    async def inspect_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, controllable_only=False)

    @app_commands.command(name="power", description="Schaltet ein unterstütztes lokales Gerät an oder aus.")
    @app_commands.describe(device="Steuerbares Gerät", state="An oder Aus")
    @app_commands.choices(state=POWER_STATES)
    async def power(
        self,
        interaction: discord.Interaction,
        device: str,
        state: app_commands.Choice[str],
    ) -> None:
        await self._run_control(
            interaction,
            lambda: self.service.power(device, state.value == "on"),
            title=f"Gerät {state.name.lower()}",
        )

    @power.autocomplete("device")
    async def power_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, capability="power")

    @app_commands.command(name="brightness", description="Setzt die Helligkeit eines unterstützten Geräts.")
    @app_commands.describe(device="Steuerbares Gerät", value="1 bis 100 Prozent")
    async def brightness(
        self,
        interaction: discord.Interaction,
        device: str,
        value: app_commands.Range[int, 1, 100],
    ) -> None:
        await self._run_control(
            interaction,
            lambda: self.service.brightness(device, int(value)),
            title=f"Helligkeit: {int(value)} %",
        )

    @brightness.autocomplete("device")
    async def brightness_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, capability="brightness")

    @app_commands.command(name="color", description="Setzt eine RGB-Farbe auf einem unterstützten Gerät.")
    @app_commands.describe(device="Steuerbares RGB-Gerät")
    async def color(
        self,
        interaction: discord.Interaction,
        device: str,
        r: app_commands.Range[int, 0, 255],
        g: app_commands.Range[int, 0, 255],
        b: app_commands.Range[int, 0, 255],
    ) -> None:
        await self._run_control(
            interaction,
            lambda: self.service.color(device, int(r), int(g), int(b)),
            title=f"RGB({int(r)}, {int(g)}, {int(b)})",
        )

    @color.autocomplete("device")
    async def color_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, capability="rgb")

    async def _run_control(self, interaction: discord.Interaction, operation, *, title: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            target = await operation()
        except (LookupError, UniversalDeviceError) as exc:
            await interaction.followup.send(
                embed=EmbedFactory.warning(
                    title="Gerät nicht steuerbar",
                    description=str(exc)[:4000],
                ),
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.exception("Universal device control failed")
            await interaction.followup.send(
                embed=EmbedFactory.error(
                    title="Gerätesteuerung fehlgeschlagen",
                    description=(str(exc).strip() or type(exc).__name__)[:4000],
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title=title,
                description=(
                    f"**{target.display_name}** über **{target.protocol}** aktualisiert."
                ),
            ),
            ephemeral=True,
        )

    def _choices(
        self,
        current: str,
        *,
        capability: str | None = None,
        controllable_only: bool = True,
    ) -> list[app_commands.Choice[str]]:
        needle = current.strip().lower()
        result: list[app_commands.Choice[str]] = []
        for device in self.service.list_devices():
            if controllable_only and not device.controllable:
                continue
            if capability is not None and capability not in device.capabilities:
                continue
            searchable = (
                f"{device.display_name} {device.vendor} {device.model} "
                f"{device.protocol} {device.address}"
            ).lower()
            if needle and needle not in searchable:
                continue
            label = f"{device.display_name} · {device.protocol}"
            result.append(
                app_commands.Choice(
                    name=label[:100],
                    value=device.selector[:100],
                )
            )
        return result[:25]

    @staticmethod
    def _device_line(device: UniversalDevice) -> str:
        marker = {
            ControlState.CONTROLLABLE: "[steuerbar]",
            ControlState.AUTH_REQUIRED: "[Login nötig]",
            ControlState.PAIRING_REQUIRED: "[Pairing nötig]",
            ControlState.DETECTED_ONLY: "[nur erkannt]",
        }[device.state]
        caps = f" · {', '.join(device.capabilities)}" if device.capabilities else ""
        return (
            f"{marker} **{device.display_name}** · `{device.protocol}`"
            f" · `{device.address}`{caps}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UniversalDevices(bot))
