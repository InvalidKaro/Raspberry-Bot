from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="avatar", description="Show a user's avatar in full resolution.")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        embed = EmbedFactory.info(title=f"Avatar • {target}")
        embed.set_image(url=target.display_avatar.with_size(1024).url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roleinfo", description="Show information about a role.")
    @app_commands.guild_only()
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role) -> None:
        embed = EmbedFactory.info(title=f"Role • {role.name}")
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Managed", value="Yes" if role.managed else "No", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="channelinfo", description="Show information about a channel.")
    @app_commands.guild_only()
    async def channelinfo(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Unsupported channel", description="Choose a text channel."), ephemeral=True)
            return
        embed = EmbedFactory.info(title=f"Channel • #{target.name}")
        embed.add_field(name="ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Category", value=target.category.name if target.category else "—", inline=True)
        embed.add_field(name="Slowmode", value=f"{target.slowmode_delay}s", inline=True)
        embed.add_field(name="Topic", value=target.topic or "—", inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
