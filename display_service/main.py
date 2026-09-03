from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from PIL import ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306

GUILD_ID = int(os.getenv("DISPLAY_GUILD_ID", "1162733312226361454"))
REPO_ROOT = Path(os.getenv("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot"))
DATABASE_PATH = Path(
    os.getenv("DISPLAY_DATABASE_PATH")
    or os.getenv("BOT_DATABASE_PATH")
    or os.getenv("DATABASE_PATH")
    or (REPO_ROOT / "data" / "bot.sqlite3")
)
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = REPO_ROOT / DATABASE_PATH

PAGES = ("clock", "system", "performance", "network", "media")
DEFAULT_LAYOUT: dict[str, Any] = {
    "i2c_address": "0x3C",
    "rotation": 0,
    "brightness": 90,
    "refresh_seconds": 10,
    "page_seconds": 5,
    "show_labels": True,
    "show_footer": True,
    "media_priority": True,
    "alert_priority": True,
}

logging.basicConfig(
    level=os.getenv("DISPLAY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s oled-display: %(message)s",
)
log = logging.getLogger("oled-display")


def _font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


FONT_TINY = _font(7)
FONT_SMALL = _font(9)
FONT_MEDIUM = _font(12)
FONT_LARGE = _font(25)


@dataclass
class Snapshot:
    cpu: float = 0.0
    ram: float = 0.0
    temp: float | None = None
    uptime: int = 0
    network: bool = False
    pihole: bool = False
    media_title: str = "Nichts läuft"
    media_active: bool = False


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True, timeout=1.0)
    con.row_factory = sqlite3.Row
    return con


def load_layout() -> dict[str, Any]:
    layout = dict(DEFAULT_LAYOUT)
    try:
        with _connect() as con:
            row = con.execute(
                "SELECT layout_json FROM dashboard_display_layout WHERE guild_id=?",
                (GUILD_ID,),
            ).fetchone()
        if row:
            raw = json.loads(row["layout_json"] or "{}")
            if isinstance(raw, dict):
                layout.update(raw)
    except (sqlite3.Error, ValueError, TypeError) as exc:
        log.debug("Layout read failed: %s", exc)

    layout["i2c_address"] = str(layout.get("i2c_address", "0x3C"))
    if layout["i2c_address"] not in {"0x3C", "0x3D"}:
        layout["i2c_address"] = "0x3C"
    layout["rotation"] = 180 if int(layout.get("rotation", 0) or 0) == 180 else 0
    layout["brightness"] = max(10, min(100, int(layout.get("brightness", 90) or 90)))
    layout["refresh_seconds"] = max(5, min(120, int(layout.get("refresh_seconds", 10) or 10)))
    layout["page_seconds"] = max(2, min(30, int(layout.get("page_seconds", 5) or 5)))
    for key in ("show_labels", "show_footer", "media_priority", "alert_priority"):
        layout[key] = layout.get(key) is not False
    return layout


def load_runtime() -> dict[str, Any]:
    try:
        with _connect() as con:
            row = con.execute(
                "SELECT state_json FROM dashboard_runtime_state WHERE guild_id=?",
                (GUILD_ID,),
            ).fetchone()
        if row:
            raw = json.loads(row["state_json"] or "{}")
            return raw if isinstance(raw, dict) else {}
    except (sqlite3.Error, ValueError, TypeError) as exc:
        log.debug("Runtime read failed: %s", exc)
    return {}


