from __future__ import annotations

import discord
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.welcome_templates import render_welcome_template


DEFAULT_WELCOME = "Welcome {user}! You are member #{member_count}."


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        settings = await self.bot.settings_repo.get_guild_settings(member.guild.id)

        role_id = settings.get("auto_role_id")
        if role_id:
            role = member.guild.get_role(int(role_id))
            me = member.guild.me
            if role is not None and me is not None and not role.managed and role < me.top_role:
                try:
                    await member.add_roles(role, reason="Raspberry-Bot automatic join role")
                except discord.HTTPException:
                    pass

        channel_id = settings.get("welcome_channel_id")
        if not channel_id:
            return
        channel = member.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        template = str(settings.get("welcome_message") or DEFAULT_WELCOME)
        embed = EmbedFactory.success(
            title=f"Welcome to {member.guild.name}",
            description=render_welcome_template(template, member, channel),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
