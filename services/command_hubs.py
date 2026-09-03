from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HubSpec:
    name: str
    description: str
    members: tuple[str, ...]
    default_permissions: discord.Permissions | None = None
    guild_only: bool = True


# Keep the root slash-command list intentionally small. A hub can have at most
# 25 direct children, so the larger feature areas are split by theme instead of
# being placed in one giant /hub command.
HUB_SPECS: tuple[HubSpec, ...] = (
    HubSpec(
        "info",
        "Profile, server and Raspberry-Bot information",
        (
            "userinfo",
            "serverinfo",
            "membercount",
            "servericon",
            "avatar",
            "roleinfo",
            "channelinfo",
            "profile",
            "profilecard",
            "botinfo",
            "permissions",
            "commandinfo",
            "invite",
        ),
    ),
    HubSpec(
        "tools",
        "Small Discord utilities and converters",
        ("timestamp", "snowflake"),
    ),
    HubSpec(
        "play",
        "Arcade, duels and interactive community games",
        (
            "blackjack",
            "battleship",
            "cipherduel",
            "escape",
            "bossfight",
            "heist",
            "detective",
            "territory",
            "wordchain",
            "reactionbattle",
            "blindrank",
            "wouldyourather",
            "hotseat",
            "story",
            "mysterybox",
            "achievementhunt",
            "season",
            "rivalry",
            "rematch",
            "spectate",
            "arcade",
        ),
    ),
    HubSpec(
        "social",
        "Community messages, voting and lightweight utilities",
        ("timecapsule", "deadman", "commandpalette", "secretvote", "poll", "suggest"),
    ),
    HubSpec(
        "media",
        "Voice, ambient audio and visual creation tools",
        (
            "soundboard",
            "radio",
            "youtube",
            "ambientsource",
            "ambient",
            "ambientcatalog",
            "tts",
            "nowplaying",
            "quoteimage",
            "poster",
            "banner",
            "meme",
            "avatarstyle",
        ),
    ),
    HubSpec(
        "sky",
        "Weather, sun, moon, ISS and astronomy tools",
        ("weatherboard", "sun", "moon", "iss", "space"),
    ),
    HubSpec(
        "overview",
        "Compact server activity, handover and timeline views",
        ("handover", "pulse", "timeline"),
    ),
    HubSpec(
        "wizard",
        "Guided server, content and member workflows",
        ("formwizard", "embedwizard", "panelwizard", "setupwizard", "onboardingwizard", "offboarding"),
        default_permissions=discord.Permissions(manage_guild=True),
    ),
    HubSpec(
        "admin",
        "Server diagnostics, permissions and administration tools",
        ("drop", "permissionmap", "roleaudit", "healthcheck", "diagnose", "anomaly", "insights"),
        default_permissions=discord.Permissions(manage_guild=True),
    ),
    HubSpec(
        "config",
        "Administrator-only configuration snapshots and comparisons",
        ("restorepoint", "configdiff"),
        default_permissions=discord.Permissions(administrator=True),
    ),
)

for _spec in HUB_SPECS:
    if len(_spec.members) > 25:
        raise RuntimeError(f"Command hub /{_spec.name} exceeds Discord's 25-child limit")


def _command_module(command: app_commands.Command | app_commands.Group) -> str:
    module = getattr(command, "module", None)
    if module:
        return str(module)
    callback = getattr(command, "callback", None)
    return str(getattr(callback, "__module__", "") or "")


def _new_group(spec: HubSpec) -> app_commands.Group:
    return app_commands.Group(
        name=spec.name,
        description=spec.description,
        guild_only=spec.guild_only,
        default_permissions=spec.default_permissions,
    )


def _root_group(tree: app_commands.CommandTree, name: str) -> app_commands.Group | None:
    command = tree.get_command(name)
    return command if isinstance(command, app_commands.Group) else None


def _detach_child(group: app_commands.Group, name: str) -> app_commands.Command | app_commands.Group | None:
    """Remove one child and explicitly clear its parent.

    discord.py's Group.remove_command currently mutates the child mapping but
    intentionally does not clear ``command.parent``. We do that here because a
    command restored to the root must no longer resolve through the old hub.
    """
    command = group.get_command(name)
    if command is None:
        return None
    group.remove_command(name)
    command.parent = None
    return command


def _create_hub_from_first(
    tree: app_commands.CommandTree,
    spec: HubSpec,
    first_name: str,
) -> app_commands.Group | None:
    """Create a hub without transiently increasing the root-command count."""
    first = tree.remove_command(first_name)
    if first is None:
        return None

    group = _new_group(spec)
    try:
        group.add_command(first)
        tree.add_command(group)
    except Exception:
        # Keep startup atomic if Discord.py rejects the group for any reason.
        _detach_child(group, first.name)
        tree.add_command(first)
        raise
    return group


def compact_command_tree(tree: app_commands.CommandTree) -> dict[str, int]:
    """Move selected root commands into stable thematic hubs.

    The function is intentionally idempotent and can be called after every
    extension load. This keeps the tree below Discord's root-command limit even
    while the bot is still starting up.
    """
    moved = 0
    created = 0

    for spec in HUB_SPECS:
        existing = tree.get_command(spec.name)
        if existing is not None and not isinstance(existing, app_commands.Group):
            logger.warning("Cannot create /%s hub: a non-group root command uses that name", spec.name)
            continue

        group = existing if isinstance(existing, app_commands.Group) else None
        candidates = [name for name in spec.members if tree.get_command(name) is not None and name != spec.name]
        if group is None and candidates:
            group = _create_hub_from_first(tree, spec, candidates.pop(0))
            if group is not None:
                created += 1
                moved += 1

        if group is None:
            continue

        for name in candidates:
            command = tree.remove_command(name)
            if command is None:
                continue
            if len(group.commands) >= 25:
                tree.add_command(command)
                logger.error("Hub /%s is full; kept /%s at root", spec.name, name)
                continue
            old_child = group.get_command(command.name)
            if old_child is not None:
                _detach_child(group, command.name)
            try:
                group.add_command(command)
            except Exception:
                command.parent = None
                tree.add_command(command)
                raise
            moved += 1

    root_count = len(tree.get_commands())
    if moved or created:
        logger.info("Command hubs compacted tree: moved=%s created=%s roots=%s", moved, created, root_count)
    return {"moved": moved, "created": created, "roots": root_count}


def prepare_extension_unload(tree: app_commands.CommandTree, extension: str) -> int:
    """Temporarily restore one extension's hub children to the root.

    discord.py removes Cog application commands by their original root names.
    Re-parented commands therefore need to be restored immediately before an
    extension unload/reload. Only the target extension is expanded, so the tree
    never balloons back to all pre-hub root commands at once.
    """
    restored = 0
    wanted = extension.strip()

    for spec in HUB_SPECS:
        group = _root_group(tree, spec.name)
        if group is None:
            continue

        for child in list(group.commands):
            module = _command_module(child)
            if module != wanted and not module.startswith(wanted + "."):
                continue
            command = _detach_child(group, child.name)
            if command is None:
                continue
            tree.add_command(command)
            restored += 1

        if not group.commands:
            tree.remove_command(spec.name)

    if restored:
        logger.info("Expanded %s hub command(s) before unloading %s", restored, wanted)
    return restored
