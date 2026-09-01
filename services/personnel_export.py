from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)
FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)

BG = (15, 17, 22)
PANEL = (25, 28, 35)
PANEL_SOFT = (31, 34, 42)
TEXT = (248, 249, 252)
MUTED = (178, 184, 197)
BLUE = (91, 110, 245)
GREEN = (71, 201, 145)
GOLD = (245, 184, 72)
TRACK = (43, 47, 57)


# The renderer only uses a small, predictable set of font sizes. Keeping 64
# scalable font objects is plenty and avoids an unnecessarily large cache on
# memory-constrained Raspberry Pi hosts.
@lru_cache(maxsize=64)
def _font(size: int, *, bold: bool = False):
    candidates = FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REGULAR
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                pass

    names = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf") if bold else (
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass

    try:
        return ImageFont.load_default(size=size)
    except TypeError as exc:
        raise RuntimeError(
            "No scalable TrueType font found for personnel PNG rendering. "
            "Install fonts-dejavu-core on the host."
        ) from exc


def _bbox(draw: ImageDraw.ImageDraw, value: str, font):
    return draw.textbbox((0, 0), str(value), font=font)


def _measure(draw: ImageDraw.ImageDraw, value: str, font) -> tuple[int, int]:
    box = _bbox(draw, value, font)
    return box[2] - box[0], box[3] - box[1]


def _fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    max_width: int,
    *,
    preferred: int,
    minimum: int,
    bold: bool = True,
):
    text = str(value)
    for size in range(preferred, minimum - 1, -2):
        font = _font(size, bold=bold)
        if _measure(draw, text, font)[0] <= max_width:
            return font
    return _font(minimum, bold=bold)


def _fit_box(
    draw: ImageDraw.ImageDraw,
    value: str,
    max_width: int,
    max_height: int,
    *,
    maximum: int,
    minimum: int,
    bold: bool = True,
):
    text = str(value)
    for size in range(maximum, minimum - 1, -2):
        font = _font(size, bold=bold)
        width, height = _measure(draw, text, font)
        if width <= max_width and height <= max_height:
            return font
    return _font(minimum, bold=bold)


def _rounded(draw: ImageDraw.ImageDraw, box, *, fill, radius: int = 24):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _row_value(row, key: str, default=0):
    try:
        value = row[key]
    except (KeyError, TypeError, IndexError):
        value = default
    return value if value is not None else default


