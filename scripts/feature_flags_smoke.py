from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from database.manager import Database
from services.feature_flags import FeatureFlagService, command_feature_candidates


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="feature-flags-smoke-") as temp_name:
        database = Database(Path(temp_name) / "bot.sqlite3")
        await database.connect()
        try:
            flags = FeatureFlagService(database, ttl_seconds=1)
            await flags.ensure_schema()

            candidates = command_feature_candidates("media youtube play")
            assert candidates[:4] == (
                "command.media.youtube.play",
                "media.youtube.play",
                "command.media.youtube",
                "media.youtube",
            ), candidates
            assert "media" in candidates

            decision = await flags.decision(1, 42, "media youtube play")
            assert decision.allowed is True

            await database.execute(
                "INSERT INTO dashboard_feature_flags(guild_id,user_id,feature_key,enabled) VALUES(1,0,'media',0)"
            )
            flags.invalidate()
            decision = await flags.decision(1, 42, "media youtube play")
            assert decision.allowed is False and decision.matched_key == "media" and decision.scope == "guild", decision

            await database.execute(
                "INSERT INTO dashboard_feature_flags(guild_id,user_id,feature_key,enabled) VALUES(1,42,'media',1)"
            )
            flags.invalidate()
            decision = await flags.decision(1, 42, "media youtube play")
            assert decision.allowed is True and decision.scope == "user", decision

            # A more specific guild flag wins over a broader per-user override.
            await database.execute(
                "INSERT INTO dashboard_feature_flags(guild_id,user_id,feature_key,enabled) VALUES(1,0,'media.youtube',0)"
            )
            flags.invalidate()
            decision = await flags.decision(1, 42, "media youtube play")
            assert decision.allowed is False and decision.matched_key == "media.youtube", decision

            # The user can be explicitly opted back in at the same specificity.
            await database.execute(
                "INSERT INTO dashboard_feature_flags(guild_id,user_id,feature_key,enabled) VALUES(1,42,'media.youtube',1)"
            )
            flags.invalidate()
            decision = await flags.decision(1, 42, "media youtube play")
            assert decision.allowed is True and decision.matched_key == "media.youtube" and decision.scope == "user", decision

            # DMs are never accidentally blocked by guild-scoped flags.
            decision = await flags.decision(None, 42, "media youtube play")
            assert decision.allowed is True

            print("Feature Lab runtime smoke test passed")
        finally:
            await database.close()


if __name__ == "__main__":
    asyncio.run(main())
