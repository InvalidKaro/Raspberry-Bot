from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from helpers.embeds import EmbedFactory
from services.action_registry import ActionSpec, action_specs_for_command, permission_allowed

logger = logging.getLogger(__name__)


def _walk_commands(bot: commands.Bot) -> list[app_commands.Command | app_commands.Group]:
    result: list[app_commands.Command | app_commands.Group] = []
    for root in bot.tree.get_commands():
        result.append(root)
        if isinstance(root, app_commands.Group):
            result.extend(root.walk_commands())
    return result


def _related_commands(
    bot: commands.Bot,
    command_name: str,
) -> list[app_commands.Command | app_commands.Group]:
    wanted = command_name.strip().lstrip("/").lower()
    commands_list = _walk_commands(bot)
    current = next((cmd for cmd in commands_list if cmd.qualified_name.lower() == wanted), None)

    if current is None:
        root_name = wanted.split(" ", 1)[0]
        return [
            cmd
            for cmd in commands_list
            if cmd.qualified_name.lower().startswith(root_name + " ")
        ][:8]

    parent = getattr(current, "parent", None)
    if isinstance(parent, app_commands.Group):
        candidates = [cmd for cmd in parent.commands if cmd is not current]
    else:
        root_name = current.qualified_name.split(" ", 1)[0]
        candidates = [
            cmd
            for cmd in commands_list
            if cmd is not current and cmd.qualified_name.split(" ", 1)[0] == root_name
        ]

    return candidates[:8]


def related_embed(bot: commands.Bot, command_name: str) -> discord.Embed:
    related = _related_commands(bot, command_name)
    if related:
        lines = [
            f"`/{cmd.qualified_name}` — {cmd.description or 'Keine Beschreibung.'}"
            for cmd in related
        ]
        description = "\n".join(lines)
    else:
        description = "Für diesen Command wurden keine direkten verwandten Commands gefunden."

    return EmbedFactory.info(
        title=f"Ähnliche Aktionen · /{command_name}",
        description=description,
    )


class ActionButton(discord.ui.Button):
    def __init__(self, spec: ActionSpec, parent: "CommandActionsView") -> None:
        super().__init__(
            label=spec.label,
            emoji=spec.emoji,
            style=spec.style,
            row=0 if spec.id != "diagnostics" else 1,
        )
        self.spec = spec
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if not permission_allowed(self.spec, interaction.user):
            await interaction.response.send_message(
                "Für diese Aktion fehlen dir die benötigten Berechtigungen.",
                ephemeral=True,
            )
            return
        await self.parent_view.run_action(interaction, self.spec)


class CommandActionsView(discord.ui.View):
    """Context-aware action bar appended to ordinary command responses.

    The registry deliberately exposes safe UI actions instead of invoking slash
    command callbacks directly. That preserves permission checks, validation and
    command-specific preconditions while still enabling logical button chains.
    """

    def __init__(self, bot: commands.Bot, user_id: int, command_name: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = int(user_id)
        self.command_name = command_name.strip().lstrip("/") or "unknown"
        for spec in action_specs_for_command(self.command_name):
            self.add_item(ActionButton(spec, self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Diese Action Suggestions gehören zur ursprünglichen Command-Session.",
            ephemeral=True,
        )
        return False

    async def run_action(self, interaction: discord.Interaction, spec: ActionSpec) -> None:
        try:
            if spec.handler == "related":
                await interaction.response.send_message(
                    embed=related_embed(self.bot, self.command_name),
                    ephemeral=True,
                )
                return

            from views.control_center import (
                ControlCenterView,
                control_center_embed,
                run_diagnostics,
                run_quick_check,
                show_system_status,
            )

            if spec.handler == "control_center":
                await interaction.response.send_message(
                    embed=control_center_embed(),
                    view=ControlCenterView(self.bot, interaction.user.id),
                    ephemeral=True,
                )
            elif spec.handler == "quick_check":
                await run_quick_check(interaction, self.bot, interaction.user.id)
            elif spec.handler == "system_status":
                await show_system_status(interaction, self.bot, interaction.user.id)
            elif spec.handler == "diagnostics":
                await run_diagnostics(interaction, self.bot, interaction.user.id)
            else:
                logger.warning("Unknown action handler %s for action %s", spec.handler, spec.id)
                await interaction.response.send_message(
                    "Diese Aktion ist derzeit nicht verfügbar.",
                    ephemeral=True,
                )
        except discord.HTTPException:
            logger.exception("Discord action %s failed for /%s", spec.id, self.command_name)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Die Aktion konnte nicht an Discord übertragen werden.",
                    ephemeral=True,
                )
        except Exception:
            logger.exception("Action %s failed for /%s", spec.id, self.command_name)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=EmbedFactory.error(
                        title="Aktion fehlgeschlagen",
                        description="Die Aktion konnte nicht abgeschlossen werden. Technische Details wurden geloggt.",
                    ),
                    ephemeral=True,
                )


class ErrorActionsView(CommandActionsView):
    """Context actions for failed commands with diagnostics prioritized."""

    def __init__(self, bot: commands.Bot, user_id: int, command_name: str) -> None:
        super().__init__(bot, user_id, command_name)
        if not any(isinstance(item, ActionButton) and item.spec.id == "diagnostics" for item in self.children):
            from services.action_registry import ACTION_REGISTRY

            self.add_item(ActionButton(ACTION_REGISTRY["diagnostics"], self))
