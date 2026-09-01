from __future__ import annotations
from datetime import date, datetime
from io import BytesIO, StringIO
import csv
from PIL import Image, ImageDraw, ImageFont

class PersonnelService:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def add(self, guild_id: int, name: str, actor_id: int, *, user_id: int | None=None, rank: str|None=None, department: str|None=None):
        await self.bot.database.execute(
            """INSERT INTO personnel_members(guild_id,user_id,display_name,rank_name,department,created_by)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(guild_id,display_name) DO UPDATE SET
               user_id=COALESCE(excluded.user_id,user_id), rank_name=COALESCE(excluded.rank_name,rank_name),
               department=COALESCE(excluded.department,department), active=1, updated_at=CURRENT_TIMESTAMP""",
            (guild_id,user_id,name.strip(),rank,department,actor_id),
        )
        return await self.get_by_name(guild_id, name)

    async def get_by_name(self, guild_id: int, name: str):
        return await self.bot.database.fetchone(
            "SELECT * FROM personnel_members WHERE guild_id=? AND lower(display_name)=lower(?)",
            (guild_id,name.strip()),
        )

    async def list_members(self, guild_id: int, active_only: bool=True):
        query="SELECT * FROM personnel_members WHERE guild_id=?"
        params=[guild_id]
        if active_only: query+=" AND active=1"
        query+=" ORDER BY display_name COLLATE NOCASE"
        return await self.bot.database.fetchall(query, params)

    async def record(self,guild_id:int, personnel_id:int, actor_id:int, *, inductions:int=0,bwg:int=0,record_date:str|None=None,period_key:str|None=None,note:str|None=None):
        d=record_date or date.today().isoformat()
        p=period_key or datetime.fromisoformat(d).strftime("%Y-%m")
        return await self.bot.database.execute(
            """INSERT INTO personnel_records(guild_id,personnel_id,record_date,period_key,inductions,bwg,note,created_by)
               VALUES(?,?,?,?,?,?,?,?)""",
            (guild_id,personnel_id,d,p,inductions,bwg,note,actor_id),
        )

    async def totals(self,guild_id:int, *, period_like:str|None=None):
        where="WHERE m.guild_id=? AND m.active=1"
        params=[guild_id]
        if period_like:
            where+=" AND r.period_key LIKE ?"; params.append(period_like)
        return await self.bot.database.fetchall(
            f"""SELECT m.id,m.display_name,m.rank_name,m.department,
               COALESCE(SUM(r.inductions),0) inductions, COALESCE(SUM(r.bwg),0) bwg,
               COALESCE(SUM(r.inductions+r.bwg),0) activity
               FROM personnel_members m
               LEFT JOIN personnel_records r ON r.personnel_id=m.id
               {where}
               GROUP BY m.id ORDER BY activity DESC, m.display_name COLLATE NOCASE""",
            params,
        )

    async def history(self,guild_id:int,personnel_id:int,limit:int=100):
        return await self.bot.database.fetchall(
            """SELECT * FROM personnel_records WHERE guild_id=? AND personnel_id=?
               ORDER BY record_date DESC,id DESC LIMIT ?""",(guild_id,personnel_id,limit)
        )

    @staticmethod
    def csv_bytes(rows) -> bytes:
        s=StringIO()
        w=csv.writer(s,delimiter=";")
        w.writerow(["Name","Einweisungen","BWG","Aktivität"])
        for r in rows: w.writerow([r["display_name"],r["inductions"],r["bwg"],r["activity"]])
        return s.getvalue().encode("utf-8-sig")

    @staticmethod
    def png_bytes(title:str, rows) -> bytes:
        width=1100; row_h=56; height=max(360,180+len(rows)*row_h)
        im=Image.new("RGB",(width,height),(31,33,38)); d=ImageDraw.Draw(im)
        font=ImageFont.load_default()
        d.text((40,30),title,fill=(255,255,255),font=font)
        maxv=max([max(int(r["inductions"]),int(r["bwg"])) for r in rows] or [1])
        y=100
        for r in rows:
            name=str(r["display_name"])[:28]; e=int(r["inductions"]); b=int(r["bwg"])
            d.text((40,y+12),name,fill=(230,230,230),font=font)
            x0=260; scale=650/maxv
            d.rectangle((x0,y,x0+e*scale,y+18),fill=(88,101,242))
            d.rectangle((x0,y+25,x0+b*scale,y+43),fill=(87,242,135))
            d.text((930,y+4),f"E {e}",fill=(255,255,255),font=font)
            d.text((930,y+29),f"BWG {b}",fill=(255,255,255),font=font)
            y+=row_h
        d.text((40,height-40),"Raspberry-Bot • MD Personalabteilung",fill=(150,150,150),font=font)
        buf=BytesIO(); im.save(buf,"PNG"); return buf.getvalue()
