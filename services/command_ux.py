from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import discord
from discord.ext import commands

from helpers.embeds import EmbedFactory
from views.command_actions import CommandActionsView, ErrorActionsView

logger = logging.getLogger(__name__)

_PROGRESS_MARKER = "raspberry-bot:command-progress"


def _bar(done: int, total: int, width: int = 12) -> str:
    total = max(1, int(total))
    done = max(0, min(int(done), total))
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled)


def _progress_embed(command_name: str, elapsed: float = 0.0) -> discord.Embed:
    command = command_name.strip().lstrip("/") or "command"
    embed = EmbedFactory.system(
        title=f"/{command} läuft",
        description=(
            f"`{_bar(1, 3)}` **wird verarbeitet**\n\n"
            "🔄 Command läuft\n"
            "▫️ Ergebnis wird vorbereitet\n"
            "▫️ Abschluss"
        ),
    )
    if elapsed > 0:
        embed.add_field(name="Laufzeit", value=f"{elapsed:.1f} s", inline=True)
    embed.set_footer(text=_PROGRESS_MARKER)
    return embed


def _finished_progress_embed(command_name: str, duration_ms: float | None) -> discord.Embed:
    command = command_name.strip().lstrip("/") or "command"
    duration = f"{duration_ms / 1000:.2f} s" if duration_ms is not None else "fertig"
    embed = EmbedFactory.success(
        title=f"/{command} abgeschlossen",
        description=(
            f"`{_bar(3, 3)}` **3/3**\n\n"
            "✅ Command ausgeführt\n"
            "✅ Ergebnis verarbeitet\n"
            "✅ Abgeschlossen"
        ),
    )
    embed.add_field(name="Dauer", value=duration, inline=True)
    embed.set_footer(text=_PROGRESS_MARKER)
    return embed


def _failed_progress_embed(command_name: str, duration_ms: float | None) -> discord.Embed:
    command = command_name.strip().lstrip("/") or "command"
    duration = f"{duration_ms / 1000:.2f} s" if duration_ms is not None else "unbekannt"
    embed = EmbedFactory.error(
        title=f"/{command} fehlgeschlagen",
        description=(
            f"`{_bar(2, 3)}` **abgebrochen**\n\n"
            "✅ Command gestartet\n"
            "⚠️ Verarbeitung abgebrochen\n"
            "▫️ Abschluss nicht erreicht"
        ),
    )
    embed.add_field(name="Dauer", value=duration, inline=True)
    embed.set_footer(text=_PROGRESS_MARKER)
    return embed


def _message_has_progress_marker(message: discord.InteractionMessage) -> bool:
    return any(
        embed.footer and embed.footer.text == _PROGRESS_MARKER
        for embed in message.embeds
    )


