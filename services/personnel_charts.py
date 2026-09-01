from __future__ import annotations

import asyncio
import math
import statistics
from dataclasses import dataclass
from io import BytesIO
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from config import settings

_render_limit = asyncio.Semaphore(max(settings.image_render_concurrency, 1))


@dataclass(slots=True)
class SeriesStats:
    total: float
    average: float
    median: float
    minimum: float
    maximum: float
    change: float | None
    change_percent: float | None


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _format_number(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k"
    if math.isclose(value, round(value), abs_tol=1e-9):
        return f"{int(round(value))}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def summarize(values: Sequence[float]) -> SeriesStats:
    if not values:
        raise ValueError("At least one value is required.")
    first = float(values[0])
    last = float(values[-1])
    change = last - first if len(values) > 1 else None
    if change is None or math.isclose(first, 0.0, abs_tol=1e-12):
        change_percent = None
    else:
        change_percent = change / abs(first) * 100.0
    return SeriesStats(
        total=float(sum(values)),
        average=float(statistics.fmean(values)),
        median=float(statistics.median(values)),
        minimum=float(min(values)),
        maximum=float(max(values)),
        change=change,
        change_percent=change_percent,
    )


def _draw_centered(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]
    draw.text((xy[0] - width / 2, xy[1]), text, font=font, fill=fill)


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: float) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    suffix = "…"
    candidate = text
    while candidate and draw.textbbox((0, 0), candidate + suffix, font=font)[2] > max_width:
        candidate = candidate[:-1]
    return (candidate + suffix) if candidate else suffix


def _nice_range(values: Sequence[float]) -> tuple[float, float]:
    minimum = min(values)
    maximum = max(values)
    if minimum >= 0:
        minimum = 0.0
    if maximum <= 0:
        maximum = 0.0
    if math.isclose(minimum, maximum, abs_tol=1e-9):
        maximum = minimum + (abs(minimum) * 0.2 or 1.0)

    span = max(maximum - minimum, 1e-9)
    rough_step = span / 5.0
    magnitude = 10 ** math.floor(math.log10(rough_step))
    normalized = rough_step / magnitude
    if normalized <= 1:
        nice = 1
    elif normalized <= 2:
        nice = 2
    elif normalized <= 5:
        nice = 5
    else:
        nice = 10
    step = nice * magnitude

    nice_min = math.floor(minimum / step) * step
    nice_max = math.ceil(maximum / step) * step
    if minimum >= 0:
        nice_min = 0.0
    if maximum <= 0:
        nice_max = 0.0
    if math.isclose(nice_min, nice_max, abs_tol=1e-9):
        nice_max = nice_min + step
    return nice_min, nice_max


