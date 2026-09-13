from __future__ import annotations

from dataclasses import dataclass

import discord


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Declarative metadata for one safe Discord interaction action.

    Actions are intentionally UI-level operations instead of arbitrary slash-command
    callbacks. This keeps permission checks explicit and avoids bypassing Discord's
    application-command validation when a button is clicked.
    """

    id: str
    label: str
    description: str
    emoji: str
    style: discord.ButtonStyle
    handler: str
    required_permission: str | None = None
    dangerous: bool = False
    confirmation_required: bool = False
    next_actions: tuple[str, ...] = ()


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "related": ActionSpec(
        id="related",
        label="Ähnliche Aktionen",
        description="Zeigt weitere Commands aus demselben Funktionsbereich.",
        emoji="💡",
        style=discord.ButtonStyle.secondary,
        handler="related",
    ),
    "control_center": ActionSpec(
        id="control_center",
        label="Control Center",
        description="Öffnet die zentrale HomePi-Bedienoberfläche.",
        emoji="🏠",
        style=discord.ButtonStyle.primary,
        handler="control_center",
        next_actions=("quick_check", "system_status", "diagnostics"),
    ),
    "quick_check": ActionSpec(
        id="quick_check",
        label="Schnellcheck",
        description="Prüft die wichtigsten HomePi-Komponenten in einer geführten Sequenz.",
        emoji="🩺",
        style=discord.ButtonStyle.success,
        handler="quick_check",
        next_actions=("system_status", "diagnostics"),
    ),
    "system_status": ActionSpec(
        id="system_status",
        label="Systemstatus",
        description="Zeigt CPU, RAM, Temperatur, Storage und Kernstatus.",
        emoji="🖥️",
        style=discord.ButtonStyle.primary,
        handler="system_status",
        next_actions=("quick_check", "diagnostics"),
    ),
    "diagnostics": ActionSpec(
        id="diagnostics",
        label="Diagnose",
        description="Führt erweiterte Diagnoseprüfungen aus.",
        emoji="🔬",
        style=discord.ButtonStyle.secondary,
        handler="diagnostics",
        required_permission="manage_guild",
        next_actions=("system_status", "quick_check"),
    ),
}


_CONTEXT_ACTIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("admin", "config", "system", "health", "diagnose", "pi", "bot"),
     ("system_status", "quick_check", "diagnostics", "related", "control_center")),
    (("overview", "pulse", "handover", "timeline"),
     ("system_status", "quick_check", "related", "control_center")),
    (("media", "radio", "spotify", "youtube", "nowplaying"),
     ("related", "control_center", "system_status")),
    (("mesh", "meshtastic"),
     ("related", "system_status", "quick_check", "control_center")),
)

_DEFAULT_ACTIONS = ("related", "control_center")


def _command_tokens(command_name: str) -> tuple[str, ...]:
    normalized = command_name.strip().lstrip("/").lower()
    return tuple(part for part in normalized.replace("-", " ").split() if part)


def action_ids_for_command(command_name: str) -> tuple[str, ...]:
    tokens = set(_command_tokens(command_name))
    for match_tokens, actions in _CONTEXT_ACTIONS:
        if tokens.intersection(match_tokens):
            return actions
    return _DEFAULT_ACTIONS


def action_specs_for_command(command_name: str, *, limit: int = 5) -> tuple[ActionSpec, ...]:
    specs = [ACTION_REGISTRY[action_id] for action_id in action_ids_for_command(command_name) if action_id in ACTION_REGISTRY]
    return tuple(specs[: max(1, min(5, int(limit)))])


def permission_allowed(spec: ActionSpec, user: discord.abc.User) -> bool:
    if spec.required_permission is None:
        return True
    if not isinstance(user, discord.Member):
        return False
    permissions = user.guild_permissions
    return bool(getattr(permissions, spec.required_permission, False))
