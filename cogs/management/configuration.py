from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database.repositories.settings import SettingsRepository
from helpers.embeds import EmbedFactory
from services.welcome_templates import placeholder_help_text, render_welcome_template


class Setup(commands.GroupCog, group_name="setup", group_description="Configure Raspberry-Bot for this server"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repo = SettingsRepository(bot.database, bot.cache)

    @app_commands.command(name="tickets", description="Configure the category and log channel for the ticket system.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def tickets(self, interaction: discord.Interaction, category: discord.CategoryChannel, log_channel: discord.TextChannel) -> None:
        if interaction.guild_id is None:
            return
        await self.repo.update_guild_settings(
            interaction.guild_id,
            ticket_category_id=category.id,
            ticket_log_channel_id=log_channel.id,
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Ticket system configured",
                description=f"Category: **{category.name}**\nLogs: {log_channel.mention}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="staff-add", description="Add a role that may claim and manage tickets.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def staff_add(self, interaction: discord.Interaction, role: discord.Role, permission_level: app_commands.Range[int, 1, 100] = 10) -> None:
        if interaction.guild_id is None:
            return
        await self.repo.add_ticket_staff_role(interaction.guild_id, role.id, int(permission_level))
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Ticket staff role added", description=f"{role.mention} • level {permission_level}"),
            ephemeral=True,
        )

    @app_commands.command(name="staff-remove", description="Remove a ticket staff role.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def staff_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild_id is None:
            return
        await self.repo.remove_ticket_staff_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Ticket staff role removed", description=role.mention),
            ephemeral=True,
        )

    @app_commands.command(name="welcome", description="Set or disable the welcome channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def welcome(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        if interaction.guild_id is None:
            return
        await self.repo.update_guild_settings(interaction.guild_id, welcome_channel_id=channel.id if channel else None)
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Welcome configuration updated",
                description=f"Welcome channel: {channel.mention if channel else '**Disabled**'}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="welcome-message", description="Set a Dyno-style welcome template with {user}, {server} and more.")
    @app_commands.describe(message="Template text. Leave empty to restore the default welcome message.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def welcome_message(self, interaction: discord.Interaction, message: str | None = None) -> None:
        if interaction.guild_id is None:
            return
        cleaned = str(message).strip() if message else None
        await self.repo.update_guild_settings(interaction.guild_id, welcome_message=cleaned or None)
        description = (
            "Custom welcome template saved. Use `/setup welcome-preview` to test it and "
            "`/setup welcome-placeholders` to see every available argument."
            if cleaned
            else "Custom welcome message cleared; the default template will be used."
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Welcome message updated", description=description),
            ephemeral=True,
        )

    @app_commands.command(name="welcome-preview", description="Preview the configured welcome message with real member data.")
    @app_commands.describe(member="Member whose data should be inserted into the placeholders.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def welcome_preview(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        if interaction.guild is None or interaction.guild_id is None:
            return
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Preview unavailable", description="Run this command inside a server."),
                ephemeral=True,
            )
            return
        data = await self.repo.get_guild_settings(interaction.guild_id)
        channel = interaction.guild.get_channel(int(data["welcome_channel_id"])) if data.get("welcome_channel_id") else interaction.channel
        template = str(data.get("welcome_message") or "Welcome {user}! You are member #{member_count}.")
        embed = EmbedFactory.success(
            title=f"Welcome to {interaction.guild.name}",
            description=render_welcome_template(template, target, channel if isinstance(channel, discord.abc.GuildChannel) else None),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Template", value=f"```text\n{template[:900]}\n```", inline=False)
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="welcome-placeholders", description="Show all available welcome-message arguments/placeholders.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def welcome_placeholders(self, interaction: discord.Interaction) -> None:
        embed = EmbedFactory.info(
            title="Welcome message placeholders",
            description=(
                "Use these directly inside `/setup welcome-message`. Unknown placeholders are left visible so typos are easy to spot.\n\n"
                + placeholder_help_text()
            ),
        )
        embed.add_field(
            name="Example",
            value="`Willkommen {user} auf {server}! Du bist Mitglied #{member_count}.`",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="autorole", description="Set or disable the automatic role for new members.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def autorole(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        if interaction.guild_id is None:
            return
        if role is not None:
            guild = interaction.guild
            me = guild.me if guild else None
            if role.managed or role.is_default() or me is None or role >= me.top_role:
                await interaction.response.send_message(
                    embed=EmbedFactory.error(title="Invalid auto role", description="Choose a normal role below the bot's highest role."),
                    ephemeral=True,
                )
                return
        await self.repo.update_guild_settings(interaction.guild_id, auto_role_id=role.id if role else None)
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Auto role updated",
                description=f"New members receive {role.mention}." if role else "Automatic join role disabled.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="suggestions", description="Set or disable the suggestions channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def suggestions(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        if interaction.guild_id is None:
            return
        await self.repo.update_guild_settings(interaction.guild_id, suggestion_channel_id=channel.id if channel else None)
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Suggestion configuration updated",
                description=f"Suggestion channel: {channel.mention if channel else '**Disabled**'}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="logs", description="Set or disable the general audit log channel.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def logs(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        if interaction.guild_id is None:
            return
        await self.repo.update_guild_settings(interaction.guild_id, general_log_channel_id=channel.id if channel else None)
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Audit logging updated",
                description=f"Log channel: {channel.mention if channel else '**Disabled**'}",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="show", description="Show the current server configuration.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def show(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        data = await self.repo.get_guild_settings(interaction.guild_id)
        roles = await self.repo.list_ticket_staff_roles(interaction.guild_id)
        embed = EmbedFactory.info(title="Server Configuration")
        embed.add_field(name="Ticket category", value=f"<#{data['ticket_category_id']}>" if data.get("ticket_category_id") else "—", inline=True)
        embed.add_field(name="Ticket logs", value=f"<#{data['ticket_log_channel_id']}>" if data.get("ticket_log_channel_id") else "—", inline=True)
        embed.add_field(name="Welcome", value=f"<#{data['welcome_channel_id']}>" if data.get("welcome_channel_id") else "Disabled", inline=True)
        embed.add_field(name="Auto role", value=f"<@&{data['auto_role_id']}>" if data.get("auto_role_id") else "Disabled", inline=True)
        embed.add_field(name="Suggestions", value=f"<#{data['suggestion_channel_id']}>" if data.get("suggestion_channel_id") else "Disabled", inline=True)
        embed.add_field(name="Audit logs", value=f"<#{data['general_log_channel_id']}>" if data.get("general_log_channel_id") else "Disabled", inline=True)
        role_text = "\n".join(f"<@&{role_id}>" for role_id in roles[:20]) or "—"
        if len(roles) > 20:
            role_text += f"\n…and {len(roles) - 20} more"
        embed.add_field(name="Ticket staff roles", value=role_text, inline=False)
        embed.add_field(name="Welcome message", value=str(data.get("welcome_message") or "Default")[:1000], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))
