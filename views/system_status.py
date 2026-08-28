from __future__ import annotations

import discord

from helpers.embeds import EmbedFactory
from helpers.formatting import human_bytes, human_duration
from services.system_charts import render_system_history
from services.system_display import build_system_embed
from services.system_metrics import collect_system_metrics, throttling_labels


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
        metrics = await collect_system_metrics()
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
        metrics = await collect_system_metrics()
        flags = throttling_labels(metrics.throttled_flags)
        embed = EmbedFactory.system(title="Raspberry Pi Details")
        embed.add_field(name="CPU", value=f"{metrics.cpu_percent:.1f}%\n{metrics.cpu_frequency_mhz or 0:.0f} MHz", inline=True)
        embed.add_field(name="Load", value=f"1m {metrics.load_1m:.2f}\n5m {metrics.load_5m:.2f}\n15m {metrics.load_15m:.2f}", inline=True)
        embed.add_field(name="RAM", value=f"{metrics.ram_percent:.1f}%\n{human_bytes(metrics.ram_used)} / {human_bytes(metrics.ram_total)}", inline=True)
        embed.add_field(name="Bot RSS", value=human_bytes(metrics.bot_memory), inline=True)
        embed.add_field(name="Host uptime", value=human_duration(metrics.uptime_seconds), inline=True)
        embed.add_field(name="Throttle flags", value="None" if not flags else "\n".join(flags), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Pi-hole",
        emoji="🛡️",
        style=discord.ButtonStyle.secondary,
        custom_id="raspberry_bot:system:pihole",
    )
    async def pihole(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        metrics = await collect_system_metrics()
        status = "🟢 **pihole-FTL is active**" if metrics.pihole_active else "🔴 **pihole-FTL is not active**"
        await interaction.response.send_message(
            embed=EmbedFactory.system(
                title="Pi-hole Status",
                description=(
                    f"{status}\n\n"
                    "Raspberry-Bot only reads the local service state. It never changes your Pi-hole DNS configuration."
                ),
            ),
            ephemeral=True,
        )

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
