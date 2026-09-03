from __future__ import annotations

import json
import logging
import sys
import time
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
from services.system_metrics import SystemMetricsSampler
from services.audit import AuditService
from services.access_control import AccessControl

logger = logging.getLogger(__name__)

EXTENSIONS: tuple[str, ...] = (
    "cogs.management.discord_log_channel",
    "cogs.core.info",
    "cogs.core.help",
    "cogs.core.utility",
    "cogs.core.tools",
    "cogs.core.bot_tools",
    "cogs.core.profile",
    "cogs.tickets.tickets",
    "cogs.moderation.moderation",
    "cogs.community.suggestions",
    "cogs.community.polls",
    "cogs.community.welcome",
    "cogs.community.reminders",
    "cogs.community.audit_logging",
    "cogs.management.configuration",
    "cogs.management.server_tools",
    "cogs.management.personnel_stats",
    "cogs.management.system_monitor",
    "cogs.management.developer",
    "cogs.community.creator_suite",
    "cogs.community.community_plus",
    "cogs.community.wizard_suite",
    "cogs.community.games_plus",
    "cogs.community.games_update",
    "cogs.community.community_tools_plus",
    "cogs.community.voice_suite",
    "cogs.community.visual_suite",
    "cogs.community.astro_weather_suite",
    "cogs.management.workspace_suite",
    "cogs.management.md_weekly_planner",
    "cogs.management.automation_suite",
    "cogs.management.admin_intelligence_plus",
    "cogs.management.pi_hardware",
    "tasks.cache_cleanup",
    "tasks.system_monitor",
    "tasks.dashboard_commands",
    "tasks.database_maintenance",
    "tasks.system_history",
    "cogs.community.onboarding",
    "cogs.management.access",
    "cogs.management.analytics",
    "cogs.management.audit",
)

CORE_EXTENSIONS = {
    "cogs.community.community_plus",
    "cogs.management.automation_suite",
}


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
        self.system_metrics = SystemMetricsSampler(settings.system_metrics_sample_interval)
        self.started_at = datetime.now(UTC)
        self.audit = AuditService(self.database)
        self.access = AccessControl(self)
        self._command_started: dict[int, float] = {}

    async def setup_hook(self) -> None:
        await self.database.connect()

        async def _maintenance_check(interaction: discord.Interaction) -> bool:
            self._command_started[interaction.id] = time.perf_counter()
            row = await self.database.fetchone("SELECT enabled, reason FROM maintenance_state WHERE id=1")
            if row and int(row["enabled"]):
                if interaction.user.id in self.settings.owner_ids:
                    return True
                if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
                    return True
                embed = EmbedFactory.warning(
                    title="Maintenance Mode",
                    description=str(row["reason"] or "Raspberry-Bot is temporarily in maintenance mode."),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return False
            return True

        self.tree.interaction_check = _maintenance_check
        await self.system_metrics.start()

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
                if extension.startswith("cogs.") and extension not in CORE_EXTENSIONS:
                    state = await self.database.fetchone(
                        "SELECT enabled FROM plugin_state WHERE extension=?",
                        (extension,),
                    )
                    if state is not None and not int(state["enabled"]):
                        logger.info("Skipped disabled plugin: %s", extension)
                        continue
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
            started = self._command_started.pop(interaction.id, None)
            duration_ms = (time.perf_counter() - started) * 1000 if started else None
            await self.database.execute(
                "INSERT INTO command_analytics(guild_id,user_id,command_name,success,duration_ms) VALUES(?,?,?,?,?)",
                (interaction.guild_id, interaction.user.id, command.qualified_name, 1, duration_ms),
            )
        except Exception:
            logger.exception("Failed to persist command usage for %s", command.qualified_name)

    async def close(self) -> None:
        await self.system_metrics.stop()
        await self.database.close()
        await super().close()


async def handle_tree_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    original = getattr(error, "original", error)
    try:
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        started = getattr(interaction.client, "_command_started", {}).pop(interaction.id, None)
        duration_ms = (time.perf_counter() - started) * 1000 if started else None
        await interaction.client.database.execute(
            "INSERT INTO command_analytics(guild_id,user_id,command_name,success,duration_ms,error_type) VALUES(?,?,?,?,?,?)",
            (interaction.guild_id, interaction.user.id, command_name, 0, duration_ms, type(original).__name__),
        )
    except Exception:
        pass

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
        embed = EmbedFactory.error(
            title="Check failed",
            description="You are not allowed to use this command here.",
        )
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
