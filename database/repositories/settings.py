from __future__ import annotations

from typing import Any

from database.manager import Database
from services.cache import CacheManager


class SettingsRepository:
    def __init__(self, database: Database, cache: CacheManager | None = None) -> None:
        self.db = database
        self.cache = cache

    async def get_guild_settings(self, guild_id: int) -> dict[str, Any]:
        if self.cache is not None:
            cached = await self.cache.get_cache("guild_settings").get(guild_id)
            if cached is not None:
                return dict(cached)

        row = await self.db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            await self.db.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            row = await self.db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        result = dict(row) if row is not None else {"guild_id": guild_id}

        if self.cache is not None:
            await self.cache.get_cache("guild_settings").set(guild_id, result)
        return result

    async def update_guild_settings(self, guild_id: int, **values: Any) -> None:
        allowed = {
            "embed_color",
            "ticket_category_id",
            "ticket_log_channel_id",
            "welcome_channel_id",
            "suggestion_channel_id",
            "general_log_channel_id",
            "auto_role_id",
            "welcome_message",
        }
        clean = {key: value for key, value in values.items() if key in allowed}
        if not clean:
            return
        await self.db.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
        assignments = ", ".join(f"{key} = ?" for key in clean)
        params = [*clean.values(), guild_id]
        await self.db.execute(
            f"UPDATE guild_settings SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ?",
            params,
        )
        if self.cache is not None:
            await self.cache.get_cache("guild_settings").delete(guild_id)

    async def add_ticket_staff_role(self, guild_id: int, role_id: int, permission_level: int = 10) -> None:
        await self.db.execute(
            "INSERT INTO ticket_staff_roles (guild_id, role_id, permission_level) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, role_id) DO UPDATE SET permission_level = excluded.permission_level",
            (guild_id, role_id, permission_level),
        )
        if self.cache is not None:
            await self.cache.get_cache("permissions").delete(("ticket_staff_roles", guild_id))

    async def remove_ticket_staff_role(self, guild_id: int, role_id: int) -> None:
        await self.db.execute(
            "DELETE FROM ticket_staff_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        if self.cache is not None:
            await self.cache.get_cache("permissions").delete(("ticket_staff_roles", guild_id))

    async def list_ticket_staff_roles(self, guild_id: int) -> list[int]:
        cache_key = ("ticket_staff_roles", guild_id)
        if self.cache is not None:
            cached = await self.cache.get_cache("permissions").get(cache_key)
            if cached is not None:
                return list(cached)

        rows = await self.db.fetchall(
            "SELECT role_id FROM ticket_staff_roles WHERE guild_id = ? ORDER BY permission_level DESC",
            (guild_id,),
        )
        result = [int(row["role_id"]) for row in rows]
        if self.cache is not None:
            await self.cache.get_cache("permissions").set(cache_key, result)
        return result
