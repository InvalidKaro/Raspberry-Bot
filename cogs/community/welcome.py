from __future__ import annotations

import discord
from discord.ext import commands

from helpers.embeds import EmbedFactory


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        settings = await self.bot.settings_repo.get_guild_settings(member.guild.id)
        channel_id = settings.get("welcome_channel_id")
        if not channel_id:
            return
        channel = member.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        embed = EmbedFactory.success(
            title=f"Welcome to {member.guild.name}",
            description=f"Welcome {member.mention}! You are member **#{member.guild.member_count or 0}**.",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
