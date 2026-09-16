from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from config import settings
from services.govee_climate_history import ClimateSample, GoveeClimateHistory

_render_limit = asyncio.Semaphore(max(settings.image_render_concurrency, 1))


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        (
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
        ),
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _period_label(hours: int) -> str:
    if hours == 6:
        return "6 Stunden"
    if hours == 24:
        return "24 Stunden"
    if hours == 168:
        return "7 Tage"
    if hours == 720:
        return "30 Tage"
    return f"{hours} Stunden"


def _local_label(sample: ClimateSample, period_hours: int) -> str:
    local = sample.recorded_at.astimezone()
    if period_hours <= 24:
        return local.strftime("%H:%M")
    if period_hours <= 168:
        return local.strftime("%a %H:%M")
    return local.strftime("%d.%m.")


def _series_values(
    samples: Sequence[ClimateSample],
    attribute: str,
) -> list[tuple[ClimateSample, float]]:
    result: list[tuple[ClimateSample, float]] = []
    for sample in samples:
        value = getattr(sample, attribute)
        if value is not None:
            result.append((sample, float(value)))
    return result


def _draw_metric_card(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    *,
    label: str,
    value: str,
    subtitle: str,
    accent: tuple[int, int, int],
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(bounds, radius=26, fill=(24, 30, 36))
    draw.rounded_rectangle(
        (left + 18, top + 18, left + 26, bottom - 18),
        radius=4,
        fill=accent,
    )
    draw.text((left + 48, top + 22), label, font=_font(18, True), fill=(160, 170, 180))
    draw.text((left + 48, top + 52), value, font=_font(36, True), fill=(244, 248, 250))
    draw.text((left + 48, bottom - 38), subtitle, font=_font(15), fill=(130, 142, 152))


def _draw_chart(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    *,
    samples: Sequence[ClimateSample],
    attribute: str,
    title: str,
    unit: str,
    accent: tuple[int, int, int],
    period_hours: int,
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(bounds, radius=26, fill=(20, 25, 31))
    draw.text((left + 28, top + 22), title, font=_font(20, True), fill=(235, 241, 244))

    data = _series_values(samples, attribute)
    if not data:
        draw.text((left + 28, top + 76), "Noch keine Messwerte", font=_font(18), fill=(135, 145, 155))
        return

    values = [value for _, value in data]
    minimum = min(values)
    maximum = max(values)
    average = sum(values) / len(values)

    draw.text(
        (right - 340, top + 24),
        f"Min {minimum:.1f}{unit}   Ø {average:.1f}{unit}   Max {maximum:.1f}{unit}",
        font=_font(15),
        fill=(145, 156, 166),
    )

    chart_left = left + 72
    chart_top = top + 72
    chart_right = right - 28
    chart_bottom = bottom - 58

    raw_span = maximum - minimum
    padding = max(raw_span * 0.15, 0.8 if unit == " °C" else 3.0)
    y_min = minimum - padding
    y_max = maximum + padding
    span = max(y_max - y_min, 1.0)

    axis_font = _font(13)
    for index in range(5):
        ratio = index / 4
        y = chart_top + (chart_bottom - chart_top) * ratio
        draw.line((chart_left, y, chart_right, y), fill=(45, 53, 61), width=1)
        axis_value = y_max - span * ratio
        text = f"{axis_value:.1f}"
        box = draw.textbbox((0, 0), text, font=axis_font)
        draw.text(
            (chart_left - 12 - (box[2] - box[0]), y - 8),
            text,
            font=axis_font,
            fill=(112, 124, 134),
        )

    if len(data) == 1:
        x = (chart_left + chart_right) / 2
        y = chart_bottom - ((data[0][1] - y_min) / span * (chart_bottom - chart_top))
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=accent)
        draw.text(
            (chart_left, chart_bottom + 20),
            "Historie startet jetzt – der Pi sammelt automatisch weiter.",
            font=_font(14),
            fill=(126, 138, 148),
        )
        return

    max_render_points = 900
    if len(data) > max_render_points:
        step = (len(data) - 1) / (max_render_points - 1)
        selected = [data[round(index * step)] for index in range(max_render_points)]
    else:
        selected = data

    points: list[tuple[float, float]] = []
    for index, (_, value) in enumerate(selected):
        x = chart_left + ((chart_right - chart_left) * index / max(len(selected) - 1, 1))
        y = chart_bottom - ((value - y_min) / span * (chart_bottom - chart_top))
        points.append((x, y))

    draw.line(points, fill=accent, width=4, joint="curve")

    first = data[0][0]
    middle = data[len(data) // 2][0]
    last = data[-1][0]
    labels = [
        (chart_left, _local_label(first, period_hours), "left"),
        ((chart_left + chart_right) / 2, _local_label(middle, period_hours), "center"),
        (chart_right, _local_label(last, period_hours), "right"),
    ]
    tick_font = _font(13)
    for x, label, alignment in labels:
        box = draw.textbbox((0, 0), label, font=tick_font)
        width = box[2] - box[0]
        if alignment == "center":
            x -= width / 2
        elif alignment == "right":
            x -= width
        draw.text((x, chart_bottom + 20), label, font=tick_font, fill=(112, 124, 134))


def _render(
    samples: Sequence[ClimateSample],
    *,
    model: str,
    period_hours: int,
) -> BytesIO:
    width, height = 1200, 820
    image = Image.new("RGB", (width, height), (10, 14, 18))
    draw = ImageDraw.Draw(image)

    draw.text((54, 42), "RAUMKLIMA", font=_font(16, True), fill=(93, 232, 171))
    draw.text((54, 68), f"{model} · {_period_label(period_hours)}", font=_font(34, True), fill=(245, 248, 250))
    draw.text((54, 112), "Lokale HomePi-Historie · Bluetooth", font=_font(16), fill=(132, 145, 156))

    latest = samples[-1]
    temperature = f"{latest.temperature_c:.1f} °C" if latest.temperature_c is not None else "–"
    humidity = f"{latest.humidity_percent:.1f} %" if latest.humidity_percent is not None else "–"
    battery = f"{latest.battery_percent:.0f} %" if latest.battery_percent is not None else "–"

    _draw_metric_card(
        draw,
        (54, 158, 414, 280),
        label="TEMPERATUR",
        value=temperature,
        subtitle=f"{len(samples)} Messpunkte",
        accent=(90, 232, 168),
    )
    _draw_metric_card(
        draw,
        (432, 158, 792, 280),
        label="LUFTFEUCHTE",
        value=humidity,
        subtitle="Relative Feuchte",
        accent=(74, 176, 255),
    )
    _draw_metric_card(
        draw,
        (810, 158, 1146, 280),
        label="BATTERIE",
        value=battery,
        subtitle="Letzter BLE-Wert",
        accent=(247, 196, 72),
    )

    _draw_chart(
        draw,
        (54, 304, 1146, 534),
        samples=samples,
        attribute="temperature_c",
        title="Temperaturverlauf",
        unit=" °C",
        accent=(90, 232, 168),
        period_hours=period_hours,
    )
    _draw_chart(
        draw,
        (54, 556, 1146, 786),
        samples=samples,
        attribute="humidity_percent",
        title="Luftfeuchteverlauf",
        unit=" %",
        accent=(74, 176, 255),
        period_hours=period_hours,
    )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    image.close()
    output.seek(0)
    return output


async def render_climate_history(
    samples: Sequence[ClimateSample],
    *,
    model: str,
    period_hours: int = 24,
) -> BytesIO:
    async with _render_limit:
        return await asyncio.to_thread(
            _render,
            samples,
            model=model,
            period_hours=period_hours,
        )
