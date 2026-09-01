from __future__ import annotations

import csv
from datetime import date, datetime
from io import StringIO


# Older dashboard builds could round Discord snowflakes in JavaScript before
# writing them back to SQLite. At current Discord ID sizes the resulting drift
# is small (normally < 128). 512 gives us enough room to recover those rows
# without broadly mixing unrelated guilds.
_GUILD_ID_DRIFT = 512


class PersonnelService:
    def __init__(self, bot) -> None:
        self.bot = bot

    @staticmethod
    def _guild_match(column: str = "guild_id") -> str:
        return f"({column}=? OR ABS(CAST({column} AS INTEGER)-?)<=?)"

    @staticmethod
    def _guild_params(guild_id: int) -> tuple[int, int, int]:
        gid = int(guild_id)
        return gid, gid, _GUILD_ID_DRIFT

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
            # Do not overwrite the display name here: get_by_name already found
            # the canonical DB profile and /perso edit is responsible for renames.
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

        await self.bot.database.execute(
            "INSERT INTO personnel_members(guild_id,user_id,display_name,rank_name,department,created_by) "
            "VALUES(?,?,?,?,?,?)",
            (guild_id, user_id, name.strip(), rank, department, actor_id),
        )
        return await self.get_by_name(guild_id, name)

    async def get_by_name(self, guild_id: int, name: str, active_only: bool = True):
        match = self._guild_match("guild_id")
        sql = (
            f"SELECT * FROM personnel_members WHERE {match} "
            "AND lower(trim(display_name))=lower(trim(?))"
            + (" AND active=1" if active_only else "")
            + " ORDER BY CASE WHEN guild_id=? THEN 0 ELSE 1 END, active DESC, id ASC LIMIT 1"
        )
        params = (*self._guild_params(guild_id), name.strip(), int(guild_id))
        return await self.bot.database.fetchone(sql, params)

    async def list_members(self, guild_id: int, active_only: bool = True):
        match = self._guild_match("guild_id")
        sql = (
            f"SELECT * FROM personnel_members WHERE {match}"
            + (" AND active=1" if active_only else "")
            + " ORDER BY display_name COLLATE NOCASE,id ASC"
        )
        return await self.bot.database.fetchall(sql, self._guild_params(guild_id))

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
        match = self._guild_match("guild_id")
        before = await self.bot.database.fetchone(
            f"SELECT * FROM personnel_members WHERE id=? AND {match}",
            (personnel_id, *self._guild_params(guild_id)),
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

        # Update by primary key after the guild ownership check above. This also
        # works for profiles whose old guild_id was rounded by the dashboard.
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
        match = self._guild_match("guild_id")
        await self.bot.database.execute(
            f"UPDATE personnel_members SET active=0,updated_at=CURRENT_TIMESTAMP "
            f"WHERE id=? AND {match}",
            (personnel_id, *self._guild_params(guild_id)),
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
        """Return every active personnel profile belonging to this guild.

        Guild ownership accepts a tiny Snowflake drift to recover rows modified
        by older dashboard builds. The profile name always comes directly from
        personnel_members, so dashboard DB name edits are reflected immediately.
        """

        member_match = self._guild_match("m.guild_id")
        record_match = self._guild_match("r.guild_id")
        recovery_match = self._guild_match("rx.guild_id")

        record_filter = record_match
        record_params: list[object] = list(self._guild_params(guild_id))
        if period_like:
            record_filter += " AND r.period_key LIKE ?"
            record_params.append(period_like)

        # Each aggregate gets its own complete filter/parameter set. The old
        # query applied period_like only to inductions, which could also make
        # overview/report data appear inconsistent.
        params: list[object] = []
        params.extend(record_params)  # inductions
        params.extend(record_params)  # bwg
        params.extend(record_params)  # activity
        params.extend(self._guild_params(guild_id))  # member profile ownership
        params.extend(self._guild_params(guild_id))  # record based recovery

        sql = f"""
            SELECT
                m.id,
                m.display_name,
                m.rank_name,
                m.department,
                COALESCE(SUM(CASE WHEN {record_filter} THEN r.inductions ELSE 0 END),0) AS inductions,
                COALESCE(SUM(CASE WHEN {record_filter} THEN r.bwg ELSE 0 END),0) AS bwg,
                COALESCE(SUM(CASE WHEN {record_filter} THEN r.inductions+r.bwg ELSE 0 END),0) AS activity
            FROM personnel_members m
            LEFT JOIN personnel_records r ON r.personnel_id=m.id
            WHERE m.active=1
              AND (
                    {member_match}
                    OR EXISTS(
                        SELECT 1
                        FROM personnel_records rx
                        WHERE rx.personnel_id=m.id
                          AND {recovery_match}
                    )
                  )
            GROUP BY m.id,m.display_name,m.rank_name,m.department
            ORDER BY activity DESC,m.display_name COLLATE NOCASE,m.id ASC
        """
        return await self.bot.database.fetchall(sql, params)

    async def history(self, guild_id: int, personnel_id: int, limit: int = 100):
        match = self._guild_match("guild_id")
        return await self.bot.database.fetchall(
            f"SELECT * FROM personnel_records WHERE {match} AND personnel_id=? "
            "ORDER BY record_date DESC,id DESC LIMIT ?",
            (*self._guild_params(guild_id), personnel_id, limit),
        )

    async def activity_feed(self, guild_id: int, limit: int = 20):
        match = self._guild_match("r.guild_id")
        return await self.bot.database.fetchall(
            f"SELECT r.*,m.display_name FROM personnel_records r "
            f"JOIN personnel_members m ON m.id=r.personnel_id WHERE {match} "
            "ORDER BY r.created_at DESC,r.id DESC LIMIT ?",
            (*self._guild_params(guild_id), limit),
        )

    async def trend(self, guild_id: int, limit: int = 12):
        match = self._guild_match("guild_id")
        return await self.bot.database.fetchall(
            f"SELECT period_key,COALESCE(SUM(inductions),0) inductions,"
            f"COALESCE(SUM(bwg),0) bwg,COALESCE(SUM(inductions+bwg),0) activity "
            f"FROM personnel_records WHERE {match} GROUP BY period_key "
            "ORDER BY period_key DESC LIMIT ?",
            (*self._guild_params(guild_id), limit),
        )

    async def add_note(self, guild_id: int, personnel_id: int, actor_id: int, content: str):
        return await self.bot.database.execute(
            "INSERT INTO personnel_notes(guild_id,personnel_id,content,created_by) VALUES(?,?,?,?)",
            (guild_id, personnel_id, content.strip(), actor_id),
        )

    async def notes(self, guild_id: int, personnel_id: int, limit: int = 10):
        match = self._guild_match("guild_id")
        return await self.bot.database.fetchall(
            f"SELECT * FROM personnel_notes WHERE {match} AND personnel_id=? "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (*self._guild_params(guild_id), personnel_id, limit),
        )

    async def set_qualification(
        self,
        guild_id: int,
        personnel_id: int,
        actor_id: int,
        name: str,
        status: str,
    ):
        await self.bot.database.execute(
            "INSERT INTO personnel_qualifications(guild_id,personnel_id,name,status,created_by) "
            "VALUES(?,?,?,?,?) ON CONFLICT(guild_id,personnel_id,name) DO UPDATE SET "
            "status=excluded.status,created_by=excluded.created_by,created_at=CURRENT_TIMESTAMP",
            (guild_id, personnel_id, name.strip(), status.strip(), actor_id),
        )

    async def qualifications(self, guild_id: int, personnel_id: int):
        match = self._guild_match("guild_id")
        return await self.bot.database.fetchall(
            f"SELECT * FROM personnel_qualifications WHERE {match} AND personnel_id=? "
            "ORDER BY name COLLATE NOCASE",
            (*self._guild_params(guild_id), personnel_id),
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
        await self.bot.database.execute(
            "INSERT INTO personnel_goals(guild_id,personnel_id,target_type,target_value,period_key,created_by) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,personnel_id,target_type,period_key) DO UPDATE SET "
            "target_value=excluded.target_value,created_by=excluded.created_by,created_at=CURRENT_TIMESTAMP",
            (guild_id, personnel_id, target_type, target_value, period_key, actor_id),
        )

    async def rank_history(self, guild_id: int, personnel_id: int, limit: int = 10):
        match = self._guild_match("guild_id")
        return await self.bot.database.fetchall(
            f"SELECT * FROM personnel_rank_history WHERE {match} AND personnel_id=? "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (*self._guild_params(guild_id), personnel_id, limit),
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
