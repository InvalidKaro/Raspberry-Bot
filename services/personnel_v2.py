from __future__ import annotations

import csv
from datetime import date, datetime
from io import StringIO


class PersonnelService:
    """Global personnel database.

    Perso data is intentionally NOT scoped to a Discord guild/server. Existing
    guild_id columns stay in SQLite for backwards compatibility with the current
    schema and old rows, but all reads and profile lookups use personnel_id/name
    only. This prevents legacy/rounded Snowflake values from hiding employees.
    """

    def __init__(self, bot) -> None:
        self.bot = bot

    async def add(
        self,
        guild_id: int,
        name: str,
        actor_id: int,
        *,
        user_id: int | None = None,
        rank: str | None = None,
        department: str | None = None,
    ):
        existing = await self.get_by_name(guild_id, name, active_only=False)
        if existing:
            await self.bot.database.execute(
                "UPDATE personnel_members "
                "SET user_id=COALESCE(?,user_id),rank_name=COALESCE(?,rank_name),"
                "department=COALESCE(?,department),active=1,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (user_id, rank, department, int(existing["id"])),
            )
            return await self.bot.database.fetchone(
                "SELECT * FROM personnel_members WHERE id=?",
                (int(existing["id"]),),
            )

        # guild_id is retained only because the legacy schema marks it NOT NULL.
        await self.bot.database.execute(
            "INSERT INTO personnel_members(guild_id,user_id,display_name,rank_name,department,created_by) "
            "VALUES(?,?,?,?,?,?)",
            (guild_id, user_id, name.strip(), rank, department, actor_id),
        )
        return await self.get_by_name(guild_id, name)

    async def get_by_name(self, guild_id: int, name: str, active_only: bool = True):
        del guild_id
        sql = (
            "SELECT * FROM personnel_members "
            "WHERE lower(trim(display_name))=lower(trim(?))"
            + (" AND active=1" if active_only else "")
            + " ORDER BY active DESC,id ASC LIMIT 1"
        )
        return await self.bot.database.fetchone(sql, (name.strip(),))

    async def list_members(self, guild_id: int, active_only: bool = True):
        del guild_id
        sql = "SELECT * FROM personnel_members"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY display_name COLLATE NOCASE,id ASC"
        return await self.bot.database.fetchall(sql)

    async def edit(
        self,
        guild_id: int,
        personnel_id: int,
        actor_id: int,
        *,
        name=None,
        rank=None,
        department=None,
        user_id=None,
    ):
        before = await self.bot.database.fetchone(
            "SELECT * FROM personnel_members WHERE id=?",
            (personnel_id,),
        )
        if not before:
            return None

        new_name = name.strip() if name else before["display_name"]
        new_rank = rank if rank is not None else before["rank_name"]
        new_department = department if department is not None else before["department"]
        new_user = user_id if user_id is not None else before["user_id"]

        if new_rank != before["rank_name"]:
            await self.bot.database.execute(
                "INSERT INTO personnel_rank_history(guild_id,personnel_id,old_rank,new_rank,changed_by) "
                "VALUES(?,?,?,?,?)",
                (guild_id, personnel_id, before["rank_name"], new_rank, actor_id),
            )

        await self.bot.database.execute(
            "UPDATE personnel_members SET display_name=?,rank_name=?,department=?,user_id=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_name, new_rank, new_department, new_user, personnel_id),
        )
        return await self.bot.database.fetchone(
            "SELECT * FROM personnel_members WHERE id=?",
            (personnel_id,),
        )

    async def archive(self, guild_id: int, personnel_id: int):
        del guild_id
        await self.bot.database.execute(
            "UPDATE personnel_members SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (personnel_id,),
        )

    async def record(
        self,
        guild_id: int,
        personnel_id: int,
        actor_id: int,
        *,
        inductions: int = 0,
        bwg: int = 0,
        record_date: str | None = None,
        period_key: str | None = None,
        note: str | None = None,
    ):
        d = record_date or date.today().isoformat()
        p = period_key or datetime.fromisoformat(d).strftime("%Y-%m")
        return await self.bot.database.execute(
            "INSERT INTO personnel_records(guild_id,personnel_id,record_date,period_key,inductions,bwg,note,created_by) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (guild_id, personnel_id, d, p, inductions, bwg, note, actor_id),
        )

    async def totals(self, guild_id: int, *, period_like: str | None = None):
        """Return every active profile and all linked records, globally."""
        del guild_id
        params: list[object] = []
        join = "LEFT JOIN personnel_records r ON r.personnel_id=m.id"
        if period_like:
            join += " AND r.period_key LIKE ?"
            params.append(period_like)

        sql = f"""
            SELECT
                m.id,
                m.display_name,
                m.rank_name,
                m.department,
                COALESCE(SUM(r.inductions),0) AS inductions,
                COALESCE(SUM(r.bwg),0) AS bwg,
                COALESCE(SUM(r.inductions+r.bwg),0) AS activity
            FROM personnel_members m
            {join}
            WHERE m.active=1
            GROUP BY m.id,m.display_name,m.rank_name,m.department
            ORDER BY activity DESC,m.display_name COLLATE NOCASE,m.id ASC
        """
        return await self.bot.database.fetchall(sql, params)

    async def history(self, guild_id: int, personnel_id: int, limit: int = 100):
        del guild_id
        return await self.bot.database.fetchall(
            "SELECT * FROM personnel_records WHERE personnel_id=? "
            "ORDER BY record_date DESC,id DESC LIMIT ?",
            (personnel_id, limit),
        )

    async def activity_feed(self, guild_id: int, limit: int = 20):
        del guild_id
        return await self.bot.database.fetchall(
            "SELECT r.*,m.display_name FROM personnel_records r "
            "JOIN personnel_members m ON m.id=r.personnel_id "
            "ORDER BY r.created_at DESC,r.id DESC LIMIT ?",
            (limit,),
        )

    async def trend(self, guild_id: int, limit: int = 12):
        del guild_id
        return await self.bot.database.fetchall(
            "SELECT period_key,COALESCE(SUM(inductions),0) inductions,"
            "COALESCE(SUM(bwg),0) bwg,COALESCE(SUM(inductions+bwg),0) activity "
            "FROM personnel_records GROUP BY period_key "
            "ORDER BY period_key DESC LIMIT ?",
            (limit,),
        )

    async def add_note(self, guild_id: int, personnel_id: int, actor_id: int, content: str):
        return await self.bot.database.execute(
            "INSERT INTO personnel_notes(guild_id,personnel_id,content,created_by) VALUES(?,?,?,?)",
            (guild_id, personnel_id, content.strip(), actor_id),
        )

    async def notes(self, guild_id: int, personnel_id: int, limit: int = 10):
        del guild_id
        return await self.bot.database.fetchall(
            "SELECT * FROM personnel_notes WHERE personnel_id=? "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (personnel_id, limit),
        )

    async def set_qualification(
        self,
        guild_id: int,
        personnel_id: int,
        actor_id: int,
        name: str,
        status: str,
    ):
        existing = await self.bot.database.fetchone(
            "SELECT id FROM personnel_qualifications "
            "WHERE personnel_id=? AND lower(trim(name))=lower(trim(?)) "
            "ORDER BY id ASC LIMIT 1",
            (personnel_id, name.strip()),
        )
        if existing:
            await self.bot.database.execute(
                "UPDATE personnel_qualifications SET status=?,created_by=?,created_at=CURRENT_TIMESTAMP WHERE id=?",
                (status.strip(), actor_id, int(existing["id"])),
            )
            return
        await self.bot.database.execute(
            "INSERT INTO personnel_qualifications(guild_id,personnel_id,name,status,created_by) "
            "VALUES(?,?,?,?,?)",
            (guild_id, personnel_id, name.strip(), status.strip(), actor_id),
        )

    async def qualifications(self, guild_id: int, personnel_id: int):
        del guild_id
        return await self.bot.database.fetchall(
            "SELECT * FROM personnel_qualifications WHERE personnel_id=? "
            "ORDER BY name COLLATE NOCASE,id ASC",
            (personnel_id,),
        )

    async def set_goal(
        self,
        guild_id: int,
        personnel_id: int | None,
        actor_id: int,
        target_type: str,
        target_value: int,
        period_key: str,
    ):
        if personnel_id is None:
            existing = await self.bot.database.fetchone(
                "SELECT id FROM personnel_goals WHERE personnel_id IS NULL "
                "AND target_type=? AND period_key=? ORDER BY id ASC LIMIT 1",
                (target_type, period_key),
            )
        else:
            existing = await self.bot.database.fetchone(
                "SELECT id FROM personnel_goals WHERE personnel_id=? "
                "AND target_type=? AND period_key=? ORDER BY id ASC LIMIT 1",
                (personnel_id, target_type, period_key),
            )
        if existing:
            await self.bot.database.execute(
                "UPDATE personnel_goals SET target_value=?,created_by=?,created_at=CURRENT_TIMESTAMP WHERE id=?",
                (target_value, actor_id, int(existing["id"])),
            )
            return
        await self.bot.database.execute(
            "INSERT INTO personnel_goals(guild_id,personnel_id,target_type,target_value,period_key,created_by) "
            "VALUES(?,?,?,?,?,?)",
            (guild_id, personnel_id, target_type, target_value, period_key, actor_id),
        )

    async def rank_history(self, guild_id: int, personnel_id: int, limit: int = 10):
        del guild_id
        return await self.bot.database.fetchall(
            "SELECT * FROM personnel_rank_history WHERE personnel_id=? "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (personnel_id, limit),
        )

    @staticmethod
    def csv_bytes(rows) -> bytes:
        stream = StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Name", "Einweisungen", "BWG", "Aktivität"])
        for row in rows:
            writer.writerow(
                [row["display_name"], row["inductions"], row["bwg"], row["activity"]]
            )
        return stream.getvalue().encode("utf-8-sig")
