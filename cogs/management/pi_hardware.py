from __future__ import annotations

import os

import discord
from discord import app_commands
from discord.ext import commands

try:
    from gpiozero import PWMLED
except ImportError:  # pragma: no cover - hardware dependency
    PWMLED = None


class PiHardware(commands.Cog):
    pi = app_commands.Group(name="pi", description="Lokale Raspberry-Pi-Hardware")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._led = None
        self._pin = int(os.getenv("PI_LED_PIN", "18"))
        self._active_high = os.getenv("PI_LED_ACTIVE_HIGH", "true").strip().lower() not in {"0", "false", "no", "off"}

    async def cog_unload(self) -> None:
        if self._led is not None:
            try:
                self._led.off()
                self._led.close()
            except Exception:
                pass
            self._led = None

    def _owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in getattr(self.bot.settings, "owner_ids", set())

    def _get_led(self):
        if PWMLED is None:
            raise RuntimeError("gpiozero fehlt")
        if self._led is None:
            self._led = PWMLED(self._pin, active_high=self._active_high, initial_value=0)
        return self._led

    @pi.command(name="led", description="Steuert die konfigurierte LED am Raspberry Pi.")
    @app_commands.describe(aktion="on/off/blink/status", helligkeit="0–100 Prozent")
    @app_commands.choices(
        aktion=[
            app_commands.Choice(name="An", value="on"),
            app_commands.Choice(name="Aus", value="off"),
            app_commands.Choice(name="Blinken", value="blink"),
            app_commands.Choice(name="Status", value="status"),
        ]
    )
    async def led(
        self,
        interaction: discord.Interaction,
        aktion: app_commands.Choice[str],
        helligkeit: app_commands.Range[int, 0, 100] = 100,
    ) -> None:
        if not self._owner(interaction):
            await interaction.response.send_message("Nur ein Bot-Owner darf lokale GPIO-Hardware steuern.", ephemeral=True)
            return
        try:
            led = self._get_led()
        except Exception as exc:
            await interaction.response.send_message(
                f"GPIO ist nicht verfügbar (`{type(exc).__name__}`). Prüfe `gpiozero`, Rechte und `PI_LED_PIN`.",
                ephemeral=True,
            )
            return
        action = aktion.value
        value = max(0.0, min(1.0, int(helligkeit) / 100.0))
        try:
            if action == "on":
                led.value = value
                text = f"💡 LED auf GPIO **BCM {self._pin}** → **{int(helligkeit)}%**."
            elif action == "off":
                led.off()
                text = f"⚫ LED auf GPIO **BCM {self._pin}** ausgeschaltet."
            elif action == "blink":
                led.blink(on_time=0.5, off_time=0.5, fade_in_time=0.15, fade_out_time=0.15, n=None, background=True)
                text = f"✨ LED auf GPIO **BCM {self._pin}** blinkt."
            else:
                text = f"💡 GPIO **BCM {self._pin}** · Wert **{float(led.value) * 100:.0f}%** · Active-high **{'ja' if self._active_high else 'nein'}**."
        except Exception as exc:
            await interaction.response.send_message(f"LED-Steuerung fehlgeschlagen: `{type(exc).__name__}`", ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PiHardware(bot))
