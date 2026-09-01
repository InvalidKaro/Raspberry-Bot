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


def _fit_big(draw: ImageDraw.ImageDraw, text: str, max_width: int, *, max_size: int, min_size: int):
    """Use the biggest readable font possible; truncate before going below min_size."""
    value = str(text)
    for size in range(max_size, min_size - 1, -2):
        font = _font(size, bold=True)
        if _text_width(draw, value, font) <= max_width:
            return value, font

    font = _font(min_size, bold=True)
    suffix = "…"
    shortened = value
    while len(shortened) > 2 and _text_width(draw, shortened + suffix, font) > max_width:
        shortened = shortened[:-1]
    return shortened + suffix, font


def render_personnel_png(title: str, rows) -> bytes:
    """Render one compact Perso image optimized for large text in Discord previews.

    Important: increasing font size while also increasing the whole canvas makes
    Discord scale the preview down again. This renderer therefore keeps the canvas
    and cards deliberately compact and sacrifices secondary details before text size.
    """
    original = list(rows)
    active = [r for r in original if int(r["inductions"]) > 0 or int(r["bwg"]) > 0]

    # 0/0 rows are expendable when there are many entries. Active staff are kept.
    shown = active if len(original) > 8 and active else original
    hidden = len(original) - len(shown)

    width = 1600
    margin = 34
    gap = 18
    columns = 2
    header_h = 300
    footer_h = 62
    card_h = 280
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

    # Large relative to the canvas. These values now actually affect preview size.
    TITLE_MAX, TITLE_MIN = 128, 96
    SUBTITLE_MAX, SUBTITLE_MIN = 54, 42
    KPI_LABEL_MAX, KPI_LABEL_MIN = 32, 26
    KPI_VALUE_MAX, KPI_VALUE_MIN = 66, 48
    NAME_MAX, NAME_MIN = 118, 92
    TOTAL_MAX, TOTAL_MIN = 70, 58
    BAR_MAX, BAR_MIN = 58, 48
    FOOTER_MAX, FOOTER_MIN = 28, 24

    total_e = sum(int(r["inductions"]) for r in original)
    total_b = sum(int(r["bwg"]) for r in original)
    total_activity = total_e + total_b
    top = original[0] if original else None

    title_text, title_font = _fit_big(draw, title, width - margin * 2, max_size=TITLE_MAX, min_size=TITLE_MIN)
    draw.text((margin, 8), title_text, font=title_font, fill=text)

    subtitle = f"{len(shown)} angezeigt"
    if hidden:
        subtitle += f" • {hidden} × 0/0 ausgeblendet"
    subtitle_text, subtitle_font = _fit_big(draw, subtitle, width - margin * 2, max_size=SUBTITLE_MAX, min_size=SUBTITLE_MIN)
    draw.text((margin, 132), subtitle_text, font=subtitle_font, fill=muted)

    kpi_y = 195
    kpi_gap = 12
    kpi_w = (width - margin * 2 - kpi_gap * 3) // 4
    kpis = [
        ("EINWEISUNGEN", str(total_e), accent_e),
        ("BWG", str(total_b), accent_b),
        ("AKTIVITÄT", str(total_activity), accent_total),
        ("TOP", str(top["display_name"]) if top else "—", text),
    ]
    for i, (label, value, accent) in enumerate(kpis):
        x = margin + i * (kpi_w + kpi_gap)
        draw.rounded_rectangle((x, kpi_y, x + kpi_w, kpi_y + 88), radius=18, fill=panel)
        draw.rounded_rectangle((x, kpi_y, x + 8, kpi_y + 88), radius=4, fill=accent)
        label_text, label_font = _fit_big(draw, label, kpi_w - 28, max_size=KPI_LABEL_MAX, min_size=KPI_LABEL_MIN)
        value_text, value_font = _fit_big(draw, value, kpi_w - 28, max_size=KPI_VALUE_MAX, min_size=KPI_VALUE_MIN)
        draw.text((x + 15, kpi_y + 5), label_text, font=label_font, fill=muted)
        draw.text((x + 15, kpi_y + 34), value_text, font=value_font, fill=accent)

    max_value = max([max(int(r["inductions"]), int(r["bwg"])) for r in shown] or [1])
    max_value = max(1, max_value)

    if not shown:
        y = header_h
        draw.rounded_rectangle((margin, y, width - margin, y + card_h), radius=22, fill=panel)
        msg, msg_font = _fit_big(draw, "Keine Perso-Daten vorhanden.", width - margin * 2 - 40, max_size=NAME_MAX, min_size=NAME_MIN)
        draw.text((margin + 20, y + 70), msg, font=msg_font, fill=muted)

    for index, r in enumerate(shown):
        col = index % columns
        row_index = index // columns
        x = margin + col * (card_w + gap)
        y = header_h + row_index * (card_h + gap)
        fill = panel if row_index % 2 == 0 else panel_alt
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=22, fill=fill)

        e = int(r["inductions"])
        b = int(r["bwg"])
        activity = int(r["activity"])

        # Primary information gets almost the whole card width. Rank/department are
        # intentionally omitted here so the name can stay genuinely large.
        name_raw = f"{index + 1:02d} {r['display_name']}"
        name, name_font = _fit_big(draw, name_raw, card_w - 36, max_size=NAME_MAX, min_size=NAME_MIN)
        draw.text((x + 18, y + 4), name, font=name_font, fill=text)

        total_text, total_font = _fit_big(draw, f"Gesamt {activity}", card_w - 36, max_size=TOTAL_MAX, min_size=TOTAL_MIN)
        total_box = draw.textbbox((0, 0), total_text, font=total_font)
        draw.text((x + card_w - 18 - (total_box[2] - total_box[0]), y + 104), total_text, font=total_font, fill=accent_total)

        bar_left = x + 18
        bar_right = x + card_w - 18
        bar_width = bar_right - bar_left
        e_w = int(bar_width * e / max_value)
        b_w = int(bar_width * b / max_value)
        e_y = y + 165
        b_y = y + 222
        bar_h = 48

        draw.rounded_rectangle((bar_left, e_y, bar_right, e_y + bar_h), radius=16, fill=track)
        draw.rounded_rectangle((bar_left, b_y, bar_right, b_y + bar_h), radius=16, fill=track)
        if e_w > 0:
            draw.rounded_rectangle((bar_left, e_y, bar_left + e_w, e_y + bar_h), radius=16, fill=accent_e)
        if b_w > 0:
            draw.rounded_rectangle((bar_left, b_y, bar_left + b_w, b_y + bar_h), radius=16, fill=accent_b)

        e_text, e_font = _fit_big(draw, f"Einweisungen {e}", bar_width - 20, max_size=BAR_MAX, min_size=BAR_MIN)
        b_text, b_font = _fit_big(draw, f"BWG {b}", bar_width - 20, max_size=BAR_MAX, min_size=BAR_MIN)
        draw.text((bar_left + 10, e_y - 5), e_text, font=e_font, fill=text)
        draw.text((bar_left + 10, b_y - 5), b_text, font=b_font, fill=text)

    footer_y = height - footer_h + 12
    left, left_font = _fit_big(draw, "Raspberry-Bot • MD Personalabteilung", 1000, max_size=FOOTER_MAX, min_size=FOOTER_MIN)
    draw.text((margin, footer_y), left, font=left_font, fill=muted)
    right, right_font = _fit_big(draw, "1 Bild • COMPACT XXL", 430, max_size=FOOTER_MAX, min_size=FOOTER_MIN)
    rb = draw.textbbox((0, 0), right, font=right_font)
    draw.text((width - margin - (rb[2] - rb[0]), footer_y), right, font=right_font, fill=muted)

    buf = BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()
