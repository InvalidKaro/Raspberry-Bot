from __future__ import annotations

from pathlib import Path

import aiosqlite


class BotDataService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.database_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def _scalar(self, db: aiosqlite.Connection, sql: str, params=()) -> int:
        try:
            cur = await db.execute(sql, params)
            row = await cur.fetchone()
            await cur.close()
            return int(row[0] or 0) if row else 0
        except aiosqlite.OperationalError:
            return 0

    async def overview(self) -> dict:
        if not self.database_path.is_file():
            return {"ok": False, "message": f"Database not found: {self.database_path}"}
        db = await self._connect()
        try:
            counts = {
                "tickets_open": await self._scalar(db, "SELECT COUNT(*) FROM tickets WHERE status='open'"),
                "tickets_total": await self._scalar(db, "SELECT COUNT(*) FROM tickets"),
                "moderation_cases": await self._scalar(db, "SELECT COUNT(*) FROM moderation_cases"),
                "suggestions_open": await self._scalar(db, "SELECT COUNT(*) FROM suggestions WHERE status='open'"),
                "commands_24h": await self._scalar(db, "SELECT COUNT(*) FROM command_usage WHERE created_at >= datetime('now','-1 day')"),
                "commands_total": await self._scalar(db, "SELECT COUNT(*) FROM command_usage"),
            }
            recent_tickets = await self._rows(db, "SELECT id,guild_id,channel_id,opener_id,subject,priority,status,claimed_by,created_at FROM tickets ORDER BY id DESC LIMIT 15")
            recent_cases = await self._rows(db, "SELECT id,guild_id,user_id,moderator_id,action,reason,active,created_at FROM moderation_cases ORDER BY id DESC LIMIT 15")
            top_commands = await self._rows(db, "SELECT command_name,COUNT(*) AS uses FROM command_usage GROUP BY command_name ORDER BY uses DESC, command_name LIMIT 12")
            guilds = await self._rows(db, "SELECT guild_id, updated_at FROM guild_settings ORDER BY updated_at DESC LIMIT 50")
        finally:
            await db.close()
        return {
            "ok": True,
            "counts": counts,
            "recent_tickets": recent_tickets,
            "recent_cases": recent_cases,
            "top_commands": top_commands,
            "guilds": guilds,
        }

    async def _rows(self, db: aiosqlite.Connection, sql: str, params=()) -> list[dict]:
        try:
            cur = await db.execute(sql, params)
            rows = await cur.fetchall()
            await cur.close()
            return [dict(row) for row in rows]
        except aiosqlite.OperationalError:
            return []

    async def metrics(self, hours: int = 24) -> dict:
        hours = max(1, min(168, int(hours)))
        if not self.database_path.is_file():
            return {"ok": False, "rows": []}
        db = await self._connect()
        try:
            rows = await self._rows(db, """
                SELECT recorded_at,
                       AVG(cpu_percent) AS cpu_percent,
                       AVG(temperature) AS temperature,
                       AVG(ram_percent) AS ram_percent,
                       AVG(disk_percent) AS disk_percent,
                       AVG(bot_memory) AS bot_memory
                FROM system_metrics
                WHERE recorded_at >= datetime('now', ?)
                GROUP BY recorded_at
                ORDER BY recorded_at ASC
                LIMIT 600
            """, (f"-{hours} hours",))
        finally:
            await db.close()
        return {"ok": True, "hours": hours, "rows": rows}
