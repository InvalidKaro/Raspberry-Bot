from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


class Tools(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="timestamp", description="Create a Discord timestamp from an offset in minutes.")
    async def timestamp(
        self,
        interaction: discord.Interaction,
        minutes_from_now: app_commands.Range[int, -525600, 525600] = 0,
    ) -> None:
        target = datetime.now(UTC) + timedelta(minutes=int(minutes_from_now))
        unix = int(target.timestamp())
        embed = EmbedFactory.info(title="Discord Timestamp")
        embed.description = (
            f"Relative: `<t:{unix}:R>` → <t:{unix}:R>\n"
            f"Date/time: `<t:{unix}:F>` → <t:{unix}:F>\n"
            f"Short: `<t:{unix}:f>` → <t:{unix}:f>"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="snowflake", description="Decode a Discord snowflake ID into its creation time.")
    async def snowflake(self, interaction: discord.Interaction, snowflake_id: str) -> None:
        try:
            value = int(snowflake_id.strip())
            if value <= 0:
                raise ValueError
            created = discord.utils.snowflake_time(value)
        except (ValueError, OverflowError):
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Invalid snowflake", description="Enter a valid positive Discord ID."),
                ephemeral=True,
            )
            return
        unix = int(created.timestamp())
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Snowflake Information",
                description=f"ID: `{value}`\nCreated: <t:{unix}:F>\nRelative: <t:{unix}:R>",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="membercount", description="Show the server member, human and bot counts.")
    @app_commands.guild_only()
    async def membercount(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        cached = list(guild.members)
        bots = sum(1 for member in cached if member.bot)
        humans = max(0, len(cached) - bots)
        embed = EmbedFactory.info(title=f"Members • {guild.name}")
        embed.add_field(name="Total", value=str(guild.member_count or len(cached)), inline=True)
        embed.add_field(name="Humans", value=str(humans), inline=True)
        embed.add_field(name="Bots", value=str(bots), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="servericon", description="Show the current server icon in full resolution.")
    @app_commands.guild_only()
    async def servericon(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if guild.icon is None:
            await interaction.response.send_message(
                embed=EmbedFactory.info(title="Server Icon", description="This server has no custom icon."),
                ephemeral=True,
            )
            return
        embed = EmbedFactory.info(title=f"Server Icon • {guild.name}")
        embed.set_image(url=guild.icon.with_size(1024).url)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tools(bot))
