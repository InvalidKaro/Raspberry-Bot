from __future__ import annotations
from datetime import date, datetime
from io import BytesIO, StringIO
import csv
from PIL import Image, ImageDraw, ImageFont


class PersonnelService:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def add(self, guild_id: int, name: str, actor_id: int, *, user_id: int | None = None, rank: str | None = None, department: str | None = None):
        existing = await self.get_by_name(guild_id, name)
        if existing:
            await self.bot.database.execute(
                """UPDATE personnel_members
                   SET user_id=COALESCE(?,user_id),
                       rank_name=COALESCE(?,rank_name),
                       department=COALESCE(?,department),
                       active=1,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (user_id, rank, department, int(existing["id"])),
            )
            return await self.bot.database.fetchone("SELECT * FROM personnel_members WHERE id=?", (int(existing["id"]),))

        await self.bot.database.execute(
            """INSERT INTO personnel_members(guild_id,user_id,display_name,rank_name,department,created_by)
               VALUES(?,?,?,?,?,?)""",
            (guild_id, user_id, name.strip(), rank, department, actor_id),
        )
        return await self.get_by_name(guild_id, name)

    async def get_by_name(self, guild_id: int, name: str):
        return await self.bot.database.fetchone(
            """SELECT * FROM personnel_members
               WHERE lower(display_name)=lower(?)
               ORDER BY active DESC, id ASC
               LIMIT 1""",
            (name.strip(),),
        )

    async def list_members(self, guild_id: int, active_only: bool = True):
        query = "SELECT * FROM personnel_members"
        params: list[object] = []
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY display_name COLLATE NOCASE, id ASC"
        return await self.bot.database.fetchall(query, params)

    async def record(self, guild_id: int, personnel_id: int, actor_id: int, *, inductions: int = 0, bwg: int = 0, record_date: str | None = None, period_key: str | None = None, note: str | None = None):
        d = record_date or date.today().isoformat()
        p = period_key or datetime.fromisoformat(d).strftime("%Y-%m")
        return await self.bot.database.execute(
            """INSERT INTO personnel_records(guild_id,personnel_id,record_date,period_key,inductions,bwg,note,created_by)
               VALUES(?,?,?,?,?,?,?,?)""",
            (guild_id, personnel_id, d, p, inductions, bwg, note, actor_id),
        )

    async def totals(self, guild_id: int, *, period_like: str | None = None):
        record_filter = ""
        params: list[object] = []
        if period_like:
            record_filter = " AND r.period_key LIKE ?"
            params.append(period_like)
        return await self.bot.database.fetchall(
            f"""SELECT m.id,m.display_name,m.rank_name,m.department,
               COALESCE(SUM(r.inductions),0) inductions,
               COALESCE(SUM(r.bwg),0) bwg,
               COALESCE(SUM(r.inductions+r.bwg),0) activity
               FROM personnel_members m
               LEFT JOIN personnel_records r
                 ON r.personnel_id=m.id{record_filter}
               WHERE m.active=1
               GROUP BY m.id
               ORDER BY activity DESC, m.display_name COLLATE NOCASE""",
            params,
        )

    async def history(self, guild_id: int, personnel_id: int, limit: int = 100):
        member = await self.bot.database.fetchone(
            "SELECT id FROM personnel_members WHERE id=?",
            (personnel_id,),
        )
        if not member:
            return []
        return await self.bot.database.fetchall(
            """SELECT * FROM personnel_records WHERE personnel_id=?
               ORDER BY record_date DESC,id DESC LIMIT ?""",
            (personnel_id, limit),
        )

    @staticmethod
    def csv_bytes(rows) -> bytes:
        s = StringIO()
        w = csv.writer(s, delimiter=";")
        w.writerow(["Name", "Einweisungen", "BWG", "Aktivität"])
        for r in rows:
            w.writerow([r["display_name"], r["inductions"], r["bwg"], r["activity"]])
        return s.getvalue().encode("utf-8-sig")

    @staticmethod
    def _font(size: int, *, bold: bool = False):
        paths = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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
    def png_bytes(title: str, rows) -> bytes:
        rows = list(rows)

        # Intentionally oversized typography. This renderer prioritizes readability
        # on Discord mobile over compactness. Three wide columns keep the export
        # landscape while names, totals and bar labels remain genuinely large.
        width = 1800
        margin = 54
        header_h = 430
        footer_h = 110
        gap = 26
        card_h = 350
        columns = 3
        card_w = (width - margin * 2 - gap * (columns - 1)) // columns
        grid_rows = max(1, (len(rows) + columns - 1) // columns)
        height = header_h + grid_rows * (card_h + gap) + footer_h

        bg = (17, 19, 24)
        panel = (27, 30, 37)
        panel_alt = (31, 34, 42)
        track = (45, 49, 59)
        text = (244, 246, 250)
        muted = (170, 176, 190)
        accent_e = (91, 110, 245)
        accent_b = (71, 201, 145)
        accent_total = (245, 184, 72)

        image = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(image)

        title_font = PersonnelService._font(108, bold=True)
        subtitle_font = PersonnelService._font(48, bold=True)
        kpi_label_font = PersonnelService._font(40, bold=True)
        kpi_value_font = PersonnelService._font(74, bold=True)
        name_font = PersonnelService._font(62, bold=True)
        meta_font = PersonnelService._font(40, bold=True)
        bar_font = PersonnelService._font(42, bold=True)
        total_font = PersonnelService._font(48, bold=True)
        footer_font = PersonnelService._font(32, bold=True)

        total_e = sum(int(r["inductions"]) for r in rows)
        total_b = sum(int(r["bwg"]) for r in rows)
        total_activity = total_e + total_b
        top = rows[0] if rows else None

        draw.text((margin, 20), title, font=title_font, fill=text)
        draw.text((margin, 145), f"{len(rows)} Mitarbeitende • gespeicherte Perso-Daten", font=subtitle_font, fill=muted)

        kpi_y = 228
        kpi_gap = 20
        kpi_w = (width - margin * 2 - kpi_gap * 3) // 4
        kpis = [
            ("EINWEISUNGEN", str(total_e), accent_e),
            ("BWG", str(total_b), accent_b),
            ("AKTIVITÄT", str(total_activity), accent_total),
            ("TOP ACTIVITY", str(top["display_name"]) if top else "—", text),
        ]
        for i, (label, value, accent) in enumerate(kpis):
            x = margin + i * (kpi_w + kpi_gap)
            draw.rounded_rectangle((x, kpi_y, x + kpi_w, kpi_y + 168), radius=24, fill=panel)
            draw.rounded_rectangle((x, kpi_y, x + 10, kpi_y + 168), radius=5, fill=accent)
            draw.text((x + 24, kpi_y + 16), label, font=kpi_label_font, fill=muted)
            display = value
            while draw.textbbox((0, 0), display, font=kpi_value_font)[2] > kpi_w - 48 and len(display) > 4:
                display = display[:-2] + "…"
            draw.text((x + 24, kpi_y + 79), display, font=kpi_value_font, fill=accent)

        max_value = max([max(int(r["inductions"]), int(r["bwg"])) for r in rows] or [1])
        max_value = max(1, max_value)

        if not rows:
            y = header_h + 10
            draw.rounded_rectangle((margin, y, width - margin, y + card_h), radius=26, fill=panel)
            draw.text((margin + 36, y + 118), "Noch keine Daten für diesen Zeitraum.", font=name_font, fill=muted)

        for index, r in enumerate(rows):
            col = index % columns
            row_index = index // columns
            x = margin + col * (card_w + gap)
            y = header_h + row_index * (card_h + gap)
            fill = panel if row_index % 2 == 0 else panel_alt
            draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=26, fill=fill)

            name = str(r["display_name"])
            if len(name) > 15:
                name = name[:14] + "…"
            rank = str(r["rank_name"] or "")
            department = str(r["department"] or "")
            meta = " • ".join(part for part in (rank, department) if part)
            if len(meta) > 23:
                meta = meta[:22] + "…"

            e = int(r["inductions"])
            b = int(r["bwg"])
            activity = int(r["activity"])

            draw.text((x + 26, y + 20), f"{index + 1:02d}  {name}", font=name_font, fill=text)
            if meta:
                draw.text((x + 26, y + 97), meta, font=meta_font, fill=muted)

            total_text = f"Gesamt {activity}"
            box = draw.textbbox((0, 0), total_text, font=total_font)
            draw.text((x + card_w - 26 - (box[2] - box[0]), y + 142), total_text, font=total_font, fill=accent_total)

            bar_left = x + 26
            bar_right = x + card_w - 26
            bar_width = bar_right - bar_left
            e_w = int(bar_width * e / max_value)
            b_w = int(bar_width * b / max_value)
            e_y = y + 205
            b_y = y + 277
            bar_h = 56

            draw.rounded_rectangle((bar_left, e_y, bar_right, e_y + bar_h), radius=21, fill=track)
            draw.rounded_rectangle((bar_left, b_y, bar_right, b_y + bar_h), radius=21, fill=track)
            PersonnelService._rounded_bar(draw, (bar_left, e_y, bar_left + e_w, e_y + bar_h), accent_e, 21)
            PersonnelService._rounded_bar(draw, (bar_left, b_y, bar_left + b_w, b_y + bar_h), accent_b, 21)
            draw.text((bar_left + 14, e_y + 3), f"Einweisungen {e}", font=bar_font, fill=text)
            draw.text((bar_left + 14, b_y + 3), f"BWG {b}", font=bar_font, fill=text)

        footer_y = height - footer_h + 34
        draw.text((margin, footer_y), "Raspberry-Bot • MD Personalabteilung", font=footer_font, fill=muted)
        right = "Landscape • MAX Schriftgröße"
        box = draw.textbbox((0, 0), right, font=footer_font)
        draw.text((width - margin - (box[2] - box[0]), footer_y), right, font=footer_font, fill=muted)

        buf = BytesIO()
        image.save(buf, "PNG", optimize=True)
        return buf.getvalue()
