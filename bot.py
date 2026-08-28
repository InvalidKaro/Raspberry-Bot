from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import settings
from database.manager import Database
from database.repositories.settings import SettingsRepository
from helpers.embeds import EmbedFactory
from helpers.logging import setup_logging
from services.cache import CacheManager
from services.tickets import TicketService

logger = logging.getLogger(__name__)

EXTENSIONS: tuple[str, ...] = (
    "cogs.core.info",
    "cogs.core.help",
    "cogs.core.utility",
    "cogs.core.profile",
    "cogs.tickets.tickets",
    "cogs.moderation.moderation",
    "cogs.community.suggestions",
    "cogs.community.polls",
    "cogs.community.welcome",
    "cogs.community.audit_logging",
    "cogs.management.configuration",
    "cogs.management.system_monitor",
    "cogs.management.developer",
    "tasks.cache_cleanup",
    "tasks.system_monitor",
)


class RaspberryBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.moderation = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
            activity=discord.Game(name="/help • Raspberry-Bot"),
        )

        self.settings = settings
        self.database = Database(settings.database_path)
        self.cache = CacheManager()
        self.settings_repo = SettingsRepository(self.database, self.cache)
        self.ticket_service = TicketService(self)
        self.started_at = datetime.now(UTC)

    async def setup_hook(self) -> None:
        await self.database.connect()

        from views.suggestions import SuggestionView
        from views.tickets.controls import TicketControlsView
        from views.tickets.panel import TicketPanelView

        self.add_view(TicketPanelView(self))
        self.add_view(TicketControlsView(self))
        self.add_view(SuggestionView(self))
        from views.system_status import SystemStatusView
        self.add_view(SystemStatusView(self))

        from views.polls import PollView
        poll_rows = await self.database.fetchall(
            "SELECT message_id, options_json FROM polls WHERE message_id IS NOT NULL ORDER BY id DESC LIMIT 100"
        )
        for row in poll_rows:
            try:
                options = json.loads(str(row["options_json"]))
                self.add_view(PollView(self, options), message_id=int(row["message_id"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Skipped invalid persisted poll view for message %s", row["message_id"])

        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Loaded extension: %s", extension)
            except Exception:
                logger.exception("Failed loading extension: %s", extension)
                raise

        if settings.dev_guild_id:
            guild = discord.Object(id=settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s commands to development guild %s", len(synced), settings.dev_guild_id)
        else:
            synced = await self.tree.sync()
            logger.info("Globally synced %s application commands", len(synced))

    async def on_ready(self) -> None:
        if self.user is None:
            return
        logger.info("Ready as %s (%s) in %s guild(s)", self.user, self.user.id, len(self.guilds))

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command | app_commands.ContextMenu,
    ) -> None:
        try:
            await self.database.execute(
                "INSERT INTO command_usage (guild_id, user_id, command_name) VALUES (?, ?, ?)",
                (interaction.guild_id, interaction.user.id, command.qualified_name),
            )
        except Exception:
            logger.exception("Failed to persist command usage for %s", command.qualified_name)

    async def close(self) -> None:
        await self.database.close()
        await super().close()


async def handle_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    original = getattr(error, "original", error)

    if isinstance(error, app_commands.CommandOnCooldown):
        embed = EmbedFactory.warning(
            title="Command cooldown",
            description=f"Try again in **{error.retry_after:.1f} seconds**.",
        )
    elif isinstance(error, app_commands.MissingPermissions):
        text = "You do not have the required Discord permissions for this command."
        embed = EmbedFactory.error(title="Permission denied", description=text)
    elif isinstance(error, app_commands.BotMissingPermissions):
        embed = EmbedFactory.error(
            title="Bot permissions missing",
            description="Raspberry-Bot is missing one or more Discord permissions required for this action.",
        )
    elif isinstance(error, app_commands.CheckFailure):
        embed = EmbedFactory.error(title="Check failed", description="You are not allowed to use this command here.")
    else:
        error_id = uuid.uuid4().hex[:8].upper()
        logger.exception("Application command error [%s]", error_id, exc_info=original)
        embed = EmbedFactory.error(
            title="Command Error",
            description=f"An unexpected error occurred.\n\nReference: `{error_id}`",
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Failed to send application command error response")


def main() -> None:
    setup_logging(settings.log_level)
    try:
        settings.validate_runtime()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    bot = RaspberryBot()
    bot.tree.on_error = handle_tree_error
    try:
        bot.run(settings.discord_token, log_handler=None)
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt")


if __name__ == "__main__":
    main()
