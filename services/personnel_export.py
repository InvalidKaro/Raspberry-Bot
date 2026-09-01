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


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    value = str(text)
    if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
        return value
    while len(value) > 4:
        value = value[:-2] + "…"
        if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
            return value
    return value


def render_personnel_png(title: str, rows) -> bytes:
    """Render exactly one Perso PNG with very large fixed typography.

    0/0 rows are omitted when the list would otherwise become unnecessarily tall.
    Active rows are never dropped just to make the canvas smaller.
    """
    original = list(rows)
    active = [r for r in original if int(r["inductions"]) > 0 or int(r["bwg"]) > 0]
    zero = [r for r in original if int(r["inductions"]) == 0 and int(r["bwg"]) == 0]

    # Keep one image. If there are more than eight entries, remove only 0/0 rows.
    # Never cut active people simply to keep the image compact.
    shown = active if len(original) > 8 and active else original
    hidden = max(0, len(original) - len(shown))

    width = 1600
    margin = 44
    columns = 2
    gap = 22
    header_h = 470
    footer_h = 92
    card_h = 440
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

    # Deliberately huge fixed font sizes. They are NOT reduced based on row count.
    title_font = _font(132, bold=True)
    subtitle_font = _font(56, bold=True)
    kpi_label_font = _font(42, bold=True)
    kpi_value_font = _font(86, bold=True)
    name_font = _font(96, bold=True)
    meta_font = _font(54, bold=True)
    total_font = _font(68, bold=True)
    bar_font = _font(62, bold=True)
    footer_font = _font(34, bold=True)

    total_e = sum(int(r["inductions"]) for r in original)
    total_b = sum(int(r["bwg"]) for r in original)
    total_activity = total_e + total_b
    top = original[0] if original else None

    draw.text((margin, 12), _fit(draw, title, title_font, width - margin * 2), font=title_font, fill=text)
    subtitle = f"{len(shown)} angezeigt"
    if hidden:
        subtitle += f" • {hidden} mit 0/0 ausgeblendet"
    draw.text((margin, 164), subtitle, font=subtitle_font, fill=muted)

    kpi_y = 255
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
        draw.rounded_rectangle((x, kpi_y, x + kpi_w, kpi_y + 174), radius=24, fill=panel)
        draw.rounded_rectangle((x, kpi_y, x + 10, kpi_y + 174), radius=4, fill=accent)
        draw.text((x + 18, kpi_y + 10), label, font=kpi_label_font, fill=muted)
        display = _fit(draw, value, kpi_value_font, kpi_w - 36)
        draw.text((x + 18, kpi_y + 66), display, font=kpi_value_font, fill=accent)

    max_value = max([max(int(r["inductions"]), int(r["bwg"])) for r in shown] or [1])
    max_value = max(1, max_value)

    if not shown:
        y = header_h
        draw.rounded_rectangle((margin, y, width - margin, y + card_h), radius=26, fill=panel)
        draw.text((margin + 32, y + 140), "Keine Perso-Daten vorhanden.", font=name_font, fill=muted)

    for index, r in enumerate(shown):
        col = index % columns
        row_index = index // columns
        x = margin + col * (card_w + gap)
        y = header_h + row_index * (card_h + gap)
        fill = panel if row_index % 2 == 0 else panel_alt
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=26, fill=fill)

        name = _fit(draw, f"{index + 1:02d} {r['display_name']}", name_font, card_w - 40)
        rank = str(r["rank_name"] or "")
        department = str(r["department"] or "")
        meta = " • ".join(part for part in (rank, department) if part)
        meta = _fit(draw, meta, meta_font, card_w - 40) if meta else ""

        e = int(r["inductions"])
        b = int(r["bwg"])
        activity = int(r["activity"])

        draw.text((x + 20, y + 10), name, font=name_font, fill=text)
        if meta:
            draw.text((x + 20, y + 120), meta, font=meta_font, fill=muted)

        total_text = f"Gesamt {activity}"
        total_box = draw.textbbox((0, 0), total_text, font=total_font)
        draw.text((x + card_w - 20 - (total_box[2] - total_box[0]), y + 174), total_text, font=total_font, fill=accent_total)

        bar_left = x + 20
        bar_right = x + card_w - 20
        bar_width = bar_right - bar_left
        e_w = int(bar_width * e / max_value)
        b_w = int(bar_width * b / max_value)
        e_y = y + 258
        b_y = y + 348
        bar_h = 72

        draw.rounded_rectangle((bar_left, e_y, bar_right, e_y + bar_h), radius=24, fill=track)
        draw.rounded_rectangle((bar_left, b_y, bar_right, b_y + bar_h), radius=24, fill=track)
        if e_w > 0:
            draw.rounded_rectangle((bar_left, e_y, bar_left + e_w, e_y + bar_h), radius=24, fill=accent_e)
        if b_w > 0:
            draw.rounded_rectangle((bar_left, b_y, bar_left + b_w, b_y + bar_h), radius=24, fill=accent_b)

        draw.text((bar_left + 12, e_y - 1), f"Einweisungen {e}", font=bar_font, fill=text)
        draw.text((bar_left + 12, b_y - 1), f"BWG {b}", font=bar_font, fill=text)

    footer_y = height - footer_h + 22
    draw.text((margin, footer_y), "Raspberry-Bot • MD Personalabteilung", font=footer_font, fill=muted)
    right = "1 Bild • XXL Schrift"
    rb = draw.textbbox((0, 0), right, font=footer_font)
    draw.text((width - margin - (rb[2] - rb[0]), footer_y), right, font=footer_font, fill=muted)

    buf = BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()
