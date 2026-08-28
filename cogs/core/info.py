from __future__ import annotations

import platform

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from helpers.formatting import human_bytes, human_duration
from services.system_metrics import collect_system_metrics, throttling_labels


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Show the current Discord gateway latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency = self.bot.latency * 1000
        await interaction.response.send_message(
            embed=EmbedFactory.info(title="Pong", description=f"Gateway latency: **{latency:.1f} ms**")
        )

    @app_commands.command(name="status", description="Show Raspberry Pi and bot health information.")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        metrics = await collect_system_metrics()
        temp = f"{metrics.temperature:.1f} °C" if metrics.temperature is not None else "Unavailable"
        throttling = throttling_labels(metrics.throttled_flags)
        health = "🟢 Healthy" if not throttling and (metrics.temperature or 0) < 70 else "🟠 Attention"
        embed = EmbedFactory.system(title="Raspberry Pi Status", description=health)
        embed.add_field(
            name="🌡️ Temperature",
            value=f"**{temp}**\nCPU: {metrics.cpu_percent:.1f}%\nLoad: {metrics.load_1m:.2f}",
            inline=True,
        )
        embed.add_field(
            name="🧠 Memory",
            value=f"System: **{metrics.ram_percent:.1f}%**\nUsed: {human_bytes(metrics.ram_used)}\nBot: {human_bytes(metrics.bot_memory)}",
            inline=True,
        )
        embed.add_field(
            name="💾 Storage",
            value=f"Usage: **{metrics.disk_percent:.1f}%**\nUsed: {human_bytes(metrics.disk_used)}\nTotal: {human_bytes(metrics.disk_total)}",
            inline=True,
        )
        embed.add_field(
            name="🌐 Network",
            value=f"RX: {human_bytes(metrics.network_rx)}\nTX: {human_bytes(metrics.network_tx)}",
            inline=True,
        )
        embed.add_field(
            name="🛡️ Pi-hole",
            value="🟢 FTL active" if metrics.pihole_active else "🔴 FTL not active",
            inline=True,
        )
        embed.add_field(
            name="⚡ Throttling",
            value="None" if not throttling else "\n".join(throttling),
            inline=True,
        )
        embed.add_field(name="⏱️ Host uptime", value=human_duration(metrics.uptime_seconds), inline=True)
        embed.add_field(
            name="Runtime",
            value=f"Python {platform.python_version()}\n{platform.machine()} • {platform.system()}",
            inline=True,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="userinfo", description="Show information about a server member.")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            return
        embed = EmbedFactory.info(title="User Information")
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="User", value=target.mention, inline=True)
        embed.add_field(name="ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Display name", value=target.display_name, inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(target.created_at, style="R"), inline=True)
        if target.joined_at:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(target.joined_at, style="R"), inline=True)
        embed.add_field(name="Roles", value=str(max(len(target.roles) - 1, 0)), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Show information about this Discord server.")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        embed = EmbedFactory.info(title=guild.name)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Members", value=str(guild.member_count or 0), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Boosts", value=str(guild.premium_subscription_count), inline=True)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
