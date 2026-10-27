"""Rookie subteam membership and live personnel queries.

Membership intervals preserve historical attribution when someone leaves the team.
All activities remain in personnel_records and are never duplicated.
"""
from __future__ import annotations

from datetime import date, timedelta


class RookieService:
    def __init__(self, database):
        self.db = database

    async def ensure_schema(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS personnel_rookie_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                personnel_id INTEGER NOT NULL REFERENCES personnel_members(id),
                joined_on TEXT NOT NULL,
                left_on TEXT,
                changed_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(left_on IS NULL OR left_on >= joined_on)
            )
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rookie_member_dates
            ON personnel_rookie_memberships(personnel_id, joined_on, left_on)
        """)

    async def join(self, personnel_id: int, actor_id: int):
        await self.ensure_schema()
        active = await self.db.fetchone(
            "SELECT id FROM personnel_rookie_memberships WHERE personnel_id=? AND left_on IS NULL",
            (personnel_id,),
        )
        if active:
            return False
        await self.db.execute(
            "INSERT INTO personnel_rookie_memberships(personnel_id,joined_on,changed_by) VALUES(?,?,?)",
            (personnel_id, date.today().isoformat(), actor_id),
        )
        return True

    async def leave(self, personnel_id: int):
        await self.ensure_schema()
        active = await self.db.fetchone(
            "SELECT id FROM personnel_rookie_memberships WHERE personnel_id=? AND left_on IS NULL",
            (personnel_id,),
        )
        if not active:
            return False
        await self.db.execute(
            "UPDATE personnel_rookie_memberships SET left_on=? WHERE id=?",
            (date.today().isoformat(), int(active["id"])),
        )
        return True

    @staticmethod
    def _date_bounds(period: str):
        today = date.today()
        if period == "week":
            start = today - timedelta(days=today.weekday())
        elif period == "month":
            start = today.replace(day=1)
        elif period == "all":
            start = date(1, 1, 1)
        else:
            raise ValueError("Unknown period")
        return start.isoformat(), today.isoformat()

    async def statistics(self, *, period: str = "week", rookie_only: bool = False):
        await self.ensure_schema()
        start, end = self._date_bounds(period)
        extra = """
            AND EXISTS (
                SELECT 1 FROM personnel_rookie_memberships rm
                WHERE rm.personnel_id=r.personnel_id
                  AND r.record_date >= rm.joined_on
                  AND (rm.left_on IS NULL OR r.record_date <= rm.left_on)
            )
        """ if rookie_only else ""
        return await self.db.fetchall(
            f"""SELECT m.id,m.display_name,
                COALESCE(SUM(r.bwg),0) AS bwg,
                COALESCE(SUM(r.inductions),0) AS inductions,
                COALESCE(SUM(r.bwg+r.inductions),0) AS activity
            FROM personnel_records r
            JOIN personnel_members m ON m.id=r.personnel_id
            WHERE r.record_date BETWEEN ? AND ? {extra}
            GROUP BY m.id,m.display_name
            ORDER BY activity DESC,m.display_name COLLATE NOCASE LIMIT 100""",
            (start, end),
        )

    async def members(self):
        await self.ensure_schema()
        return await self.db.fetchall(
            """SELECT m.id,m.display_name,rm.joined_on
            FROM personnel_rookie_memberships rm
            JOIN personnel_members m ON m.id=rm.personnel_id
            WHERE rm.left_on IS NULL AND m.active=1
            ORDER BY m.display_name COLLATE NOCASE LIMIT 100"""
        )

    async def records(self, *, period: str = "week", rookie_only: bool = False, limit: int = 20, offset: int = 0):
        await self.ensure_schema()
        start, end = self._date_bounds(period)
        extra = """
            AND EXISTS (
                SELECT 1 FROM personnel_rookie_memberships rm
                WHERE rm.personnel_id=r.personnel_id
                AND r.record_date >= rm.joined_on
                AND (rm.left_on IS NULL OR r.record_date <= rm.left_on)
            )
        """ if rookie_only else ""
        return await self.db.fetchall(
            f"""SELECT r.id,r.record_date,r.bwg,r.inductions,m.display_name
            FROM personnel_records r JOIN personnel_members m ON m.id=r.personnel_id
            WHERE r.record_date BETWEEN ? AND ? {extra}
            ORDER BY r.record_date DESC,r.id DESC LIMIT ? OFFSET ?""",
            (start, end, min(max(limit, 1), 25), max(offset, 0)),
        )
