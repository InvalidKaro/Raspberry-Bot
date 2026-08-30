from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory
from helpers.formatting import human_bytes, human_duration
from services.system_metrics import SystemMetrics, throttling_labels


def _cpu_health(metrics: SystemMetrics) -> str:
    if metrics.cpu_average_30s >= 90:
        return "🔴"
    if metrics.cpu_average_30s >= 70:
        return "🟠"
    return "🟢"


def build_system_embed(metrics: SystemMetrics) -> discord.Embed:
    labels = throttling_labels(metrics.throttled_flags)
    temp = metrics.temperature
    healthy = (
        not labels
        and (temp is None or temp < 70)
        and metrics.ram_percent < 80
        and metrics.disk_percent < 85
        and metrics.cpu_average_30s < 80
    )
    embed = EmbedFactory.system(
        title="Raspberry Pi Status",
        description=(
            "🟢 **System healthy**" if healthy else "🟠 **System needs attention**"
        )
        + f"\nSampling every **{metrics.sample_interval_seconds}s** • sample age **{metrics.sample_age_seconds:.0f}s**",
    )

    temperature_text = f"**{temp:.1f} °C**" if temp is not None else "**Unavailable**"
    embed.add_field(
        name="🌡️ System / CPU",
        value=(
            f"{temperature_text}\n"
            f"{_cpu_health(metrics)} Current: **{metrics.cpu_percent:.1f}%**\n"
            f"30s avg: **{metrics.cpu_average_30s:.1f}%**\n"
            f"5m avg: **{metrics.cpu_average_5m:.1f}%**\n"
            f"Load: {metrics.load_1m:.2f} / {metrics.load_5m:.2f} / {metrics.load_15m:.2f}"
        ),
        inline=True,
    )

    dashboard_cpu = "—" if metrics.dashboard_cpu_percent is None else f"{metrics.dashboard_cpu_percent:.1f}%"
    dashboard_ram = "—" if metrics.dashboard_memory is None else human_bytes(metrics.dashboard_memory)
    embed.add_field(
        name="🤖 Processes",
        value=(
            f"Bot CPU: **{metrics.bot_cpu_percent:.1f}%**\n"
            f"Bot RAM: **{human_bytes(metrics.bot_memory)}**\n"
            f"Dashboard CPU: **{dashboard_cpu}**\n"
            f"Dashboard RAM: **{dashboard_ram}**"
        ),
        inline=True,
    )

    embed.add_field(
        name="🧠 Memory",
        value=(
            f"RAM: **{metrics.ram_percent:.1f}%**\n"
            f"Used: {human_bytes(metrics.ram_used)} / {human_bytes(metrics.ram_total)}\n"
            f"Available: **{human_bytes(metrics.ram_available)}**\n"
            f"Swap: {metrics.swap_percent:.1f}% ({human_bytes(metrics.swap_used)})"
        ),
        inline=True,
    )

    embed.add_field(
        name="💾 Storage",
        value=(
            f"Usage: **{metrics.disk_percent:.1f}%**\n"
            f"{human_bytes(metrics.disk_used)} / {human_bytes(metrics.disk_total)}"
        ),
        inline=True,
    )

    embed.add_field(
        name="🌐 Network",
        value=(
            f"RX: {human_bytes(metrics.network_rx)} total\n"
            f"TX: {human_bytes(metrics.network_tx)} total\n"
            f"Rate: ↓ {human_bytes(metrics.network_rx_rate)}/s • ↑ {human_bytes(metrics.network_tx_rate)}/s"
        ),
        inline=True,
    )

    if metrics.pihole_active:
        pihole = "🟢 FTL active"
        if metrics.pihole_blocking is True:
            pihole += "\n🛡️ Blocking enabled"
        elif metrics.pihole_blocking is False:
            pihole += "\n⚠️ Blocking disabled"
    else:
        pihole = "🔴 FTL inactive"
    embed.add_field(name="🛡️ Pi-hole", value=pihole, inline=True)

    embed.add_field(
        name="⚡ Power / Throttling",
        value="✅ No flags" if not labels else "\n".join(f"• {label}" for label in labels),
        inline=True,
    )
    embed.add_field(name="⏱️ Host uptime", value=human_duration(metrics.uptime_seconds), inline=True)
    if metrics.cpu_frequency_mhz is not None:
        embed.add_field(name="CPU frequency", value=f"{metrics.cpu_frequency_mhz:.0f} MHz", inline=True)
    return embed
