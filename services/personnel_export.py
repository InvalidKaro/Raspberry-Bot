from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(size: int, *, bold: bool = False):
    path = FONT_BOLD if bold else FONT_REGULAR
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _measure(draw: ImageDraw.ImageDraw, value: str, font) -> int:
    box = draw.textbbox((0, 0), str(value), font=font)
    return box[2] - box[0]


def _fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    max_width: int,
    *,
    preferred: int,
    minimum: int,
    bold: bool = True,
):
    """Return readable text + the largest font that fits.

    We never shrink below ``minimum``. If the full value still does not fit at the
    minimum size, the value is shortened with an ellipsis instead of becoming tiny.
    """
    value = str(value)

    for size in range(preferred, minimum - 1, -2):
        font = _font(size, bold=bold)
        if _measure(draw, value, font) <= max_width:
            return value, font

    font = _font(minimum, bold=bold)
    suffix = "…"
    trimmed = value
    while len(trimmed) > 1 and _measure(draw, trimmed + suffix, font) > max_width:
        trimmed = trimmed[:-1]
    return trimmed + suffix, font


def _row_value(row, key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError):
        return 0


def render_personnel_png(title: str, rows) -> bytes:
    """Render one fresh, fixed-size, mobile-readable personnel dashboard PNG."""
    all_rows = list(rows)

    # Readability wins over fitting an unlimited amount of data into one preview.
    # Active people are shown first; inactive 0/0 rows only fill remaining slots.
    active = [r for r in all_rows if _row_value(r, "inductions") or _row_value(r, "bwg")]
    inactive = [r for r in all_rows if not (_row_value(r, "inductions") or _row_value(r, "bwg"))]
    ordered = active + inactive
    shown = ordered[:8]
    hidden = max(0, len(all_rows) - len(shown))

    total_e = sum(_row_value(r, "inductions") for r in all_rows)
    total_b = sum(_row_value(r, "bwg") for r in all_rows)
    total_activity = total_e + total_b

    # Fixed canvas: changing the amount of data no longer makes the whole image
    # endlessly taller and therefore tiny inside Discord's preview.
    width = 1920
    height = 1280
    margin = 54
    gap = 24

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

    # -------------------------------------------------------------------------
    # Header
    # -------------------------------------------------------------------------
    title_text, title_font = _fit_text(
        draw,
        title,
        width - margin * 2,
        preferred=92,
        minimum=70,
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

    # -------------------------------------------------------------------------
    # KPI row
    # -------------------------------------------------------------------------
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

        label_text, label_font = _fit_text(
            draw,
            label,
            kpi_w - 50,
            preferred=30,
            minimum=24,
        )
        value_text, value_font = _fit_text(
            draw,
            value,
            kpi_w - 50,
            preferred=64,
            minimum=44,
        )
        draw.text((x + 26, kpi_y + 18), label_text, font=label_font, fill=muted)
        draw.text((x + 26, kpi_y + 62), value_text, font=value_font, fill=accent)

    # -------------------------------------------------------------------------
    # Personnel cards: 2 columns × 4 rows, fixed geometry.
    # -------------------------------------------------------------------------
    grid_top = 395
    columns = 2
    rows_count = 4
    card_gap_x = 24
    card_gap_y = 20
    card_w = (width - margin * 2 - card_gap_x) // columns
    card_h = 190

    if not shown:
        empty_y = grid_top + 160
        empty_text, empty_font = _fit_text(
            draw,
            "Keine Perso-Daten vorhanden.",
            width - margin * 2,
            preferred=78,
            minimum=58,
        )
        draw.text((margin, empty_y), empty_text, font=empty_font, fill=muted)
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
            activity = _row_value(row, "activity")
            if activity == 0:
                activity = e + b

            # Large name line.
            name = f"{index + 1:02d}  {row['display_name']}"
            name_text, name_font = _fit_text(
                draw,
                name,
                card_w - 44,
                preferred=72,
                minimum=50,
            )
            draw.text((x + 22, y + 12), name_text, font=name_font, fill=text)

            # Three large stat blocks. No charts/bars: they consumed space without
            # improving readability on mobile.
            stat_y = y + 105
            stat_gap = 14
            stat_w = (card_w - 44 - stat_gap * 2) // 3
            stats = (
                ("E", str(e), blue),
                ("BWG", str(b), green),
                ("GESAMT", str(activity), gold),
            )

            for stat_index, (label, value, accent) in enumerate(stats):
                sx = x + 22 + stat_index * (stat_w + stat_gap)
                draw.rounded_rectangle((sx, stat_y, sx + stat_w, stat_y + 64), radius=16, fill=bg)

                label_text, label_font = _fit_text(
                    draw,
                    label,
                    stat_w - 22,
                    preferred=24,
                    minimum=19,
                )
                value_text, value_font = _fit_text(
                    draw,
                    value,
                    stat_w - 22,
                    preferred=42,
                    minimum=32,
                )

                draw.text((sx + 11, stat_y + 5), label_text, font=label_font, fill=muted)
                value_width = _measure(draw, value_text, value_font)
                draw.text((sx + stat_w - 11 - value_width, stat_y + 8), value_text, font=value_font, fill=accent)

    # -------------------------------------------------------------------------
    # Footer
    # -------------------------------------------------------------------------
    footer_y = height - 62
    footer_font = _font(26, bold=False)
    draw.text((margin, footer_y), "Raspberry-Bot • MD Personalabteilung", font=footer_font, fill=muted)

    if hidden:
        hidden_text = f"Lesbarkeitsmodus: max. 8 Personen pro Bild"
    else:
        hidden_text = "Lesbarkeitsmodus • 1 Bild"
    hidden_font = _font(26, bold=False)
    hidden_width = _measure(draw, hidden_text, hidden_font)
    draw.text((width - margin - hidden_width, footer_y), hidden_text, font=hidden_font, fill=muted)

    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()
