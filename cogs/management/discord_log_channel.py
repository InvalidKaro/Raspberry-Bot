from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.discord_log_forwarder import DiscordLogForwarder

LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class DiscordLogChannel(commands.GroupCog, group_name="botlog", group_description="Mirror Raspberry-Bot logs to Discord"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.forwarder = DiscordLogForwarder(bot)

    async def cog_load(self) -> None:
        await self.bot.database.execute(
            """
            CREATE TABLE IF NOT EXISTS discord_log_channels (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                min_level INTEGER NOT NULL DEFAULT 20,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.forwarder.load_configs()
        await self.forwarder.start()
        self.bot.discord_log_forwarder = self.forwarder

    async def cog_unload(self) -> None:
        await self.forwarder.stop()
        if getattr(self.bot, "discord_log_forwarder", None) is self.forwarder:
            delattr(self.bot, "discord_log_forwarder")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.bot.settings.owner_ids:
            return True
        await interaction.response.send_message(
            embed=EmbedFactory.error(
                title="Owner only",
                description="Bot log forwarding can only be configured by a bot owner.",
            ),
            ephemeral=True,
        )
        return False

    @staticmethod
    def _level_value(level: str) -> int | None:
        return LEVELS.get(level.strip().upper())

    @app_commands.command(name="setup", description="Mirror bot logs into a Discord text channel.")
    @app_commands.describe(
        channel="Channel that should receive the bot logs",
        level="Minimum log level to forward",
    )
    @app_commands.choices(
        level=[
            app_commands.Choice(name="DEBUG - everything", value="DEBUG"),
            app_commands.Choice(name="INFO - recommended", value="INFO"),
            app_commands.Choice(name="WARNING", value="WARNING"),
            app_commands.Choice(name="ERROR", value="ERROR"),
            app_commands.Choice(name="CRITICAL", value="CRITICAL"),
        ]
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        level: app_commands.Choice[str] | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Server only", description="Run this command inside a server."),
                ephemeral=True,
            )
            return

        selected_name = level.value if level else "INFO"
        selected_level = self._level_value(selected_name) or logging.INFO

        me = interaction.guild.me if interaction.guild else None
        perms = channel.permissions_for(me) if me else None
        if perms is not None and (not perms.view_channel or not perms.send_messages):
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title="Missing channel permissions",
                    description=f"I need **View Channel** and **Send Messages** in {channel.mention}.",
                ),
                ephemeral=True,
            )
            return

        await self.bot.database.execute(
            """
            INSERT INTO discord_log_channels (guild_id, channel_id, min_level, enabled, updated_at)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                min_level = excluded.min_level,
                enabled = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (interaction.guild_id, channel.id, selected_level),
        )
        self.forwarder.set_config(interaction.guild_id, channel.id, selected_level)

        embed = EmbedFactory.success(
            title="Bot log channel enabled",
            description=(
                f"Bot logs from **{selected_name}** and above will be mirrored to {channel.mention}.\n\n"
                "Messages are batched to reduce Discord API traffic. Mentions are disabled and log messages are sent silently."
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        logging.getLogger(__name__).info(
            "Discord bot log forwarding enabled for guild %s -> channel %s at level %s",
            interaction.guild_id,
            channel.id,
            selected_name,
        )

    @app_commands.command(name="disable", description="Stop forwarding bot logs in this server.")
    async def disable(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Server only", description="Run this command inside a server."),
                ephemeral=True,
            )
            return

        await self.bot.database.execute(
            "UPDATE discord_log_channels SET enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ?",
            (interaction.guild_id,),
        )
        self.forwarder.remove_config(interaction.guild_id)
        await interaction.response.send_message(
            embed=EmbedFactory.success(title="Bot log channel disabled", description="Discord log forwarding is now disabled."),
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show the current Discord log forwarding configuration.")
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                embed=EmbedFactory.error(title="Server only", description="Run this command inside a server."),
                ephemeral=True,
            )
            return

        row = await self.bot.database.fetchone(
            "SELECT channel_id, min_level, enabled, updated_at FROM discord_log_channels WHERE guild_id = ?",
            (interaction.guild_id,),
        )
        if not row or not int(row["enabled"]):
            await interaction.response.send_message(
                embed=EmbedFactory.system(title="Bot Log Forwarding", description="Status: **Disabled**"),
                ephemeral=True,
            )
            return

        channel_id = int(row["channel_id"])
        level_no = int(row["min_level"])
        level_name = logging.getLevelName(level_no)
        queue_size = self.forwarder.queue.qsize()

        embed = EmbedFactory.system(title="Bot Log Forwarding")
        embed.add_field(name="Status", value="🟢 Enabled", inline=True)
        embed.add_field(name="Minimum level", value=f"`{level_name}`", inline=True)
        embed.add_field(name="Queue", value=f"`{queue_size}` pending", inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>\n`{channel_id}`", inline=False)
        embed.add_field(name="Updated", value=f"`{row['updated_at']}`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="test", description="Write a test entry to the configured bot log channel.")
    async def test(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or self.forwarder.get_config(interaction.guild_id) is None:
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title="Bot log channel disabled",
                    description="Configure it first with `/botlog setup`.",
                ),
                ephemeral=True,
            )
            return

        logging.getLogger("raspberry_bot.botlog_test").info(
            "Discord bot log test requested by %s (%s) in guild %s",
            interaction.user,
            interaction.user.id,
            interaction.guild_id,
        )
        await interaction.response.send_message(
            embed=EmbedFactory.success(
                title="Test queued",
                description="The test log entry should appear in the configured channel within a few seconds.",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiscordLogChannel(bot))
