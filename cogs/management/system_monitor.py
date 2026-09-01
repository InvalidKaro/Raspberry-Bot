from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
from pathlib import Path

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from config import settings
from helpers.embeds import EmbedFactory
from helpers.formatting import human_bytes, human_duration
from services.pihole import collect_pihole_stats
from services.system_charts import render_system_history
from services.system_display import build_system_embed
from services.system_metrics import collect_system_metrics, throttling_labels
from views.system_status import SystemStatusView


def _tailscale_ipv4_sync() -> str | None:
    binary = shutil.which("tailscale")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        value = result.stdout.strip().splitlines()
        return value[0].strip() if result.returncode == 0 and value else None
    except (OSError, subprocess.SubprocessError):
        return None


def _top_processes_sync(limit: int = 10) -> list[dict[str, object]]:
    processes: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "username"]):
        try:
            proc.cpu_percent(interval=None)
            processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    time.sleep(0.5)
    rows: list[dict[str, object]] = []
    for proc in processes:
        try:
            rows.append(
                {
                    "pid": proc.pid,
                    "name": proc.name()[:28],
                    "cpu": float(proc.cpu_percent(interval=None)),
                    "ram": int(proc.memory_info().rss),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    rows.sort(key=lambda row: (float(row["cpu"]), int(row["ram"])), reverse=True)
    return rows[: max(1, min(limit, 15))]


def _network_interfaces_sync() -> list[str]:
    lines: list[str] = []
    stats = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        if name == "lo":
            continue
        ips = []
        for address in addresses:
            if address.family in {socket.AF_INET, socket.AF_INET6}:
                value = address.address.split("%", 1)[0]
                if value and not value.startswith("fe80:"):
                    ips.append(value)
        if not ips:
            continue
        state = "up" if stats.get(name) and stats[name].isup else "down"
        lines.append(f"**{name}** ({state}) • " + " • ".join(f"`{ip}`" for ip in ips[:3]))
    return lines[:10]


class SystemMonitor(commands.GroupCog, group_name="system", group_description="Raspberry Pi monitoring and health"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in self.bot.settings.owner_ids

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(interaction):
            return True
        await interaction.response.send_message(
            embed=EmbedFactory.error(
                title="Owner only",
                description="Detailed host administration is restricted to configured bot owners.",
            ),
            ephemeral=True,
        )
        return False

    @app_commands.command(name="now", description="Show detailed current Raspberry Pi and bot health.")
    async def now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        metrics = await collect_system_metrics(self.bot)
        await interaction.followup.send(embed=build_system_embed(metrics), view=SystemStatusView(self.bot))

    @app_commands.command(name="health", description="Run a concise Raspberry Pi health assessment.")
    async def health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        metrics = await collect_system_metrics(self.bot)
        flags = throttling_labels(metrics.throttled_flags)
        checks = [
            ("CPU 30s average", metrics.cpu_average_30s < 80, f"{metrics.cpu_average_30s:.1f}%"),
            ("Temperature", metrics.temperature is None or metrics.temperature < settings.system_temp_warning, f"{metrics.temperature:.1f} °C" if metrics.temperature is not None else "N/A"),
            ("RAM", metrics.ram_percent < settings.system_ram_warning, f"{metrics.ram_percent:.1f}%"),
            ("Storage", metrics.disk_percent < settings.system_disk_warning, f"{metrics.disk_percent:.1f}%"),
            ("Power / throttling", not flags, "No flags" if not flags else ", ".join(flags)),
            ("Pi-hole FTL", metrics.pihole_active, "Active" if metrics.pihole_active else "Inactive"),
        ]
        good = sum(1 for _, ok, _ in checks if ok)
        lines = [f"{'✅' if ok else '⚠️'} **{name}:** {value}" for name, ok, value in checks]
        embed = EmbedFactory.system(
            title="HomePi Health Check",
            description=f"**{good}/{len(checks)} checks healthy**\n\n" + "\n".join(lines),
        )
        embed.add_field(
            name="Sampler",
            value=f"Every {metrics.sample_interval_seconds}s • latest sample {metrics.sample_age_seconds:.0f}s old",
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="memory", description="Show RAM, swap and bot/dashboard process memory.")
    async def memory(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        metrics = await collect_system_metrics(self.bot)
        dashboard_ram = human_bytes(metrics.dashboard_memory) if metrics.dashboard_memory is not None else "Not detected"
        embed = EmbedFactory.system(title="Memory Details")
        embed.add_field(
            name="System RAM",
            value=(
                f"Usage: **{metrics.ram_percent:.1f}%**\n"
                f"Used: {human_bytes(metrics.ram_used)}\n"
                f"Available: **{human_bytes(metrics.ram_available)}**\n"
                f"Total: {human_bytes(metrics.ram_total)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Swap",
            value=(
                f"Usage: **{metrics.swap_percent:.1f}%**\n"
                f"Used: {human_bytes(metrics.swap_used)}\n"
                f"Total: {human_bytes(metrics.swap_total)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Processes",
            value=(
                f"Raspberry-Bot: **{human_bytes(metrics.bot_memory)}**\n"
                f"Dashboard: **{dashboard_ram}**"
            ),
            inline=True,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="storage", description="Show root filesystem and bot database storage usage.")
    async def storage(self, interaction: discord.Interaction) -> None:
        metrics = await collect_system_metrics(self.bot)
        db_path = Path(self.bot.settings.database_path)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        try:
            db_size = db_path.stat().st_size
        except OSError:
            db_size = 0
        embed = EmbedFactory.system(title="Storage Details")
        embed.add_field(
            name="Root filesystem",
            value=(
                f"Usage: **{metrics.disk_percent:.1f}%**\n"
                f"Used: {human_bytes(metrics.disk_used)}\n"
                f"Free: {human_bytes(max(metrics.disk_total - metrics.disk_used, 0))}\n"
                f"Total: {human_bytes(metrics.disk_total)}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Bot database",
            value=f"Size: **{human_bytes(db_size)}**\nPath: `{db_path}`",
            inline=True,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="network", description="Show network traffic and interface details (owner only).")
    async def network(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        metrics, interfaces, tailscale_ip = await asyncio.gather(
            collect_system_metrics(self.bot),
            asyncio.to_thread(_network_interfaces_sync),
            asyncio.to_thread(_tailscale_ipv4_sync),
        )
        embed = EmbedFactory.system(title="Network Details")
        embed.add_field(
            name="Traffic",
            value=(
                f"Download: **{human_bytes(metrics.network_rx_rate)}/s**\n"
                f"Upload: **{human_bytes(metrics.network_tx_rate)}/s**\n"
                f"RX total: {human_bytes(metrics.network_rx)}\n"
                f"TX total: {human_bytes(metrics.network_tx)}"
            ),
            inline=True,
        )
        embed.add_field(name="Tailscale", value=f"`{tailscale_ip}`" if tailscale_ip else "Not detected", inline=True)
        embed.add_field(name="Interfaces", value="\n".join(interfaces) if interfaces else "No active interface data.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="processes", description="Show the busiest host processes (owner only).")
    async def processes(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await asyncio.to_thread(_top_processes_sync, 10)
        if not rows:
            description = "No process data available."
        else:
            description = "\n".join(
                f"`{int(row['pid']):>5}` **{row['name']}** • CPU {float(row['cpu']):.1f}% • RAM {human_bytes(int(row['ram']))}"
                for row in rows
            )
        await interaction.followup.send(
            embed=EmbedFactory.system(title="Top Processes", description=description),
            ephemeral=True,
        )

    @app_commands.command(name="pihole", description="Show detailed Pi-hole service and blocking statistics.")
    async def pihole(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        stats = await collect_pihole_stats(force=True)
        if not stats.installed:
            await interaction.followup.send(
                embed=EmbedFactory.error(title="Pi-hole", description="Pi-hole was not detected on this host.")
            )
            return

        state = "🟢 Active" if stats.active else "🔴 Inactive"
        if stats.blocking is True:
            blocking = "🛡️ Enabled"
        elif stats.blocking is False:
            blocking = "⚠️ Disabled"
        else:
            blocking = "Unknown"

        embed = EmbedFactory.system(title="Pi-hole Details", description=f"FTL: **{state}** • Blocking: **{blocking}**")
        if stats.api_available:
            embed.add_field(
                name="DNS queries",
                value=(
                    f"Total: **{stats.total_queries or 0:,}**\n"
                    f"Blocked: **{stats.blocked_queries or 0:,}**\n"
                    f"Block rate: **{(stats.percent_blocked or 0):.1f}%**"
                ),
                inline=True,
            )
            embed.add_field(
                name="Resolver",
                value=(
                    f"Forwarded: **{stats.forwarded_queries or 0:,}**\n"
                    f"Cached: **{stats.cached_queries or 0:,}**\n"
                    f"Unique domains: **{stats.unique_domains or 0:,}**"
                ),
                inline=True,
            )
            embed.add_field(
                name="Clients / gravity",
                value=(
                    f"Clients: **{stats.total_clients or stats.active_clients or 0:,}**\n"
                    f"Active clients: **{stats.active_clients or 0:,}**\n"
                    f"Blocked domains: **{stats.domains_blocked or 0:,}**"
                ),
                inline=True,
            )
        else:
            if stats.permission_limited:
                api_text = (
                    "Detailed counters are unavailable because the bot user cannot read Pi-hole's v6 configuration. "
                    "Raspberry-Bot now skips repeated CLI calls in this state, so this does **not** spam the journal.\n"
                    f"Detail: `{stats.permission_detail or 'permission limited'}`"
                )
            else:
                api_text = "Detailed counters were not available to the bot. The FTL service status still works."
            embed.add_field(name="Statistics API", value=api_text, inline=False)

        versions = []
        if stats.core_version:
            versions.append(f"Core {stats.core_version}")
        if stats.web_version:
            versions.append(f"Web {stats.web_version}")
        if stats.ftl_version:
            versions.append(f"FTL {stats.ftl_version}")
        if versions:
            embed.add_field(name="Versions", value=" • ".join(versions), inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="dashboard", description="Open private HomePi dashboard links (bot owner only).")
    async def dashboard(self, interaction: discord.Interaction) -> None:
        if not await self._require_owner(interaction):
            return
        tailscale_ip = await asyncio.to_thread(_tailscale_ipv4_sync)
        hostname = socket.gethostname() or "homepi"
        port = int(self.bot.settings.dashboard_port)
        lan_url = f"http://{hostname}.local:{port}"
        ts_url = f"http://{tailscale_ip}:{port}" if tailscale_ip else None

        view = discord.ui.View(timeout=120)
        if ts_url:
            view.add_item(discord.ui.Button(label="Open via Tailscale", emoji="🔐", url=ts_url))
        view.add_item(discord.ui.Button(label="Open on LAN", emoji="🏠", url=lan_url))

        description = (
            "These links are shown **ephemerally** and only to configured bot owners.\n\n"
            f"LAN: `{lan_url}`"
        )
        if ts_url:
            description += f"\nTailscale: `{ts_url}`"
        await interaction.response.send_message(
            embed=EmbedFactory.system(title="HomePi Dashboard", description=description),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="setup", description="Configure the live Raspberry Pi status and alert channels.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup_monitor(
        self,
        interaction: discord.Interaction,
        status_channel: discord.TextChannel,
        alert_channel: discord.TextChannel | None = None,
        interval_seconds: app_commands.Range[int, 15, 300] = 30,
    ) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        metrics = await collect_system_metrics(self.bot)
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
                int(interval_seconds),
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
                    f"Discord update interval: **{interval_seconds}s**\n"
                    f"CPU sampler interval: **{self.bot.system_metrics.interval_seconds}s**"
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="config", description="Show the current system monitor configuration.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        row = await self.bot.database.fetchone(
            "SELECT * FROM system_monitor_config WHERE guild_id = ?",
            (interaction.guild_id,),
        )
        if row is None:
            await interaction.response.send_message(
                embed=EmbedFactory.info(title="System monitor", description="No monitor configuration exists yet. Use `/system setup`."),
                ephemeral=True,
            )
            return
        data = dict(row)
        status_channel = self.bot.get_channel(int(data["status_channel_id"])) if data.get("status_channel_id") else None
        alert_channel = self.bot.get_channel(int(data["alert_channel_id"])) if data.get("alert_channel_id") else None
        embed = EmbedFactory.system(title="System Monitor Configuration")
        embed.add_field(name="Enabled", value="Yes" if data.get("enabled") else "No", inline=True)
        embed.add_field(name="Update interval", value=f"{data.get('interval_seconds', 30)}s", inline=True)
        embed.add_field(name="Sampler", value=f"{self.bot.system_metrics.interval_seconds}s", inline=True)
        embed.add_field(name="Status channel", value=getattr(status_channel, "mention", "Not set"), inline=True)
        embed.add_field(name="Alert channel", value=getattr(alert_channel, "mention", "Disabled"), inline=True)
        embed.add_field(
            name="Thresholds",
            value=(
                f"Temp warning: {float(data.get('temp_warning') or 0):.1f} °C\n"
                f"Temp critical: {float(data.get('temp_critical') or 0):.1f} °C\n"
                f"RAM: {float(data.get('ram_warning') or 0):.1f}%\n"
                f"Disk: {float(data.get('disk_warning') or 0):.1f}%"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

    @app_commands.command(name="graph", description="Render a lightweight system graph for a selected time window.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 20.0, key=lambda interaction: interaction.user.id)
    async def graph(
        self,
        interaction: discord.Interaction,
        hours: app_commands.Range[int, 1, 168] = 24,
    ) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(thinking=True)
        rows = await self.bot.database.fetchall(
            "SELECT cpu_percent, temperature, ram_percent, disk_percent, recorded_at FROM system_metrics "
            "WHERE guild_id = ? AND recorded_at >= datetime('now', ?) ORDER BY recorded_at ASC",
            (interaction.guild_id, f"-{int(hours)} hours"),
        )
        data = [dict(row) for row in rows]
        image = await render_system_history(data)
        await interaction.followup.send(
            embed=EmbedFactory.system(
                title=f"{hours} Hour Health",
                description=f"Rendered from **{len(data)}** stored samples.",
            ),
            file=discord.File(image, filename=f"homepi-{hours}h.png"),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SystemMonitor(bot))
