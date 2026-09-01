from __future__ import annotations
from datetime import date, datetime
from io import BytesIO, StringIO
import csv
from PIL import Image, ImageDraw, ImageFont


class PersonnelService:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def add(self, guild_id: int, name: str, actor_id: int, *, user_id: int | None = None, rank: str | None = None, department: str | None = None):
        await self.bot.database.execute(
            """INSERT INTO personnel_members(guild_id,user_id,display_name,rank_name,department,created_by)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(guild_id,display_name) DO UPDATE SET
               user_id=COALESCE(excluded.user_id,user_id), rank_name=COALESCE(excluded.rank_name,rank_name),
               department=COALESCE(excluded.department,department), active=1, updated_at=CURRENT_TIMESTAMP""",
            (guild_id, user_id, name.strip(), rank, department, actor_id),
        )
        return await self.get_by_name(guild_id, name)

    async def get_by_name(self, guild_id: int, name: str):
        # Prefer the exact guild id, but tolerate a member row whose guild_id was
        # previously damaged by the dashboard Snowflake precision bug if one of its
        # linked records still carries the correct guild id.
        return await self.bot.database.fetchone(
            """SELECT m.*
               FROM personnel_members m
               WHERE lower(m.display_name)=lower(?)
                 AND (
                   m.guild_id=?
                   OR EXISTS (
                     SELECT 1 FROM personnel_records r
                     WHERE r.personnel_id=m.id AND r.guild_id=?
                   )
                 )
               ORDER BY CASE WHEN m.guild_id=? THEN 0 ELSE 1 END, m.id
               LIMIT 1""",
            (name.strip(), guild_id, guild_id, guild_id),
        )

    async def list_members(self, guild_id: int, active_only: bool = True):
        query = """SELECT DISTINCT m.*
                   FROM personnel_members m
                   WHERE (
                     m.guild_id=?
                     OR EXISTS (
                       SELECT 1 FROM personnel_records r
                       WHERE r.personnel_id=m.id AND r.guild_id=?
                     )
                   )"""
        params = [guild_id, guild_id]
        if active_only:
            query += " AND m.active=1"
        query += " ORDER BY m.display_name COLLATE NOCASE"
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
        # personnel_id is the authoritative relation between records and members.
        # Once a member is resolved to the current guild, all records belonging to
        # that member are safe to aggregate even if an old dashboard edit damaged
        # the redundant guild_id stored on an individual record.
        member_scope = """(
            m.guild_id=?
            OR EXISTS (
                SELECT 1 FROM personnel_records rx
                WHERE rx.personnel_id=m.id AND rx.guild_id=?
            )
        )"""
        params: list[object] = [guild_id, guild_id]
        record_filter = ""
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
               WHERE m.active=1 AND {member_scope}
               GROUP BY m.id
               ORDER BY activity DESC, m.display_name COLLATE NOCASE""",
            params,
        )

    async def history(self, guild_id: int, personnel_id: int, limit: int = 100):
        # personnel_id uniquely identifies the member, so do not hide history merely
        # because an old dashboard edit damaged the redundant guild_id on a record.
        member = await self.bot.database.fetchone(
            """SELECT id FROM personnel_members m
               WHERE m.id=? AND (
                 m.guild_id=? OR EXISTS (
                   SELECT 1 FROM personnel_records r
                   WHERE r.personnel_id=m.id AND r.guild_id=?
                 )
               )""",
            (personnel_id, guild_id, guild_id),
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
        width = 900
        margin = 34
        header_h = 360
        footer_h = 64
        gap = 18
        card_h = 230
        columns = 2
        card_w = (width - margin * 2 - gap) // columns
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

        title_font = PersonnelService._font(52, bold=True)
        subtitle_font = PersonnelService._font(27)
        kpi_label_font = PersonnelService._font(22, bold=True)
        kpi_value_font = PersonnelService._font(42, bold=True)
        name_font = PersonnelService._font(34, bold=True)
        meta_font = PersonnelService._font(23)
        bar_font = PersonnelService._font(25, bold=True)
        total_font = PersonnelService._font(28, bold=True)
        footer_font = PersonnelService._font(18)

        total_e = sum(int(r["inductions"]) for r in rows)
        total_b = sum(int(r["bwg"]) for r in rows)
        total_activity = total_e + total_b
        top = rows[0] if rows else None

        draw.text((margin, 24), title, font=title_font, fill=text)
        draw.text((margin, 88), f"{len(rows)} Mitarbeitende • gespeicherte Perso-Daten", font=subtitle_font, fill=muted)

        kpi_y = 138
        kpi_gap = 14
        kpi_w = (width - margin * 2 - kpi_gap) // 2
        kpis = [
            ("EINWEISUNGEN", str(total_e), accent_e),
            ("BWG", str(total_b), accent_b),
            ("AKTIVITÄT", str(total_activity), accent_total),
            ("TOP ACTIVITY", str(top["display_name"]) if top else "—", text),
        ]
        for i, (label, value, accent) in enumerate(kpis):
            col = i % 2
            row_i = i // 2
            x = margin + col * (kpi_w + kpi_gap)
            y = kpi_y + row_i * 100
            draw.rounded_rectangle((x, y, x + kpi_w, y + 86), radius=16, fill=panel)
            draw.rounded_rectangle((x, y, x + 6, y + 86), radius=3, fill=accent)
            draw.text((x + 18, y + 10), label, font=kpi_label_font, fill=muted)
            display = value
            while draw.textbbox((0, 0), display, font=kpi_value_font)[2] > kpi_w - 36 and len(display) > 4:
                display = display[:-2] + "…"
            draw.text((x + 18, y + 38), display, font=kpi_value_font, fill=accent)

        max_value = max([max(int(r["inductions"]), int(r["bwg"])) for r in rows] or [1])
        max_value = max(1, max_value)

        if not rows:
            y = header_h + 8
            draw.rounded_rectangle((margin, y, width - margin, y + card_h), radius=20, fill=panel)
            draw.text((margin + 28, y + 72), "Noch keine Daten für diesen Zeitraum.", font=name_font, fill=muted)

        for index, r in enumerate(rows):
            col = index % columns
            row_index = index // columns
            x = margin + col * (card_w + gap)
            y = header_h + row_index * (card_h + gap)
            fill = panel if row_index % 2 == 0 else panel_alt
            draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=20, fill=fill)

            name = str(r["display_name"])
            if len(name) > 18:
                name = name[:17] + "…"
            rank = str(r["rank_name"] or "")
            department = str(r["department"] or "")
            meta = " • ".join(part for part in (rank, department) if part)
            if len(meta) > 30:
                meta = meta[:29] + "…"

            e = int(r["inductions"])
            b = int(r["bwg"])
            activity = int(r["activity"])

            draw.text((x + 20, y + 18), f"{index + 1:02d}  {name}", font=name_font, fill=text)
            if meta:
                draw.text((x + 20, y + 61), meta, font=meta_font, fill=muted)

            total_text = f"Gesamt {activity}"
            box = draw.textbbox((0, 0), total_text, font=total_font)
            draw.text((x + card_w - 20 - (box[2] - box[0]), y + 88), total_text, font=total_font, fill=accent_total)

            bar_left = x + 20
            bar_right = x + card_w - 20
            bar_width = bar_right - bar_left
            e_w = int(bar_width * e / max_value)
            b_w = int(bar_width * b / max_value)
            e_y = y + 126
            b_y = y + 178
            bar_h = 38

            draw.rounded_rectangle((bar_left, e_y, bar_right, e_y + bar_h), radius=15, fill=track)
            draw.rounded_rectangle((bar_left, b_y, bar_right, b_y + bar_h), radius=15, fill=track)
            PersonnelService._rounded_bar(draw, (bar_left, e_y, bar_left + e_w, e_y + bar_h), accent_e, 15)
            PersonnelService._rounded_bar(draw, (bar_left, b_y, bar_left + b_w, b_y + bar_h), accent_b, 15)
            draw.text((bar_left + 10, e_y + 3), f"Einweisungen {e}", font=bar_font, fill=text)
            draw.text((bar_left + 10, b_y + 3), f"BWG {b}", font=bar_font, fill=text)

        footer_y = height - footer_h + 18
        draw.text((margin, footer_y), "Raspberry-Bot • MD Personalabteilung", font=footer_font, fill=muted)

        buf = BytesIO()
        image.save(buf, "PNG", optimize=True)
        return buf.getvalue()
