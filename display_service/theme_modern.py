from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw

from display_service import main_base as _base

_ORIGINAL_RENDER_IMAGE = _base.render_image
_PAGE_LABELS = {
    "clock": "HOME",
    "system": "SYSTEM",
    "performance": "PERF",
    "network": "NET",
    "media": "MEDIA",
}
_WEEKDAYS = ("MO", "DI", "MI", "DO", "FR", "SA", "SO")
_TIMEZONE_NAME = os.getenv("DISPLAY_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin"
try:
    _DISPLAY_TIMEZONE = ZoneInfo(_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    _DISPLAY_TIMEZONE = ZoneInfo("UTC")


def _now() -> datetime:
    return datetime.now(_DISPLAY_TIMEZONE)


def _text_width(draw: ImageDraw.ImageDraw, value: str, font) -> int:
    box = draw.textbbox((0, 0), str(value), font=font)
    return max(0, int(box[2] - box[0]))


def _right_text(draw: ImageDraw.ImageDraw, y: int, value: str, font, *, right: int = 126) -> None:
    draw.text((right - _text_width(draw, value, font), y), value, font=font, fill=255)


def _header(draw: ImageDraw.ImageDraw, title: str, status: str = "") -> None:
    draw.text((2, 1), title.upper(), font=_base.FONT_TINY, fill=255)
    if status:
        _right_text(draw, 1, status.upper(), _base.FONT_TINY)
    draw.line((0, 11, 127, 11), fill=255)


def _bar(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, value: float, *, height: int = 5) -> None:
    value = max(0.0, min(100.0, float(value)))
    x2 = x + max(4, width) - 1
    y2 = y + max(3, height) - 1
    draw.rectangle((x, y, x2, y2), outline=255)
    inner = max(0, width - 4)
    fill_width = round(inner * value / 100.0)
    if fill_width > 0:
        draw.rectangle((x + 2, y + 2, x + 1 + fill_width, y2 - 2 if height >= 6 else y2 - 1), fill=255)


def _status_box(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, enabled: bool) -> None:
    """High-contrast status box that stays readable on small blue SSD1306 panels."""
    height = 19
    draw.rounded_rectangle((x, y, x + width - 1, y + height - 1), radius=3, outline=255)
    label = "ON" if enabled else "OFF"
    font = _base.FONT_MEDIUM
    text_width = _text_width(draw, label, font)
    draw.text((x + max(2, (width - text_width) // 2), y + 2), label, font=font, fill=255)
    if enabled:
        draw.rectangle((x + 3, y + height - 4, x + width - 4, y + height - 3), fill=255)


def _footer(draw: ImageDraw.ImageDraw, page: str, layout: dict[str, Any]) -> None:
    if not layout.get("show_footer", True):
        return
    draw.line((0, 54, 127, 54), fill=255)
    draw.text((2, 56), _PAGE_LABELS.get(page, page.upper())[:7], font=_base.FONT_TINY, fill=255)

    pages = tuple(_base.PAGES)
    try:
        active = pages.index(page)
    except ValueError:
        active = 0
    start_x = 52
    for index in range(len(pages)):
        x = start_x + index * 7
        if index == active:
            draw.ellipse((x, 58, x + 3, 61), fill=255)
        else:
            draw.ellipse((x, 58, x + 3, 61), outline=255)

    draw.text((111, 56), "HP", font=_base.FONT_TINY, fill=255)


def _finish(image: Image.Image, page: str, layout: dict[str, Any]) -> Image.Image:
    draw = ImageDraw.Draw(image)
    _footer(draw, page, layout)
    if int(layout.get("rotation", 0) or 0) == 180:
        return image.rotate(180)
    return image


def _render_clock(snap: _base.Snapshot, layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    now = _now()

    draw.text((2, 1), "HOMEPI", font=_base.FONT_TINY, fill=255)
    _right_text(draw, 1, "NET ON" if snap.network else "NET OFF", _base.FONT_TINY)

    value = now.strftime("%H:%M")
    width = _text_width(draw, value, _base.FONT_LARGE)
    draw.text(((128 - width) // 2, 10), value, font=_base.FONT_LARGE, fill=255)

    weekday = _WEEKDAYS[now.weekday()]
    subtitle = f"{weekday}  {now.strftime('%d.%m.%Y')}"
    sw = _text_width(draw, subtitle, _base.FONT_SMALL)
    draw.text(((128 - sw) // 2, 38), subtitle, font=_base.FONT_SMALL, fill=255)

    temp = "--C" if snap.temp is None else f"{snap.temp:.0f}C"
    draw.text((2, 47), temp, font=_base.FONT_TINY, fill=255)
    _right_text(draw, 47, f"RAM {snap.ram:.0f}%", _base.FONT_TINY)
    return _finish(image, "clock", layout)


def _render_system(snap: _base.Snapshot, layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "SYSTEM", "HEALTH")

    draw.text((3, 15), "TEMP", font=_base.FONT_TINY, fill=255)
    temp = "--" if snap.temp is None else f"{snap.temp:.0f}C"
    temp_width = _text_width(draw, temp, _base.FONT_MEDIUM)
    draw.text((3 + max(0, (55 - temp_width) // 2), 26), temp, font=_base.FONT_MEDIUM, fill=255)
    draw.text((3, 43), "CPU TEMP", font=_base.FONT_TINY, fill=255)

    draw.line((62, 15, 62, 49), fill=255)

    draw.text((68, 15), "RAM", font=_base.FONT_TINY, fill=255)
    _right_text(draw, 23, f"{snap.ram:.0f}%", _base.FONT_MEDIUM, right=124)
    _bar(draw, 68, 41, 56, snap.ram, height=6)

    return _finish(image, "system", layout)


def _render_performance(snap: _base.Snapshot, layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "PERFORMANCE", "LIVE")

    draw.text((3, 15), "CPU", font=_base.FONT_TINY, fill=255)
    draw.text((3, 23), f"{snap.cpu:.0f}%", font=_base.FONT_MEDIUM, fill=255)
    _bar(draw, 3, 41, 58, snap.cpu, height=6)

    draw.line((64, 15, 64, 49), fill=255)
    draw.text((70, 15), "UPTIME", font=_base.FONT_TINY, fill=255)
    uptime = _base._uptime(snap.uptime)
    _right_text(draw, 25, uptime, _base.FONT_SMALL, right=124)
    draw.text((70, 42), "RUNNING", font=_base.FONT_TINY, fill=255)

    return _finish(image, "performance", layout)


def _render_network(snap: _base.Snapshot, layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "NETWORK", "HOMEPI")

    draw.text((3, 14), "NETWORK", font=_base.FONT_TINY, fill=255)
    _status_box(draw, 3, 23, 54, snap.network)

    draw.text((70, 14), "PI-HOLE", font=_base.FONT_TINY, fill=255)
    _status_box(draw, 70, 23, 54, snap.pihole)

    status = "ALL SERVICES OK" if snap.network and snap.pihole else "CHECK SERVICES"
    status_width = _text_width(draw, status, _base.FONT_TINY)
    draw.text((max(2, (128 - status_width) // 2), 45), status, font=_base.FONT_TINY, fill=255)
    return _finish(image, "network", layout)


def _wrap(draw: ImageDraw.ImageDraw, value: str, max_width: int, max_lines: int) -> list[str]:
    words = " ".join(str(value or "").split()).split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_width(draw, candidate, _base.FONT_SMALL) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return [_base._truncate(draw, line, _base.FONT_SMALL, max_width) for line in lines[:max_lines]]


def _render_media(snap: _base.Snapshot, layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "NOW PLAYING", "PLAY" if snap.media_active else "IDLE")

    if snap.media_active:
        lines = _wrap(draw, snap.media_title, 122, 3)
        y = 17
        for index, line in enumerate(lines):
            font = _base.FONT_SMALL if index else _base.FONT_MEDIUM
            line = _base._truncate(draw, line, font, 122)
            draw.text((3, y), line, font=font, fill=255)
            y += 13 if index == 0 else 10
    else:
        draw.text((3, 21), "Nichts laeuft", font=_base.FONT_MEDIUM, fill=255)
        draw.text((3, 39), "Radio / Spotify / YouTube", font=_base.FONT_TINY, fill=255)

    return _finish(image, "media", layout)


def render_image(page: str, snap: _base.Snapshot, layout: dict[str, Any]) -> Image.Image:
    if page == "clock":
        return _render_clock(snap, layout)
    if page == "system":
        return _render_system(snap, layout)
    if page == "performance":
        return _render_performance(snap, layout)
    if page == "network":
        return _render_network(snap, layout)
    if page == "media":
        return _render_media(snap, layout)
    return _ORIGINAL_RENDER_IMAGE(page, snap, layout)


def install() -> None:
    """Install the modern renderer before display_service.main adds media-specific pages."""
    if getattr(_base, "_modern_oled_theme_installed", False):
        return
    _base.render_image = render_image
    _base._modern_oled_theme_installed = True
