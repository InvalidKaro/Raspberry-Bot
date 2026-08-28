from __future__ import annotations

from typing import Any

from database.manager import Database


class TicketRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def create(self, *, guild_id: int, opener_id: int, subject: str, description: str, category_name: str) -> int:
        return await self.db.execute(
            "INSERT INTO tickets (guild_id, opener_id, subject, description, category_name) VALUES (?, ?, ?, ?, ?)",
            (guild_id, opener_id, subject, description, category_name),
        )

    async def set_channel(self, ticket_id: int, channel_id: int) -> None:
        await self.db.execute(
            "UPDATE tickets SET channel_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (channel_id, ticket_id),
        )

    async def set_control_message(self, ticket_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE tickets SET control_message_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (message_id, ticket_id),
        )

    async def get_by_channel(self, channel_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
        return dict(row) if row else None

    async def get(self, ticket_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return dict(row) if row else None

    async def count_open_for_user(self, guild_id: int, opener_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM tickets WHERE guild_id = ? AND opener_id = ? AND status = 'open'",
            (guild_id, opener_id),
        )
        return int(row["count"] if row else 0)

    async def set_claimed(self, ticket_id: int, user_id: int | None) -> None:
        await self.db.execute(
            "UPDATE tickets SET claimed_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id, ticket_id),
        )

    async def set_priority(self, ticket_id: int, priority: str) -> None:
        await self.db.execute(
            "UPDATE tickets SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (priority, ticket_id),
        )

    async def add_note(self, ticket_id: int, author_id: int, content: str) -> None:
        await self.db.execute(
            "INSERT INTO ticket_notes (ticket_id, author_id, content) VALUES (?, ?, ?)",
            (ticket_id, author_id, content),
        )

    async def add_member(self, ticket_id: int, user_id: int, added_by: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO ticket_members (ticket_id, user_id, added_by) VALUES (?, ?, ?)",
            (ticket_id, user_id, added_by),
        )

    async def remove_member(self, ticket_id: int, user_id: int) -> None:
        await self.db.execute("DELETE FROM ticket_members WHERE ticket_id = ? AND user_id = ?", (ticket_id, user_id))


    async def list_notes(self, ticket_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM ticket_notes WHERE ticket_id = ? ORDER BY id DESC LIMIT ?",
            (ticket_id, limit),
        )
        return [dict(row) for row in rows]

    async def log_event(
        self,
        *,
        ticket_id: int,
        guild_id: int,
        actor_id: int | None,
        event_type: str,
        old_value: str | None = None,
        new_value: str | None = None,
        metadata: str | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO ticket_events (ticket_id, guild_id, actor_id, event_type, old_value, new_value, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, guild_id, actor_id, event_type, old_value, new_value, metadata),
        )

    async def close(self, ticket_id: int, closed_by: int, reason: str) -> None:
        await self.db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = ?, close_reason = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (closed_by, reason, ticket_id),
        )

    async def reopen(self, ticket_id: int) -> None:
        await self.db.execute(
            "UPDATE tickets SET status = 'open', closed_at = NULL, closed_by = NULL, close_reason = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ticket_id,),
        )

    async def queue(self, guild_id: int, limit: int = 25) -> list[dict[str, Any]]:
        rank = "CASE priority WHEN 'critical' THEN 5 WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'normal' THEN 2 ELSE 1 END"
        rows = await self.db.fetchall(
            f"SELECT * FROM tickets WHERE guild_id = ? AND status = 'open' ORDER BY {rank} DESC, created_at ASC LIMIT ?",
            (guild_id, limit),
        )
        return [dict(row) for row in rows]
