from __future__ import annotations

import math
import os
import time
from typing import Any

import psutil
from PIL import Image, ImageDraw

from display_service import main_base as _core


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


ANIMATION_ENABLED = _env_bool("DISPLAY_ANIMATION_ENABLED", True)
ANIMATION_INTERVAL_SECONDS = max(30.0, float(os.getenv("DISPLAY_ANIMATION_INTERVAL_SECONDS", "60") or 60))
ANIMATION_DURATION_SECONDS = max(5.0, min(15.0, float(os.getenv("DISPLAY_ANIMATION_DURATION_SECONDS", "10") or 10)))
ANIMATION_FPS = max(5.0, min(20.0, float(os.getenv("DISPLAY_ANIMATION_FPS", "10") or 10)))
ANIMATION_STYLE = (os.getenv("DISPLAY_ANIMATION_STYLE", "mix").strip().lower() or "mix")

_ORIGINAL_RUN = _core.DisplayService.run


def _text_width(draw: ImageDraw.ImageDraw, value: str, font) -> int:
    box = draw.textbbox((0, 0), str(value), font=font)
    return max(0, int(box[2] - box[0]))


def _center_text(draw: ImageDraw.ImageDraw, y: int, value: str, font) -> None:
    draw.text(((128 - _text_width(draw, value, font)) // 2, y), value, font=font, fill=255)


def _finish(image: Image.Image, layout: dict[str, Any]) -> Image.Image:
    if int(layout.get("rotation", 0) or 0) == 180:
        return image.rotate(180)
    return image


def _spectrum_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Retro car-head-unit spectrum analyser with animated peak markers."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    draw.text((2, 1), "HOMEPI", font=_core.FONT_TINY, fill=255)
    right = "STEREO" if int(elapsed * 2) % 2 == 0 else "SPECTRUM"
    draw.text((126 - _text_width(draw, right, _core.FONT_TINY), 1), right, font=_core.FONT_TINY, fill=255)
    draw.line((0, 10, 127, 10), fill=255)

    bar_count = 16
    bar_width = 5
    gap = 3
    x0 = 4
    baseline = 58
    max_height = 43

    for index in range(bar_count):
        # Several overlapping waves make the bars feel music-driven while staying deterministic.
        wave = (
            math.sin(elapsed * 4.2 + index * 0.72) * 0.34
            + math.sin(elapsed * 2.1 - index * 1.17) * 0.23
            + math.sin(elapsed * 7.4 + index * 0.29) * 0.13
        )
        envelope = 0.52 + 0.28 * math.sin(elapsed * 0.83 + index * 0.16)
        level = max(0.08, min(1.0, envelope + wave))
        height = max(3, int(max_height * level))
        x = x0 + index * (bar_width + gap)
        top = baseline - height

        # Segmented blocks look closer to classic head-unit EQ displays than solid bars.
        y = baseline
        while y - 3 >= top:
            draw.rectangle((x, y - 2, x + bar_width - 1, y), fill=255)
            y -= 5

        peak_y = max(13, top - 3 - int(2 * (1 + math.sin(elapsed * 5.0 + index))))
        draw.line((x, peak_y, x + bar_width - 1, peak_y), fill=255)

    return _finish(image, layout)


def _wave_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Oscilloscope / waveform scene with a moving scanner highlight."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    draw.text((2, 1), "SIGNAL", font=_core.FONT_TINY, fill=255)
    draw.text((99, 1), "L  R", font=_core.FONT_TINY, fill=255)
    draw.line((0, 10, 127, 10), fill=255)
    draw.line((0, 32, 127, 32), fill=255)

    points: list[tuple[int, int]] = []
    for x in range(128):
        phase = x / 128.0 * math.tau
        amplitude = 12.0 + 7.0 * math.sin(elapsed * 1.35)
        y = 32 + int(
            math.sin(phase * 2.0 + elapsed * 5.0) * amplitude * 0.62
            + math.sin(phase * 5.0 - elapsed * 3.1) * 4.0
        )
        points.append((x, max(13, min(58, y))))
    draw.line(points, fill=255, width=1)

    scan_x = int((elapsed * 42.0) % 144) - 8
    if -2 <= scan_x <= 129:
        draw.line((scan_x, 12, scan_x, 60), fill=255)
        if scan_x + 2 <= 127:
            draw.point((scan_x + 2, 16), fill=255)
            draw.point((scan_x + 2, 48), fill=255)

    draw.text((2, 55), "AUTO LEVEL", font=_core.FONT_TINY, fill=255)
    return _finish(image, layout)


def _tunnel_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Perspective tunnel inspired by the playful demo animations of older radios."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    cx = 64 + int(math.sin(elapsed * 0.9) * 8)
    cy = 32 + int(math.cos(elapsed * 1.1) * 4)
    phase = (elapsed * 0.72) % 1.0

    for ring in range(8):
        z = (ring + phase) / 8.0
        scale = z * z
        half_w = 4 + int(scale * 61)
        half_h = 2 + int(scale * 29)
        x1 = max(0, cx - half_w)
        y1 = max(0, cy - half_h)
        x2 = min(127, cx + half_w)
        y2 = min(63, cy + half_h)
        draw.rectangle((x1, y1, x2, y2), outline=255)

    # Vanishing-point rays add motion without needing many pixels.
    for angle in (0.0, 0.25, 0.5, 0.75):
        theta = angle * math.tau + elapsed * 0.17
        ex = cx + int(math.cos(theta) * 78)
        ey = cy + int(math.sin(theta) * 42)
        draw.line((cx, cy, ex, ey), fill=255)

    _center_text(draw, 27, "HOMEPI", _core.FONT_SMALL)
    return _finish(image, layout)


def _scanner_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Bouncing segmented scanner / level meter."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    _center_text(draw, 2, "DIGITAL SOUND PROCESSOR", _core.FONT_TINY)
    draw.line((0, 11, 127, 11), fill=255)

    segment_count = 20
    active = int((math.sin(elapsed * 2.4) * 0.5 + 0.5) * (segment_count - 1))
    for idx in range(segment_count):
        x = 4 + idx * 6
        y = 25 + int(math.sin(elapsed * 3.0 + idx * 0.55) * 8)
        if abs(idx - active) <= 2:
            draw.rectangle((x, y - 4, x + 3, y + 4), fill=255)
        else:
            draw.rectangle((x, y - 2, x + 3, y + 2), outline=255)

    left_level = int((math.sin(elapsed * 4.5) * 0.5 + 0.5) * 48)
    right_level = int((math.sin(elapsed * 4.1 + 1.7) * 0.5 + 0.5) * 48)
    draw.text((2, 48), "L", font=_core.FONT_TINY, fill=255)
    draw.rectangle((10, 49, 58, 54), outline=255)
    if left_level:
        draw.rectangle((12, 51, 11 + left_level, 52), fill=255)
    draw.text((66, 48), "R", font=_core.FONT_TINY, fill=255)
    draw.rectangle((74, 49, 122, 54), outline=255)
    if right_level:
        draw.rectangle((76, 51, 75 + right_level, 52), fill=255)

    _center_text(draw, 57, "DSP  ·  LOUD  ·  STEREO", _core.FONT_TINY)
    return _finish(image, layout)


def render_animation_frame(elapsed: float, duration: float, layout: dict[str, Any], *, sequence: int = 0) -> Image.Image:
    style = ANIMATION_STYLE
    if style == "spectrum":
        return _spectrum_frame(elapsed, layout)
    if style == "wave":
        return _wave_frame(elapsed, layout)
    if style == "tunnel":
        return _tunnel_frame(elapsed, layout)
    if style == "scanner":
        return _scanner_frame(elapsed, layout)

    # Default "mix": one 5-15 second mini demo, similar to the playful visualizer
    # modes found on older head units. The starting scene rotates each time.
    scene_count = 4
    scene_length = max(1.0, duration / scene_count)
    scene_index = (int(elapsed / scene_length) + sequence) % scene_count
    local_elapsed = elapsed + sequence * 0.37
    if scene_index == 0:
        return _spectrum_frame(local_elapsed, layout)
    if scene_index == 1:
        return _scanner_frame(local_elapsed, layout)
    if scene_index == 2:
        return _wave_frame(local_elapsed, layout)
    return _tunnel_frame(local_elapsed, layout)


def _play_animation(service: _core.DisplayService, sequence: int) -> None:
    start = time.monotonic()
    next_frame = start
    retry_at = start
    service.current_page_name = "animation"
    service.write_status(start, force=True)

    while True:
        now = time.monotonic()
        elapsed = now - start
        if elapsed >= ANIMATION_DURATION_SECONDS:
            break

        if now >= retry_at:
            service.hardware.ensure(service.layout)
            retry_at = now + 1.0

        image = render_animation_frame(
            elapsed,
            ANIMATION_DURATION_SECONDS,
            service.layout,
            sequence=sequence,
        )
        service.hardware.display(image)
        service.write_preview(image, now)

        next_frame += 1.0 / ANIMATION_FPS
        delay = next_frame - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_frame = time.monotonic()


def _run_with_retro_animation(self: _core.DisplayService) -> None:
    _core.log.info(
        "Starting display service; DB=%s guild=%s hardware_optional=%s animation=%s/%ss@%sfps",
        _core.DATABASE_PATH,
        _core.GUILD_ID,
        _core.ALLOW_MISSING_HARDWARE,
        "on" if ANIMATION_ENABLED else "off",
        int(ANIMATION_DURATION_SECONDS),
        int(ANIMATION_FPS),
    )
    psutil.cpu_percent(interval=None)
    self.snapshot = _core.build_snapshot()
    self.hardware.ensure(self.layout, force=True)

    if not self.hardware.connected and not _core.ALLOW_MISSING_HARDWARE:
        raise RuntimeError(f"OLED required but unavailable: {self.hardware.last_error}")

    last_animation_at = time.monotonic()
    animation_sequence = 0

    while True:
        try:
            now = time.monotonic()
            self.maybe_refresh(now)
            self.hardware.ensure(self.layout)

            previous_index = self.page_index
            self.current_page_name = self.current_page(now)
            wrapped_cycle = (
                previous_index == len(_core.PAGES) - 1
                and self.page_index == 0
                and self.current_page_name == _core.PAGES[0]
            )

            if (
                ANIMATION_ENABLED
                and wrapped_cycle
                and now - last_animation_at >= ANIMATION_INTERVAL_SECONDS
                and (self.hardware.connected or _core.WRITE_HEADLESS_PREVIEW)
            ):
                animation_sequence += 1
                _play_animation(self, animation_sequence)
                last_animation_at = time.monotonic()
                # The animation owns the screen for its full duration. Afterwards the
                # normal carousel restarts cleanly on HOME instead of instantly skipping.
                self.page_index = 0
                self.last_page_switch = last_animation_at
                self.priority_page = None
                self.current_page_name = _core.PAGES[0]
                self.write_status(last_animation_at, force=True)
                continue

            image = _core.render_image(self.current_page_name, self.snapshot, self.layout)
            self.hardware.display(image)
            self.write_preview(image, now)
            self.write_status(now)
        except KeyboardInterrupt:
            break
        except Exception:
            _core.log.exception("Display loop failed; continuing")
            time.sleep(2)
            continue
        time.sleep(1)

    self.hardware.clear()
    self.write_status(time.monotonic(), force=True)


def install() -> None:
    if getattr(_core, "_retro_oled_animation_installed", False):
        return
    _core.DisplayService.run = _run_with_retro_animation
    _core._retro_oled_animation_installed = True
