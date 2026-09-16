from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.govee_ble import GoveeBleUnavailableError
from services.govee_ble_light import GoveeBleControlError
from services.govee_smart_home import (
    GoveeDeviceSummary,
    GoveeSmartHomeService,
)

logger = logging.getLogger(__name__)

SMART_HOME_GUILD_ID = 1162733312226361454
SMART_HOME_GUILD = discord.Object(id=SMART_HOME_GUILD_ID)


class SmartHomeDeviceSelect(discord.ui.Select):
    def __init__(self, view: "SmartHomePanel", devices: list[GoveeDeviceSummary]) -> None:
        self.panel_view = view
        options = [
            discord.SelectOption(
                label=device.display_name[:100],
                value=device.selector[:100],
                description=(
                    f"{device.transport} · {', '.join(device.capabilities)}"
                )[:100],
            )
            for device in devices[:25]
        ]
        super().__init__(
            placeholder="Govee-Gerät auswählen",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.panel_view.selected_selector = self.values[0]
        selected = self.panel_view.device_by_selector(self.values[0])
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Gerät ausgewählt",
                description=(
                    f"**{selected.display_name if selected else 'Govee'}** ist jetzt aktiv."
                ),
            ),
            ephemeral=True,
        )


class SmartHomePanel(discord.ui.View):
    def __init__(
        self,
        cog: "SmartHome",
        *,
        owner_id: int,
        devices: list[GoveeDeviceSummary],
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.devices = devices[:25]
        self.selected_selector = self.devices[0].selector
        self.add_item(SmartHomeDeviceSelect(self, self.devices))

    def device_by_selector(self, selector: str) -> GoveeDeviceSummary | None:
        return next(
            (device for device in self.devices if device.selector == selector),
            None,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != SMART_HOME_GUILD_ID:
            await interaction.response.send_message(
                "Dieses Smart-Home-Panel ist auf diesem Server nicht verfügbar.",
                ephemeral=True,
            )
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Dieses private Panel gehört zu einer anderen Interaktion.",
                ephemeral=True,
            )
            return False
        return True

    async def _preset(
        self,
        interaction: discord.Interaction,
        preset: str,
        title: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.cog.service.apply_preset(
                self.selected_selector,
                preset,
            )
        except Exception as exc:
            await self.cog.send_control_error(interaction, exc)
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title=title,
                description=(
                    f"**{result.display_name}** über **{result.transport}** aktualisiert."
                ),
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="An", style=discord.ButtonStyle.success, row=1)
    async def power_on(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self._preset(interaction, "on", "Licht eingeschaltet")

    @discord.ui.button(label="Aus", style=discord.ButtonStyle.danger, row=1)
    async def power_off(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self._preset(interaction, "off", "Licht ausgeschaltet")

    @discord.ui.button(label="Nacht", style=discord.ButtonStyle.secondary, row=1)
    async def night(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self._preset(interaction, "night", "Nachtmodus")

    @discord.ui.button(label="Gaming", style=discord.ButtonStyle.primary, row=1)
    async def gaming(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self._preset(interaction, "gaming", "Gaming-Szene")

    @discord.ui.button(label="Alle aus", style=discord.ButtonStyle.danger, row=2)
    async def all_off(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.cog.service.apply_preset_all("off")
        except Exception as exc:
            await self.cog.send_control_error(interaction, exc)
            return

        description = f"**{result.applied}** Gerät(e) ausgeschaltet."
        if result.failed:
            description += f"\n**{result.failed}** Gerät(e) konnten nicht erreicht werden."

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="Smart Home ausgeschaltet",
                description=description,
            ),
            ephemeral=True,
        )


@app_commands.guilds(SMART_HOME_GUILD)
class SmartHome(
    commands.GroupCog,
    group_name="home",
    group_description="Lokale Govee-Smart-Home-Steuerung",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._service: GoveeSmartHomeService | None = None

    @property
    def service(self) -> GoveeSmartHomeService:
        if self._service is None:
            self._service = GoveeSmartHomeService()
        return self._service

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != SMART_HOME_GUILD_ID:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Diese Befehle sind nur auf dem freigeschalteten Server verfügbar.",
                    ephemeral=True,
                )
            return False
        return True

    @app_commands.command(
        name="scan",
        description="Sucht lokale Govee-Geräte über WLAN und Bluetooth.",
    )
    async def scan(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.service.refresh_devices()
        except Exception as exc:
            await self.send_control_error(interaction, exc, title="Govee-Scan fehlgeschlagen")
            return

        embed = EmbedFactory.system(
            title="Govee Discovery",
            description=(
                f"**{len(result.lan)} WLAN/LAN** und **{len(result.ble)} Bluetooth** "
                "Govee-Gerät(e) aktuell gefunden."
            ),
        )

        if result.lan:
            lines = [
                f"`{device.sku}` · WLAN · `{device.ip}`"
                for device in result.lan[:12]
            ]
            embed.add_field(
                name="WLAN / LAN",
                value="\n".join(lines)[:1024],
                inline=False,
            )

        if result.ble:
            lines: list[str] = []
            for device in result.ble[:12]:
                flags: list[str] = []
                if self.service.ble_lights.supports(device):
                    flags.append("steuerbar")
                if device.temperature_c is not None or device.humidity_percent is not None:
                    flags.append("Sensor")
                suffix = f" · {', '.join(flags)}" if flags else " · erkannt"
                values: list[str] = []
                if device.temperature_c is not None:
                    values.append(f"{device.temperature_c:.1f} °C")
                if device.humidity_percent is not None:
                    values.append(f"{device.humidity_percent:.1f} %")
                sensor = f" · {' / '.join(values)}" if values else ""
                lines.append(
                    f"`{device.model or 'Govee'}` · Bluetooth · "
                    f"`{device.masked_address}`{suffix}{sensor}"
                )
            embed.add_field(
                name="Bluetooth",
                value="\n".join(lines)[:1024],
                inline=False,
            )

        warnings = [
            warning
            for warning in (
                f"WLAN/LAN: {result.lan_error}" if result.lan_error else None,
                f"Bluetooth: {result.ble_error}" if result.ble_error else None,
            )
            if warning
        ]
        if warnings:
            embed.add_field(
                name="Teilweise nicht verfügbar",
                value="\n".join(warnings)[:1024],
                inline=False,
            )

        if not result.lan and not result.ble:
            embed.description = (
                "Keine Govee-Geräte gefunden. Bei WLAN-Leuchten muss für "
                "unterstützte Modelle **LAN Control** in Govee Home aktiviert sein."
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="status",
        description="Zeigt erkannte Govee-Geräte, Transporte und Fähigkeiten.",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            discovery = await self.service.refresh_devices()
        except Exception as exc:
            await self.send_control_error(interaction, exc, title="Smart-Home-Status fehlgeschlagen")
            return

        controllable = self.service.controllable_devices()
        sensors = self.service.sensor_devices()
        embed = EmbedFactory.system(
            title="Govee Smart Home",
            description=(
                f"**{len(controllable)} steuerbar** · **{len(sensors)} Klimasensor(en)** · "
                f"Guild `{SMART_HOME_GUILD_ID}`"
            ),
        )

        if controllable:
            embed.add_field(
                name="Steuerbare Geräte",
                value="\n".join(
                    f"`{device.model}` · **{device.transport}** · "
                    f"{', '.join(device.capabilities)}"
                    for device in controllable[:12]
                )[:1024],
                inline=False,
            )

        if sensors:
            embed.add_field(
                name="Sensoren",
                value="\n".join(self._sensor_line(device) for device in sensors[:10])[:1024],
                inline=False,
            )

        unsupported_ble = [
            device
            for device in discovery.ble
            if not self.service.ble_lights.supports(device)
            and device not in sensors
        ]
        if unsupported_ble:
            embed.add_field(
                name="BLE erkannt, nicht aktiv gesteuert",
                value="\n".join(
                    f"`{device.model or 'Govee'}` · `{device.masked_address}`"
                    for device in unsupported_ble[:10]
                )[:1024],
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="panel",
        description="Öffnet die kompakte Govee-Gerätesteuerung.",
    )
    async def panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.refresh_devices()
            devices = self.service.controllable_devices()
        except Exception as exc:
            await self.send_control_error(interaction, exc, title="Smart-Home-Panel nicht verfügbar")
            return

        if not devices:
            await interaction.followup.send(
                embed=EmbedFactory.warning(
                    title="Keine steuerbaren Geräte",
                    description=(
                        "Es wurde kein lokal steuerbares Govee-Gerät gefunden. "
                        "Nutze `/home scan` für die Diagnose."
                    ),
                ),
                ephemeral=True,
            )
            return

        default_device = devices[0]
        embed = EmbedFactory.system(
            title="Govee Control Center",
            description=(
                f"**{len(devices)}** steuerbare Gerät(e) gefunden.\n"
                f"Standardauswahl: **{default_device.display_name}**\n\n"
                "Gerät oben auswählen und anschließend eine Aktion ausführen."
            ),
        )
        await interaction.followup.send(
            embed=embed,
            view=SmartHomePanel(
                self,
                owner_id=interaction.user.id,
                devices=devices,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="climate",
        description="Liest Govee Temperatur-/Feuchtesensoren per Bluetooth.",
    )
    async def climate(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.ble.scan(7.0)
            sensors = self.service.sensor_devices()
        except Exception as exc:
            await self.send_control_error(interaction, exc, title="Bluetooth-Klimascan fehlgeschlagen")
            return

        if not sensors:
            await interaction.followup.send(
                embed=EmbedFactory.warning(
                    title="Kein Hygrometer dekodiert",
                    description=(
                        "Bluetooth funktioniert, aber beim aktuellen Scan wurde kein "
                        "unterstütztes Govee-Klimagerät mit Messwerten empfangen."
                    ),
                ),
                ephemeral=True,
            )
            return

        embed = EmbedFactory.system(title="Raumklima")
        for device in sensors[:10]:
            values: list[str] = []
            if device.temperature_c is not None:
                values.append(f"Temperatur: **{device.temperature_c:.1f} °C**")
            if device.humidity_percent is not None:
                values.append(f"Luftfeuchte: **{device.humidity_percent:.1f} %**")
            if device.battery_percent is not None:
                values.append(f"Batterie: **{device.battery_percent:.0f} %**")
            embed.add_field(
                name=f"{device.model or 'Govee'} · {device.name}",
                value="\n".join(values),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="power",
        description="Schaltet eine lokale Govee-Leuchte an oder aus.",
    )
    @app_commands.describe(device="Govee-Gerät", state="An oder Aus")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="An", value="on"),
            app_commands.Choice(name="Aus", value="off"),
        ]
    )
    async def power(
        self,
        interaction: discord.Interaction,
        device: str,
        state: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.service.power_device(device, state.value == "on")
        except Exception as exc:
            await self.send_control_error(interaction, exc)
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="Govee aktualisiert",
                description=(
                    f"**{result.display_name}** → **{state.name}** "
                    f"über **{result.transport}**"
                ),
            ),
            ephemeral=True,
        )

    @power.autocomplete("device")
    async def power_device_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self.device_choices(current)

    @app_commands.command(
        name="brightness",
        description="Setzt die Helligkeit einer Govee-Leuchte.",
    )
    @app_commands.describe(device="Govee-Gerät", value="1 bis 100 Prozent")
    async def brightness(
        self,
        interaction: discord.Interaction,
        device: str,
        value: app_commands.Range[int, 1, 100],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.service.brightness_device(device, int(value))
        except Exception as exc:
            await self.send_control_error(interaction, exc)
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="Helligkeit gesetzt",
                description=(
                    f"**{result.display_name}** → **{int(value)} %** "
                    f"über **{result.transport}**"
                ),
            ),
            ephemeral=True,
        )

    @brightness.autocomplete("device")
    async def brightness_device_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self.device_choices(current)

    @app_commands.command(
        name="color",
        description="Setzt eine RGB-Farbe auf einer Govee-Leuchte.",
    )
    @app_commands.describe(device="Govee-Gerät")
    async def color(
        self,
        interaction: discord.Interaction,
        device: str,
        r: app_commands.Range[int, 0, 255],
        g: app_commands.Range[int, 0, 255],
        b: app_commands.Range[int, 0, 255],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.service.color_device(
                device,
                int(r),
                int(g),
                int(b),
            )
        except Exception as exc:
            await self.send_control_error(interaction, exc)
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="Farbe gesetzt",
                description=(
                    f"**{result.display_name}** → "
                    f"`RGB({int(r)}, {int(g)}, {int(b)})` "
                    f"über **{result.transport}**"
                ),
            ),
            ephemeral=True,
        )

    @color.autocomplete("device")
    async def color_device_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self.device_choices(current)

    def device_choices(self, current: str) -> list[app_commands.Choice[str]]:
        needle = current.strip().lower()
        choices: list[app_commands.Choice[str]] = []
        for device in self.service.controllable_devices():
            searchable = (
                f"{device.display_name} {device.model} "
                f"{device.transport} {device.detail}"
            ).lower()
            if needle and needle not in searchable:
                continue
            choices.append(
                app_commands.Choice(
                    name=(
                        f"{device.model} · {device.transport} · {device.detail}"
                    )[:100],
                    value=device.selector[:100],
                )
            )
        return choices[:25]

    @staticmethod
    def _sensor_line(device: object) -> str:
        model = getattr(device, "model", None) or "Govee"
        temperature = getattr(device, "temperature_c", None)
        humidity = getattr(device, "humidity_percent", None)
        values: list[str] = []
        if temperature is not None:
            values.append(f"{temperature:.1f} °C")
        if humidity is not None:
            values.append(f"{humidity:.1f} %")
        return f"`{model}` · {' · '.join(values) if values else 'Messwerte erkannt'}"

    async def send_control_error(
        self,
        interaction: discord.Interaction,
        exc: Exception,
        *,
        title: str = "Govee-Steuerung fehlgeschlagen",
    ) -> None:
        logger.exception("Govee smart-home operation failed")

        if isinstance(exc, GoveeBleUnavailableError):
            description = (
                f"{exc}\n\n"
                "Prüfe auf dem Pi: `bluetoothctl show` → `Powered: yes`."
            )
        elif isinstance(exc, GoveeBleControlError):
            description = str(exc)
        else:
            description = str(exc).strip() or type(exc).__name__

        await interaction.followup.send(
            embed=EmbedFactory.error(
                title=title,
                description=description[:4000],
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SmartHome(bot))
