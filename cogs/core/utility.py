from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="avatar", description="Show a user's avatar with direct high-resolution links.")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        asset = target.display_avatar
        embed = EmbedFactory.info(title=f"Avatar • {target}")
        embed.set_image(url=asset.with_size(1024).url)
        embed.add_field(name="User ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Animated", value="Yes" if asset.is_animated() else "No", inline=True)
        embed.add_field(name="Source", value="Server avatar" if isinstance(target, discord.Member) and target.guild_avatar else "Global avatar", inline=True)
        view = discord.ui.View(timeout=120)
        view.add_item(discord.ui.Button(label="Open 1024px", url=asset.with_size(1024).url))
        try:
            view.add_item(discord.ui.Button(label="PNG", url=asset.with_format("png").with_size(1024).url))
        except ValueError:
            pass
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="roleinfo", description="Show detailed information and key permissions for a role.")
    @app_commands.guild_only()
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role) -> None:
        permissions = []
        for label, enabled in (
            ("Administrator", role.permissions.administrator),
            ("Manage Guild", role.permissions.manage_guild),
            ("Manage Channels", role.permissions.manage_channels),
            ("Manage Roles", role.permissions.manage_roles),
            ("Manage Messages", role.permissions.manage_messages),
            ("Moderate Members", role.permissions.moderate_members),
            ("Ban Members", role.permissions.ban_members),
            ("Kick Members", role.permissions.kick_members),
        ):
            if enabled:
                permissions.append(label)

        embed = EmbedFactory.info(title=f"Role • {role.name}")
        if role.icon:
            embed.set_thumbnail(url=role.icon.url)
        embed.add_field(name="Role", value=f"{role.mention}\n`{role.id}`", inline=True)
        embed.add_field(name="Members", value=f"**{len(role.members)}**", inline=True)
        embed.add_field(name="Position", value=f"**{role.position}** / {len(role.guild.roles) - 1}", inline=True)
        embed.add_field(name="Color", value=f"`{str(role.color)}`\n`#{role.color.value:06X}`", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(role.created_at, style="R"), inline=True)
        embed.add_field(name="Flags", value=f"Hoisted: {'Yes' if role.hoist else 'No'}\nMentionable: {'Yes' if role.mentionable else 'No'}\nManaged: {'Yes' if role.managed else 'No'}", inline=True)
        embed.add_field(name="Key permissions", value=", ".join(permissions) if permissions else "None", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="channelinfo", description="Show detailed information about a Discord channel.")
    @app_commands.guild_only()
    async def channelinfo(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Unsupported channel", description="Choose a text channel."),
                ephemeral=True,
            )
            return

        embed = EmbedFactory.info(title=f"Channel • #{target.name}")
        embed.add_field(name="Channel", value=f"{target.mention}\n`{target.id}`", inline=True)
        embed.add_field(name="Category", value=target.category.name if target.category else "—", inline=True)
        embed.add_field(name="Position", value=str(target.position), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(target.created_at, style="R"), inline=True)
        embed.add_field(name="Slowmode", value=f"{target.slowmode_delay}s", inline=True)
        embed.add_field(name="NSFW", value="Yes" if target.nsfw else "No", inline=True)
        embed.add_field(name="Permissions synced", value="Yes" if target.permissions_synced else "No", inline=True)
        embed.add_field(name="Threads", value=f"Active: **{len(target.threads)}**", inline=True)
        embed.add_field(name="Default auto archive", value=f"{target.default_auto_archive_duration} min", inline=True)
        embed.add_field(name="Topic", value=target.topic or "—", inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