def _temperature() -> float | None:
    for path in (Path("/sys/class/thermal/thermal_zone0/temp"),):
        try:
            return round(float(path.read_text().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            pass
    return None


def _pihole_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "pihole-FTL.service"],
            timeout=0.8,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _network_active() -> bool:
    try:
        return any(
            name != "lo" and stats.isup
            for name, stats in psutil.net_if_stats().items()
        )
    except Exception:
        return False


def build_snapshot() -> Snapshot:
    runtime = load_runtime()
    voice = runtime.get("voice") or {}
    youtube = runtime.get("youtube") or {}
    current = youtube.get("current") or {}
    title = current.get("title") or voice.get("title") or voice.get("source_name") or "Nichts läuft"
    active = bool(
        current.get("title")
        or voice.get("playing")
        or str(voice.get("kind") or "").lower() == "radio"
    )
    return Snapshot(
        cpu=round(float(psutil.cpu_percent(interval=None)), 1),
        ram=round(float(psutil.virtual_memory().percent), 1),
        temp=_temperature(),
        uptime=max(0, int(time.time() - psutil.boot_time())),
        network=_network_active(),
        pihole=_pihole_active(),
        media_title=str(title),
        media_active=active,
    )


def _truncate(draw, text: str, font, max_width: int) -> str:
    text = str(text)
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    suffix = "…"
    while text and draw.textbbox((0, 0), text + suffix, font=font)[2] > max_width:
        text = text[:-1]
    return (text + suffix) if text else suffix


def _uptime(seconds: int) -> str:
    days, rem = divmod(max(0, seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _split(draw, left_label: str, left_value: str, right_label: str, right_value: str, show_labels: bool) -> None:
    draw.line((63, 3, 63, 53), fill="white")
    if show_labels:
        draw.text((3, 3), left_label, font=FONT_TINY, fill="white")
        draw.text((67, 3), right_label, font=FONT_TINY, fill="white")
        y = 20
    else:
        y = 14
    left = _truncate(draw, left_value, FONT_MEDIUM, 56)
    right = _truncate(draw, right_value, FONT_MEDIUM, 57)
    lw = draw.textbbox((0, 0), left, font=FONT_MEDIUM)[2]
    rw = draw.textbbox((0, 0), right, font=FONT_MEDIUM)[2]
    draw.text((3 + max(0, (57 - lw) // 2), y), left, font=FONT_MEDIUM, fill="white")
    draw.text((67 + max(0, (57 - rw) // 2), y), right, font=FONT_MEDIUM, fill="white")


def render_page(device, page: str, snap: Snapshot, layout: dict[str, Any]) -> None:
    with canvas(device) as draw:
        if page == "clock":
            value = time.strftime("%H:%M")
            width = draw.textbbox((0, 0), value, font=FONT_LARGE)[2]
            draw.text(((128 - width) // 2, 8), value, font=FONT_LARGE, fill="white")
            date = time.strftime("%d.%m.%Y")
            dwidth = draw.textbbox((0, 0), date, font=FONT_SMALL)[2]
            draw.text(((128 - dwidth) // 2, 39), date, font=FONT_SMALL, fill="white")
        elif page == "system":
            _split(
                draw,
                "TEMP",
                "—" if snap.temp is None else f"{snap.temp:.0f}°",
                "RAM",
                f"{snap.ram:.0f}%",
                layout["show_labels"],
            )
        elif page == "performance":
            _split(draw, "CPU", f"{snap.cpu:.0f}%", "UP", _uptime(snap.uptime), layout["show_labels"])
        elif page == "network":
            _split(
                draw,
                "NET",
                "ON" if snap.network else "OFF",
                "PIHOLE",
                "ON" if snap.pihole else "OFF",
                layout["show_labels"],
            )
        elif page == "media":
            if layout["show_labels"]:
                draw.text((3, 3), "NOW PLAYING", font=FONT_TINY, fill="white")
                y = 17
            else:
                y = 8
            title = snap.media_title if snap.media_active else "Nichts läuft"
            words = title.split()
            lines: list[str] = []
            current = ""
            for word in words:
                candidate = (current + " " + word).strip()
                if draw.textbbox((0, 0), candidate, font=FONT_SMALL)[2] <= 122:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = word
                if len(lines) >= 3:
                    break
            if current and len(lines) < 3:
                lines.append(current)
            for line in lines[:3]:
                draw.text((3, y), _truncate(draw, line, FONT_SMALL, 122), font=FONT_SMALL, fill="white")
                y += 11

        if layout["show_footer"]:
            draw.line((0, 55, 127, 55), fill="white")
            draw.text((2, 57), page.upper()[:10], font=FONT_TINY, fill="white")
            draw.text((103, 57), "HP", font=FONT_TINY, fill="white")


class DisplayService:
    def __init__(self) -> None:
        self.layout = load_layout()
        self.device = None
        self.device_key: tuple[str, int] | None = None
        self.snapshot = Snapshot()
        self.page_index = 0
        self.last_page_switch = 0.0
        self.last_data_refresh = 0.0
        self.last_layout_refresh = 0.0
        self.last_media_active = False
        self.alert_cooldown_until = 0.0
        self.priority_page: str | None = None
        self.priority_until = 0.0

    def ensure_device(self) -> None:
        address = self.layout["i2c_address"]
        rotation = self.layout["rotation"]
        key = (address, rotation)
        if self.device is not None and key == self.device_key:
            try:
                self.device.contrast(round(255 * self.layout["brightness"] / 100))
            except Exception:
                pass
            return
        serial = i2c(port=1, address=int(address, 16))
        self.device = ssd1306(serial, width=128, height=64, rotate=2 if rotation == 180 else 0)
        self.device.contrast(round(255 * self.layout["brightness"] / 100))
        self.device_key = key
        log.info("OLED ready: 128x64 SSD1306 I2C %s rotation=%s", address, rotation)

    def maybe_refresh(self, now: float) -> None:
        if now - self.last_layout_refresh >= 5:
            previous = self.layout
            self.layout = load_layout()
            self.last_layout_refresh = now
            if self.layout != previous:
                log.info("Display configuration reloaded")
                self.ensure_device()
        if now - self.last_data_refresh >= self.layout["refresh_seconds"]:
            self.snapshot = build_snapshot()
            self.last_data_refresh = now
            if self.layout["media_priority"] and self.snapshot.media_active and not self.last_media_active:
                self.priority_page = "media"
                self.priority_until = now + self.layout["page_seconds"]
            self.last_media_active = self.snapshot.media_active

            alert = (
                self.layout["alert_priority"]
                and now >= self.alert_cooldown_until
                and ((self.snapshot.temp or 0) >= 70 or self.snapshot.cpu >= 85)
            )
            if alert:
                self.priority_page = "performance" if self.snapshot.cpu >= 85 else "system"
                self.priority_until = now + self.layout["page_seconds"]
                self.alert_cooldown_until = now + 60

    def current_page(self, now: float) -> str:
        if self.priority_page and now < self.priority_until:
            return self.priority_page
        if self.priority_page:
            self.priority_page = None
            self.last_page_switch = now
        if self.last_page_switch == 0:
            self.last_page_switch = now
        elif now - self.last_page_switch >= self.layout["page_seconds"]:
            self.page_index = (self.page_index + 1) % len(PAGES)
            self.last_page_switch = now
        return PAGES[self.page_index]

    def run(self) -> None:
        log.info("Starting OLED service; DB=%s guild=%s", DATABASE_PATH, GUILD_ID)
        psutil.cpu_percent(interval=None)
        while True:
            try:
                now = time.monotonic()
                self.maybe_refresh(now)
                self.ensure_device()
                render_page(self.device, self.current_page(now), self.snapshot, self.layout)
            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("OLED loop failed; retrying")
                time.sleep(3)
                continue
            time.sleep(1)
        try:
            if self.device:
                self.device.clear()
        except Exception:
            pass


def main() -> None:
    DisplayService().run()


if __name__ == "__main__":
    main()