class CommandUXService:
    """Cross-cutting UI behavior for every application command.

    - Adds contextual action buttons to plain command responses.
    - Shows a lightweight progress card when a command explicitly defers and
      keeps running long enough to benefit from feedback.
    - Never replaces a View a command already supplied.
    """

    def __init__(self, bot: commands.Bot, *, progress_delay: float = 1.0) -> None:
        self.bot = bot
        self.progress_delay = max(0.5, float(progress_delay))
        self._watchers: dict[int, asyncio.Task[None]] = {}
        self._started: dict[int, float] = {}
        self._auto_progress: set[int] = set()

    def begin(self, interaction: discord.Interaction, command_name: str) -> None:
        self.cancel(interaction.id)
        self._started[interaction.id] = time.perf_counter()
        self._watchers[interaction.id] = asyncio.create_task(
            self._watch_deferred(interaction, command_name),
            name=f"command-ux:{interaction.id}",
        )

    def cancel(self, interaction_id: int) -> None:
        task = self._watchers.pop(interaction_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._started.pop(interaction_id, None)

    async def close(self) -> None:
        tasks = list(self._watchers.values())
        self._watchers.clear()
        self._started.clear()
        self._auto_progress.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _watch_deferred(self, interaction: discord.Interaction, command_name: str) -> None:
        try:
            await asyncio.sleep(self.progress_delay)

            if not interaction.response.is_done():
                return

            response_type = interaction.response.type
            if response_type != discord.InteractionResponseType.deferred_channel_message:
                return

            try:
                message = await interaction.original_response()
            except (discord.NotFound, discord.HTTPException):
                return

            # A command that already rendered content or a custom View owns its
            # UX. Do not overwrite it with the generic progress fallback.
            if message.content or message.embeds or message.components:
                return

            started = self._started.get(interaction.id)
            elapsed = time.perf_counter() - started if started is not None else 0.0
            await interaction.edit_original_response(
                embed=_progress_embed(command_name, elapsed),
            )
            self._auto_progress.add(interaction.id)
        except asyncio.CancelledError:
            raise
        except (discord.NotFound, discord.HTTPException):
            logger.debug("Could not render automatic command progress for %s", command_name)
        except Exception:
            logger.exception("Automatic command progress failed for %s", command_name)

    async def complete(
        self,
        interaction: discord.Interaction,
        command_name: str,
        *,
        duration_ms: float | None = None,
    ) -> None:
        task = self._watchers.pop(interaction.id, None)
        if task is not None and not task.done():
            task.cancel()
        self._started.pop(interaction.id, None)

        try:
            message = await interaction.original_response()
        except (discord.NotFound, discord.HTTPException):
            self._auto_progress.discard(interaction.id)
            return

        try:
            if interaction.id in self._auto_progress and _message_has_progress_marker(message):
                await interaction.edit_original_response(
                    embed=_finished_progress_embed(command_name, duration_ms),
                    view=CommandActionsView(self.bot, interaction.user.id, command_name),
                )
                return

            # Existing buttons/selects belong to the command. Keep them intact.
            if message.components:
                return

            await interaction.edit_original_response(
                view=CommandActionsView(self.bot, interaction.user.id, command_name),
            )
        except (discord.NotFound, discord.HTTPException):
            logger.debug("Could not attach action suggestions for %s", command_name)
        finally:
            self._auto_progress.discard(interaction.id)

    async def fail(
        self,
        interaction: discord.Interaction,
        command_name: str,
        *,
        duration_ms: float | None = None,
    ) -> None:
        task = self._watchers.pop(interaction.id, None)
        if task is not None and not task.done():
            task.cancel()
        self._started.pop(interaction.id, None)

        if interaction.id not in self._auto_progress:
            return

        try:
            message = await interaction.original_response()
            if _message_has_progress_marker(message):
                await interaction.edit_original_response(
                    embed=_failed_progress_embed(command_name, duration_ms),
                    view=ErrorActionsView(self.bot, interaction.user.id, command_name),
                )
        except (discord.NotFound, discord.HTTPException):
            logger.debug("Could not finalize failed command progress for %s", command_name)
        finally:
            self._auto_progress.discard(interaction.id)

    def action_view(self, interaction: discord.Interaction, command_name: str) -> CommandActionsView:
        return CommandActionsView(self.bot, interaction.user.id, command_name)

    def error_view(self, interaction: discord.Interaction, command_name: str) -> ErrorActionsView:
        return ErrorActionsView(self.bot, interaction.user.id, command_name)


@dataclass(slots=True)
class ProgressStep:
    label: str
    state: str = "pending"
    detail: str | None = None


class CommandProgress:
    """Reusable detailed progress UI for multi-stage commands.

    Commands with real stages should use this instead of the automatic generic
    fallback. It edits one response message, keeping Discord channels clean.
    """

    def __init__(
        self,
        interaction: discord.Interaction,
        title: str,
        steps: list[str],
        *,
        ephemeral: bool = True,
    ) -> None:
        if not steps:
            raise ValueError("CommandProgress requires at least one step")
        self.interaction = interaction
        self.title = title
        self.steps = [ProgressStep(label=label) for label in steps]
        self.ephemeral = ephemeral
        self.started_at = time.perf_counter()

    def _embed(self, *, final: bool = False, failed: bool = False) -> discord.Embed:
        completed = sum(step.state in {"done", "warn", "failed"} for step in self.steps)
        lines: list[str] = []
        for step in self.steps:
            icon = {
                "pending": "▫️",
                "running": "🔄",
                "done": "✅",
                "warn": "⚠️",
                "failed": "❌",
            }.get(step.state, "▫️")
            detail = f" — {step.detail}" if step.detail else ""
            lines.append(f"{icon} **{step.label}**{detail}")

        description = f"`{_bar(completed, len(self.steps))}` **{completed}/{len(self.steps)}**\n\n" + "\n".join(lines)
        if failed:
            embed = EmbedFactory.error(title=f"{self.title} fehlgeschlagen", description=description)
        elif final:
            embed = EmbedFactory.success(title=f"{self.title} abgeschlossen", description=description)
        else:
            embed = EmbedFactory.system(title=f"{self.title} läuft", description=description)
        embed.set_footer(text=_PROGRESS_MARKER)
        return embed

    async def start(self) -> None:
        self.steps[0].state = "running"
        embed = self._embed()
        if self.interaction.response.is_done():
            await self.interaction.edit_original_response(embed=embed, view=None)
        else:
            await self.interaction.response.send_message(
                embed=embed,
                ephemeral=self.ephemeral,
            )

    async def running(self, index: int, detail: str | None = None) -> None:
        self._check_index(index)
        for idx, step in enumerate(self.steps):
            if idx != index and step.state == "running":
                step.state = "done"
        self.steps[index].state = "running"
        self.steps[index].detail = detail
        await self.interaction.edit_original_response(embed=self._embed(), view=None)

    async def done(self, index: int, detail: str | None = None) -> None:
        self._check_index(index)
        self.steps[index].state = "done"
        self.steps[index].detail = detail
        await self.interaction.edit_original_response(embed=self._embed(), view=None)

    async def warn(self, index: int, detail: str | None = None) -> None:
        self._check_index(index)
        self.steps[index].state = "warn"
        self.steps[index].detail = detail
        await self.interaction.edit_original_response(embed=self._embed(), view=None)

    async def failed(self, index: int, detail: str | None = None) -> None:
        self._check_index(index)
        self.steps[index].state = "failed"
        self.steps[index].detail = detail
        await self.interaction.edit_original_response(embed=self._embed(failed=True), view=None)

    async def finish(self, *, view: discord.ui.View | None = None) -> None:
        for step in self.steps:
            if step.state in {"pending", "running"}:
                step.state = "done"
        await self.interaction.edit_original_response(embed=self._embed(final=True), view=view)

    def _check_index(self, index: int) -> None:
        if index < 0 or index >= len(self.steps):
            raise IndexError(f"Progress step index {index} out of range")
