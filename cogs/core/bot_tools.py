from __future__ import annotations

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from helpers.formatting import human_duration


def _all_app_commands(bot: commands.Bot) -> list[app_commands.Command | app_commands.Group]:
    result: list[app_commands.Command | app_commands.Group] = []
    for root in bot.tree.get_commands():
        result.append(root)
        if isinstance(root, app_commands.Group):
            result.extend(list(root.walk_commands()))
    return result


class BotTools(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="botinfo", description="Show detailed information about Raspberry-Bot.")
    async def botinfo(self, interaction: discord.Interaction) -> None:
        user = self.bot.user
        started_at = getattr(self.bot, "started_at", datetime.now(UTC))
        uptime = max((datetime.now(UTC) - started_at).total_seconds(), 0)
        commands_list = _all_app_commands(self.bot)
        members = sum(guild.member_count or len(guild.members) for guild in self.bot.guilds)

        embed = EmbedFactory.info(title="Raspberry-Bot")
        if user is not None:
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="Bot", value=f"{user.mention}\n`{user.id}`", inline=True)
        embed.add_field(name="Gateway latency", value=f"**{max(self.bot.latency * 1000, 0):.1f} ms**", inline=True)
        embed.add_field(name="Uptime", value=human_duration(uptime), inline=True)
        embed.add_field(name="Guilds", value=f"**{len(self.bot.guilds)}**", inline=True)
        embed.add_field(name="Visible members", value=f"**{members:,}**", inline=True)
        embed.add_field(name="Application commands", value=f"**{len(commands_list)}**", inline=True)
        embed.add_field(name="Loaded extensions", value=f"**{len(self.bot.extensions)}**", inline=True)
        embed.add_field(name="Owners configured", value=f"**{len(self.bot.settings.owner_ids)}**", inline=True)
        embed.add_field(name="Environment", value=f"`{self.bot.settings.environment}`", inline=True)
        embed.add_field(name="Database", value=f"`{self.bot.settings.database_path}`", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="permissions", description="Check Raspberry-Bot permissions in the current channel.")
    @app_commands.guild_only()
    async def permissions(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None or guild.me is None:
            return
        perms = channel.permissions_for(guild.me)
        checks = [
            ("View channel", perms.view_channel),
            ("Send messages", perms.send_messages),
            ("Embed links", perms.embed_links),
            ("Attach files", perms.attach_files),
            ("Read history", perms.read_message_history),
            ("Manage messages", perms.manage_messages),
            ("Manage channels", perms.manage_channels),
            ("Manage roles", perms.manage_roles),
            ("Moderate members", perms.moderate_members),
            ("Kick members", perms.kick_members),
            ("Ban members", perms.ban_members),
        ]
        lines = [f"{'✅' if enabled else '❌'} {name}" for name, enabled in checks]
        missing = sum(1 for _, enabled in checks if not enabled)
        embed = EmbedFactory.info(
            title="Bot Permissions",
            description=f"Channel: {getattr(channel, 'mention', '#unknown')}\nMissing **{missing}/{len(checks)}** checked permissions.\n\n" + "\n".join(lines),
        )
        embed.add_field(name="Bot top role", value=guild.me.top_role.mention, inline=True)
        embed.add_field(name="Administrator", value="Yes" if perms.administrator else "No", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="commandinfo", description="Show metadata about one Raspberry-Bot slash command.")
    async def commandinfo(self, interaction: discord.Interaction, command_name: str) -> None:
        wanted = command_name.strip().lstrip("/").lower()
        commands_list = _all_app_commands(self.bot)
        command = next((cmd for cmd in commands_list if cmd.qualified_name.lower() == wanted), None)
        if command is None:
            matches = [cmd.qualified_name for cmd in commands_list if wanted in cmd.qualified_name.lower()][:12]
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title="Command not found",
                    description=("Possible matches:\n" + "\n".join(f"`/{name}`" for name in matches)) if matches else "No matching command found.",
                ),
                ephemeral=True,
            )
            return

        embed = EmbedFactory.info(title=f"/{command.qualified_name}", description=command.description or "No description.")
        embed.add_field(name="Type", value="Command group" if isinstance(command, app_commands.Group) else "Slash command", inline=True)
        parent = getattr(command, "parent", None)
        embed.add_field(name="Group", value=f"`/{parent.qualified_name}`" if parent else "Top level", inline=True)
        if isinstance(command, app_commands.Command):
            params = []
            for parameter in command.parameters:
                required = "required" if parameter.required else "optional"
                params.append(f"`{parameter.name}` • {required} • {parameter.description or 'No description'}")
            embed.add_field(name="Parameters", value="\n".join(params)[:1000] if params else "None", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="invite", description="Generate a private invite link for Raspberry-Bot.")
    async def invite(self, interaction: discord.Interaction) -> None:
        if self.bot.user is None:
            return
        permissions = discord.Permissions(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            manage_messages=True,
            manage_channels=True,
            manage_roles=True,
            moderate_members=True,
            kick_members=True,
            ban_members=True,
        )
        url = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=permissions,
            scopes=("bot", "applications.commands"),
        )
        view = discord.ui.View(timeout=120)
        view.add_item(discord.ui.Button(label="Invite Raspberry-Bot", emoji="🤖", url=url))
        await interaction.response.send_message(
            embed=EmbedFactory.info(
                title="Bot Invite",
                description="The generated invite requests the permissions needed by the moderation, ticket and management modules. Review them on Discord before authorizing.",
            ),
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotTools(bot))
