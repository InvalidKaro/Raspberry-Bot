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
ANIMATION_FPS = max(5.0, min(20.0, float(os.getenv("DISPLAY_ANIMATION_FPS", "12") or 12)))
ANIMATION_STYLE = (os.getenv("DISPLAY_ANIMATION_STYLE", "cycle").strip().lower() or "cycle")


def _text_width(draw: ImageDraw.ImageDraw, value: str, font) -> int:
    box = draw.textbbox((0, 0), str(value), font=font)
    return max(0, int(box[2] - box[0]))


def _center_text(draw: ImageDraw.ImageDraw, y: int, value: str, font) -> None:
    draw.text(((128 - _text_width(draw, value, font)) // 2, y), value, font=font, fill=255)


def _finish(image: Image.Image, layout: dict[str, Any]) -> Image.Image:
    if int(layout.get("rotation", 0) or 0) == 180:
        return image.rotate(180)
    return image


def _seed_unit(index: int, salt: int) -> float:
    """Cheap deterministic pseudo-random 0..1 value without mutable RNG state."""
    value = math.sin(index * 12.9898 + salt * 78.233) * 43758.5453
    return value - math.floor(value)


def _starfield_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """3D hyperspace starfield: stars fly out of a moving vanishing point."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    cx = 64 + int(math.sin(elapsed * 0.55) * 5)
    cy = 31 + int(math.cos(elapsed * 0.43) * 3)
    speed = 0.34

    for index in range(42):
        sx = (_seed_unit(index, 1) * 2.0 - 1.0) * 70.0
        sy = (_seed_unit(index, 2) * 2.0 - 1.0) * 39.0
        base_z = 0.12 + _seed_unit(index, 3) * 0.88
        z = ((base_z - elapsed * speed) % 1.0)
        z = max(0.055, z)
        prev_z = min(1.0, z + 0.075)

        x = int(cx + sx / z)
        y = int(cy + sy / z)
        px = int(cx + sx / prev_z)
        py = int(cy + sy / prev_z)

        if -8 <= x <= 135 and -8 <= y <= 71:
            if z < 0.35:
                draw.line((px, py, x, y), fill=255)
            elif 0 <= x < 128 and 0 <= y < 64:
                draw.point((x, y), fill=255)

    # Small center reticle gives the scene a distinct old-head-unit demo feel.
    pulse = 2 + int((math.sin(elapsed * 4.0) + 1.0) * 1.5)
    draw.rectangle((cx - pulse, cy - pulse, cx + pulse, cy + pulse), outline=255)
    draw.point((cx, cy), fill=255)
    draw.text((2, 2), "HOMEPI // WARP", font=_core.FONT_TINY, fill=255)
    return _finish(image, layout)


def _road_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Scrolling night road with perspective lane markers and a tiny pixel car."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    horizon = 18
    center = 64 + int(math.sin(elapsed * 0.65) * 5)

    # Stars and a simple moving mountain silhouette.
    for index in range(18):
        x = int((_seed_unit(index, 11) * 128 + elapsed * (2 + index % 3)) % 128)
        y = 2 + int(_seed_unit(index, 12) * 13)
        draw.point((x, y), fill=255)
    mountain_points = [(0, horizon)]
    for x in range(0, 129, 8):
        y = horizon - 2 - int((math.sin(x * 0.13 + elapsed * 0.35) + 1.0) * 3)
        mountain_points.append((x, y))
    mountain_points.append((127, horizon))
    draw.line(mountain_points, fill=255)
    draw.line((0, horizon, 127, horizon), fill=255)

    # Road edges.
    draw.line((center - 7, horizon, 10, 63), fill=255)
    draw.line((center + 7, horizon, 118, 63), fill=255)

    # Perspective center lane dashes moving toward the viewer.
    phase = (elapsed * 0.75) % 1.0
    for index in range(8):
        z = (index + phase) / 8.0
        z2 = min(1.0, z + 0.075)
        y1 = horizon + int((z * z) * (63 - horizon))
        y2 = horizon + int((z2 * z2) * (63 - horizon))
        x1 = center + int(math.sin(elapsed * 0.65) * z * 3)
        x2 = center + int(math.sin(elapsed * 0.65) * z2 * 3)
        width = max(1, int(1 + z * 2))
        draw.line((x1, y1, x2, y2), fill=255, width=width)

    # Roadside reflector posts.
    for side in (-1, 1):
        for index in range(5):
            z = ((index / 5.0) + phase) % 1.0
            y = horizon + int((z * z) * (61 - horizon))
            road_half = 7 + int(z * 48)
            x = center + side * road_half
            h = max(2, int(2 + z * 6))
            draw.line((x, y - h, x, y), fill=255)
            draw.point((x - side, y - h), fill=255)

    # Tiny rear-view pixel car, gently steering through the bends.
    car_x = center - 8 + int(math.sin(elapsed * 1.15) * 7)
    car_y = 48
    draw.rectangle((car_x + 3, car_y, car_x + 12, car_y + 2), fill=255)
    draw.rectangle((car_x, car_y + 3, car_x + 15, car_y + 8), outline=255)
    draw.rectangle((car_x + 3, car_y + 3, car_x + 6, car_y + 5), fill=255)
    draw.rectangle((car_x + 9, car_y + 3, car_x + 12, car_y + 5), fill=255)
    draw.rectangle((car_x + 1, car_y + 8, car_x + 4, car_y + 10), fill=255)
    draw.rectangle((car_x + 11, car_y + 8, car_x + 14, car_y + 10), fill=255)

    draw.text((2, 2), "NIGHT DRIVE", font=_core.FONT_TINY, fill=255)
    return _finish(image, layout)


def _orbit_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Planet, ring and orbiting satellite screensaver."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    for index in range(24):
        x = int(_seed_unit(index, 21) * 127)
        y = int(_seed_unit(index, 22) * 63)
        if (index + int(elapsed * 2)) % 4:
            draw.point((x, y), fill=255)

    planet_x = 64
    planet_y = 33
    radius = 16
    draw.ellipse((planet_x - radius, planet_y - radius, planet_x + radius, planet_y + radius), outline=255)

    # Planet bands slowly slide to make the sphere feel alive.
    for offset in (-8, -3, 4, 9):
        y = planet_y + offset + int(math.sin(elapsed * 0.7 + offset) * 1.5)
        span = int(math.sqrt(max(0, radius * radius - (y - planet_y) ** 2)))
        if span > 2:
            draw.line((planet_x - span + 2, y, planet_x + span - 2, y), fill=255)

    # Ring passes behind and in front of the planet.
    ring_box = (planet_x - 28, planet_y - 8, planet_x + 28, planet_y + 8)
    draw.arc(ring_box, 185, 355, fill=255)
    draw.arc(ring_box, 5, 175, fill=255)

    angle = elapsed * 1.15
    moon_x = planet_x + int(math.cos(angle) * 37)
    moon_y = planet_y + int(math.sin(angle) * 15)
    draw.ellipse((moon_x - 2, moon_y - 2, moon_x + 2, moon_y + 2), fill=255)

    # Little satellite on a second orbit.
    sat_angle = -elapsed * 1.8 + 1.2
    sat_x = planet_x + int(math.cos(sat_angle) * 46)
    sat_y = planet_y + int(math.sin(sat_angle) * 22)
    draw.rectangle((sat_x - 2, sat_y - 1, sat_x + 2, sat_y + 1), fill=255)
    draw.line((sat_x - 5, sat_y, sat_x - 3, sat_y), fill=255)
    draw.line((sat_x + 3, sat_y, sat_x + 5, sat_y), fill=255)

    draw.text((2, 2), "ORBIT // HOMEPI", font=_core.FONT_TINY, fill=255)
    return _finish(image, layout)


def _rotate_point(x: float, y: float, z: float, ax: float, ay: float) -> tuple[float, float, float]:
    cos_y = math.cos(ay)
    sin_y = math.sin(ay)
    x1 = x * cos_y + z * sin_y
    z1 = -x * sin_y + z * cos_y

    cos_x = math.cos(ax)
    sin_x = math.sin(ax)
    y2 = y * cos_x - z1 * sin_x
    z2 = y * sin_x + z1 * cos_x
    return x1, y2, z2


def _cube_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Smooth rotating wireframe cube with an HP core."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    vertices = [
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    ]
    edges = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )

    projected: list[tuple[int, int]] = []
    ax = elapsed * 0.72
    ay = elapsed * 1.02
    for x, y, z in vertices:
        rx, ry, rz = _rotate_point(x, y, z, ax, ay)
        depth = 4.2 + rz
        scale = 58.0 / depth
        px = int(64 + rx * scale)
        py = int(31 + ry * scale)
        projected.append((px, py))

    for a, b in edges:
        draw.line((*projected[a], *projected[b]), fill=255)

    # Pulsing core inside the cube.
    pulse = 5 + int((math.sin(elapsed * 3.0) + 1.0) * 2)
    draw.rounded_rectangle((64 - pulse, 31 - pulse, 64 + pulse, 31 + pulse), radius=2, outline=255)
    _center_text(draw, 27, "HP", _core.FONT_TINY)

    for index in range(12):
        x = int((_seed_unit(index, 31) * 128 + elapsed * (1 + index % 2)) % 128)
        y = int(_seed_unit(index, 32) * 64)
        draw.point((x, y), fill=255)

    draw.text((2, 2), "3D CORE", font=_core.FONT_TINY, fill=255)
    return _finish(image, layout)


def _city_frame(elapsed: float, layout: dict[str, Any]) -> Image.Image:
    """Side-scrolling night city with parallax buildings and a blinking skyline."""
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    # Moon.
    moon_x = 104
    moon_y = 10
    draw.ellipse((moon_x - 5, moon_y - 5, moon_x + 5, moon_y + 5), outline=255)
    draw.ellipse((moon_x - 2, moon_y - 5, moon_x + 6, moon_y + 3), fill=0)

    # Far stars.
    for index in range(14):
        x = int((_seed_unit(index, 41) * 128 - elapsed * 3.0) % 128)
        y = 2 + int(_seed_unit(index, 42) * 20)
        if (index + int(elapsed * 3)) % 3:
            draw.point((x, y), fill=255)

    # Deterministic scrolling buildings.
    scroll = int(elapsed * 14) % 180
    x = -scroll
    building_index = 0
    while x < 150:
        width = 10 + int(_seed_unit(building_index, 43) * 10)
        height = 16 + int(_seed_unit(building_index, 44) * 26)
        left = x
        top = 63 - height
        right = x + width
        draw.rectangle((left, top, right, 63), outline=255)

        for wx in range(left + 3, right - 2, 5):
            for wy in range(top + 4, 59, 7):
                lit = int((wx * 3 + wy * 5 + building_index + int(elapsed * 2))) % 4 == 0
                if lit:
                    draw.point((wx, wy), fill=255)
                    if wy + 1 < 63:
                        draw.point((wx, wy + 1), fill=255)

        # Rooftop antenna on some buildings.
        if building_index % 3 == 0:
            mid = (left + right) // 2
            draw.line((mid, top - 5, mid, top), fill=255)
            if int(elapsed * 4 + building_index) % 2 == 0:
                draw.point((mid, top - 6), fill=255)

        x += width + 4
        building_index += 1

    draw.text((2, 2), "HOMEPI CITY", font=_core.FONT_TINY, fill=255)
    return _finish(image, layout)


_SCENES = (
    ("starfield", _starfield_frame),
    ("road", _road_frame),
    ("orbit", _orbit_frame),
    ("cube", _cube_frame),
    ("city", _city_frame),
)


def render_animation_frame(elapsed: float, duration: float, layout: dict[str, Any], *, sequence: int = 0) -> Image.Image:
    del duration
    style = ANIMATION_STYLE

    # "mix" is kept as a backwards-compatible alias for the new scene cycle.
    if style in {"cycle", "mix", "random"}:
        _, renderer = _SCENES[sequence % len(_SCENES)]
        return renderer(elapsed, layout)

    for name, renderer in _SCENES:
        if style == name:
            return renderer(elapsed, layout)

    # Unknown style: fail soft and rotate through the proper screensavers.
    _, renderer = _SCENES[sequence % len(_SCENES)]
    return renderer(elapsed, layout)


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
        "Starting display service; DB=%s guild=%s hardware_optional=%s animation=%s/%ss@%sfps style=%s",
        _core.DATABASE_PATH,
        _core.GUILD_ID,
        _core.ALLOW_MISSING_HARDWARE,
        "on" if ANIMATION_ENABLED else "off",
        int(ANIMATION_DURATION_SECONDS),
        int(ANIMATION_FPS),
        ANIMATION_STYLE,
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
                _play_animation(self, animation_sequence)
                animation_sequence += 1
                last_animation_at = time.monotonic()
                # The screensaver owns all 128x64 pixels for the complete duration.
                # Afterwards the normal carousel starts cleanly at HOME again.
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
