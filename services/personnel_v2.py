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
    def _font(size: int, *, bold: bool = False):
        paths = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        )
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _rounded_bar(draw: ImageDraw.ImageDraw, box, fill, radius: int = 9) -> None:
        x1, y1, x2, y2 = box
        if x2 <= x1:
            return
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill)

    @staticmethod
    def png_bytes(title:str, rows) -> bytes:
        # Rendering only: existing personnel_members/personnel_records rows are never modified here.
        rows = list(rows)

        width = 1440
        margin = 64
        header_h = 340
        row_h = 136
        footer_h = 104
        height = header_h + max(1, len(rows)) * row_h + footer_h

        bg = (17, 19, 24)
        panel = (26, 29, 36)
        panel_alt = (30, 33, 41)
        grid = (54, 59, 70)
        text = (241, 243, 247)
        muted = (159, 166, 181)
        accent_e = (91, 110, 245)
        accent_b = (71, 201, 145)
        accent_total = (245, 184, 72)

        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)

        title_font = PersonnelService._font(64, bold=True)
        subtitle_font = PersonnelService._font(30)
        card_label_font = PersonnelService._font(34, bold=True)
        card_value_font = PersonnelService._font(48, bold=True)
        name_font = PersonnelService._font(34, bold=True)
        meta_font = PersonnelService._font(22)
        value_font = PersonnelService._font(30, bold=True)
        footer_font = PersonnelService._font(22)

        total_e = sum(int(r["inductions"]) for r in rows)
        total_b = sum(int(r["bwg"]) for r in rows)
        total_activity = total_e + total_b
        top = rows[0] if rows else None

        draw.text((margin, 34), title, font=title_font, fill=text)
        draw.text(
            (margin, 116),
            f"{len(rows)} Mitarbeitende • Einweisungen & BWG • automatisch aus gespeicherten Perso-Daten",
            font=subtitle_font,
            fill=muted,
        )

        card_y = 180
        gap = 18
        card_w = (width - margin * 2 - gap * 3) // 4
        cards = [
            ("EINWEISUNGEN", str(total_e), accent_e),
            ("BWG", str(total_b), accent_b),
            ("AKTIVITÄT", str(total_activity), accent_total),
            ("TOP ACTIVITY", str(top["display_name"]) if top else "—", text),
        ]
        for i, (label, value, accent) in enumerate(cards):
            x = margin + i * (card_w + gap)
            draw.rounded_rectangle((x, card_y, x + card_w, card_y + 132), radius=18, fill=panel)
            draw.rounded_rectangle((x, card_y, x + 6, card_y + 132), radius=3, fill=accent)
            draw.text((x + 22, card_y + 20), label, font=card_label_font, fill=muted)
            display = value
            while draw.textbbox((0, 0), display, font=card_value_font)[2] > card_w - 44 and len(display) > 4:
                display = display[:-2] + "…"
            draw.text((x + 22, card_y + 68), display, font=card_value_font, fill=accent)

        chart_top = header_h
        label_x = margin + 16
        bars_x = 500
        bars_right = width - margin - 230
        values_x = width - margin - 192
        available_bar_w = bars_right - bars_x
        max_value = max([max(int(r["inductions"]), int(r["bwg"])) for r in rows] or [1])
        max_value = max(1, max_value)

        for step in range(5):
            x = bars_x + int(available_bar_w * step / 4)
            draw.line((x, chart_top - 8, x, height - footer_h - 10), fill=grid, width=1)
            tick = round(max_value * step / 4)
            draw.text((x - 10, chart_top - 44), str(tick), font=meta_font, fill=muted)

        if not rows:
            box_y = chart_top + 18
            draw.rounded_rectangle((margin, box_y, width - margin, box_y + 76), radius=16, fill=panel)
            draw.text((margin + 24, box_y + 22), "Noch keine Daten für diesen Zeitraum.", font=name_font, fill=muted)

        for index, row in enumerate(rows):
            y = chart_top + index * row_h
            row_bg = panel if index % 2 == 0 else panel_alt
            draw.rounded_rectangle((margin, y + 7, width - margin, y + row_h - 7), radius=16, fill=row_bg)

            name = str(row["display_name"])
            if len(name) > 22:
                name = name[:21] + "…"
            rank = str(row["rank_name"] or "")
            department = str(row["department"] or "")
            meta = " • ".join(part for part in (rank, department) if part)

            draw.text((label_x, y + 30), f"{index + 1:02d}  {name}", font=name_font, fill=text)
            if meta:
                if len(meta) > 34:
                    meta = meta[:33] + "…"
                draw.text((label_x + 48, y + 86), meta, font=meta_font, fill=muted)

            e = int(row["inductions"])
            b = int(row["bwg"])
            activity = int(row["activity"])
            e_w = int(available_bar_w * e / max_value)
            b_w = int(available_bar_w * b / max_value)

            draw.rounded_rectangle((bars_x, y + 28, bars_right, y + 62), radius=11, fill=(42, 46, 56))
            draw.rounded_rectangle((bars_x, y + 76, bars_right, y + 110), radius=11, fill=(42, 46, 56))
            PersonnelService._rounded_bar(draw, (bars_x, y + 28, bars_x + e_w, y + 62), accent_e, 11)
            PersonnelService._rounded_bar(draw, (bars_x, y + 76, bars_x + b_w, y + 110), accent_b, 11)

            draw.text((values_x, y + 29), f"E  {e}", font=value_font, fill=accent_e)
            draw.text((values_x, y + 77), f"B  {b}", font=value_font, fill=accent_b)
            draw.text((width - margin - 62, y + 54), str(activity), font=value_font, fill=accent_total)

        footer_y = height - footer_h + 24
        draw.rounded_rectangle((margin, footer_y - 5, margin + 16, footer_y + 11), radius=4, fill=accent_e)
        draw.text((margin + 26, footer_y - 8), "Einweisungen", font=footer_font, fill=muted)
        draw.rounded_rectangle((margin + 172, footer_y - 5, margin + 188, footer_y + 11), radius=4, fill=accent_b)
        draw.text((margin + 198, footer_y - 8), "BWG", font=footer_font, fill=muted)
        draw.text((width - margin - 420, footer_y - 8), "Gesamtaktivität rechts", font=footer_font, fill=muted)
        footer_text = "Raspberry-Bot • MD Personalabteilung"
        footer_box = draw.textbbox((0, 0), footer_text, font=footer_font)
        draw.text((width - margin - (footer_box[2] - footer_box[0]), footer_y + 27), footer_text, font=footer_font, fill=(105, 112, 126))

        buf = BytesIO()
        image.save(buf, "PNG", optimize=True)
        return buf.getvalue()
