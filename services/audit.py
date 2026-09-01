from __future__ import annotations
import json
from typing import Any

class AuditService:
    def __init__(self, database) -> None:
        self.database = database

    async def record(
        self,
        action: str,
        *,
        guild_id: int | None = None,
        actor_id: int | None = None,
        target_type: str | None = None,
        target_id: str | int | None = None,
        before: Any = None,
        after: Any = None,
        metadata: Any = None,
    ) -> None:
        await self.database.execute(
            """INSERT INTO bot_audit_log
            (guild_id, actor_id, action, target_type, target_id, before_json, after_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                guild_id, actor_id, action, target_type,
                str(target_id) if target_id is not None else None,
                json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
                json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
                json.dumps(metadata, ensure_ascii=False, default=str) if metadata is not None else None,
            ),
        )
