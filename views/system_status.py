from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess

import discord

from helpers.embeds import EmbedFactory
from helpers.formatting import human_bytes, human_duration
from services.pihole import collect_pihole_stats
from services.system_charts import render_system_history
from services.system_display import build_system_embed
from services.system_metrics import collect_system_metrics, throttling_labels


def _tailscale_ipv4() -> str | None:
    binary = shutil.which("tailscale")
    if not binary:
        return None
    try:
        result = subprocess.run([binary, "ip", "-4"], capture_output=True, text=True, timeout=4, check=False)
        lines = result.stdout.strip().splitlines()
        return lines[0].strip() if result.returncode == 0 and lines else None
    except (OSError, subprocess.SubprocessError):
        return None


class SystemStatusView(discord.ui.View):
    def __init__(self, bot: object) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="raspberry_bot:system:refresh",
    )
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        metrics = await collect_system_metrics(self.bot)
        if interaction.message is not None:
            await interaction.message.edit(embed=build_system_embed(metrics), view=self)

    @discord.ui.button(
        label="Details",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        custom_id="raspberry_bot:system:details",
    )
    async def details(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        metrics = await collect_system_metrics(self.bot)
        flags = throttling_labels(metrics.throttled_flags)
        dashboard_cpu = "—" if metrics.dashboard_cpu_percent is None else f"{metrics.dashboard_cpu_percent:.1f}%"
        dashboard_ram = "—" if metrics.dashboard_memory is None else human_bytes(metrics.dashboard_memory)

        embed = EmbedFactory.system(title="Raspberry Pi Details")
        embed.add_field(
            name="System CPU",
            value=(
                f"Current: **{metrics.cpu_percent:.1f}%**\n"
                f"30s avg: **{metrics.cpu_average_30s:.1f}%**\n"
                f"5m avg: **{metrics.cpu_average_5m:.1f}%**\n"
                f"Frequency: {metrics.cpu_frequency_mhz or 0:.0f} MHz"
            ),
            inline=True,
        )
        embed.add_field(
            name="Processes",
            value=(
                f"Bot: **{metrics.bot_cpu_percent:.1f}%** • {human_bytes(metrics.bot_memory)}\n"
                f"Dashboard: **{dashboard_cpu}** • {dashboard_ram}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Load",
            value=f"1m {metrics.load_1m:.2f}\n5m {metrics.load_5m:.2f}\n15m {metrics.load_15m:.2f}",
            inline=True,
        )
        embed.add_field(
            name="RAM / Swap",
            value=(
                f"RAM: **{metrics.ram_percent:.1f}%**\n"
                f"Available: {human_bytes(metrics.ram_available)}\n"
                f"Swap: {metrics.swap_percent:.1f}%"
            ),
            inline=True,
        )
        embed.add_field(
            name="Network rate",
            value=f"↓ {human_bytes(metrics.network_rx_rate)}/s\n↑ {human_bytes(metrics.network_tx_rate)}/s",
            inline=True,
        )
        embed.add_field(name="Host uptime", value=human_duration(metrics.uptime_seconds), inline=True)
        embed.add_field(
            name="Sampler",
            value=f"Every {metrics.sample_interval_seconds}s • sample age {metrics.sample_age_seconds:.1f}s",
            inline=True,
        )
        embed.add_field(name="Throttle flags", value="None" if not flags else "\n".join(flags), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Pi-hole",
        emoji="🛡️",
        style=discord.ButtonStyle.secondary,
        custom_id="raspberry_bot:system:pihole",
    )
    async def pihole(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await collect_pihole_stats(force=True)
        status = "🟢 **FTL active**" if stats.active else "🔴 **FTL inactive**"
        blocking = "enabled" if stats.blocking is True else "disabled" if stats.blocking is False else "unknown"
        description = f"{status}\nBlocking: **{blocking}**"
        embed = EmbedFactory.system(title="Pi-hole Status", description=description)
        if stats.api_available:
            embed.add_field(
                name="Queries",
                value=(
                    f"Total: **{stats.total_queries or 0:,}**\n"
                    f"Blocked: **{stats.blocked_queries or 0:,}**\n"
                    f"Rate: **{(stats.percent_blocked or 0):.1f}%**"
                ),
                inline=True,
            )
            embed.add_field(
                name="DNS",
                value=(
                    f"Cached: **{stats.cached_queries or 0:,}**\n"
                    f"Forwarded: **{stats.forwarded_queries or 0:,}**\n"
                    f"Clients: **{stats.total_clients or stats.active_clients or 0:,}**"
                ),
                inline=True,
            )
            if stats.domains_blocked is not None:
                embed.add_field(name="Gravity", value=f"**{stats.domains_blocked:,}** blocked domains", inline=True)
        else:
            embed.add_field(name="Statistics", value="Detailed Pi-hole API counters are currently unavailable to the bot.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="24h Graph",
        emoji="📈",
        style=discord.ButtonStyle.secondary,
        custom_id="raspberry_bot:system:graph",
    )
    async def graph(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild_id is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.bot.database.fetchall(
            "SELECT cpu_percent, temperature, ram_percent, disk_percent, recorded_at FROM system_metrics "
            "WHERE guild_id = ? AND recorded_at >= datetime('now', '-24 hours') ORDER BY recorded_at ASC",
            (interaction.guild_id,),
        )
        data = [dict(row) for row in rows]
        image = await render_system_history(data)
        await interaction.followup.send(
            embed=EmbedFactory.system(title="24 Hour Health", description=f"Samples: **{len(data)}**"),
            file=discord.File(image, filename="homepi-24h.png"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Dashboard",
        emoji="🖥️",
        style=discord.ButtonStyle.secondary,
        custom_id="raspberry_bot:system:dashboard",
    )
    async def dashboard(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        owner_ids = getattr(getattr(self.bot, "settings", None), "owner_ids", set())
        if interaction.user.id not in owner_ids:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Owner only", description="The private dashboard link is only available to bot owners."),
                ephemeral=True,
            )
            return

        tailscale_ip = await asyncio.to_thread(_tailscale_ipv4)
        port = int(getattr(getattr(self.bot, "settings", None), "dashboard_port", 8080))
        hostname = socket.gethostname() or "homepi"
        lan_url = f"http://{hostname}.local:{port}"
        ts_url = f"http://{tailscale_ip}:{port}" if tailscale_ip else None

        links = discord.ui.View(timeout=120)
        if ts_url:
            links.add_item(discord.ui.Button(label="Tailscale", emoji="🔐", url=ts_url))
        links.add_item(discord.ui.Button(label="Home LAN", emoji="🏠", url=lan_url))
        text = f"LAN: `{lan_url}`"
        if ts_url:
            text += f"\nTailscale: `{ts_url}`"
        await interaction.response.send_message(
            embed=EmbedFactory.system(title="Private HomePi Dashboard", description=text),
            view=links,
            ephemeral=True,
        )
