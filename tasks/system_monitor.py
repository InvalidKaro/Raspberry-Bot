from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

from helpers.embeds import EmbedFactory
from services.system_display import build_system_embed
from services.system_metrics import collect_system_metrics
from views.system_status import SystemStatusView

logger = logging.getLogger(__name__)


class SystemMonitorTask(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_run: dict[int, float] = {}
        self._alert_state: dict[tuple[int, str], bool] = {}
        self.monitor_loop.start()

    def cog_unload(self) -> None:
        self.monitor_loop.cancel()

    async def _send_alert(self, guild_id: int, channel_id: int | None, key: str, active: bool, title: str, description: str) -> None:
        if not channel_id:
            return
        state_key = (guild_id, key)
        previous = self._alert_state.get(state_key, False)
        if previous == active:
            return
        self._alert_state[state_key] = active
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        if active:
            embed = EmbedFactory.warning(title=title, description=description)
        else:
            embed = EmbedFactory.success(title=f"{title} recovered", description=description)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            logger.exception("Could not send system alert in guild %s", guild_id)

    @tasks.loop(seconds=60)
    async def monitor_loop(self) -> None:
        rows = await self.bot.database.fetchall("SELECT * FROM system_monitor_config WHERE enabled = 1")
        if not rows:
            return
        now = time.monotonic()
        metrics = None
        for row in rows:
            config = dict(row)
            guild_id = int(config["guild_id"])
            interval = max(int(config["interval_seconds"]), 60)
            if now - self._last_run.get(guild_id, 0) < interval:
                continue
            self._last_run[guild_id] = now
            if metrics is None:
                metrics = await collect_system_metrics()
            await self.bot.database.execute(
                "INSERT INTO system_metrics (guild_id, cpu_percent, temperature, ram_percent, disk_percent, load_1m, network_rx, network_tx, bot_memory, throttled_flags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    metrics.cpu_percent,
                    metrics.temperature,
                    metrics.ram_percent,
                    metrics.disk_percent,
                    metrics.load_1m,
                    metrics.network_rx,
                    metrics.network_tx,
                    metrics.bot_memory,
                    metrics.throttled_flags,
                ),
            )
            channel = self.bot.get_channel(int(config["status_channel_id"])) if config.get("status_channel_id") else None
            if isinstance(channel, discord.TextChannel):
                message = None
                if config.get("status_message_id"):
                    try:
                        message = await channel.fetch_message(int(config["status_message_id"]))
                    except discord.HTTPException:
                        message = None
                try:
                    if message is None:
                        message = await channel.send(embed=build_system_embed(metrics), view=SystemStatusView(self.bot))
                        await self.bot.database.execute(
                            "UPDATE system_monitor_config SET status_message_id = ? WHERE guild_id = ?",
                            (message.id, guild_id),
                        )
                    else:
                        await message.edit(embed=build_system_embed(metrics), view=SystemStatusView(self.bot))
                except discord.HTTPException:
                    logger.exception("Could not update system status for guild %s", guild_id)

            alert_channel_id = int(config["alert_channel_id"]) if config.get("alert_channel_id") else None
            temp = metrics.temperature or 0.0
            await self._send_alert(
                guild_id, alert_channel_id, "temperature_warning", temp >= float(config["temp_warning"]),
                "High Raspberry Pi temperature", f"Current temperature: **{temp:.1f} °C**",
            )
            await self._send_alert(
                guild_id, alert_channel_id, "temperature_critical", temp >= float(config["temp_critical"]),
                "Critical Raspberry Pi temperature", f"Current temperature: **{temp:.1f} °C**",
            )
            await self._send_alert(
                guild_id, alert_channel_id, "ram", metrics.ram_percent >= float(config["ram_warning"]),
                "High RAM usage", f"Current RAM usage: **{metrics.ram_percent:.1f}%**",
            )
            await self._send_alert(
                guild_id, alert_channel_id, "disk", metrics.disk_percent >= float(config["disk_warning"]),
                "High disk usage", f"Current disk usage: **{metrics.disk_percent:.1f}%**",
            )
            await self._send_alert(
                guild_id, alert_channel_id, "throttle", metrics.throttled_flags != 0,
                "Raspberry Pi throttle / power flag", f"vcgencmd flags: `0x{metrics.throttled_flags:x}`",
            )

        await self.bot.database.execute(
            "DELETE FROM system_metrics WHERE recorded_at < datetime('now', '-31 days')"
        )

    @monitor_loop.before_loop
    async def before_monitor(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SystemMonitorTask(bot))
