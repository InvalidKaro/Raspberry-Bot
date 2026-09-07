from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from luma.core.interface.serial import i2c as luma_i2c
    from luma.oled.device import ssd1306
except Exception as exc:
    luma_i2c = None
    ssd1306 = None
    LUMA_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    LUMA_IMPORT_ERROR = ""

REPO_ROOT = Path(os.getenv("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot"))
I2C_BUS = max(0, int(os.getenv("DISPLAY2_I2C_BUS", "3")))
I2C_ADDRESS = os.getenv("DISPLAY2_I2C_ADDRESS", "0x3C").strip()
BRIGHTNESS = max(10, min(100, int(os.getenv("DISPLAY2_BRIGHTNESS", "90"))))
PAGE_SECONDS = max(2, min(30, int(os.getenv("DISPLAY2_PAGE_SECONDS", "5"))))
REFRESH_SECONDS = max(1, min(60, int(os.getenv("DISPLAY2_REFRESH_SECONDS", "3"))))
MESSAGE_SECONDS = max(5, min(120, int(os.getenv("DISPLAY2_MESSAGE_SECONDS", "15"))))
RETRY_SECONDS = max(5, int(os.getenv("DISPLAY2_HARDWARE_RETRY_SECONDS", "20")))
ROTATION = 180 if int(os.getenv("DISPLAY2_ROTATION", "0") or 0) == 180 else 0
ALLOW_MISSING = os.getenv("DISPLAY2_ALLOW_MISSING_HARDWARE", "1").lower() not in {"0", "false", "no", "off"}
WRITE_PREVIEW = os.getenv("DISPLAY2_HEADLESS_PREVIEW", "1").lower() not in {"0", "false", "no", "off"}
MESH_STATE_PATH = Path(os.getenv("MESHTASTIC_STATE_PATH", str(REPO_ROOT / "data" / "meshtastic_state.json")))
STATUS_PATH = Path(os.getenv("DISPLAY2_STATUS_PATH", str(REPO_ROOT / "data" / "display2_status.json")))
PREVIEW_PATH = Path(os.getenv("DISPLAY2_PREVIEW_PATH", str(REPO_ROOT / "data" / "display2_preview.png")))
PAGES = ("mesh", "rf", "nodes", "services")

logging.basicConfig(
    level=os.getenv("DISPLAY2_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s homepi-display2: %(message)s",
)
log = logging.getLogger("homepi-display2")


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
FONT_LARGE = _font(20)


@dataclass(slots=True)
class Snapshot:
    connected: bool = False
    nodes_total: int = 0
    nodes_10m: int = 0
    nodes_60m: int = 0
    rx_packets: int = 0
    last_packet_at: float = 0.0
    last_rssi: float | None = None
    last_snr: float | None = None
    last_from: str = ""
    local_name: str = ""
    local_id: str = ""
    last_message_text: str = ""
    last_message_from: str = ""
    last_message_at: float = 0.0


def _read_state() -> dict[str, Any]:
    try:
        raw = json.loads(MESH_STATE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_snapshot() -> Snapshot:
    raw = _read_state()
    message = raw.get("last_message") if isinstance(raw.get("last_message"), dict) else {}
    local = raw.get("local") if isinstance(raw.get("local"), dict) else {}
    return Snapshot(
        connected=bool(raw.get("connected")),
        nodes_total=_as_int(raw.get("nodes_total")),
        nodes_10m=_as_int(raw.get("nodes_active_10m")),
        nodes_60m=_as_int(raw.get("nodes_active_60m")),
        rx_packets=_as_int(raw.get("rx_packets")),
        last_packet_at=float(raw.get("last_packet_at") or 0),
        last_rssi=_as_float(raw.get("last_rssi")),
        last_snr=_as_float(raw.get("last_snr")),
        last_from=str(raw.get("last_from") or ""),
        local_name=str(local.get("name") or ""),
        local_id=str(local.get("id") or ""),
        last_message_text=str(message.get("text") or ""),
        last_message_from=str(message.get("from") or ""),
        last_message_at=float(message.get("received_at") or 0),
    )


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    text = str(text)
    if draw.textbbox((0, 0), text, font=font)[2] <= width:
        return text
    suffix = "..."
    while text and draw.textbbox((0, 0), text + suffix, font=font)[2] > width:
        text = text[:-1]
    return text + suffix if text else suffix


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int) -> list[str]:
    words = " ".join(str(text or "").split()).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return [_truncate(draw, line, font, width) for line in lines[:max_lines]]


def _ago(timestamp: float) -> str:
    if timestamp <= 0:
        return "--"
    age = max(0, int(time.time() - timestamp))
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"


def _service_active(name: str) -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.7,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _header(draw: ImageDraw.ImageDraw, title: str, status: str = "") -> None:
    draw.text((2, 2), title, font=FONT_SMALL, fill=255)
    if status:
        width = draw.textbbox((0, 0), status, font=FONT_TINY)[2]
        draw.text((126 - width, 3), status, font=FONT_TINY, fill=255)
    draw.line((0, 13, 127, 13), fill=255)


def render_message(snap: Snapshot) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "LORA MESSAGE", _ago(snap.last_message_at))
    draw.text((2, 17), _truncate(draw, snap.last_message_from or "Unbekannt", FONT_TINY, 124), font=FONT_TINY, fill=255)
    y = 27
    for line in _wrap(draw, snap.last_message_text or "(leer)", FONT_SMALL, 124, 3):
        draw.text((2, y), line, font=FONT_SMALL, fill=255)
        y += 11
    return image.rotate(180) if ROTATION == 180 else image


