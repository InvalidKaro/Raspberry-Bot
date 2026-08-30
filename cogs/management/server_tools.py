from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


class ServerTools(commands.GroupCog, group_name="manage", group_description="Server management utilities"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    async def _role_error(interaction: discord.Interaction, message: str) -> None:
        await interaction.response.send_message(
            embed=EmbedFactory.error(title="Role action blocked", description=message),
            ephemeral=True,
        )

    @app_commands.command(name="role-add", description="Add a role to a member.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def role_add(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role) -> None:
        guild = interaction.guild
        if guild is None:
            return
        me = guild.me
        if role.is_default() or role.managed:
            await self._role_error(interaction, "That role cannot be assigned manually.")
            return
        if me is None or role >= me.top_role:
            await self._role_error(interaction, "The role is above or equal to the bot's highest role.")
            return
        try:
            await member.add_roles(role, reason=f"Dashboard bot command by {interaction.user} ({interaction.user.id})")
        except discord.HTTPException as exc:
            await self._role_error(interaction, f"Discord rejected the role change: {exc}")
            return
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Role added", description=f"Added {role.mention} to {member.mention}."),
            ephemeral=True,
        )

    @app_commands.command(name="role-remove", description="Remove a role from a member.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def role_remove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role) -> None:
        guild = interaction.guild
        if guild is None:
            return
        me = guild.me
        if role.is_default() or role.managed:
            await self._role_error(interaction, "That role cannot be removed manually.")
            return
        if me is None or role >= me.top_role:
            await self._role_error(interaction, "The role is above or equal to the bot's highest role.")
            return
        try:
            await member.remove_roles(role, reason=f"Dashboard bot command by {interaction.user} ({interaction.user.id})")
        except discord.HTTPException as exc:
            await self._role_error(interaction, f"Discord rejected the role change: {exc}")
            return
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Role removed", description=f"Removed {role.mention} from {member.mention}."),
            ephemeral=True,
        )

    @app_commands.command(name="nickname", description="Change or clear a member nickname.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_nicknames=True)
    async def nickname(self, interaction: discord.Interaction, member: discord.Member, nickname: str | None = None) -> None:
        value = nickname.strip()[:32] if nickname and nickname.strip() else None
        try:
            await member.edit(nick=value, reason=f"Nickname command by {interaction.user} ({interaction.user.id})")
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Nickname failed", description=str(exc)),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Nickname updated",
                description=f"{member.mention}: **{value or 'cleared'}**",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="announce", description="Send a clean announcement embed to a channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def announce(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str,
        message: str,
    ) -> None:
        embed = EmbedFactory.base(title=str(title), description=str(message))
        embed.set_footer(text=f"Announcement • {interaction.user}")
        try:
            sent = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Announcement failed", description=str(exc)),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Announcement sent", description=f"[Open message]({sent.jump_url}) in {channel.mention}."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerTools(bot))
