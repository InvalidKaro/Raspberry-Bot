from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

import aiosqlite

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cogs.management.md_weekly_planner import (  # noqa: E402
    PlannerBuilderView,
    PlannerRecoveryView,
    RemoveEntryView,
    STANDARD_RP_DAYS,
    _insert_standard_rp,
    _split_chunks,
    ensure_md_schema,
)


class FakeDatabase:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def execute(self, query: str, parameters=()) -> int:
        cursor = await self.connection.execute(query, parameters)
        await self.connection.commit()
        value = int(cursor.lastrowid or 0)
        await cursor.close()
        return value

    async def fetchone(self, query: str, parameters=()):
        cursor = await self.connection.execute(query, parameters)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetchall(self, query: str, parameters=()):
        cursor = await self.connection.execute(query, parameters)
        rows = await cursor.fetchall()
        await cursor.close()
        return rows


class FakeBot:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database


async def main() -> None:
    path = Path(tempfile.mkdtemp(prefix="mdplan-smoke-")) / "bot.sqlite3"

    # Reproduce the old live schema: planner columns existed, while Dashboard
    # Pro expected title / owner_text / sort_time compatibility fields.
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE md_weekly_drafts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                channel_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                published_at TEXT,
                UNIQUE(guild_id,owner_id)
            );
            CREATE TABLE md_weekly_entries(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                day_index INTEGER NOT NULL,
                start_sort TEXT NOT NULL,
                time_text TEXT NOT NULL,
                kind TEXT NOT NULL,
                teachers TEXT,
                topic TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO md_weekly_drafts(guild_id,owner_id,week_start,channel_id)
            VALUES(1,2,'2026-09-07',123);

            INSERT INTO md_weekly_entries(draft_id,day_index,start_sort,time_text,kind,teachers,topic)
            VALUES(1,1,'20:00','20:00','RTW-Schulung','Test Lehrer','Patientenübergabe');

            -- Legacy fixed RP slot from the old Thursday default: must move.
            INSERT INTO md_weekly_entries(draft_id,day_index,start_sort,time_text,kind,teachers,topic)
            VALUES(1,3,'22:30','ab 22:30','RP mit Staatsfraktionen','','');

            -- Intentional Thursday RP with metadata: must NOT be migrated.
            INSERT INTO md_weekly_entries(draft_id,day_index,start_sort,time_text,kind,teachers,topic)
            VALUES(1,3,'21:00','21:00','RP mit Staatsfraktionen','Leitung','Sonder-RP');
            """
        )

    connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA foreign_keys=ON")
    database = FakeDatabase(connection)
    bot = FakeBot(database)

    try:
        await ensure_md_schema(database)

        cursor = await connection.execute("PRAGMA table_info(md_weekly_entries)")
        columns = {str(row["name"]) for row in await cursor.fetchall()}
        await cursor.close()
        required = {
            "day_index",
            "start_sort",
            "sort_time",
            "time_text",
            "kind",
            "teachers",
            "topic",
            "title",
            "owner_text",
        }
        assert required <= columns, (required - columns, columns)

        row = await database.fetchone(
            "SELECT title,owner_text,start_sort,sort_time FROM md_weekly_entries WHERE id=1"
        )
        assert row["title"] == "Patientenübergabe", dict(row)
        assert row["owner_text"] == "Test Lehrer", dict(row)
        assert row["start_sort"] == "20:00", dict(row)
        assert row["sort_time"] == "20:00", dict(row)

        moved = await database.fetchone("SELECT day_index FROM md_weekly_entries WHERE id=2")
        assert int(moved["day_index"]) == 2, dict(moved)
        intentional = await database.fetchone("SELECT day_index FROM md_weekly_entries WHERE id=3")
        assert int(intentional["day_index"]) == 3, dict(intentional)

        # Exact Dashboard Pro contract, including its sort_time ordering.
        dashboard_rows = await database.fetchall(
            """SELECT e.id,d.week_start,e.day_index,e.time_text,e.title,e.owner_text,e.kind
            FROM md_weekly_entries e JOIN md_weekly_drafts d ON d.id=e.draft_id
            WHERE d.guild_id=? ORDER BY d.week_start,e.day_index,e.sort_time""",
            (1,),
        )
        assert len(dashboard_rows) == 3
        assert dashboard_rows[0]["title"] == "Patientenübergabe"

        draft_id = await database.execute(
            "INSERT INTO md_weekly_drafts(guild_id,owner_id,week_start,channel_id) VALUES(?,?,?,?)",
            (3, 4, "2026-09-07", 456),
        )
        await _insert_standard_rp(bot, draft_id)
        rows = await database.fetchall(
            "SELECT day_index,time_text,kind,title,start_sort,sort_time FROM md_weekly_entries WHERE draft_id=? ORDER BY day_index",
            (draft_id,),
        )
        assert tuple(int(row["day_index"]) for row in rows) == STANDARD_RP_DAYS, rows
        assert all(row["time_text"] == "ab 22:30" for row in rows), rows
        assert all(row["title"] == "RP mit Staatsfraktionen" for row in rows), rows
        assert all(row["start_sort"] == row["sort_time"] == "22:30" for row in rows), rows

        huge_day = "MITTWOCH\n\n" + ("Sehr langer Terminblock " * 300)
        chunks = _split_chunks("HEADER", [huge_day], "FOOTER", limit=300)
        assert len(chunks) > 1
        assert all(0 < len(chunk) <= 300 for chunk in chunks), [len(chunk) for chunk in chunks]

        view = PlannerBuilderView(bot, draft_id, 4)
        labels = {getattr(child, "label", None) for child in view.children}
        expected_labels = {"Hinzufügen", "Entfernen", "Vorschau", "Termine", "Veröffentlichen", "Hilfe"}
        assert expected_labels <= labels, labels

        recovery = PlannerRecoveryView(bot, 4)
        recovery_labels = {getattr(child, "label", None) for child in recovery.children}
        assert "Wochenplan öffnen" in recovery_labels, recovery_labels

        fake_rows = [
            {
                "id": index,
                "day_index": index % 7,
                "time_text": "20:00",
                "kind": "Testtermin",
                "teachers": "",
                "topic": "",
            }
            for index in range(1, 31)
        ]
        remove_view = RemoveEntryView(bot, draft_id, 4, fake_rows)
        assert remove_view.page_count == 2
        remove_labels = {getattr(child, "label", None) for child in remove_view.children}
        assert {"Zurück", "Weiter", "Zum Plan"} <= remove_labels, remove_labels
        select = next(child for child in remove_view.children if hasattr(child, "options"))
        assert len(select.options) == 25

        source = (REPO_ROOT / "cogs" / "management" / "md_weekly_planner.py").read_text(encoding="utf-8")
        assert "Nutze `/mdplan" not in source
        assert "mit `/mdplan remove`" not in source

        print("mdplan smoke: ok")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