def _png_bytes(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_personnel_png(title: str, rows) -> bytes:
    width, height = 1920, 1280
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    margin = 54
    title_font = _fit_text(draw, title, width - (margin * 2), preferred=86, minimum=66)
    draw.text((margin, 48), title, font=title_font, fill=TEXT)

    all_rows = list(rows)
    active = [r for r in all_rows if _row_value(r, "inductions") or _row_value(r, "bwg")]
    inactive = [r for r in all_rows if not (_row_value(r, "inductions") or _row_value(r, "bwg"))]
    shown = (active + inactive)[:8]
    hidden = max(0, len(all_rows) - len(shown))

    subtitle = f"{len(all_rows)} Mitarbeiter • Aktivität auf einen Blick"
    if hidden:
        subtitle += f" • {hidden} weitere ausgeblendet"
    draw.text((margin, 145), subtitle, font=_font(34), fill=MUTED)

    total_e = sum(int(_row_value(r, "inductions")) for r in all_rows)
    total_b = sum(int(_row_value(r, "bwg")) for r in all_rows)
    total_a = total_e + total_b

    kpi_y, kpi_h, gap = 205, 145, 24
    kpi_w = (width - margin * 2 - gap * 2) // 3
    kpis = (
        ("EINWEISUNGEN", total_e, BLUE),
        ("BWG", total_b, GREEN),
        ("GESAMT", total_a, GOLD),
    )
    for i, (label, value, accent) in enumerate(kpis):
        x = margin + i * (kpi_w + gap)
        _rounded(draw, (x, kpi_y, x + kpi_w, kpi_y + kpi_h), fill=PANEL)
        draw.rounded_rectangle((x, kpi_y, x + 9, kpi_y + kpi_h), radius=4, fill=accent)
        draw.text((x + 34, kpi_y + 24), label, font=_font(28, bold=True), fill=MUTED)
        draw.text((x + 34, kpi_y + 61), str(value), font=_font(58, bold=True), fill=TEXT)

    grid_top = 385
    col_gap, row_gap = 28, 22
    card_w = (width - margin * 2 - col_gap) // 2
    card_h = 190

    for index, row in enumerate(shown):
        col = index % 2
        rindex = index // 2
        x = margin + col * (card_w + col_gap)
        y = grid_top + rindex * (card_h + row_gap)
        _rounded(draw, (x, y, x + card_w, y + card_h), fill=PANEL)

        rank = index + 1
        badge_fill = GOLD if rank == 1 else PANEL_SOFT
        draw.ellipse((x + 24, y + 22, x + 68, y + 66), fill=badge_fill)
        badge_text = str(rank)
        badge_font = _font(21, bold=True)
        bw, bh = _measure(draw, badge_text, badge_font)
        draw.text((x + 46 - bw / 2, y + 44 - bh / 2 - 2), badge_text, font=badge_font, fill=BG if rank == 1 else TEXT)

        name = str(_row_value(row, "display_name", "Unbekannt"))
        name_x = x + 88
        name_max_w = card_w - 116
        name_font = _fit_box(
            draw,
            name,
            name_max_w,
            78,
            maximum=96,
            minimum=44,
            bold=True,
        )
        draw.text((name_x, y + 18), name, font=name_font, fill=TEXT)

        e = int(_row_value(row, "inductions"))
        b = int(_row_value(row, "bwg"))
        activity = int(_row_value(row, "activity", e + b))
        stat_y = y + 119
        stat_gap = 22
        stat_w = (card_w - 48 - stat_gap * 2) // 3
        stats = (("E", e, BLUE), ("BWG", b, GREEN), ("GESAMT", activity, GOLD))
        for si, (label, value, accent) in enumerate(stats):
            sx = x + 24 + si * (stat_w + stat_gap)
            _rounded(draw, (sx, stat_y, sx + stat_w, stat_y + 53), fill=PANEL_SOFT, radius=14)
            draw.text((sx + 14, stat_y + 15), label, font=_font(21, bold=True), fill=accent)
            value_text = str(value)
            value_font = _font(36, bold=True)
            vw, _ = _measure(draw, value_text, value_font)
            draw.text((sx + stat_w - vw - 14, stat_y + 7), value_text, font=value_font, fill=TEXT)

    footer = "Raspberry-Bot • Perso 2.0"
    draw.text((margin, height - 46), footer, font=_font(24), fill=MUTED)
    return _png_bytes(image)


def render_personnel_chart(title: str, rows) -> bytes:
    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    margin = 70

    title_font = _fit_text(draw, title, width - margin * 2, preferred=76, minimum=56)
    draw.text((margin, 45), title, font=title_font, fill=TEXT)
    draw.text((margin, 135), "Einweisungen vs. BWG • Top 10 nach Gesamtaktivität", font=_font(30), fill=MUTED)

    values = sorted(
        list(rows),
        key=lambda r: int(_row_value(r, "activity", int(_row_value(r, "inductions")) + int(_row_value(r, "bwg")))),
        reverse=True,
    )[:10]
    max_total = max((int(_row_value(r, "activity", int(_row_value(r, "inductions")) + int(_row_value(r, "bwg")))) for r in values), default=1)
    max_total = max(max_total, 1)

    legend_y = 194
    draw.rounded_rectangle((margin, legend_y, margin + 26, legend_y + 26), radius=6, fill=BLUE)
    draw.text((margin + 38, legend_y - 3), "Einweisungen", font=_font(25, bold=True), fill=TEXT)
    draw.rounded_rectangle((margin + 230, legend_y, margin + 256, legend_y + 26), radius=6, fill=GREEN)
    draw.text((margin + 268, legend_y - 3), "BWG", font=_font(25, bold=True), fill=TEXT)

    if not values:
        draw.text((margin, 320), "Noch keine Perso-Daten vorhanden.", font=_font(44, bold=True), fill=MUTED)
        return _png_bytes(image)

    top = 260
    row_h = 72
    name_w = 430
    chart_x = margin + name_w
    chart_w = width - chart_x - margin

    for index, row in enumerate(values):
        y = top + index * row_h
        name = str(_row_value(row, "display_name", "Unbekannt"))
        name_font = _fit_text(draw, name, name_w - 55, preferred=30, minimum=20)
        draw.text((margin, y + 14), f"{index + 1}. {name}", font=name_font, fill=TEXT)

        e = int(_row_value(row, "inductions"))
        b = int(_row_value(row, "bwg"))
        total = max(e + b, 0)
        track_y = y + 12
        track_h = 42
        draw.rounded_rectangle((chart_x, track_y, chart_x + chart_w, track_y + track_h), radius=14, fill=TRACK)
        e_w = int(chart_w * e / max_total)
        b_w = int(chart_w * b / max_total)
        if e_w:
            draw.rounded_rectangle((chart_x, track_y, chart_x + e_w, track_y + track_h), radius=14, fill=BLUE)
        if b_w:
            bx = chart_x + e_w
            draw.rounded_rectangle((bx, track_y, min(bx + b_w, chart_x + chart_w), track_y + track_h), radius=14, fill=GREEN)

        label = f"E {e}   BWG {b}   = {total}"
        label_font = _font(23, bold=True)
        lw, _ = _measure(draw, label, label_font)
        lx = min(chart_x + max(e_w + b_w, 12) + 12, width - margin - lw)
        draw.text((lx, y + 18), label, font=label_font, fill=TEXT)

    total_e = sum(int(_row_value(r, "inductions")) for r in rows)
    total_b = sum(int(_row_value(r, "bwg")) for r in rows)
    footer = f"Gesamt • Einweisungen {total_e}  |  BWG {total_b}  |  Aktivität {total_e + total_b}"
    draw.text((margin, height - 65), footer, font=_font(28, bold=True), fill=MUTED)
    return _png_bytes(image)