def render_page(page: str, snap: Snapshot) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)

    if page == "mesh":
        _header(draw, "MESHTASTIC", "ONLINE" if snap.connected else "WAIT")
        draw.text((2, 18), f"Nodes {snap.nodes_total}", font=FONT_MEDIUM, fill=255)
        draw.text((73, 18), f"RX {snap.rx_packets}", font=FONT_MEDIUM, fill=255)
        draw.text((2, 36), f"10m {snap.nodes_10m}", font=FONT_SMALL, fill=255)
        draw.text((48, 36), f"1h {snap.nodes_60m}", font=FONT_SMALL, fill=255)
        draw.text((88, 36), f"Last {_ago(snap.last_packet_at)}", font=FONT_TINY, fill=255)
        draw.text((2, 52), _truncate(draw, snap.local_name or snap.local_id or "USB radio", FONT_TINY, 124), font=FONT_TINY, fill=255)
    elif page == "rf":
        _header(draw, "LAST RF", "MESH" if snap.connected else "OFF")
        rssi = "--" if snap.last_rssi is None else f"{snap.last_rssi:.0f}"
        snr = "--" if snap.last_snr is None else f"{snap.last_snr:+.1f}"
        draw.text((2, 18), "RSSI", font=FONT_TINY, fill=255)
        draw.text((2, 29), f"{rssi} dBm", font=FONT_MEDIUM, fill=255)
        draw.text((73, 18), "SNR", font=FONT_TINY, fill=255)
        draw.text((73, 29), f"{snr} dB", font=FONT_MEDIUM, fill=255)
        draw.text((2, 48), "FROM", font=FONT_TINY, fill=255)
        draw.text((31, 47), _truncate(draw, snap.last_from or "--", FONT_SMALL, 94), font=FONT_SMALL, fill=255)
    elif page == "nodes":
        _header(draw, "MESH NODES", "LIVE" if snap.connected else "CACHE")
        draw.text((2, 18), "KNOWN", font=FONT_TINY, fill=255)
        draw.text((83, 16), str(snap.nodes_total), font=FONT_LARGE, fill=255)
        draw.text((2, 35), f"active 10m   {snap.nodes_10m}", font=FONT_SMALL, fill=255)
        draw.text((2, 47), f"active 1h    {snap.nodes_60m}", font=FONT_SMALL, fill=255)
    elif page == "services":
        _header(draw, "SERVICES")
        services = (
            ("BOT", "raspberry-bot.service"),
            ("DASH", "raspberry-dashboard.service"),
            ("OLED1", "raspberry-display.service"),
            ("MESH", "raspberry-meshtastic.service"),
        )
        positions = ((2, 18), (67, 18), (2, 37), (67, 37))
        for (label, unit), (x, y) in zip(services, positions):
            draw.text((x, y), label, font=FONT_TINY, fill=255)
            draw.text((x, y + 9), "ON" if _service_active(unit) else "OFF", font=FONT_SMALL, fill=255)

    return image.rotate(180) if ROTATION == 180 else image


