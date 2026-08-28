from __future__ import annotations

from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from database.repositories.moderation import ModerationRepository
from helpers.embeds import EmbedFactory


def _can_target(actor: discord.Member, target: discord.Member, guild: discord.Guild) -> bool:
    if target.id == guild.owner_id:
        return False
    if actor.id == guild.owner_id:
        return True
    return actor.top_role > target.top_role


class Moderation(commands.GroupCog, group_name="mod", group_description="Moderation and case management"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repo = ModerationRepository(bot.database)

    async def _require_member(self, interaction: discord.Interaction) -> tuple[discord.Guild, discord.Member]:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise RuntimeError("This command is only available in servers.")
        return interaction.guild, interaction.user

    @app_commands.command(name="warn", description="Warn a member and create a moderation case.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        guild, actor = await self._require_member(interaction)
        if not _can_target(actor, member, guild):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Hierarchy check failed", description="You cannot moderate this member."), ephemeral=True)
            return
        case_id = await self.repo.create_case(
            guild_id=guild.id,
            user_id=member.id,
            moderator_id=actor.id,
            action="warn",
            reason=reason[:1000],
        )
        try:
            await member.send(embed=EmbedFactory.moderation(title=f"Warning in {guild.name}", description=f"**Reason:** {reason}\n**Case:** #{case_id}"))
        except discord.HTTPException:
            pass
        await interaction.response.send_message(embed=EmbedFactory.moderation(title=f"Case #{case_id} • Warning", description=f"User: {member.mention}\nModerator: {actor.mention}\nReason: {reason}"))

    @app_commands.command(name="warnings", description="Show recent moderation cases for a member.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild_id is None:
            return
        cases = await self.repo.get_user_cases(interaction.guild_id, member.id, 20)
        if not cases:
            await interaction.response.send_message(embed=EmbedFactory.moderation(title="Moderation History", description=f"{member.mention} has no stored cases."), ephemeral=True)
            return
        lines = [
            f"**#{int(case['id'])}** • `{case['action']}` • <@{case['moderator_id']}>\n└ {str(case.get('reason') or 'No reason')[:300]}"
            for case in cases[:10]
        ]
        await interaction.response.send_message(embed=EmbedFactory.moderation(title=f"History • {member}", description="\n\n".join(lines)), ephemeral=True)

    @app_commands.command(name="case", description="Show a stored moderation case by ID.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def case(self, interaction: discord.Interaction, case_id: int) -> None:
        if interaction.guild_id is None:
            return
        case = await self.repo.get_case(interaction.guild_id, case_id)
        if case is None:
            await interaction.response.send_message(embed=EmbedFactory.error(title="Case not found", description=f"No case `#{case_id}` exists in this server."), ephemeral=True)
            return
        embed = EmbedFactory.moderation(title=f"Case #{case_id}")
        embed.add_field(name="Action", value=str(case["action"]), inline=True)
        embed.add_field(name="User", value=f"<@{int(case['user_id'])}>", inline=True)
        embed.add_field(name="Moderator", value=f"<@{int(case['moderator_id'])}>", inline=True)
        embed.add_field(name="Active", value="Yes" if int(case["active"]) else "No", inline=True)
        embed.add_field(name="Reason", value=str(case.get("reason") or "No reason"), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="unwarn", description="Deactivate a stored warning case.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def unwarn(self, interaction: discord.Interaction, case_id: int) -> None:
        if interaction.guild_id is None:
            return
        case = await self.repo.get_case(interaction.guild_id, case_id)
        if case is None or str(case["action"]) != "warn":
            await interaction.response.send_message(embed=EmbedFactory.error(title="Warning not found", description="The supplied case is not a warning in this server."), ephemeral=True)
            return
        await self.repo.deactivate_case(interaction.guild_id, case_id)
        await interaction.response.send_message(embed=EmbedFactory.success(title="Warning deactivated", description=f"Case `#{case_id}` is now inactive."), ephemeral=True)

    @app_commands.command(name="timeout", description="Timeout a member for a number of minutes.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: str = "No reason provided",
    ) -> None:
        guild, actor = await self._require_member(interaction)
        if not _can_target(actor, member, guild):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Hierarchy check failed", description="You cannot moderate this member."), ephemeral=True)
            return
        until = datetime.now(UTC) + timedelta(minutes=int(minutes))
        await member.timeout(until, reason=f"{actor}: {reason}")
        case_id = await self.repo.create_case(
            guild_id=guild.id,
            user_id=member.id,
            moderator_id=actor.id,
            action="timeout",
            reason=reason[:1000],
            duration_seconds=int(minutes) * 60,
            expires_at=until.isoformat(),
        )
        await interaction.response.send_message(embed=EmbedFactory.moderation(title=f"Case #{case_id} • Timeout", description=f"User: {member.mention}\nDuration: **{minutes} min**\nReason: {reason}"))

    @app_commands.command(name="untimeout", description="Remove a member timeout.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: str = "Timeout removed") -> None:
        guild, actor = await self._require_member(interaction)
        if not _can_target(actor, member, guild):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Hierarchy check failed", description="You cannot moderate this member."), ephemeral=True)
            return
        await member.timeout(None, reason=f"{actor}: {reason}")
        case_id = await self.repo.create_case(guild_id=guild.id, user_id=member.id, moderator_id=actor.id, action="untimeout", reason=reason[:1000])
        await interaction.response.send_message(embed=EmbedFactory.moderation(title=f"Case #{case_id} • Timeout removed", description=f"User: {member.mention}\nReason: {reason}"))

    @app_commands.command(name="kick", description="Kick a member from the server.")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided") -> None:
        guild, actor = await self._require_member(interaction)
        if not _can_target(actor, member, guild):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Hierarchy check failed", description="You cannot moderate this member."), ephemeral=True)
            return
        case_id = await self.repo.create_case(guild_id=guild.id, user_id=member.id, moderator_id=actor.id, action="kick", reason=reason[:1000])
        await member.kick(reason=f"{actor}: {reason}")
        await interaction.response.send_message(embed=EmbedFactory.moderation(title=f"Case #{case_id} • Kick", description=f"User: **{member}**\nReason: {reason}"))

    @app_commands.command(name="ban", description="Ban a member from the server.")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided", delete_message_hours: app_commands.Range[int, 0, 168] = 0) -> None:
        guild, actor = await self._require_member(interaction)
        if not _can_target(actor, member, guild):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Hierarchy check failed", description="You cannot moderate this member."), ephemeral=True)
            return
        case_id = await self.repo.create_case(guild_id=guild.id, user_id=member.id, moderator_id=actor.id, action="ban", reason=reason[:1000])
        await guild.ban(member, reason=f"{actor}: {reason}", delete_message_seconds=int(delete_message_hours) * 3600)
        await interaction.response.send_message(embed=EmbedFactory.moderation(title=f"Case #{case_id} • Ban", description=f"User: **{member}**\nReason: {reason}"))

    @app_commands.command(name="unban", description="Unban a user by user ID.")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "Unbanned") -> None:
        guild, actor = await self._require_member(interaction)
        try:
            target_id = int(user_id)
            user = await self.bot.fetch_user(target_id)
        except (ValueError, discord.HTTPException):
            await interaction.response.send_message(embed=EmbedFactory.error(title="Invalid user", description="Provide a valid Discord user ID."), ephemeral=True)
            return
        await guild.unban(user, reason=f"{actor}: {reason}")
        case_id = await self.repo.create_case(guild_id=guild.id, user_id=user.id, moderator_id=actor.id, action="unban", reason=reason[:1000])
        await interaction.response.send_message(embed=EmbedFactory.moderation(title=f"Case #{case_id} • Unban", description=f"User: **{user}**\nReason: {reason}"))

    @app_commands.command(name="clear", description="Delete recent messages from the current text channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 200]) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=int(amount), reason=f"Clear command by {interaction.user}")
        await interaction.followup.send(embed=EmbedFactory.success(title="Messages cleared", description=f"Deleted **{len(deleted)}** messages."), ephemeral=True)

    @app_commands.command(name="lock", description="Lock the current text channel for the default role.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Locked by {interaction.user}")
        await interaction.response.send_message(embed=EmbedFactory.moderation(title="Channel locked", description=interaction.channel.mention))

    @app_commands.command(name="unlock", description="Unlock the current text channel for the default role.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {interaction.user}")
        await interaction.response.send_message(embed=EmbedFactory.success(title="Channel unlocked", description=interaction.channel.mention))

    @app_commands.command(name="slowmode", description="Set slowmode for the current text channel in seconds.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        await interaction.channel.edit(slowmode_delay=int(seconds), reason=f"Slowmode changed by {interaction.user}")
        await interaction.response.send_message(embed=EmbedFactory.success(title="Slowmode updated", description=f"New delay: **{seconds}s**"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
