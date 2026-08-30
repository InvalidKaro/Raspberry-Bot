from __future__ import annotations

import re
from pathlib import Path

import aiosqlite


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseBrowserService:
    """Read-only SQLite browser for the dashboard.

    The service never accepts raw SQL. Table and column names are validated
    against SQLite metadata before they are interpolated into a query.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(f"file:{self.database_path}?mode=ro", uri=True)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=3000")
        return db

    async def tables(self) -> dict:
        if not self.database_path.is_file():
            return {"ok": False, "message": f"Database not found: {self.database_path}"}
        db = await self._connect()
        try:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            names = [str(row[0]) for row in await cur.fetchall()]
            await cur.close()
            result = []
            for name in names:
                quoted = self._quote(name)
                cur = await db.execute(f"SELECT COUNT(*) FROM {quoted}")
                row = await cur.fetchone()
                await cur.close()
                result.append({"name": name, "rows": int(row[0] if row else 0)})
            cur = await db.execute("PRAGMA journal_mode")
            journal_row = await cur.fetchone()
            await cur.close()
        finally:
            await db.close()
        stat = self.database_path.stat()
        return {
            "ok": True,
            "path": str(self.database_path),
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
            "journal_mode": str(journal_row[0]) if journal_row else "unknown",
            "tables": result,
        }

    async def table(self, name: str, *, limit: int = 50, offset: int = 0, query: str = "") -> dict:
        limit = max(1, min(100, int(limit)))
        offset = max(0, int(offset))
        db = await self._connect()
        try:
            await self._assert_table(db, name)
            qtable = self._quote(name)
            cur = await db.execute(f"PRAGMA table_info({qtable})")
            info = await cur.fetchall()
            await cur.close()
            columns = [str(row[1]) for row in info]
            if not columns:
                return {"ok": False, "message": "Table has no columns."}

            where = ""
            params: list[object] = []
            query = query.strip()
            if query:
                searchable = [self._quote(column) for column in columns[:24]]
                where = " WHERE " + " OR ".join(f"CAST({column} AS TEXT) LIKE ?" for column in searchable)
                params.extend([f"%{query}%"] * len(searchable))

            cur = await db.execute(f"SELECT COUNT(*) FROM {qtable}{where}", params)
            total_row = await cur.fetchone()
            await cur.close()
            total = int(total_row[0] if total_row else 0)

            order = self._preferred_order(columns)
            sql = f"SELECT * FROM {qtable}{where}{order} LIMIT ? OFFSET ?"
            cur = await db.execute(sql, (*params, limit, offset))
            rows = [dict(row) for row in await cur.fetchall()]
            await cur.close()
        finally:
            await db.close()

        return {
            "ok": True,
            "table": name,
            "columns": columns,
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": query,
        }

    async def schema(self, name: str) -> dict:
        db = await self._connect()
        try:
            await self._assert_table(db, name)
            cur = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
                (name,),
            )
            row = await cur.fetchone()
            await cur.close()
        finally:
            await db.close()
        return {"ok": True, "table": name, "sql": str(row[0] if row and row[0] else "")}

    async def _assert_table(self, db: aiosqlite.Connection, name: str) -> None:
        if not _IDENT.fullmatch(name):
            raise ValueError("Invalid table name.")
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? AND name NOT LIKE 'sqlite_%'",
            (name,),
        )
        exists = await cur.fetchone()
        await cur.close()
        if exists is None:
            raise ValueError("Unknown database table.")

    @staticmethod
    def _quote(value: str) -> str:
        if not _IDENT.fullmatch(value):
            raise ValueError("Invalid SQLite identifier.")
        return f'"{value}"'

    @staticmethod
    def _preferred_order(columns: list[str]) -> str:
        for column in ("id", "created_at", "updated_at", "recorded_at", "due_at"):
            if column in columns:
                return f' ORDER BY "{column}" DESC'
        return ""
