from __future__ import annotations

from pathlib import Path

import aiosqlite


ALLOWED_FIELDS = {
    "embed_color",
    "ticket_category_id",
    "ticket_log_channel_id",
    "welcome_channel_id",
    "suggestion_channel_id",
    "general_log_channel_id",
    "auto_role_id",
    "welcome_message",
}


class BotConfigService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.database_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        await self._ensure_schema(db)
        return db

    async def _ensure_schema(self, db: aiosqlite.Connection) -> None:
        cur = await db.execute("PRAGMA table_info(guild_settings)")
        columns = {str(row[1]) for row in await cur.fetchall()}
        await cur.close()
        additions = {
            "auto_role_id": "INTEGER",
            "welcome_message": "TEXT",
        }
        changed = False
        for name, definition in additions.items():
            if name not in columns:
                await db.execute(f"ALTER TABLE guild_settings ADD COLUMN {name} {definition}")
                changed = True
        if changed:
            await db.commit()

    async def get(self, guild_id: int) -> dict:
        db = await self._connect()
        try:
            await db.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            await db.commit()
            cursor = await db.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
            row = await cursor.fetchone()
            await cursor.close()
            cursor = await db.execute(
                "SELECT role_id, permission_level FROM ticket_staff_roles WHERE guild_id = ? ORDER BY permission_level DESC, role_id",
                (guild_id,),
            )
            role_rows = await cursor.fetchall()
            await cursor.close()
        finally:
            await db.close()
        data = dict(row) if row else {"guild_id": guild_id}
        data["staff_roles"] = [
            {"role_id": int(r["role_id"]), "permission_level": int(r["permission_level"])} for r in role_rows
        ]
        return data

    @staticmethod
    def _optional_snowflake(value) -> int | None:
        if value in (None, "", 0, "0"):
            return None
        number = int(value)
        if number <= 0:
            raise ValueError("Discord IDs must be positive integers.")
        return number

    async def update(self, guild_id: int, payload: dict) -> dict:
        clean: dict[str, int | str | None] = {}
        for key in ALLOWED_FIELDS:
            if key not in payload:
                continue
            if key == "embed_color":
                raw_value = payload[key]
                if raw_value is None or str(raw_value).strip() == "":
                    clean[key] = None
                else:
                    raw = str(raw_value).strip().lstrip("#")
                    if len(raw) != 6:
                        raise ValueError("Embed color must be a six-digit hex color.")
                    value = int(raw, 16)
                    if not 0 <= value <= 0xFFFFFF:
                        raise ValueError("Embed color must be a six-digit hex color.")
                    clean[key] = value
            elif key == "welcome_message":
                value = str(payload[key] or "").strip()
                clean[key] = value[:1000] if value else None
            else:
                clean[key] = self._optional_snowflake(payload[key])

        staff_roles = payload.get("staff_roles", [])
        parsed_roles: list[tuple[int, int]] = []
        if not isinstance(staff_roles, list):
            raise ValueError("staff_roles must be a list.")
        seen: set[int] = set()
        for item in staff_roles[:50]:
            if isinstance(item, dict):
                role_id = self._optional_snowflake(item.get("role_id"))
                level = int(item.get("permission_level", 10))
            else:
                role_id = self._optional_snowflake(item)
                level = 10
            if role_id is None or role_id in seen:
                continue
            seen.add(role_id)
            parsed_roles.append((role_id, max(1, min(100, level))))

        db = await self._connect()
        try:
            await db.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            if clean:
                assignments = ", ".join(f"{key} = ?" for key in clean)
                await db.execute(
                    f"UPDATE guild_settings SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ?",
                    (*clean.values(), guild_id),
                )
            if "staff_roles" in payload:
                await db.execute("DELETE FROM ticket_staff_roles WHERE guild_id = ?", (guild_id,))
                if parsed_roles:
                    await db.executemany(
                        "INSERT INTO ticket_staff_roles (guild_id, role_id, permission_level) VALUES (?, ?, ?)",
                        [(guild_id, role_id, level) for role_id, level in parsed_roles],
                    )
            await db.commit()
        finally:
            await db.close()
        return await self.get(guild_id)

    async def list_guild_ids(self) -> list[int]:
        # Dashboard Pro runs in a separate process from discord.py, so it cannot
        # read bot.guilds directly. dashboard_runtime_state is maintained by the
        # bot's DashboardTelemetry cog for every connected guild and is therefore
        # the most reliable guild registry for the dashboard. The additional
        # tables remain useful fallbacks for older databases and startup timing.
        queries = [
            "SELECT guild_id FROM dashboard_runtime_state",
            "SELECT DISTINCT guild_id FROM dashboard_activity WHERE guild_id IS NOT NULL",
            "SELECT guild_id FROM guild_settings",
            "SELECT guild_id FROM system_monitor_config",
            "SELECT DISTINCT guild_id FROM tickets",
            "SELECT DISTINCT guild_id FROM moderation_cases",
            "SELECT DISTINCT guild_id FROM suggestions",
            "SELECT DISTINCT guild_id FROM command_usage WHERE guild_id IS NOT NULL",
        ]
        db = await self._connect()
        try:
            values: set[int] = set()
            for query in queries:
                try:
                    cursor = await db.execute(query)
                    rows = await cursor.fetchall()
                    await cursor.close()
                except aiosqlite.OperationalError:
                    continue
                for row in rows:
                    if row[0] is not None:
                        values.add(int(row[0]))
            return sorted(values)
        finally:
            await db.close()
