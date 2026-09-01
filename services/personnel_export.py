from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont


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


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def _scaled_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    *,
    max_size: int,
    min_size: int,
    bold: bool = True,
):
    """Return the largest font that fits, but never go below min_size.

    This is width-driven, not row-count-driven: short text remains very large while
    long text is reduced only as much as required to fit its available area.
    """
    value = str(text)
    lo = max(1, min_size)
    hi = max(lo, max_size)
    best = _font(lo, bold=bold)

    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _font(mid, bold=bold)
        if _text_width(draw, value, candidate) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    return best


def _fit_at_minimum(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    *,
    max_size: int,
    min_size: int,
    bold: bool = True,
):
    """Scale text as large as possible; truncate only if min_size still does not fit."""
    value = str(text)
    font = _scaled_font(
        draw,
        value,
        max_width,
        max_size=max_size,
        min_size=min_size,
        bold=bold,
    )

    if _text_width(draw, value, font) <= max_width:
        return value, font

    # We reached the readability floor. Keep that floor and shorten the string
    # instead of silently making the text tiny.
    font = _font(min_size, bold=bold)
    suffix = "…"
    while len(value) > 2 and _text_width(draw, value + suffix, font) > max_width:
        value = value[:-1]
    return value + suffix, font


def render_personnel_png(title: str, rows) -> bytes:
    """Render exactly one Perso PNG with adaptive, readability-first typography.

    Fonts always start very large and are reduced only when a specific string does
    not fit. Every text category has a minimum readable size. 0/0 rows are omitted
    first when the export would otherwise become unnecessarily tall.
    """
    original = list(rows)
    active = [r for r in original if int(r["inductions"]) > 0 or int(r["bwg"]) > 0]

    # Always one image. With many entries, remove only completely inactive 0/0 rows.
    shown = active if len(original) > 8 and active else original
    hidden = max(0, len(original) - len(shown))

    width = 1600
    margin = 44
    columns = 2
    gap = 22
    header_h = 560
    footer_h = 96
    card_h = 540
    card_w = (width - margin * 2 - gap) // columns
    grid_rows = max(1, (len(shown) + columns - 1) // columns)
    height = header_h + grid_rows * (card_h + gap) + footer_h

    bg = (17, 19, 24)
    panel = (27, 30, 37)
    panel_alt = (31, 34, 42)
    track = (48, 52, 62)
    text = (247, 248, 251)
    muted = (181, 187, 200)
    accent_e = (91, 110, 245)
    accent_b = (71, 201, 145)
    accent_total = (245, 184, 72)

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)

    # Readability floors. Automatic scaling is allowed ABOVE these values only.
    # In other words: a long name may shrink from 118 px, but never below 72 px.
    TITLE_MAX, TITLE_MIN = 220, 150
    SUBTITLE_MAX, SUBTITLE_MIN = 110, 72
    KPI_LABEL_MAX, KPI_LABEL_MIN = 82, 54
    KPI_VALUE_MAX, KPI_VALUE_MIN = 150, 96
    NAME_MAX, NAME_MIN = 200, 120
    META_MAX, META_MIN = 105, 70
    TOTAL_MAX, TOTAL_MIN = 140, 92
    BAR_MAX, BAR_MIN = 130, 88
    FOOTER_MAX, FOOTER_MIN = 64, 42

    total_e = sum(int(r["inductions"]) for r in original)
    total_b = sum(int(r["bwg"]) for r in original)
    total_activity = total_e + total_b
    top = original[0] if original else None

    title_text, title_font = _fit_at_minimum(
        draw,
        title,
        width - margin * 2,
        max_size=TITLE_MAX,
        min_size=TITLE_MIN,
    )
    draw.text((margin, 10), title_text, font=title_font, fill=text)

    subtitle = f"{len(shown)} angezeigt"
    if hidden:
        subtitle += f" • {hidden} mit 0/0 ausgeblendet"
    subtitle_text, subtitle_font = _fit_at_minimum(
        draw,
        subtitle,
        width - margin * 2,
        max_size=SUBTITLE_MAX,
        min_size=SUBTITLE_MIN,
    )
    draw.text((margin, 205), subtitle_text, font=subtitle_font, fill=muted)

    kpi_y = 320
    kpi_gap = 14
    kpi_w = (width - margin * 2 - kpi_gap * 3) // 4
    kpis = [
        ("EINWEISUNGEN", str(total_e), accent_e),
        ("BWG", str(total_b), accent_b),
        ("AKTIVITÄT", str(total_activity), accent_total),
        ("TOP", str(top["display_name"]) if top else "—", text),
    ]
    for i, (label, value, accent) in enumerate(kpis):
        x = margin + i * (kpi_w + kpi_gap)
        draw.rounded_rectangle((x, kpi_y, x + kpi_w, kpi_y + 205), radius=24, fill=panel)
        draw.rounded_rectangle((x, kpi_y, x + 10, kpi_y + 205), radius=4, fill=accent)

        label_text, label_font = _fit_at_minimum(
            draw,
            label,
            kpi_w - 36,
            max_size=KPI_LABEL_MAX,
            min_size=KPI_LABEL_MIN,
        )
        draw.text((x + 18, kpi_y + 9), label_text, font=label_font, fill=muted)

        value_text, value_font = _fit_at_minimum(
            draw,
            value,
            kpi_w - 36,
            max_size=KPI_VALUE_MAX,
            min_size=KPI_VALUE_MIN,
        )
        draw.text((x + 18, kpi_y + 88), value_text, font=value_font, fill=accent)

    max_value = max([max(int(r["inductions"]), int(r["bwg"])) for r in shown] or [1])
    max_value = max(1, max_value)

    if not shown:
        y = header_h
        draw.rounded_rectangle((margin, y, width - margin, y + card_h), radius=26, fill=panel)
        empty_text, empty_font = _fit_at_minimum(
            draw,
            "Keine Perso-Daten vorhanden.",
            width - margin * 2 - 64,
            max_size=NAME_MAX,
            min_size=NAME_MIN,
        )
        draw.text((margin + 32, y + 150), empty_text, font=empty_font, fill=muted)

    for index, r in enumerate(shown):
        col = index % columns
        row_index = index // columns
        x = margin + col * (card_w + gap)
        y = header_h + row_index * (card_h + gap)
        fill = panel if row_index % 2 == 0 else panel_alt
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=26, fill=fill)

        name_raw = f"{index + 1:02d} {r['display_name']}"
        name, name_font = _fit_at_minimum(
            draw,
            name_raw,
            card_w - 40,
            max_size=NAME_MAX,
            min_size=NAME_MIN,
        )

        rank = str(r["rank_name"] or "")
        department = str(r["department"] or "")
        meta_raw = " • ".join(part for part in (rank, department) if part)
        meta = ""
        meta_font = None
        if meta_raw:
            meta, meta_font = _fit_at_minimum(
                draw,
                meta_raw,
                card_w - 40,
                max_size=META_MAX,
                min_size=META_MIN,
            )

        e = int(r["inductions"])
        b = int(r["bwg"])
        activity = int(r["activity"])

        draw.text((x + 20, y + 10), name, font=name_font, fill=text)
        if meta and meta_font:
            draw.text((x + 20, y + 155), meta, font=meta_font, fill=muted)

        total_text = f"Gesamt {activity}"
        total_display, total_font = _fit_at_minimum(
            draw,
            total_text,
            card_w - 40,
            max_size=TOTAL_MAX,
            min_size=TOTAL_MIN,
        )
        total_box = draw.textbbox((0, 0), total_display, font=total_font)
        total_width = total_box[2] - total_box[0]
        draw.text((x + card_w - 20 - total_width, y + 225), total_display, font=total_font, fill=accent_total)

        bar_left = x + 20
        bar_right = x + card_w - 20
        bar_width = bar_right - bar_left
        e_w = int(bar_width * e / max_value)
        b_w = int(bar_width * b / max_value)
        e_y = y + 335
        b_y = y + 440
        bar_h = 84

        draw.rounded_rectangle((bar_left, e_y, bar_right, e_y + bar_h), radius=24, fill=track)
        draw.rounded_rectangle((bar_left, b_y, bar_right, b_y + bar_h), radius=24, fill=track)
        if e_w > 0:
            draw.rounded_rectangle((bar_left, e_y, bar_left + e_w, e_y + bar_h), radius=24, fill=accent_e)
        if b_w > 0:
            draw.rounded_rectangle((bar_left, b_y, bar_left + b_w, b_y + bar_h), radius=24, fill=accent_b)

        e_text, e_font = _fit_at_minimum(
            draw,
            f"Einweisungen {e}",
            bar_width - 24,
            max_size=BAR_MAX,
            min_size=BAR_MIN,
        )
        b_text, b_font = _fit_at_minimum(
            draw,
            f"BWG {b}",
            bar_width - 24,
            max_size=BAR_MAX,
            min_size=BAR_MIN,
        )
        draw.text((bar_left + 12, e_y - 3), e_text, font=e_font, fill=text)
        draw.text((bar_left + 12, b_y - 3), b_text, font=b_font, fill=text)

    footer_y = height - footer_h + 22
    footer_left = "Raspberry-Bot • MD Personalabteilung"
    footer_left_text, footer_font = _fit_at_minimum(
        draw,
        footer_left,
        1000,
        max_size=FOOTER_MAX,
        min_size=FOOTER_MIN,
    )
    draw.text((margin, footer_y), footer_left_text, font=footer_font, fill=muted)

    right = "1 Bild • AUTO-SCALE"
    right_text, right_font = _fit_at_minimum(
        draw,
        right,
        420,
        max_size=FOOTER_MAX,
        min_size=FOOTER_MIN,
    )
    rb = draw.textbbox((0, 0), right_text, font=right_font)
    draw.text((width - margin - (rb[2] - rb[0]), footer_y), right_text, font=right_font, fill=muted)

    buf = BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()
