from __future__ import annotations

from typing import Any

from database.manager import Database


class ModerationRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def create_case(
        self,
        *,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        duration_seconds: int | None = None,
        expires_at: str | None = None,
    ) -> int:
        return await self.db.execute(
            "INSERT INTO moderation_cases (guild_id, user_id, moderator_id, action, reason, duration_seconds, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, action, reason, duration_seconds, expires_at),
        )

    async def get_user_cases(self, guild_id: int, user_id: int, limit: int = 25) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM moderation_cases WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, user_id, limit),
        )
        return [dict(row) for row in rows]

    async def get_case(self, guild_id: int, case_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM moderation_cases WHERE guild_id = ? AND id = ?",
            (guild_id, case_id),
        )
        return dict(row) if row else None

    async def deactivate_case(self, guild_id: int, case_id: int) -> None:
        await self.db.execute(
            "UPDATE moderation_cases SET active = 0 WHERE guild_id = ? AND id = ?",
            (guild_id, case_id),
        )
