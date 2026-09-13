from __future__ import annotations

import logging
from typing import Any

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


def _button_invokable(command: app_commands.Command | app_commands.Group) -> bool:
    """Return whether a related command can be safely invoked without user input.

    A button has no slash-command option payload. Commands that require at least
    one argument therefore remain visible as suggestions but do not get an
    execution button. Optional parameters are invoked with their declared
    defaults.
    """

    if not isinstance(command, app_commands.Command):
        return False
    return not any(parameter.required for parameter in command.parameters)


def _missing_default_permissions(
    command: app_commands.Command,
    user: discord.abc.User,
) -> list[str]:
    required = command.default_permissions
    if required is None:
        return []
    if not isinstance(user, discord.Member):
        return [name for name, enabled in required if enabled]

    actual = user.guild_permissions
    return [
        name
        for name, enabled in required
        if enabled and not bool(getattr(actual, name, False))
    ]


def related_embed(bot: commands.Bot, command_name: str) -> discord.Embed:
    related = _related_commands(bot, command_name)
    if related:
        lines: list[str] = []
        for command in related:
            executable = _button_invokable(command)
            marker = "▶️" if executable else "⌨️"
            suffix = "" if executable else " · benötigt Eingaben"
            lines.append(
                f"{marker} `/{command.qualified_name}` — {command.description or 'Keine Beschreibung.'}{suffix}"
            )
        description = "\n".join(lines)
    else:
        description = "Für diesen Command wurden keine direkten verwandten Commands gefunden."

    embed = EmbedFactory.info(
        title=f"Ähnliche Aktionen · /{command_name}",
        description=description,
    )
    if any(_button_invokable(command) for command in related):
        embed.set_footer(text="▶️ Buttons führen den jeweiligen Command direkt aus.")
    return embed


async def _related_preflight(
    bot: commands.Bot,
    interaction: discord.Interaction,
    command: app_commands.Command,
) -> tuple[bool, str | None]:
    if command.guild_only and interaction.guild is None:
        return False, "Dieser Command kann nur auf einem Server ausgeführt werden."

    missing = _missing_default_permissions(command, interaction.user)
    if missing:
        readable = ", ".join(name.replace("_", " ") for name in missing[:5])
        return False, f"Dir fehlen die benötigten Discord-Berechtigungen: `{readable}`."

    database = getattr(bot, "database", None)
    if database is not None:
        try:
            row = await database.fetchone("SELECT enabled, reason FROM maintenance_state WHERE id=1")
        except Exception:
            logger.exception("Related-command maintenance preflight failed")
        else:
            if row and int(row["enabled"]):
                bypass = interaction.user.id in getattr(getattr(bot, "settings", None), "owner_ids", set())
                if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
                    bypass = True
                if not bypass:
                    return False, str(row["reason"] or "Raspberry-Bot ist derzeit im Wartungsmodus.")

    feature_flags = getattr(bot, "feature_flags", None)
    if feature_flags is not None:
        try:
            decision = await feature_flags.decision(
                interaction.guild_id,
                interaction.user.id,
                command.qualified_name,
            )
        except Exception:
            logger.exception("Related-command feature-flag preflight failed for /%s", command.qualified_name)
            return False, "Der Feature-Status konnte nicht geprüft werden."
        if not decision.allowed:
            return False, f"`/{command.qualified_name}` ist für diese Session deaktiviert."

    try:
        # discord.py's own app-command dispatcher runs this exact command-level
        # check path before transforming arguments and calling the callback. We
        # reuse it so Cog/Group/app-command checks are not bypassed by buttons.
        allowed = await command._check_can_run(interaction)  # type: ignore[attr-defined]
    except app_commands.AppCommandError as exc:
        return False, str(exc) or "Der Command-Check ist fehlgeschlagen."
    except Exception:
        logger.exception("Related-command checks failed for /%s", command.qualified_name)
        return False, "Der Command konnte nicht freigegeben werden."

    if not allowed:
        return False, "Du darfst diesen Command in diesem Kontext nicht ausführen."
    return True, None


