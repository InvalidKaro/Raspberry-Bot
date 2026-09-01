from __future__ import annotations
from datetime import date, datetime
from io import StringIO
import csv


class PersonnelService:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def add(self, guild_id: int, name: str, actor_id: int, *, user_id: int | None = None, rank: str | None = None, department: str | None = None):
        existing = await self.get_by_name(guild_id, name, active_only=False)
        if existing:
            await self.bot.database.execute(
                "UPDATE personnel_members SET user_id=COALESCE(?,user_id),rank_name=COALESCE(?,rank_name),department=COALESCE(?,department),active=1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?",
                (user_id, rank, department, int(existing["id"]), guild_id),
            )
            return await self.bot.database.fetchone("SELECT * FROM personnel_members WHERE id=? AND guild_id=?", (int(existing["id"]), guild_id))
        await self.bot.database.execute(
            "INSERT INTO personnel_members(guild_id,user_id,display_name,rank_name,department,created_by) VALUES(?,?,?,?,?,?)",
            (guild_id, user_id, name.strip(), rank, department, actor_id),
        )
        return await self.get_by_name(guild_id, name)

    async def get_by_name(self, guild_id: int, name: str, active_only: bool = True):
        sql = "SELECT * FROM personnel_members WHERE guild_id=? AND lower(display_name)=lower(?)"
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY active DESC,id ASC LIMIT 1"
        return await self.bot.database.fetchone(sql, (guild_id, name.strip()))

    async def list_members(self, guild_id: int, active_only: bool = True):
        sql = "SELECT * FROM personnel_members WHERE guild_id=?"
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY display_name COLLATE NOCASE,id ASC"
        return await self.bot.database.fetchall(sql, (guild_id,))

    async def edit(self, guild_id: int, personnel_id: int, actor_id: int, *, name=None, rank=None, department=None, user_id=None):
        before = await self.bot.database.fetchone("SELECT * FROM personnel_members WHERE id=? AND guild_id=?", (personnel_id, guild_id))
        if not before:
            return None
        new_name = name.strip() if name else before["display_name"]
        new_rank = rank if rank is not None else before["rank_name"]
        new_department = department if department is not None else before["department"]
        new_user = user_id if user_id is not None else before["user_id"]
        if new_rank != before["rank_name"]:
            await self.bot.database.execute("INSERT INTO personnel_rank_history(guild_id,personnel_id,old_rank,new_rank,changed_by) VALUES(?,?,?,?,?)", (guild_id, personnel_id, before["rank_name"], new_rank, actor_id))
        await self.bot.database.execute("UPDATE personnel_members SET display_name=?,rank_name=?,department=?,user_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?", (new_name,new_rank,new_department,new_user,personnel_id,guild_id))
        return await self.bot.database.fetchone("SELECT * FROM personnel_members WHERE id=? AND guild_id=?", (personnel_id,guild_id))

    async def archive(self, guild_id: int, personnel_id: int):
        await self.bot.database.execute("UPDATE personnel_members SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?", (personnel_id,guild_id))

    async def record(self, guild_id: int, personnel_id: int, actor_id: int, *, inductions: int = 0, bwg: int = 0, record_date: str | None = None, period_key: str | None = None, note: str | None = None):
        d = record_date or date.today().isoformat()
        p = period_key or datetime.fromisoformat(d).strftime("%Y-%m")
        return await self.bot.database.execute("INSERT INTO personnel_records(guild_id,personnel_id,record_date,period_key,inductions,bwg,note,created_by) VALUES(?,?,?,?,?,?,?,?)", (guild_id,personnel_id,d,p,inductions,bwg,note,actor_id))

    async def totals(self, guild_id: int, *, period_like: str | None = None):
        params: list[object] = [guild_id, guild_id]
        record_filter = ""
        if period_like:
            record_filter = " AND r.period_key LIKE ?"
            params.append(period_like)
        return await self.bot.database.fetchall(
            f"""SELECT m.id,m.display_name,m.rank_name,m.department,COALESCE(SUM(r.inductions),0) inductions,COALESCE(SUM(r.bwg),0) bwg,COALESCE(SUM(r.inductions+r.bwg),0) activity
            FROM personnel_members m LEFT JOIN personnel_records r ON r.personnel_id=m.id AND r.guild_id=?{record_filter}
            WHERE m.guild_id=? AND m.active=1 GROUP BY m.id ORDER BY activity DESC,m.display_name COLLATE NOCASE""",
            ([guild_id, period_like, guild_id] if period_like else params),
        )

    async def history(self, guild_id: int, personnel_id: int, limit: int = 100):
        return await self.bot.database.fetchall("SELECT * FROM personnel_records WHERE guild_id=? AND personnel_id=? ORDER BY record_date DESC,id DESC LIMIT ?", (guild_id,personnel_id,limit))

    async def activity_feed(self, guild_id: int, limit: int = 20):
        return await self.bot.database.fetchall("""SELECT r.*,m.display_name FROM personnel_records r JOIN personnel_members m ON m.id=r.personnel_id WHERE r.guild_id=? ORDER BY r.created_at DESC,r.id DESC LIMIT ?""", (guild_id,limit))

    async def trend(self, guild_id: int, limit: int = 12):
        return await self.bot.database.fetchall("""SELECT period_key,COALESCE(SUM(inductions),0) inductions,COALESCE(SUM(bwg),0) bwg,COALESCE(SUM(inductions+bwg),0) activity FROM personnel_records WHERE guild_id=? GROUP BY period_key ORDER BY period_key DESC LIMIT ?""", (guild_id,limit))

    async def add_note(self, guild_id: int, personnel_id: int, actor_id: int, content: str):
        return await self.bot.database.execute("INSERT INTO personnel_notes(guild_id,personnel_id,content,created_by) VALUES(?,?,?,?)", (guild_id,personnel_id,content.strip(),actor_id))

    async def notes(self, guild_id: int, personnel_id: int, limit: int = 10):
        return await self.bot.database.fetchall("SELECT * FROM personnel_notes WHERE guild_id=? AND personnel_id=? ORDER BY created_at DESC,id DESC LIMIT ?", (guild_id,personnel_id,limit))

    async def set_qualification(self, guild_id: int, personnel_id: int, actor_id: int, name: str, status: str):
        await self.bot.database.execute("""INSERT INTO personnel_qualifications(guild_id,personnel_id,name,status,created_by) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,personnel_id,name) DO UPDATE SET status=excluded.status,created_by=excluded.created_by,created_at=CURRENT_TIMESTAMP""", (guild_id,personnel_id,name.strip(),status.strip(),actor_id))

    async def qualifications(self, guild_id: int, personnel_id: int):
        return await self.bot.database.fetchall("SELECT * FROM personnel_qualifications WHERE guild_id=? AND personnel_id=? ORDER BY name COLLATE NOCASE", (guild_id,personnel_id))

    async def set_goal(self, guild_id: int, personnel_id: int | None, actor_id: int, target_type: str, target_value: int, period_key: str):
        await self.bot.database.execute("""INSERT INTO personnel_goals(guild_id,personnel_id,target_type,target_value,period_key,created_by) VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,personnel_id,target_type,period_key) DO UPDATE SET target_value=excluded.target_value,created_by=excluded.created_by,created_at=CURRENT_TIMESTAMP""", (guild_id,personnel_id,target_type,target_value,period_key,actor_id))

    async def rank_history(self, guild_id: int, personnel_id: int, limit: int = 10):
        return await self.bot.database.fetchall("SELECT * FROM personnel_rank_history WHERE guild_id=? AND personnel_id=? ORDER BY created_at DESC,id DESC LIMIT ?", (guild_id,personnel_id,limit))

    @staticmethod
    def csv_bytes(rows) -> bytes:
        s=StringIO(); w=csv.writer(s,delimiter=";"); w.writerow(["Name","Einweisungen","BWG","Aktivität"])
        for r in rows: w.writerow([r["display_name"],r["inductions"],r["bwg"],r["activity"]])
        return s.getvalue().encode("utf-8-sig")
