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


@lru_cache(maxsize=256)
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
    value = str(value)
    for size in range(preferred, minimum - 1, -2):
        font = _font(size, bold=bold)
        width, _ = _measure(draw, value, font)
        if width <= max_width:
            return value, font

    font = _font(minimum, bold=bold)
    suffix = "…"
    trimmed = value
    while len(trimmed) > 1:
        width, _ = _measure(draw, trimmed + suffix, font)
        if width <= max_width:
            break
        trimmed = trimmed[:-1]
    return trimmed + suffix, font


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
    value = str(value).strip()
    for size in range(maximum, minimum - 1, -2):
        font = _font(size, bold=bold)
        width, height = _measure(draw, value, font)
        if width <= max_width and height <= max_height:
            return value, font

    font = _font(minimum, bold=bold)
    suffix = "…"
    trimmed = value
    while len(trimmed) > 1:
        width, height = _measure(draw, trimmed + suffix, font)
        if width <= max_width and height <= max_height:
            break
        trimmed = trimmed[:-1]
    return trimmed + suffix, font


def _row_value(row, key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError):
        return 0


def _png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def render_personnel_png(title: str, rows) -> bytes:
    """Render the compact dashboard-style personnel overview."""
    all_rows = list(rows)
    active = [r for r in all_rows if _row_value(r, "inductions") or _row_value(r, "bwg")]
    inactive = [r for r in all_rows if not (_row_value(r, "inductions") or _row_value(r, "bwg"))]
    shown = (active + inactive)[:8]
    hidden = max(0, len(all_rows) - len(shown))

    total_e = sum(_row_value(r, "inductions") for r in all_rows)
    total_b = sum(_row_value(r, "bwg") for r in all_rows)
    total_activity = total_e + total_b

    width = 1920
    height = 1280
    margin = 54

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_text, title_font = _fit_text(draw, title, width - margin * 2, preferred=86, minimum=66)
    draw.text((margin, 38), title_text, font=title_font, fill=TEXT)

    subtitle = f"{len(shown)} von {len(all_rows)} Personen angezeigt"
    if hidden:
        subtitle += f"  •  {hidden} weitere nicht im Bild"
    subtitle_text, subtitle_font = _fit_text(
        draw, subtitle, width - margin * 2, preferred=34, minimum=28, bold=False
    )
    draw.text((margin, 140), subtitle_text, font=subtitle_font, fill=MUTED)

    kpi_y = 205
    kpi_h = 145
    kpi_gap = 18
    kpi_w = (width - margin * 2 - kpi_gap * 3) // 4
    top_name = str(all_rows[0]["display_name"]) if all_rows else "—"
    kpis = (
        ("EINWEISUNGEN", str(total_e), BLUE),
        ("BWG", str(total_b), GREEN),
        ("GESAMT", str(total_activity), GOLD),
        ("TOP", top_name, TEXT),
    )

    for index, (label, value, accent) in enumerate(kpis):
        x = margin + index * (kpi_w + kpi_gap)
        draw.rounded_rectangle((x, kpi_y, x + kpi_w, kpi_y + kpi_h), radius=24, fill=PANEL)
        draw.rounded_rectangle((x, kpi_y, x + 10, kpi_y + kpi_h), radius=5, fill=accent)
        label_text, label_font = _fit_text(draw, label, kpi_w - 50, preferred=28, minimum=22)
        value_text, value_font = _fit_text(draw, value, kpi_w - 50, preferred=58, minimum=40)
        draw.text((x + 26, kpi_y + 18), label_text, font=label_font, fill=MUTED)
        draw.text((x + 26, kpi_y + 60), value_text, font=value_font, fill=accent)

    grid_top = 385
    columns = 2
    card_gap_x = 24
    card_gap_y = 20
    card_w = (width - margin * 2 - card_gap_x) // columns
    card_h = 190

    if not shown:
        empty_text, empty_font = _fit_text(
            draw, "Keine Perso-Daten vorhanden.", width - margin * 2, preferred=72, minimum=54
        )
        draw.text((margin, grid_top + 160), empty_text, font=empty_font, fill=MUTED)
    else:
        for index, row in enumerate(shown):
            col = index % columns
            row_index = index // columns
            x = margin + col * (card_w + card_gap_x)
            y = grid_top + row_index * (card_h + card_gap_y)
            fill = PANEL if row_index % 2 == 0 else PANEL_SOFT
            draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=24, fill=fill)

            e = _row_value(row, "inductions")
            b = _row_value(row, "bwg")
            activity = _row_value(row, "activity") or (e + b)

            badge_size = 44
            badge_x = x + 18
            badge_y = y + 19
            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_size, badge_y + badge_size),
                radius=12,
                fill=BG,
            )
            badge_font = _font(21, bold=True)
            badge_value = str(index + 1)
            badge_w, badge_h = _measure(draw, badge_value, badge_font)
            draw.text(
                (badge_x + (badge_size - badge_w) / 2, badge_y + (badge_size - badge_h) / 2 - 3),
                badge_value,
                font=badge_font,
                fill=MUTED,
            )

            name_left = badge_x + badge_size + 16
            name_right = x + card_w - 18
            name_top = y + 4
            name_bottom = y + 105
            name_text, name_font = _fit_box(
                draw,
                str(row["display_name"]),
                name_right - name_left,
                name_bottom - name_top,
                maximum=96,
                minimum=44,
            )
            _, name_h = _measure(draw, name_text, name_font)
            name_y = name_top + max(0, ((name_bottom - name_top) - name_h) // 2) - 4
            draw.text((name_left, name_y), name_text, font=name_font, fill=TEXT)

            stat_y = y + 111
            stat_gap = 14
            stat_w = (card_w - 44 - stat_gap * 2) // 3
            stats = (("E", str(e), BLUE), ("BWG", str(b), GREEN), ("GESAMT", str(activity), GOLD))

            for stat_index, (label, value, accent) in enumerate(stats):
                sx = x + 22 + stat_index * (stat_w + stat_gap)
                draw.rounded_rectangle((sx, stat_y, sx + stat_w, stat_y + 60), radius=16, fill=BG)
                label_text, label_font = _fit_text(draw, label, stat_w - 22, preferred=21, minimum=17)
                value_text, value_font = _fit_text(draw, value, stat_w - 22, preferred=36, minimum=28)
                draw.text((sx + 11, stat_y + 5), label_text, font=label_font, fill=MUTED)
                value_width, _ = _measure(draw, value_text, value_font)
                draw.text((sx + stat_w - 11 - value_width, stat_y + 8), value_text, font=value_font, fill=accent)

    footer_y = height - 62
    footer_font = _font(24, bold=False)
    draw.text((margin, footer_y), "Raspberry-Bot • MD Personalabteilung", font=footer_font, fill=MUTED)
    footer_right = "Übersicht • 1 Bild" if not hidden else f"{hidden} weitere • 1 Bild"
    footer_right_width, _ = _measure(draw, footer_right, footer_font)
    draw.text((width - margin - footer_right_width, footer_y), footer_right, font=footer_font, fill=MUTED)

    return _png_bytes(image)


def render_personnel_chart(title: str, rows) -> bytes:
    """Render a dedicated horizontal E/BWG activity chart for up to 10 people."""
    all_rows = list(rows)
    ordered = sorted(
        all_rows,
        key=lambda r: (_row_value(r, "inductions") + _row_value(r, "bwg")),
        reverse=True,
    )
    shown = ordered[:10]

    width = 1920
    height = 1080
    margin = 70
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_text, title_font = _fit_text(draw, title, width - margin * 2, preferred=82, minimum=62)
    draw.text((margin, 42), title_text, font=title_font, fill=TEXT)

    subtitle = "Einweisungen vs. BWG • Aktivität pro Mitarbeiter"
    subtitle_font = _font(32, bold=False)
    draw.text((margin, 142), subtitle, font=subtitle_font, fill=MUTED)

    legend_y = 196
    legend_font = _font(28, bold=True)
    draw.rounded_rectangle((margin, legend_y, margin + 34, legend_y + 22), radius=8, fill=BLUE)
    draw.text((margin + 48, legend_y - 7), "Einweisungen", font=legend_font, fill=TEXT)
    draw.rounded_rectangle((margin + 300, legend_y, margin + 334, legend_y + 22), radius=8, fill=GREEN)
    draw.text((margin + 348, legend_y - 7), "BWG", font=legend_font, fill=TEXT)

    chart_left = 500
    chart_right = width - margin
    chart_top = 270
    chart_bottom = height - 95
    chart_width = chart_right - chart_left

    max_value = max(
        [max(_row_value(r, "inductions"), _row_value(r, "bwg")) for r in shown] or [1]
    )
    max_value = max(1, max_value)

    guide_font = _font(22, bold=False)
    for step in range(6):
        value = round(max_value * step / 5)
        gx = chart_left + int(chart_width * step / 5)
        draw.line((gx, chart_top - 12, gx, chart_bottom), fill=(34, 38, 47), width=2)
        label = str(value)
        label_w, _ = _measure(draw, label, guide_font)
        draw.text((gx - label_w / 2, chart_top - 48), label, font=guide_font, fill=MUTED)

    if not shown:
        empty_font = _font(60, bold=True)
        draw.text((margin, 470), "Keine Perso-Daten vorhanden.", font=empty_font, fill=MUTED)
    else:
        available_h = chart_bottom - chart_top
        row_h = max(62, min(76, available_h // len(shown)))
        name_font_max = 40 if len(shown) <= 8 else 34
        name_font_min = 26
        bar_h = max(16, min(22, row_h // 3))

        for index, row in enumerate(shown):
            y = chart_top + index * row_h
            name = str(row["display_name"])
            name_text, name_font = _fit_text(
                draw, name, chart_left - margin - 35, preferred=name_font_max, minimum=name_font_min
            )
            draw.text((margin, y + 7), name_text, font=name_font, fill=TEXT)

            e = _row_value(row, "inductions")
            b = _row_value(row, "bwg")
            e_width = int(chart_width * e / max_value)
            b_width = int(chart_width * b / max_value)
            e_y = y + 5
            b_y = y + 34

            draw.rounded_rectangle((chart_left, e_y, chart_right, e_y + bar_h), radius=8, fill=TRACK)
            draw.rounded_rectangle((chart_left, b_y, chart_right, b_y + bar_h), radius=8, fill=TRACK)
            if e_width > 0:
                draw.rounded_rectangle((chart_left, e_y, chart_left + e_width, e_y + bar_h), radius=8, fill=BLUE)
            if b_width > 0:
                draw.rounded_rectangle((chart_left, b_y, chart_left + b_width, b_y + bar_h), radius=8, fill=GREEN)

            value_font = _font(24, bold=True)
            e_label = str(e)
            b_label = str(b)
            e_label_w, _ = _measure(draw, e_label, value_font)
            b_label_w, _ = _measure(draw, b_label, value_font)
            e_label_x = min(chart_right - e_label_w, chart_left + max(8, e_width) + 10)
            b_label_x = min(chart_right - b_label_w, chart_left + max(8, b_width) + 10)
            draw.text((e_label_x, e_y - 5), e_label, font=value_font, fill=TEXT)
            draw.text((b_label_x, b_y - 5), b_label, font=value_font, fill=TEXT)

    total_e = sum(_row_value(r, "inductions") for r in all_rows)
    total_b = sum(_row_value(r, "bwg") for r in all_rows)
    footer_font = _font(26, bold=False)
    footer = f"Gesamt: {total_e} Einweisungen • {total_b} BWG • {total_e + total_b} Aktivität"
    draw.text((margin, height - 58), footer, font=footer_font, fill=MUTED)

    return _png_bytes(image)