async def _invoke_related_command(
    bot: commands.Bot,
    interaction: discord.Interaction,
    command: app_commands.Command,
) -> None:
    if not _button_invokable(command):
        await interaction.response.send_message(
            f"`/{command.qualified_name}` benötigt Eingaben und kann deshalb nicht blind über einen Button gestartet werden.",
            ephemeral=True,
        )
        return

    allowed, reason = await _related_preflight(bot, interaction, command)
    if not allowed:
        await interaction.response.send_message(
            embed=EmbedFactory.warning(
                title="Aktion nicht verfügbar",
                description=reason or "Dieser Command kann hier nicht ausgeführt werden.",
            ),
            ephemeral=True,
        )
        return

    params: dict[str, Any] = {
        parameter.name: parameter.default
        for parameter in command.parameters
        if not parameter.required
    }

    try:
        callback = command.callback
        binding = getattr(command, "binding", None)
        if binding is not None:
            await callback(binding, interaction, **params)
        else:
            await callback(interaction, **params)
    except app_commands.AppCommandError as exc:
        logger.warning("Related command /%s rejected: %s", command.qualified_name, exc)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=EmbedFactory.warning(
                    title="Command nicht ausgeführt",
                    description=str(exc) or "Der Command wurde durch einen Check abgelehnt.",
                ),
                ephemeral=True,
            )
        return
    except Exception:
        logger.exception("Related command /%s failed", command.qualified_name)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=EmbedFactory.error(
                    title="Command fehlgeschlagen",
                    description=f"`/{command.qualified_name}` konnte nicht ausgeführt werden. Details wurden geloggt.",
                ),
                ephemeral=True,
            )
        return

    # Button-invoked callbacks do not pass through CommandTree's completion
    # event. Attach a fresh contextual action bar ourselves when the callback
    # returned a normal response without its own component UI.
    try:
        message = await interaction.original_response()
        if not message.components and (message.content or message.embeds or message.attachments):
            await interaction.edit_original_response(
                view=CommandActionsView(bot, interaction.user.id, command.qualified_name),
            )
    except (discord.NotFound, discord.HTTPException, discord.ClientException):
        logger.debug("Could not attach follow-up actions after /%s", command.qualified_name)


class RelatedCommandButton(discord.ui.Button):
    def __init__(
        self,
        bot: commands.Bot,
        command: app_commands.Command,
        *,
        row: int,
    ) -> None:
        label = f"/{command.qualified_name}"
        super().__init__(
            label=label[:80],
            emoji="▶️",
            style=discord.ButtonStyle.primary,
            row=row,
        )
        self.bot = bot
        self.command = command

    async def callback(self, interaction: discord.Interaction) -> None:
        await _invoke_related_command(self.bot, interaction, self.command)


class RelatedCommandsView(discord.ui.View):
    def __init__(self, bot: commands.Bot, user_id: int, command_name: str) -> None:
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        executable = [
            command
            for command in _related_commands(bot, command_name)
            if isinstance(command, app_commands.Command) and _button_invokable(command)
        ][:5]
        for index, command in enumerate(executable):
            self.add_item(RelatedCommandButton(bot, command, row=index // 3))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Diese Command-Buttons gehören zur ursprünglichen Command-Session.",
            ephemeral=True,
        )
        return False


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
    """Context-aware action bar appended to ordinary command responses."""

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
                view = RelatedCommandsView(self.bot, interaction.user.id, self.command_name)
                await interaction.response.send_message(
                    embed=related_embed(self.bot, self.command_name),
                    view=view if view.children else None,
                    ephemeral=True,
                )
                return

            if spec.handler == "services":
                from views.service_control import open_service_control

                await open_service_control(interaction)
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
