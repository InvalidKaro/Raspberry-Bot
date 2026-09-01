from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseAdminService:
    """Safe, metadata-driven SQLite CRUD for the authenticated dashboard.

    Raw SQL is never accepted. Every table/column is validated, values are
    parameterized, update/delete require a real primary key, writes are guarded
    by an optimistic row snapshot, and every mutation creates a DB backup first.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.backup_dir = database_path.parent / "dashboard-edit-backups"

    async def metadata(self, table: str) -> dict:
        db = await self._connect(readonly=True)
        try:
            await self._assert_table(db, table)
            columns = await self._columns(db, table)
            pks = self._primary_keys(columns, required=False)
            return {
                "ok": True,
                "table": table,
                "columns": columns,
                "primary_key": pks,
                "editable": bool(pks),
                "message": "" if pks else "No primary key: update/delete disabled for safety.",
            }
        finally:
            await db.close()

    async def insert(self, table: str, values: dict) -> dict:
        db = await self._connect(readonly=False)
        try:
            await self._assert_table(db, table)
            columns = await self._columns(db, table)
            allowed = {row["name"]: row for row in columns}
            clean = self._clean_values(values, allowed)
            if not clean:
                raise ValueError("At least one value is required.")
            backup = await self._backup()
            names = list(clean)
            sql = (
                f"INSERT INTO {self._quote(table)} "
                f"({', '.join(self._quote(x) for x in names)}) "
                f"VALUES ({', '.join('?' for _ in names)})"
            )
            cur = await db.execute(sql, tuple(clean[name] for name in names))
            await db.commit()
            rowid = int(cur.lastrowid or 0)
            await cur.close()
            return {"ok": True, "message": "Entry created.", "rowid": rowid, "backup": backup}
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def update(self, table: str, key: dict, values: dict, expected: dict | None = None) -> dict:
        db = await self._connect(readonly=False)
        try:
            await self._assert_table(db, table)
            columns = await self._columns(db, table)
            allowed = {row["name"]: row for row in columns}
            pks = self._primary_keys(columns)
            self._validate_key(key, pks)
            current = await self._get(db, table, key, pks)
            if current is None:
                raise ValueError("Entry no longer exists.")
            self._check_expected(current, expected, allowed)
            clean = self._clean_values(values, allowed)
            for pk in pks:
                clean.pop(pk, None)
            clean = {name: value for name, value in clean.items() if not self._values_equal(current.get(name), value, allowed[name])}
            if not clean:
                return {"ok": True, "message": "No changes detected.", "changed": 0}
            backup = await self._backup()
            where, params = self._where(key, pks)
            set_sql = ", ".join(f"{self._quote(name)}=?" for name in clean)
            cur = await db.execute(
                f"UPDATE {self._quote(table)} SET {set_sql} WHERE {where}",
                tuple(clean[name] for name in clean) + tuple(params),
            )
            if cur.rowcount != 1:
                await cur.close()
                raise ValueError("Update was not uniquely targeted; nothing was committed.")
            await cur.close()
            await db.commit()
            return {"ok": True, "message": "Entry updated.", "changed": 1, "backup": backup}
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def delete(self, table: str, key: dict, expected: dict | None = None) -> dict:
        db = await self._connect(readonly=False)
        try:
            await self._assert_table(db, table)
            columns = await self._columns(db, table)
            allowed = {row["name"]: row for row in columns}
            pks = self._primary_keys(columns)
            self._validate_key(key, pks)
            current = await self._get(db, table, key, pks)
            if current is None:
                raise ValueError("Entry no longer exists.")
            self._check_expected(current, expected, allowed)
            backup = await self._backup()
            where, params = self._where(key, pks)
            cur = await db.execute(f"DELETE FROM {self._quote(table)} WHERE {where}", tuple(params))
            if cur.rowcount != 1:
                await cur.close()
                raise ValueError("Delete was not uniquely targeted; nothing was committed.")
            await cur.close()
            await db.commit()
            return {"ok": True, "message": "Entry deleted.", "deleted": 1, "backup": backup}
        except sqlite3.IntegrityError as exc:
            await db.rollback()
            raise ValueError(f"Delete blocked by related data: {exc}") from exc
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def _connect(self, *, readonly: bool) -> aiosqlite.Connection:
        if readonly:
            db = await aiosqlite.connect(f"file:{self.database_path}?mode=ro", uri=True)
        else:
            db = await aiosqlite.connect(self.database_path)
            await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def _columns(self, db: aiosqlite.Connection, table: str) -> list[dict]:
        cur = await db.execute(f"PRAGMA table_info({self._quote(table)})")
        rows = await cur.fetchall()
        await cur.close()
        return [{
            "name": str(row[1]), "type": str(row[2] or ""), "notnull": bool(row[3]),
            "default": row[4], "pk": int(row[5]),
        } for row in rows]

    async def _assert_table(self, db: aiosqlite.Connection, table: str) -> None:
        if not _IDENT.fullmatch(table):
            raise ValueError("Invalid table name.")
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? AND name NOT LIKE 'sqlite_%'", (table,)
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise ValueError("Unknown table.")

    async def _get(self, db: aiosqlite.Connection, table: str, key: dict, pks: list[str]) -> dict | None:
        where, params = self._where(key, pks)
        cur = await db.execute(f"SELECT * FROM {self._quote(table)} WHERE {where} LIMIT 2", tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        if len(rows) > 1:
            raise ValueError("Primary key matched multiple entries.")
        return dict(rows[0]) if rows else None

    async def _backup(self) -> str:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        target = self.backup_dir / f"before-dashboard-edit-{stamp}.sqlite3"

        def make_backup() -> None:
            src = sqlite3.connect(self.database_path)
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close(); src.close()
            files = sorted(self.backup_dir.glob("before-dashboard-edit-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[20:]:
                old.unlink(missing_ok=True)

        await asyncio.to_thread(make_backup)
        return str(target)

    @staticmethod
    def _quote(name: str) -> str:
        if not _IDENT.fullmatch(name):
            raise ValueError("Invalid SQLite identifier.")
        return f'"{name}"'

    @staticmethod
    def _primary_keys(columns: list[dict], required: bool = True) -> list[str]:
        pks = [row["name"] for row in sorted(columns, key=lambda r: r["pk"] or 9999) if row["pk"]]
        if required and not pks:
            raise ValueError("This table has no primary key, so editing is disabled for safety.")
        return pks

    @staticmethod
    def _validate_key(key: dict, pks: list[str]) -> None:
        if not isinstance(key, dict) or set(key) != set(pks):
            raise ValueError(f"Complete primary key required: {', '.join(pks)}")

    @staticmethod
    def _where(key: dict, pks: list[str]) -> tuple[str, list]:
        parts, params = [], []
        for name in pks:
            value = key[name]
            if value is None:
                parts.append(f'{DatabaseAdminService._quote(name)} IS NULL')
            else:
                parts.append(f'{DatabaseAdminService._quote(name)}=?'); params.append(value)
        return " AND ".join(parts), params

    @staticmethod
    def _clean_values(values: dict, allowed: dict[str, dict]) -> dict:
        if not isinstance(values, dict):
            raise ValueError("Values must be an object.")
        clean = {}
        for name, value in values.items():
            if name not in allowed:
                raise ValueError(f"Unknown column: {name}")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if value is None and allowed[name]["notnull"] and allowed[name]["default"] is None:
                raise ValueError(f"{name} cannot be NULL.")
            clean[name] = value
        return clean

    @staticmethod
    def _is_numeric(column: dict) -> bool:
        type_name = str(column.get("type") or "").upper()
        return any(token in type_name for token in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC"))

    @staticmethod
    def _normalize(value, column: dict):
        if value is None:
            return None
        if DatabaseAdminService._is_numeric(column):
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str):
                text = value.strip()
                try:
                    if any(token in str(column.get("type") or "").upper() for token in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
                        return float(text)
                    return int(text)
                except ValueError:
                    return value
        return value

    @staticmethod
    def _values_equal(left, right, column: dict) -> bool:
        return DatabaseAdminService._normalize(left, column) == DatabaseAdminService._normalize(right, column)

    @staticmethod
    def _check_expected(current: dict, expected: dict | None, allowed: dict[str, dict]) -> None:
        if expected is None:
            return
        if not isinstance(expected, dict):
            raise ValueError("Invalid row snapshot.")
        for name, value in expected.items():
            column = allowed.get(name)
            if column is not None and name in current and not DatabaseAdminService._values_equal(current[name], value, column):
                raise ValueError("This entry changed since you opened it. Reload before editing again.")
