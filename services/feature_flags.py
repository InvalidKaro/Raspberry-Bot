from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass


FEATURE_FLAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_feature_flags(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    feature_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id,user_id,feature_key)
)
"""

# Dashboard catalog names that intentionally differ from slash-command roots.
ROOT_ALIASES: dict[str, tuple[str, ...]] = {
    "ticket": ("tickets",),
    "mod": ("moderation",),
    "system": ("history", "thermal"),
    "automation": ("workflows",),
    "creator": ("messages", "panels"),
}


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    allowed: bool
    matched_key: str | None = None
    scope: str | None = None


def command_feature_candidates(qualified_name: str) -> tuple[str, ...]:
    """Return specific-to-general Feature Lab keys for one slash command.

    Examples for ``media youtube play`` include ``command.media.youtube.play``,
    ``media.youtube.play``, ``command.media.youtube``, ``media.youtube`` and
    finally ``command.media`` / ``media``. This keeps Feature Lab useful both
    for broad suites and for individual subcommands without hard-coding every
    command in the bot.
    """

    parts = [
        re.sub(r"[^a-z0-9_-]+", "-", part.lower()).strip("-")
        for part in str(qualified_name or "").split()
    ]
    parts = [part for part in parts if part]
    if not parts:
        return ()

    candidates: list[str] = []
    for length in range(len(parts), 0, -1):
        stem = ".".join(parts[:length])
        candidates.extend((f"command.{stem}", stem))

    root = parts[0]
    for alias in ROOT_ALIASES.get(root, ()):
        candidates.extend((f"command.{alias}", alias))

    # Preserve order while removing duplicates.
    return tuple(dict.fromkeys(candidates))


class FeatureFlagService:
    """Low-overhead runtime bridge for Dashboard Pro's Feature Lab.

    Flags are cached per guild/user for a few seconds so normal slash-command
    traffic does not add a SQLite query per interaction. A user-specific flag
    overrides a guild-wide flag at the same feature specificity. More specific
    keys always win over broader keys.
    """

    def __init__(self, database, *, ttl_seconds: float = 5.0) -> None:
        self.database = database
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._cache: dict[tuple[int, int], tuple[float, dict[tuple[int, str], bool]]] = {}
        self._lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        await self.database.execute(FEATURE_FLAG_SCHEMA)

    async def _flags(self, guild_id: int, user_id: int) -> dict[tuple[int, str], bool]:
        cache_key = (guild_id, user_id)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self.ttl_seconds:
            return cached[1]

        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.ttl_seconds:
                return cached[1]
            try:
                rows = await self.database.fetchall(
                    """
                    SELECT user_id,feature_key,enabled
                    FROM dashboard_feature_flags
                    WHERE guild_id=? AND user_id IN (0,?)
                    """,
                    (guild_id, user_id),
                )
            except Exception:
                # Feature Lab must fail open if its optional table is damaged or
                # temporarily unavailable; it must never take the bot offline.
                return {}
            flags = {
                (int(row["user_id"]), str(row["feature_key"]).strip().lower()): bool(row["enabled"])
                for row in rows
            }
            self._cache[cache_key] = (now, flags)
            return flags

    async def decision(self, guild_id: int | None, user_id: int, qualified_name: str) -> FeatureDecision:
        if guild_id is None:
            return FeatureDecision(True)
        candidates = command_feature_candidates(qualified_name)
        if not candidates:
            return FeatureDecision(True)

        flags = await self._flags(int(guild_id), int(user_id))
        for key in candidates:
            user_match = flags.get((int(user_id), key))
            if user_match is not None:
                return FeatureDecision(user_match, key, "user")
            guild_match = flags.get((0, key))
            if guild_match is not None:
                return FeatureDecision(guild_match, key, "guild")
        return FeatureDecision(True)

    def invalidate(self, guild_id: int | None = None, user_id: int | None = None) -> None:
        if guild_id is None:
            self._cache.clear()
            return
        for key in list(self._cache):
            if key[0] == int(guild_id) and (user_id is None or key[1] == int(user_id)):
                self._cache.pop(key, None)
