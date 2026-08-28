from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from config import settings

_render_limit = asyncio.Semaphore(max(settings.image_render_concurrency, 1))


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render(rows: Sequence[dict[str, object]], title: str) -> BytesIO:
    width, height = 1100, 620
    image = Image.new("RGB", (width, height), (18, 20, 24))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((25, 25, width - 25, height - 25), radius=28, fill=(34, 37, 43))
    draw.text((60, 55), title, font=_font(32, True), fill=(245, 246, 247))
    draw.text((60, 100), "Raspberry-Bot • system history", font=_font(18), fill=(170, 175, 185))
    chart = (75, 175, width - 75, 490)
    left, top, right, bottom = chart
    draw.rounded_rectangle(chart, radius=18, fill=(25, 28, 33))
    for index in range(5):
        y = top + (bottom - top) * index / 4
        draw.line((left + 20, y, right - 20, y), fill=(55, 59, 66), width=1)
    temps = [float(row["temperature"]) for row in rows if row.get("temperature") is not None]
    if len(temps) >= 2:
        minimum = min(min(temps), 30.0)
        maximum = max(max(temps), 80.0)
        span = max(maximum - minimum, 1.0)
        points = []
        for index, value in enumerate(temps):
            x = left + 25 + (right - left - 50) * index / max(len(temps) - 1, 1)
            y = bottom - 25 - (bottom - top - 50) * (value - minimum) / span
            points.append((x, y))
        draw.line(points, fill=(87, 242, 135), width=4, joint="curve")
        draw.text((75, 510), f"Temperature  avg {sum(temps)/len(temps):.1f} °C  •  max {max(temps):.1f} °C", font=_font(18, True), fill=(220, 225, 230))
    else:
        draw.text((360, 315), "Not enough history yet", font=_font(22, True), fill=(190, 195, 205))
    if rows:
        cpu = [float(row["cpu_percent"]) for row in rows]
        ram = [float(row["ram_percent"]) for row in rows]
        footer = f"CPU avg {sum(cpu)/len(cpu):.1f}%  •  RAM avg {sum(ram)/len(ram):.1f}%  •  Samples {len(rows)}"
        draw.text((75, 550), footer, font=_font(17), fill=(170, 175, 185))
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    image.close()
    output.seek(0)
    return output


async def render_system_history(rows: Sequence[dict[str, object]], title: str = "HOMEPI • 24H HEALTH") -> BytesIO:
    async with _render_limit:
        return await asyncio.to_thread(_render, rows, title)
