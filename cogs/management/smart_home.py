from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.govee_smart_home import GoveeSmartHomeService

logger = logging.getLogger(__name__)

SMART_HOME_GUILD_ID = 1162733312226361454
SMART_HOME_GUILD = discord.Object(id=SMART_HOME_GUILD_ID)


class SmartHomePanel(discord.ui.View):
    def __init__(self, cog: "SmartHome") -> None:
        super().__init__(timeout=300)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != SMART_HOME_GUILD_ID:
            await interaction.response.send_message(
                "Dieses Smart-Home-Panel ist auf diesem Server nicht verfügbar.",
                ephemeral=True,
            )
            return False
        return True

    async def _scene(self, interaction: discord.Interaction, scene: str, label: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await self.cog.service.scene(scene)
        except Exception as exc:
            logger.exception("Govee scene failed: %s", scene)
            await interaction.followup.send(
                embed=EmbedFactory.error(
                    title="Govee-Steuerung fehlgeschlagen",
                    description=f"`{type(exc).__name__}`: {exc}",
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title=label,
                description=f"Auf **{count}** LAN-Gerät(en) angewendet.",
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Alle an", style=discord.ButtonStyle.success)
    async def all_on(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._scene(interaction, "on", "Licht eingeschaltet")

    @discord.ui.button(label="Alle aus", style=discord.ButtonStyle.danger)
    async def all_off(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._scene(interaction, "off", "Licht ausgeschaltet")

    @discord.ui.button(label="Nacht", style=discord.ButtonStyle.secondary)
    async def night(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._scene(interaction, "night", "Nachtmodus")

    @discord.ui.button(label="Gaming", style=discord.ButtonStyle.primary)
    async def gaming(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._scene(interaction, "gaming", "Gaming-Szene")


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

    @app_commands.command(name="scan", description="Sucht lokale Govee-Geräte über WLAN und Bluetooth.")
    async def scan(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.service.discover_all()
        except Exception as exc:
            logger.exception("Govee discovery failed")
            await interaction.followup.send(
                embed=EmbedFactory.error(
                    title="Govee-Scan fehlgeschlagen",
                    description=f"`{type(exc).__name__}`: {exc}",
                ),
                ephemeral=True,
            )
            return

        embed = EmbedFactory.system(
            title="Govee Discovery",
            description=(
                f"**{len(result.lan)} WLAN/LAN** und **{len(result.ble)} Bluetooth** "
                "Govee-Gerät(e) gefunden."
            ),
        )

        if result.lan:
            lan_lines = [
                f"`{device.sku}` · `{device.ip}` · `{device.device_id}`"
                for device in result.lan[:12]
            ]
            embed.add_field(
                name="WLAN / LAN",
                value="\n".join(lan_lines)[:1024],
                inline=False,
            )

        if result.ble:
            ble_lines = []
            for device in result.ble[:12]:
                model = device.model or "Modell unbekannt"
                sensor_bits = []
                if device.temperature_c is not None:
                    sensor_bits.append(f"{device.temperature_c:.1f} °C")
                if device.humidity_percent is not None:
                    sensor_bits.append(f"{device.humidity_percent:.1f} %")
                extra = f" · {' · '.join(sensor_bits)}" if sensor_bits else ""
                ble_lines.append(
                    f"`{model}` · {device.name} · `{device.address}`{extra}"
                )
            embed.add_field(
                name="Bluetooth",
                value="\n".join(ble_lines)[:1024],
                inline=False,
            )

        if not result.lan and not result.ble:
            embed.description = (
                "Keine Govee-Geräte gefunden. Bei WLAN-Leuchten muss in der "
                "Govee-App für unterstützte Modelle **LAN Control** aktiviert sein."
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="panel", description="Öffnet die kompakte Govee-Lichtsteuerung.")
    async def panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            devices = await self.service.ensure_lan_devices()
        except Exception as exc:
            logger.exception("Govee LAN discovery failed")
            await interaction.followup.send(
                embed=EmbedFactory.error(
                    title="Keine Govee-LAN-Verbindung",
                    description=f"`{type(exc).__name__}`: {exc}",
                ),
                ephemeral=True,
            )
            return

        embed = EmbedFactory.system(
            title="Govee Control",
            description=(
                f"**{len(devices)}** lokal steuerbare WLAN-Gerät(e).\n"
                "Die Buttons wirken auf alle aktuell gefundenen Govee-LAN-Leuchten."
            ),
        )
        await interaction.followup.send(
            embed=embed,
            view=SmartHomePanel(self),
            ephemeral=True,
        )

    @app_commands.command(name="climate", description="Liest Govee Temperatur-/Feuchtesensoren per BLE.")
    async def climate(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            devices = await self.service.ble.scan(6.0)
        except Exception as exc:
            logger.exception("Govee BLE climate scan failed")
            await interaction.followup.send(
                embed=EmbedFactory.error(
                    title="Bluetooth-Scan fehlgeschlagen",
                    description=f"`{type(exc).__name__}`: {exc}",
                ),
                ephemeral=True,
            )
            return

        sensors = [
            device
            for device in devices
            if device.temperature_c is not None or device.humidity_percent is not None
        ]
        if not sensors:
            await interaction.followup.send(
                embed=EmbedFactory.warning(
                    title="Kein Hygrometer gefunden",
                    description=(
                        "Der Pi hat beim Scan kein unterstütztes Govee-BLE-"
                        "Temperatur-/Feuchtegerät dekodiert."
                    ),
                ),
                ephemeral=True,
            )
            return

        embed = EmbedFactory.system(title="Raumklima")
        for device in sensors[:10]:
            values = []
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

    @app_commands.command(name="power", description="Schaltet eine lokale Govee-Leuchte an oder aus.")
    @app_commands.describe(device="Gerät aus /home scan", state="An oder Aus")
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
            await self.service.ensure_lan_devices()
            target = self.service.lan.resolve(device)
            await self.service.lan.power(target, state.value == "on")
        except Exception as exc:
            await self._send_control_error(interaction, exc)
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="Govee aktualisiert",
                description=f"**{target.display_name}** → **{state.name}**",
            ),
            ephemeral=True,
        )

    @power.autocomplete("device")
    async def power_device_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self._device_choices(current)

    @app_commands.command(name="brightness", description="Setzt die Helligkeit einer Govee-Leuchte.")
    @app_commands.describe(device="Gerät aus /home scan", value="1 bis 100 Prozent")
    async def brightness(
        self,
        interaction: discord.Interaction,
        device: str,
        value: app_commands.Range[int, 1, 100],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.ensure_lan_devices()
            target = self.service.lan.resolve(device)
            await self.service.lan.brightness(target, int(value))
        except Exception as exc:
            await self._send_control_error(interaction, exc)
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="Helligkeit gesetzt",
                description=f"**{target.display_name}** → **{int(value)} %**",
            ),
            ephemeral=True,
        )

    @brightness.autocomplete("device")
    async def brightness_device_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return self._device_choices(current)

    @app_commands.command(name="color", description="Setzt eine RGB-Farbe auf einer Govee-Leuchte.")
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
            await self.service.ensure_lan_devices()
            target = self.service.lan.resolve(device)
            await self.service.lan.color(target, int(r), int(g), int(b))
        except Exception as exc:
            await self._send_control_error(interaction, exc)
            return

        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="Farbe gesetzt",
                description=(
                    f"**{target.display_name}** → "
                    f"`RGB({int(r)}, {int(g)}, {int(b)})`"
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
        return self._device_choices(current)

    def _device_choices(self, current: str) -> list[app_commands.Choice[str]]:
        needle = current.strip().lower()
        choices: list[app_commands.Choice[str]] = []
        for device in self.service.lan.cached_devices():
            name = f"{device.sku} · {device.ip}"
            if needle and needle not in name.lower() and needle not in device.device_id.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=name[:100],
                    value=device.device_id[:100],
                )
            )
        return choices[:25]

    async def _send_control_error(
        self,
        interaction: discord.Interaction,
        exc: Exception,
    ) -> None:
        logger.exception("Govee control failed")
        await interaction.followup.send(
            embed=EmbedFactory.error(
                title="Govee-Steuerung fehlgeschlagen",
                description=f"`{type(exc).__name__}`: {exc}",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SmartHome(bot))
