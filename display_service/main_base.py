from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from PIL import Image, ImageDraw, ImageFont

try:
    from luma.core.interface.serial import i2c as luma_i2c
    from luma.oled.device import ssd1306
except Exception as exc:  # hardware stack is optional until the OLED is installed
    luma_i2c = None
    ssd1306 = None
    LUMA_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    LUMA_IMPORT_ERROR = ""


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

STATUS_PATH = Path(os.getenv("DISPLAY_STATUS_PATH", str(REPO_ROOT / "data" / "display_status.json")))
PREVIEW_PATH = Path(os.getenv("DISPLAY_PREVIEW_PATH", str(REPO_ROOT / "data" / "display_preview.png")))
HARDWARE_RETRY_SECONDS = max(5, int(os.getenv("DISPLAY_HARDWARE_RETRY_SECONDS", "20")))
ALLOW_MISSING_HARDWARE = os.getenv("DISPLAY_ALLOW_MISSING_HARDWARE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
WRITE_HEADLESS_PREVIEW = os.getenv("DISPLAY_HEADLESS_PREVIEW", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

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
    format="%(asctime)s %(levelname)s homepi-display: %(message)s",
)
log = logging.getLogger("homepi-display")


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


@dataclass(slots=True)
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
    try:
        return round(float(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000.0, 1)
    except (OSError, ValueError):
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
        return any(name != "lo" and stats.isup for name, stats in psutil.net_if_stats().items())
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


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
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


def _split(
    draw: ImageDraw.ImageDraw,
    left_label: str,
    left_value: str,
    right_label: str,
    right_value: str,
    show_labels: bool,
) -> None:
    draw.line((63, 3, 63, 53), fill=255)
    if show_labels:
        draw.text((3, 3), left_label, font=FONT_TINY, fill=255)
        draw.text((67, 3), right_label, font=FONT_TINY, fill=255)
        y = 20
    else:
        y = 14
    left = _truncate(draw, left_value, FONT_MEDIUM, 56)
    right = _truncate(draw, right_value, FONT_MEDIUM, 57)
    lw = draw.textbbox((0, 0), left, font=FONT_MEDIUM)[2]
    rw = draw.textbbox((0, 0), right, font=FONT_MEDIUM)[2]
    draw.text((3 + max(0, (57 - lw) // 2), y), left, font=FONT_MEDIUM, fill=255)
    draw.text((67 + max(0, (57 - rw) // 2), y), right, font=FONT_MEDIUM, fill=255)


def render_image(page: str, snap: Snapshot, layout: dict[str, Any]) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    if page == "clock":
        value = time.strftime("%H:%M")
        width = draw.textbbox((0, 0), value, font=FONT_LARGE)[2]
        draw.text(((128 - width) // 2, 8), value, font=FONT_LARGE, fill=255)
        date = time.strftime("%d.%m.%Y")
        dwidth = draw.textbbox((0, 0), date, font=FONT_SMALL)[2]
        draw.text(((128 - dwidth) // 2, 39), date, font=FONT_SMALL, fill=255)
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
            draw.text((3, 3), "NOW PLAYING", font=FONT_TINY, fill=255)
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
            draw.text((3, y), _truncate(draw, line, FONT_SMALL, 122), font=FONT_SMALL, fill=255)
            y += 11

    if layout["show_footer"]:
        draw.line((0, 55, 127, 55), fill=255)
        draw.text((2, 57), page.upper()[:10], font=FONT_TINY, fill=255)
        draw.text((103, 57), "HP", font=FONT_TINY, fill=255)

    if layout["rotation"] == 180:
        image = image.rotate(180)
    return image


class HardwareController:
    def __init__(self) -> None:
        self.device = None
        self.key: tuple[str, int] | None = None
        self.last_error = ""
        self.next_retry_at = 0.0
        self._last_logged_error = ""

    @property
    def connected(self) -> bool:
        return self.device is not None

    def disconnect(self, error: str = "") -> None:
        self.device = None
        self.key = None
        self.last_error = error
        self.next_retry_at = time.monotonic() + HARDWARE_RETRY_SECONDS

    def ensure(self, layout: dict[str, Any], *, force: bool = False) -> bool:
        key = (layout["i2c_address"], layout["rotation"])
        if self.device is not None and self.key == key:
            try:
                self.device.contrast(round(255 * layout["brightness"] / 100))
            except Exception:
                pass
            return True

        if self.device is not None and self.key != key:
            self.disconnect("Display configuration changed")

        now = time.monotonic()
        if not force and now < self.next_retry_at:
            return False

        if luma_i2c is None or ssd1306 is None:
            error = f"luma.oled unavailable: {LUMA_IMPORT_ERROR or 'not installed'}"
            self._standby(error)
            return False

        try:
            serial = luma_i2c(port=1, address=int(layout["i2c_address"], 16))
            device = ssd1306(serial, width=128, height=64, rotate=0)
            device.contrast(round(255 * layout["brightness"] / 100))
            self.device = device
            self.key = key
            self.last_error = ""
            self._last_logged_error = ""
            log.info(
                "OLED connected: SSD1306 128x64 I2C %s rotation=%s",
                layout["i2c_address"],
                layout["rotation"],
            )
            return True
        except Exception as exc:
            self._standby(f"{type(exc).__name__}: {exc}")
            return False

    def _standby(self, error: str) -> None:
        self.device = None
        self.key = None
        self.last_error = error
        self.next_retry_at = time.monotonic() + HARDWARE_RETRY_SECONDS
        if error != self._last_logged_error:
            log.warning(
                "OLED hardware not available; service stays in standby and retries every %ss: %s",
                HARDWARE_RETRY_SECONDS,
                error,
            )
            self._last_logged_error = error

    def display(self, image: Image.Image) -> bool:
        if self.device is None:
            return False
        try:
            self.device.display(image)
            return True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.warning("OLED disconnected or write failed; returning to standby: %s", error)
            self.disconnect(error)
            return False

    def clear(self) -> None:
        if self.device is None:
            return
        try:
            self.device.clear()
        except Exception:
            pass


class DisplayService:
    def __init__(self) -> None:
        self.layout = load_layout()
        self.hardware = HardwareController()
        self.snapshot = Snapshot()
        self.page_index = 0
        self.last_page_switch = 0.0
        self.last_data_refresh = 0.0
        self.last_layout_refresh = 0.0
        self.last_media_active = False
        self.alert_cooldown_until = 0.0
        self.priority_page: str | None = None
        self.priority_until = 0.0
        self.last_preview_write = 0.0
        self.last_status_write = 0.0
        self.current_page_name = PAGES[0]

    def maybe_refresh(self, now: float) -> None:
        if now - self.last_layout_refresh >= 5:
            previous = self.layout
            self.layout = load_layout()
            self.last_layout_refresh = now
            if self.layout != previous:
                log.info("Display configuration reloaded")
                self.hardware.ensure(self.layout, force=True)

        if now - self.last_data_refresh >= self.layout["refresh_seconds"]:
            self.snapshot = build_snapshot()
            self.last_data_refresh = now

            if self.layout["media_priority"] and self.snapshot.media_active and not self.last_media_active:
                self.priority_page = "media"
                self.priority_until = now + self.layout["page_seconds"]
            self.last_media_active = self.snapshot.media_active

            if self.layout["alert_priority"] and now >= self.alert_cooldown_until:
                if self.snapshot.cpu >= 85:
                    self.priority_page = "performance"
                    self.priority_until = now + self.layout["page_seconds"]
                    self.alert_cooldown_until = now + 60
                elif (self.snapshot.temp or 0) >= 75:
                    self.priority_page = "system"
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

    def write_preview(self, image: Image.Image, now: float) -> None:
        if not WRITE_HEADLESS_PREVIEW or now - self.last_preview_write < 5:
            return
        try:
            PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
            image.save(PREVIEW_PATH, format="PNG")
            self.last_preview_write = now
        except OSError as exc:
            log.debug("Preview write failed: %s", exc)

    def write_status(self, now: float, *, force: bool = False) -> None:
        if not force and now - self.last_status_write < 10:
            return
        payload = {
            "ok": True,
            "mode": "hardware" if self.hardware.connected else "standby",
            "hardware_connected": self.hardware.connected,
            "hardware_optional": ALLOW_MISSING_HARDWARE,
            "hardware_error": self.hardware.last_error or None,
            "guild_id": str(GUILD_ID),
            "database": str(DATABASE_PATH),
            "current_page": self.current_page_name,
            "pages": list(PAGES),
            "layout": {
                "i2c_address": self.layout["i2c_address"],
                "rotation": self.layout["rotation"],
                "brightness": self.layout["brightness"],
                "page_seconds": self.layout["page_seconds"],
                "refresh_seconds": self.layout["refresh_seconds"],
            },
            "snapshot": {
                "cpu": self.snapshot.cpu,
                "ram": self.snapshot.ram,
                "temperature": self.snapshot.temp,
                "network": self.snapshot.network,
                "pihole": self.snapshot.pihole,
                "media_active": self.snapshot.media_active,
                "media_title": self.snapshot.media_title,
            },
            "preview_path": str(PREVIEW_PATH) if WRITE_HEADLESS_PREVIEW else None,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        try:
            STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_PATH.with_suffix(STATUS_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATUS_PATH)
            self.last_status_write = now
        except OSError as exc:
            log.debug("Status write failed: %s", exc)

    def run(self) -> None:
        log.info(
            "Starting display service; DB=%s guild=%s hardware_optional=%s",
            DATABASE_PATH,
            GUILD_ID,
            ALLOW_MISSING_HARDWARE,
        )
        psutil.cpu_percent(interval=None)
        self.snapshot = build_snapshot()
        self.hardware.ensure(self.layout, force=True)

        if not self.hardware.connected and not ALLOW_MISSING_HARDWARE:
            raise RuntimeError(f"OLED required but unavailable: {self.hardware.last_error}")

        while True:
            try:
                now = time.monotonic()
                self.maybe_refresh(now)
                self.hardware.ensure(self.layout)
                self.current_page_name = self.current_page(now)
                image = render_image(self.current_page_name, self.snapshot, self.layout)
                self.hardware.display(image)
                self.write_preview(image, now)
                self.write_status(now)
            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Display loop failed; continuing")
                time.sleep(2)
                continue
            time.sleep(1)

        self.hardware.clear()
        self.write_status(time.monotonic(), force=True)


def check() -> int:
    layout = load_layout()
    hardware = HardwareController()
    connected = hardware.ensure(layout, force=True)
    result = {
        "service_ready": True,
        "hardware_connected": connected,
        "hardware_optional": ALLOW_MISSING_HARDWARE,
        "hardware_error": hardware.last_error or None,
        "luma_available": luma_i2c is not None and ssd1306 is not None,
        "database_exists": DATABASE_PATH.exists(),
        "database": str(DATABASE_PATH),
        "guild_id": str(GUILD_ID),
        "i2c_address": layout["i2c_address"],
        "preview_path": str(PREVIEW_PATH),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    hardware.clear()
    if connected or ALLOW_MISSING_HARDWARE:
        return 0
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(description="HomePi 0.96 SSD1306 display service")
    parser.add_argument("--check", action="store_true", help="Run deployment/hardware diagnostics and exit")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(check())
    DisplayService().run()


if __name__ == "__main__":
    main()
