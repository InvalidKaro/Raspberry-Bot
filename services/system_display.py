from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory
from helpers.formatting import human_bytes, human_duration
from services.system_metrics import SystemMetrics, throttling_labels


def build_system_embed(metrics: SystemMetrics) -> discord.Embed:
    labels = throttling_labels(metrics.throttled_flags)
    temp = metrics.temperature
    healthy = not labels and (temp is None or temp < 70) and metrics.ram_percent < 80 and metrics.disk_percent < 85
    embed = EmbedFactory.system(
        title="Raspberry Pi Status",
        description="🟢 **System healthy**" if healthy else "🟠 **System needs attention**",
    )
    temperature_text = f"**{temp:.1f} °C**\n" if temp is not None else "**Unavailable**\n"
    embed.add_field(
        name="🌡️ Temperature / CPU",
        value=temperature_text + f"CPU: {metrics.cpu_percent:.1f}%\nLoad: {metrics.load_1m:.2f} / {metrics.load_5m:.2f} / {metrics.load_15m:.2f}",
        inline=True,
    )
    embed.add_field(
        name="🧠 Memory",
        value=f"RAM: **{metrics.ram_percent:.1f}%**\n{human_bytes(metrics.ram_used)} / {human_bytes(metrics.ram_total)}\nBot: {human_bytes(metrics.bot_memory)}",
        inline=True,
    )
    embed.add_field(
        name="💾 Storage",
        value=f"Usage: **{metrics.disk_percent:.1f}%**\n{human_bytes(metrics.disk_used)} / {human_bytes(metrics.disk_total)}",
        inline=True,
    )
    embed.add_field(
        name="🌐 Network",
        value=f"RX: {human_bytes(metrics.network_rx)}\nTX: {human_bytes(metrics.network_tx)}",
        inline=True,
    )
    embed.add_field(
        name="🛡️ Pi-hole",
        value="🟢 FTL active" if metrics.pihole_active else "🔴 FTL inactive",
        inline=True,
    )
    embed.add_field(
        name="⚡ Power / Throttling",
        value="✅ No flags" if not labels else "\n".join(f"• {label}" for label in labels),
        inline=True,
    )
    embed.add_field(name="⏱️ Host uptime", value=human_duration(metrics.uptime_seconds), inline=True)
    if metrics.cpu_frequency_mhz is not None:
        embed.add_field(name="CPU frequency", value=f"{metrics.cpu_frequency_mhz:.0f} MHz", inline=True)
    return embed
