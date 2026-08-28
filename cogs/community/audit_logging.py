from __future__ import annotations

import discord
from discord.ext import commands

from helpers.embeds import EmbedFactory


class AuditLogging(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        settings = await self.bot.settings_repo.get_guild_settings(guild.id)
        channel_id = settings.get("general_log_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _send(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await self._channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        embed = EmbedFactory.warning(title="Message Deleted")
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Content", value=(message.content or "—")[:1000], inline=False)
        await self._send(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        embed = EmbedFactory.info(title="Message Edited")
        embed.add_field(name="Author", value=before.author.mention, inline=True)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="Before", value=(before.content or "—")[:900], inline=False)
        embed.add_field(name="After", value=(after.content or "—")[:900], inline=False)
        embed.add_field(name="Jump", value=after.jump_url, inline=False)
        await self._send(before.guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        embed = EmbedFactory.success(title="Member Joined", description=f"{member.mention} • `{member.id}`")
        await self._send(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        embed = EmbedFactory.warning(title="Member Left", description=f"**{member}** • `{member.id}`")
        await self._send(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        changes: list[str] = []
        if before.nick != after.nick:
            changes.append(f"Nickname: `{before.nick or '—'}` → `{after.nick or '—'}`")
        before_roles = {role.id for role in before.roles}
        after_roles = {role.id for role in after.roles}
        added = [role.mention for role in after.roles if role.id not in before_roles]
        removed = [role.mention for role in before.roles if role.id not in after_roles]
        if added:
            changes.append("Roles added: " + ", ".join(added[:20]))
        if removed:
            changes.append("Roles removed: " + ", ".join(removed[:20]))
        if not changes:
            return
        embed = EmbedFactory.info(title="Member Updated", description=(f"{after.mention}\n" + "\n".join(changes))[:4000])
        await self._send(after.guild, embed)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel == after.channel:
            return
        if before.channel is None and after.channel is not None:
            text = f"{member.mention} joined **{after.channel.name}**."
        elif before.channel is not None and after.channel is None:
            text = f"{member.mention} left **{before.channel.name}**."
        else:
            text = f"{member.mention} moved **{before.channel.name if before.channel else '—'}** → **{after.channel.name if after.channel else '—'}**."
        await self._send(member.guild, EmbedFactory.info(title="Voice Activity", description=text))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(channel.guild, EmbedFactory.success(title="Channel Created", description=f"**{channel.name}** • `{channel.id}`"))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(channel.guild, EmbedFactory.warning(title="Channel Deleted", description=f"**{channel.name}** • `{channel.id}`"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AuditLogging(bot))