def _render_chart(
    *,
    labels: Sequence[str],
    values: Sequence[float],
    title: str,
    x_label: str,
    y_label: str,
    series_name: str,
    chart_type: str,
    second_values: Sequence[float] | None,
    second_series_name: str | None,
    author_label: str | None,
) -> BytesIO:
    width, height = 1400, 820
    bg = (16, 18, 23)
    card = (31, 34, 41)
    plot_bg = (24, 27, 33)
    grid = (65, 69, 78)
    text = (245, 246, 248)
    muted = (167, 173, 184)
    primary = (88, 101, 242)
    secondary = (87, 242, 135)
    axis = (125, 131, 143)

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=30, fill=card)

    draw.text((60, 50), title[:70], font=_font(34, True), fill=text)
    subtitle = "MD Personnel Statistics • Raspberry-Bot"
    if author_label:
        subtitle += f" • {author_label[:40]}"
    draw.text((60, 98), subtitle, font=_font(18), fill=muted)

    left, top, right, bottom = 125, 180, width - 70, 650
    draw.rounded_rectangle((left, top, right, bottom), radius=20, fill=plot_bg)

    all_values = list(values)
    if second_values:
        all_values.extend(second_values)
    y_min, y_max = _nice_range(all_values)
    y_span = max(y_max - y_min, 1e-9)

    def y_pos(value: float) -> float:
        return bottom - 36 - ((value - y_min) / y_span) * (bottom - top - 72)

    plot_left = left + 80
    plot_right = right - 30
    plot_top = top + 30
    plot_bottom = bottom - 36

    # Horizontal grid and numeric ticks.
    tick_font = _font(15)
    for index in range(6):
        ratio = index / 5
        value = y_max - ratio * (y_max - y_min)
        y = plot_top + ratio * (plot_bottom - plot_top)
        draw.line((plot_left, y, plot_right, y), fill=grid, width=1)
        label = _format_number(value)
        box = draw.textbbox((0, 0), label, font=tick_font)
        draw.text((plot_left - 15 - (box[2] - box[0]), y - 9), label, font=tick_font, fill=muted)

    zero_y = y_pos(0.0) if y_min <= 0 <= y_max else plot_bottom
    draw.line((plot_left, zero_y, plot_right, zero_y), fill=axis, width=2)

    count = len(labels)
    slot = (plot_right - plot_left) / max(count, 1)
    label_font = _font(14)

    if chart_type == "line":
        def make_points(series: Sequence[float]) -> list[tuple[float, float]]:
            points: list[tuple[float, float]] = []
            for index, value in enumerate(series):
                x = plot_left + slot * (index + 0.5)
                points.append((x, y_pos(float(value))))
            return points

        first_points = make_points(values)
        if len(first_points) >= 2:
            draw.line(first_points, fill=primary, width=5, joint="curve")
        for index, (x, y) in enumerate(first_points):
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=primary, outline=text, width=2)
            if count <= 12:
                _draw_centered(draw, (x, y - 27), _format_number(float(values[index])), _font(13, True), text)

        if second_values:
            second_points = make_points(second_values)
            if len(second_points) >= 2:
                draw.line(second_points, fill=secondary, width=5, joint="curve")
            for index, (x, y) in enumerate(second_points):
                draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=secondary, outline=text, width=2)
                if count <= 12:
                    _draw_centered(draw, (x, y + 10), _format_number(float(second_values[index])), _font(13, True), text)
    else:
        dual = second_values is not None
        group_width = slot * 0.68
        bar_width = group_width / (2.25 if dual else 1.35)
        for index, value in enumerate(values):
            center = plot_left + slot * (index + 0.5)
            if dual:
                x1 = center - bar_width - 4
                x2 = center - 4
            else:
                x1 = center - bar_width / 2
                x2 = center + bar_width / 2
            yv = y_pos(float(value))
            top_y, bottom_y = sorted((yv, zero_y))
            if math.isclose(top_y, bottom_y, abs_tol=1):
                top_y -= 1
            draw.rounded_rectangle((x1, top_y, x2, bottom_y), radius=7, fill=primary)
            if count <= 12:
                _draw_centered(draw, ((x1 + x2) / 2, max(top_y - 23, plot_top + 2)), _format_number(float(value)), _font(13, True), text)

            if second_values is not None:
                second = float(second_values[index])
                x1b = center + 4
                x2b = center + bar_width + 4
                yb = y_pos(second)
                top_b, bottom_b = sorted((yb, zero_y))
                if math.isclose(top_b, bottom_b, abs_tol=1):
                    top_b -= 1
                draw.rounded_rectangle((x1b, top_b, x2b, bottom_b), radius=7, fill=secondary)
                if count <= 12:
                    _draw_centered(draw, ((x1b + x2b) / 2, max(top_b - 23, plot_top + 2)), _format_number(second), _font(13, True), text)

    # X-axis category labels.
    max_label_width = max(slot - 8, 35)
    for index, raw in enumerate(labels):
        center = plot_left + slot * (index + 0.5)
        label = _ellipsize(draw, str(raw), label_font, max_label_width)
        _draw_centered(draw, (center, bottom + 8), label, label_font, muted)

    # Axis titles.
    _draw_centered(draw, ((plot_left + plot_right) / 2, height - 102), x_label[:50], _font(18, True), text)
    draw.text((45, top + 5), y_label[:45], font=_font(18, True), fill=text)

    # Legend + compact summary.
    legend_y = height - 62
    draw.rounded_rectangle((60, legend_y - 9, 74, legend_y + 5), radius=3, fill=primary)
    draw.text((84, legend_y - 13), series_name[:28], font=_font(16, True), fill=text)
    if second_values is not None:
        draw.rounded_rectangle((330, legend_y - 9, 344, legend_y + 5), radius=3, fill=secondary)
        draw.text((354, legend_y - 13), (second_series_name or "Series 2")[:28], font=_font(16, True), fill=text)

    stats = summarize(values)
    summary = f"Σ {_format_number(stats.total)}  •  Ø {_format_number(stats.average)}  •  Min {_format_number(stats.minimum)}  •  Max {_format_number(stats.maximum)}"
    box = draw.textbbox((0, 0), summary, font=_font(15))
    draw.text((width - 60 - (box[2] - box[0]), legend_y - 12), summary, font=_font(15), fill=muted)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    image.close()
    output.seek(0)
    return output


async def render_personnel_chart(
    *,
    labels: Sequence[str],
    values: Sequence[float],
    title: str,
    x_label: str,
    y_label: str,
    series_name: str,
    chart_type: str = "bar",
    second_values: Sequence[float] | None = None,
    second_series_name: str | None = None,
    author_label: str | None = None,
) -> BytesIO:
    if not labels or not values:
        raise ValueError("Labels and values must not be empty.")
    if len(labels) != len(values):
        raise ValueError("The number of X labels must match the number of Y values.")
    if second_values is not None and len(second_values) != len(values):
        raise ValueError("The second series must contain exactly as many values as the first series.")
    if not 1 <= len(labels) <= 24:
        raise ValueError("Use between 1 and 24 data points per chart.")
    if chart_type not in {"bar", "line"}:
        raise ValueError("Chart type must be 'bar' or 'line'.")

    async with _render_limit:
        return await asyncio.to_thread(
            _render_chart,
            labels=labels,
            values=values,
            title=title,
            x_label=x_label,
            y_label=y_label,
            series_name=series_name,
            chart_type=chart_type,
            second_values=second_values,
            second_series_name=second_series_name,
            author_label=author_label,
        )
