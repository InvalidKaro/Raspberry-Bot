from __future__ import annotations

import platform
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from helpers.formatting import human_duration
from services.system_display import build_system_embed
from services.system_metrics import collect_system_metrics
from views.system_status import SystemStatusView


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Show Discord latency, bot uptime and runtime information.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency = max(self.bot.latency * 1000, 0.0)
        started_at = getattr(self.bot, "started_at", datetime.now(UTC))
        uptime = max((datetime.now(UTC) - started_at).total_seconds(), 0)
        embed = EmbedFactory.info(title="Pong")
        embed.add_field(name="Gateway", value=f"**{latency:.1f} ms**", inline=True)
        embed.add_field(name="Bot uptime", value=human_duration(uptime), inline=True)
        embed.add_field(name="Guilds", value=f"**{len(self.bot.guilds)}**", inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Runtime", value=f"{platform.machine()} • {platform.system()}", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="status", description="Show detailed Raspberry Pi, bot and Pi-hole health information.")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        metrics = await collect_system_metrics(self.bot)
        embed = build_system_embed(metrics)
        started_at = getattr(self.bot, "started_at", datetime.now(UTC))
        bot_uptime = max((datetime.now(UTC) - started_at).total_seconds(), 0)
        embed.add_field(
            name="🤖 Discord Bot",
            value=(
                f"Gateway: **{max(self.bot.latency * 1000, 0):.1f} ms**\n"
                f"Uptime: **{human_duration(bot_uptime)}**\n"
                f"Guilds: **{len(self.bot.guilds)}**"
            ),
            inline=True,
        )
        await interaction.followup.send(embed=embed, view=SystemStatusView(self.bot))

    @app_commands.command(name="userinfo", description="Show detailed information about a server member.")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            return

        embed = EmbedFactory.info(title=f"User • {target}")
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="User", value=f"{target.mention}\n`{target.id}`", inline=True)
        embed.add_field(name="Display name", value=target.display_name, inline=True)
        embed.add_field(name="Bot account", value="Yes" if target.bot else "No", inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(target.created_at, style="F") + "\n" + discord.utils.format_dt(target.created_at, style="R"), inline=True)
        if target.joined_at:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(target.joined_at, style="F") + "\n" + discord.utils.format_dt(target.joined_at, style="R"), inline=True)
        embed.add_field(name="Top role", value=target.top_role.mention if target.top_role != target.guild.default_role else "@everyone", inline=True)
        embed.add_field(name="Role count", value=str(max(len(target.roles) - 1, 0)), inline=True)
        embed.add_field(name="Server owner", value="Yes" if target.id == target.guild.owner_id else "No", inline=True)
        embed.add_field(name="Boosting", value=discord.utils.format_dt(target.premium_since, style="R") if target.premium_since else "No", inline=True)
        embed.add_field(name="Timed out", value=discord.utils.format_dt(target.timed_out_until, style="R") if target.timed_out_until else "No", inline=True)

        key_permissions = []
        permissions = target.guild_permissions
        for label, enabled in (
            ("Administrator", permissions.administrator),
            ("Manage Guild", permissions.manage_guild),
            ("Manage Channels", permissions.manage_channels),
            ("Manage Roles", permissions.manage_roles),
            ("Moderate", permissions.moderate_members),
            ("Ban", permissions.ban_members),
            ("Kick", permissions.kick_members),
        ):
            if enabled:
                key_permissions.append(label)
        embed.add_field(name="Key permissions", value=", ".join(key_permissions) if key_permissions else "None", inline=False)

        roles = [role.mention for role in reversed(target.roles[1:])]
        if roles:
            value = " ".join(roles)
            if len(value) > 950:
                value = value[:947] + "…"
            embed.add_field(name="Roles", value=value, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Show detailed information about this Discord server.")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return

        humans = sum(1 for member in guild.members if not member.bot)
        bots = sum(1 for member in guild.members if member.bot)
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        forum_channels = sum(1 for channel in guild.channels if isinstance(channel, discord.ForumChannel))

        embed = EmbedFactory.info(title=guild.name)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        owner = guild.owner.mention if guild.owner else f"`{guild.owner_id}`"
        embed.add_field(name="Owner", value=owner, inline=True)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(
            name="Members",
            value=f"Total: **{guild.member_count or len(guild.members)}**\nHumans: {humans}\nBots: {bots}",
            inline=True,
        )
        embed.add_field(
            name="Channels",
            value=f"Text: **{text_channels}**\nVoice: **{voice_channels}**\nForums: **{forum_channels}**\nCategories: **{categories}**",
            inline=True,
        )
        embed.add_field(name="Roles", value=f"**{len(guild.roles)}**", inline=True)
        embed.add_field(
            name="Boosts",
            value=f"Level: **{guild.premium_tier}**\nBoosts: **{guild.premium_subscription_count}**",
            inline=True,
        )
        embed.add_field(name="Verification", value=str(guild.verification_level).replace("_", " ").title(), inline=True)
        embed.add_field(name="Filesize limit", value=f"{guild.filesize_limit / 1024 / 1024:.0f} MB", inline=True)
        features = [feature.replace("_", " ").title() for feature in guild.features]
        if features:
            feature_text = ", ".join(features)
            embed.add_field(name="Features", value=feature_text[:1000], inline=False)
        if guild.description:
            embed.add_field(name="Description", value=guild.description[:1000], inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
