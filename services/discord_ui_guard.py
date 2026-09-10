from __future__ import annotations

import functools
import logging
from typing import Any, Callable

import discord

logger = logging.getLogger(__name__)

# Discord accepts actual Unicode emoji or custom emoji IDs in component emoji
# fields. Some ordinary Unicode symbols look like emoji in the client but are
# rejected by the API (e.g. ↻ and ⌂). Normalize the common UI symbols here so
# every View in the bot is protected, including legacy views.
_EMOJI_REPLACEMENTS: dict[str, str] = {
    "↻": "🔄",
    "↺": "🔄",
    "⌂": "🏠",
    "←": "⬅️",
    "→": "➡️",
    "↑": "⬆️",
    "↓": "⬇️",
    "▶": "▶️",
    "◀": "◀️",
    "■": "⏹️",
    "||": "⏸️",
    "✖": "✖️",
}


def _looks_like_unicode_emoji(name: str) -> bool:
    if not name:
        return False

    # Explicit emoji presentation / composed emoji.
    if "\ufe0f" in name or "\u200d" in name or "\u20e3" in name:
        return True

    for char in name:
        cp = ord(char)
        # Modern pictographs, flags, symbols, transport, people, etc.
        if cp >= 0x1F000:
            return True
        # Misc symbols + dingbats contain the classic Discord-compatible emoji
        # such as ✅, ❌, ❓, ⚙, ☀, ⭐.
        if 0x2600 <= cp <= 0x27BF:
            return True
    return False


def _emoji_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    name = getattr(value, "name", None)
    return str(name) if name else None


def _emoji_has_custom_id(value: Any) -> bool:
    return bool(getattr(value, "id", None))


def _normalize_emoji(value: Any) -> tuple[str | None, bool]:
    """Return (replacement, changed).

    replacement=None with changed=True means the invalid decoration should be
    removed. The button/select remains fully functional without an emoji.
    """
    if value is None or _emoji_has_custom_id(value):
        return None, False

    name = _emoji_name(value)
    if not name:
        return None, False

    replacement = _EMOJI_REPLACEMENTS.get(name)
    if replacement is not None:
        return replacement, replacement != name

    if _looks_like_unicode_emoji(name):
        return None, False

    return None, True


def sanitize_view_emojis(view: discord.ui.View) -> int:
    """Normalize unsafe component emoji in one View.

    This intentionally touches only component decoration. It never changes
    labels, custom IDs, callbacks, values, URLs or permissions.
    """
    changed = 0

    for item in view.children:
        if hasattr(item, "emoji"):
            current = getattr(item, "emoji", None)
            replacement, should_change = _normalize_emoji(current)
            if should_change:
                try:
                    setattr(item, "emoji", replacement)
                    changed += 1
                    logger.warning(
                        "Normalized invalid Discord component emoji %r -> %r on %s",
                        _emoji_name(current),
                        replacement,
                        type(item).__name__,
                    )
                except (TypeError, ValueError, AttributeError):
                    logger.exception("Failed to normalize component emoji on %s", type(item).__name__)

        # Select option emoji are serialized separately from the select itself.
        for option in getattr(item, "options", ()) or ():
            current = getattr(option, "emoji", None)
            replacement, should_change = _normalize_emoji(current)
            if not should_change:
                continue
            try:
                option.emoji = replacement
                changed += 1
                logger.warning(
                    "Normalized invalid Discord select-option emoji %r -> %r",
                    _emoji_name(current),
                    replacement,
                )
            except (TypeError, ValueError, AttributeError):
                logger.exception("Failed to normalize Discord select-option emoji")

    return changed


def install_discord_ui_guard() -> None:
    """Install one process-wide guard around discord.py View serialization."""
    view_cls = discord.ui.View
    if getattr(view_cls, "_raspberry_bot_emoji_guard", False):
        return

    original: Callable[..., Any] | None = getattr(view_cls, "to_components", None)
    if original is None:
        logger.warning("discord.ui.View.to_components unavailable; emoji guard not installed")
        return

    @functools.wraps(original)
    def guarded_to_components(self: discord.ui.View, *args: Any, **kwargs: Any) -> Any:
        sanitize_view_emojis(self)
        return original(self, *args, **kwargs)

    setattr(view_cls, "to_components", guarded_to_components)
    setattr(view_cls, "_raspberry_bot_emoji_guard", True)
    logger.info("Installed bot-wide Discord component emoji guard")
