from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import settings
from helpers.embeds import EmbedFactory
from services.system_charts import render_system_history
from services.system_display import build_system_embed
from services.system_metrics import collect_system_metrics
from views.system_status import SystemStatusView


class SystemMonitor(commands.GroupCog, group_name="system", group_description="Raspberry Pi monitoring and health"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="now", description="Show the current Raspberry Pi status.")
    async def now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        metrics = await collect_system_metrics()
        await interaction.followup.send(embed=build_system_embed(metrics))

    @app_commands.command(name="setup", description="Configure the live Raspberry Pi status and alert channels.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup_monitor(
        self,
        interaction: discord.Interaction,
        status_channel: discord.TextChannel,
        alert_channel: discord.TextChannel | None = None,
        interval_minutes: app_commands.Range[int, 1, 60] = 5,
    ) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        metrics = await collect_system_metrics()
        message = await status_channel.send(embed=build_system_embed(metrics), view=SystemStatusView(self.bot))
        await self.bot.database.execute(
            "INSERT INTO system_monitor_config (guild_id, enabled, status_channel_id, status_message_id, alert_channel_id, interval_seconds, temp_warning, temp_critical, ram_warning, disk_warning) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET enabled=1, status_channel_id=excluded.status_channel_id, status_message_id=excluded.status_message_id, "
            "alert_channel_id=excluded.alert_channel_id, interval_seconds=excluded.interval_seconds, updated_at=CURRENT_TIMESTAMP",
            (
                interaction.guild_id,
                status_channel.id,
                message.id,
                alert_channel.id if alert_channel else None,
                int(interval_minutes) * 60,
                settings.system_temp_warning,
                settings.system_temp_critical,
                settings.system_ram_warning,
                settings.system_disk_warning,
            ),
        )
        await interaction.followup.send(
            embed=EmbedFactory.success(
                title="System monitor enabled",
                description=(
                    f"Live status: {status_channel.mention}\n"
                    f"Alerts: {alert_channel.mention if alert_channel else 'disabled'}\n"
                    f"Interval: {interval_minutes} min"
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="thresholds", description="Configure Raspberry Pi alert thresholds.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def thresholds(
        self,
        interaction: discord.Interaction,
        temperature_warning: app_commands.Range[float, 40, 90] = 70.0,
        temperature_critical: app_commands.Range[float, 50, 95] = 80.0,
        ram_warning: app_commands.Range[float, 50, 99] = 80.0,
        disk_warning: app_commands.Range[float, 50, 99] = 85.0,
    ) -> None:
        if interaction.guild_id is None:
            return
        if float(temperature_critical) <= float(temperature_warning):
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Invalid thresholds", description="Critical temperature must be higher than warning temperature."),
                ephemeral=True,
            )
            return
        await self.bot.database.execute(
            "INSERT INTO system_monitor_config (guild_id, temp_warning, temp_critical, ram_warning, disk_warning) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET temp_warning=excluded.temp_warning, temp_critical=excluded.temp_critical, "
            "ram_warning=excluded.ram_warning, disk_warning=excluded.disk_warning, updated_at=CURRENT_TIMESTAMP",
            (interaction.guild_id, float(temperature_warning), float(temperature_critical), float(ram_warning), float(disk_warning)),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Alert thresholds updated",
                description=(
                    f"Temperature warning: **{float(temperature_warning):.1f} °C**\n"
                    f"Temperature critical: **{float(temperature_critical):.1f} °C**\n"
                    f"RAM warning: **{float(ram_warning):.1f}%**\n"
                    f"Disk warning: **{float(disk_warning):.1f}%**"
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="disable", description="Disable automatic Raspberry Pi status updates.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await self.bot.database.execute(
            "UPDATE system_monitor_config SET enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ?",
            (interaction.guild_id,),
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="System monitor disabled", description="Automatic status updates have been stopped."),
            ephemeral=True,
        )

    @app_commands.command(name="graph", description="Render a lightweight Pillow graph for the last 24 hours.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 30.0, key=lambda interaction: interaction.user.id)
    async def graph(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(thinking=True)
        rows = await self.bot.database.fetchall(
            "SELECT cpu_percent, temperature, ram_percent, disk_percent, recorded_at FROM system_metrics "
            "WHERE guild_id = ? AND recorded_at >= datetime('now', '-24 hours') ORDER BY recorded_at ASC",
            (interaction.guild_id,),
        )
        data = [dict(row) for row in rows]
        image = await render_system_history(data)
        await interaction.followup.send(
            embed=EmbedFactory.system(title="24 Hour Health", description=f"Rendered from **{len(data)}** stored samples."),
            file=discord.File(image, filename="homepi-24h.png"),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SystemMonitor(bot))
