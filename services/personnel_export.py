from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


# Raspberry Pi OS Lite does not always ship the DejaVu font in the exact path we
# previously assumed. Falling back to ImageFont.load_default() was the real reason
# changing 50/100/200 px barely changed the export: Pillow's bitmap fallback has a
# basically fixed visual size. Search several normal Linux font locations instead.
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


@lru_cache(maxsize=128)
def _font(size: int, *, bold: bool = False):
    """Return a genuinely scalable TrueType font at the requested pixel size."""
    candidates = FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REGULAR

    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                pass

    # Pillow can often resolve DejaVu by filename even if distro paths differ.
    names = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf") if bold else (
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass

    # Newer Pillow versions support a sized fallback. This is still preferable to
    # silently returning the tiny fixed bitmap font.
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
    """Fit one line as large as possible into the complete available box."""
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


def render_personnel_png(title: str, rows) -> bytes:
    """Render one fixed-size personnel dashboard with maximum name readability."""
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

    bg = (15, 17, 22)
    panel = (25, 28, 35)
    panel_soft = (31, 34, 42)
    text = (248, 249, 252)
    muted = (178, 184, 197)
    blue = (91, 110, 245)
    green = (71, 201, 145)
    gold = (245, 184, 72)

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)

    # Header
    title_text, title_font = _fit_text(
        draw, title, width - margin * 2, preferred=92, minimum=70
    )
    draw.text((margin, 38), title_text, font=title_font, fill=text)

    subtitle = f"{len(shown)} von {len(all_rows)} Personen angezeigt"
    if hidden:
        subtitle += f"  •  {hidden} weitere nicht im Bild"
    subtitle_text, subtitle_font = _fit_text(
        draw,
        subtitle,
        width - margin * 2,
        preferred=38,
        minimum=30,
        bold=False,
    )
    draw.text((margin, 144), subtitle_text, font=subtitle_font, fill=muted)

    # KPI row
    kpi_y = 210
    kpi_h = 150
    kpi_gap = 18
    kpi_w = (width - margin * 2 - kpi_gap * 3) // 4

    top_name = str(all_rows[0]["display_name"]) if all_rows else "—"
    kpis = (
        ("EINWEISUNGEN", str(total_e), blue),
        ("BWG", str(total_b), green),
        ("GESAMT", str(total_activity), gold),
        ("TOP", top_name, text),
    )

    for index, (label, value, accent) in enumerate(kpis):
        x = margin + index * (kpi_w + kpi_gap)
        draw.rounded_rectangle((x, kpi_y, x + kpi_w, kpi_y + kpi_h), radius=24, fill=panel)
        draw.rounded_rectangle((x, kpi_y, x + 10, kpi_y + kpi_h), radius=5, fill=accent)

        label_text, label_font = _fit_text(draw, label, kpi_w - 50, preferred=30, minimum=24)
        value_text, value_font = _fit_text(draw, value, kpi_w - 50, preferred=64, minimum=44)
        draw.text((x + 26, kpi_y + 18), label_text, font=label_font, fill=muted)
        draw.text((x + 26, kpi_y + 62), value_text, font=value_font, fill=accent)

    # Personnel cards: 2 columns × 4 rows.
    grid_top = 395
    columns = 2
    card_gap_x = 24
    card_gap_y = 20
    card_w = (width - margin * 2 - card_gap_x) // columns
    card_h = 190

    if not shown:
        empty_text, empty_font = _fit_text(
            draw,
            "Keine Perso-Daten vorhanden.",
            width - margin * 2,
            preferred=78,
            minimum=58,
        )
        draw.text((margin, grid_top + 160), empty_text, font=empty_font, fill=muted)
    else:
        for index, row in enumerate(shown):
            col = index % columns
            row_index = index // columns
            x = margin + col * (card_w + card_gap_x)
            y = grid_top + row_index * (card_h + card_gap_y)
            fill = panel if row_index % 2 == 0 else panel_soft
            draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=24, fill=fill)

            e = _row_value(row, "inductions")
            b = _row_value(row, "bwg")
            activity = _row_value(row, "activity") or (e + b)

            # Keep ranking compact; let the name consume essentially the entire
            # remaining upper card area. A real scalable TTF now makes this visible.
            badge_size = 46
            badge_x = x + 18
            badge_y = y + 18
            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_size, badge_y + badge_size),
                radius=13,
                fill=bg,
            )
            badge_font = _font(23, bold=True)
            badge_value = str(index + 1)
            badge_w, badge_h = _measure(draw, badge_value, badge_font)
            draw.text(
                (
                    badge_x + (badge_size - badge_w) / 2,
                    badge_y + (badge_size - badge_h) / 2 - 3,
                ),
                badge_value,
                font=badge_font,
                fill=muted,
            )

            name_left = badge_x + badge_size + 16
            name_right = x + card_w - 18
            name_top = y + 2
            name_bottom = y + 108
            name_box_w = name_right - name_left
            name_box_h = name_bottom - name_top

            name_text, name_font = _fit_box(
                draw,
                str(row["display_name"]),
                name_box_w,
                name_box_h,
                maximum=126,
                minimum=60,
            )
            _, name_h = _measure(draw, name_text, name_font)
            name_y = name_top + max(0, (name_box_h - name_h) // 2) - 5
            draw.text((name_left, name_y), name_text, font=name_font, fill=text)

            stat_y = y + 111
            stat_gap = 14
            stat_w = (card_w - 44 - stat_gap * 2) // 3
            stats = (
                ("E", str(e), blue),
                ("BWG", str(b), green),
                ("GESAMT", str(activity), gold),
            )

            for stat_index, (label, value, accent) in enumerate(stats):
                sx = x + 22 + stat_index * (stat_w + stat_gap)
                draw.rounded_rectangle((sx, stat_y, sx + stat_w, stat_y + 60), radius=16, fill=bg)

                label_text, label_font = _fit_text(draw, label, stat_w - 22, preferred=22, minimum=18)
                value_text, value_font = _fit_text(draw, value, stat_w - 22, preferred=40, minimum=30)

                draw.text((sx + 11, stat_y + 4), label_text, font=label_font, fill=muted)
                value_width, _ = _measure(draw, value_text, value_font)
                draw.text((sx + stat_w - 11 - value_width, stat_y + 6), value_text, font=value_font, fill=accent)

    footer_y = height - 62
    footer_font = _font(26, bold=False)
    draw.text((margin, footer_y), "Raspberry-Bot • MD Personalabteilung", font=footer_font, fill=muted)

    footer_right = "Namensfeld: maximale Fläche" if not hidden else f"{hidden} weitere • maximale Namensfläche"
    footer_right_font = _font(26, bold=False)
    footer_right_width, _ = _measure(draw, footer_right, footer_right_font)
    draw.text((width - margin - footer_right_width, footer_y), footer_right, font=footer_right_font, fill=muted)

    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()