class HardwareController:
    def __init__(self) -> None:
        self.device = None
        self.last_error = ""
        self.next_retry_at = 0.0

    @property
    def connected(self) -> bool:
        return self.device is not None

    def ensure(self, *, force: bool = False) -> bool:
        if self.device is not None:
            return True
        now = time.monotonic()
        if not force and now < self.next_retry_at:
            return False
        if luma_i2c is None or ssd1306 is None:
            self.last_error = f"luma.oled unavailable: {LUMA_IMPORT_ERROR or 'not installed'}"
            self.next_retry_at = now + RETRY_SECONDS
            return False
        try:
            serial = luma_i2c(port=I2C_BUS, address=int(I2C_ADDRESS, 16))
            self.device = ssd1306(serial, width=128, height=64, rotate=0)
            self.device.contrast(round(255 * BRIGHTNESS / 100))
            self.last_error = ""
            log.info("OLED2 connected: bus=%s address=%s", I2C_BUS, I2C_ADDRESS)
            return True
        except Exception as exc:
            self.device = None
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.next_retry_at = now + RETRY_SECONDS
            return False

    def display(self, image: Image.Image) -> bool:
        if self.device is None:
            return False
        try:
            self.device.display(image)
            return True
        except Exception as exc:
            self.device = None
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.next_retry_at = time.monotonic() + RETRY_SECONDS
            return False


class Display2Service:
    def __init__(self) -> None:
        self.hardware = HardwareController()
        self.snapshot = Snapshot()
        self.page_index = 0
        self.last_page_switch = 0.0
        self.last_refresh = 0.0
        self.last_preview = 0.0
        self.last_status = 0.0
        self.current_page = PAGES[0]

    def _page(self, now: float) -> str:
        if self.snapshot.last_message_text and time.time() - self.snapshot.last_message_at <= MESSAGE_SECONDS:
            return "message"
        if self.last_page_switch == 0:
            self.last_page_switch = now
        elif now - self.last_page_switch >= PAGE_SECONDS:
            self.page_index = (self.page_index + 1) % len(PAGES)
            self.last_page_switch = now
        return PAGES[self.page_index]

    def _write_status(self, now: float) -> None:
        if now - self.last_status < 5:
            return
        payload = {
            "ok": True,
            "mode": "hardware" if self.hardware.connected else "standby",
            "hardware_connected": self.hardware.connected,
            "hardware_optional": ALLOW_MISSING,
            "hardware_error": self.hardware.last_error or None,
            "i2c_bus": I2C_BUS,
            "i2c_address": I2C_ADDRESS,
            "current_page": self.current_page,
            "pages": list(PAGES),
            "meshtastic_connected": self.snapshot.connected,
            "nodes_total": self.snapshot.nodes_total,
            "preview_path": str(PREVIEW_PATH) if WRITE_PREVIEW else None,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        try:
            STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_PATH.with_suffix(STATUS_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATUS_PATH)
            self.last_status = now
        except OSError as exc:
            log.debug("Status write failed: %s", exc)

    def run(self) -> None:
        self.snapshot = load_snapshot()
        self.hardware.ensure(force=True)
        if not self.hardware.connected and not ALLOW_MISSING:
            raise RuntimeError(f"OLED2 required but unavailable: {self.hardware.last_error}")
        while True:
            try:
                now = time.monotonic()
                if now - self.last_refresh >= REFRESH_SECONDS:
                    self.snapshot = load_snapshot()
                    self.last_refresh = now
                self.hardware.ensure()
                self.current_page = self._page(now)
                image = render_message(self.snapshot) if self.current_page == "message" else render_page(self.current_page, self.snapshot)
                self.hardware.display(image)
                if WRITE_PREVIEW and now - self.last_preview >= 3:
                    PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
                    image.save(PREVIEW_PATH, format="PNG")
                    self.last_preview = now
                self._write_status(now)
            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("OLED2 loop failed; continuing")
                time.sleep(2)
                continue
            time.sleep(0.5)


def check() -> int:
    hardware = HardwareController()
    connected = hardware.ensure(force=True)
    print(json.dumps({
        "service_ready": True,
        "hardware_connected": connected,
        "hardware_optional": ALLOW_MISSING,
        "hardware_error": hardware.last_error or None,
        "i2c_bus": I2C_BUS,
        "i2c_address": I2C_ADDRESS,
        "meshtastic_state": str(MESH_STATE_PATH),
        "preview_path": str(PREVIEW_PATH),
    }, ensure_ascii=False, indent=2))
    return 0 if connected or ALLOW_MISSING else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="HomePi second 0.96 SSD1306 display")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(check())
    Display2Service().run()


if __name__ == "__main__":
    main()
