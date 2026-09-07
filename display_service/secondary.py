from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil
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
DATABASE_PATH = Path(os.getenv("DISPLAY2_DATABASE_PATH") or os.getenv("BOT_DATABASE_PATH") or os.getenv("DATABASE_PATH") or (REPO_ROOT / "data" / "bot.sqlite3"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = REPO_ROOT / DATABASE_PATH
GUILD_ID = int(os.getenv("DISPLAY_GUILD_ID", "1162733312226361454"))
I2C_BUS = max(0, int(os.getenv("DISPLAY2_I2C_BUS", "3")))
I2C_ADDRESS = os.getenv("DISPLAY2_I2C_ADDRESS", "0x3C").strip()
BRIGHTNESS = max(10, min(100, int(os.getenv("DISPLAY2_BRIGHTNESS", "90"))))
PAGE_SECONDS = max(2, min(30, int(os.getenv("DISPLAY2_PAGE_SECONDS", "5"))))
REFRESH_SECONDS = max(1, min(60, int(os.getenv("DISPLAY2_REFRESH_SECONDS", "3"))))
MESSAGE_SECONDS = max(5, min(120, int(os.getenv("DISPLAY2_MESSAGE_SECONDS", "15"))))
VOICE_SECONDS = max(5, min(120, int(os.getenv("DISPLAY2_VOICE_SECONDS", "12"))))
ALERT_SECONDS = max(5, min(120, int(os.getenv("DISPLAY2_ALERT_SECONDS", "15"))))
RETRY_SECONDS = max(5, int(os.getenv("DISPLAY2_HARDWARE_RETRY_SECONDS", "20")))
ROTATION = 180 if int(os.getenv("DISPLAY2_ROTATION", "0") or 0) == 180 else 0
ALLOW_MISSING = os.getenv("DISPLAY2_ALLOW_MISSING_HARDWARE", "1").lower() not in {"0", "false", "no", "off"}
WRITE_PREVIEW = os.getenv("DISPLAY2_HEADLESS_PREVIEW", "1").lower() not in {"0", "false", "no", "off"}
PUBLIC_IP_ENABLED = os.getenv("DISPLAY2_PUBLIC_IP", "1").lower() not in {"0", "false", "no", "off"}
MESH_STATE_PATH = Path(os.getenv("MESHTASTIC_STATE_PATH", str(REPO_ROOT / "data" / "meshtastic_state.json")))
VOICE_EVENT_PATH = Path(os.getenv("DISPLAY2_VOICE_EVENT_PATH", str(REPO_ROOT / "data" / "voice_display_event.json")))
STATUS_PATH = Path(os.getenv("DISPLAY2_STATUS_PATH", str(REPO_ROOT / "data" / "display2_status.json")))
PREVIEW_PATH = Path(os.getenv("DISPLAY2_PREVIEW_PATH", str(REPO_ROOT / "data" / "display2_preview.png")))
MESH_PAGES = ("mesh", "rf", "nodes")
HOME_PAGES = ("discord", "pihole", "traffic", "git", "storage", "remote", "internet", "activity")
PAGES = MESH_PAGES + HOME_PAGES

logging.basicConfig(level=os.getenv("DISPLAY2_LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s homepi-display2: %(message)s")
log = logging.getLogger("homepi-display2")


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"):
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
class MeshSnapshot:
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


@dataclass(slots=True)
class VoiceEvent:
    text: str = ""
    action: str = ""
    unit: str = ""
    status: str = ""
    speech: str = ""
    created_at: float = 0.0


@dataclass(slots=True)
class ActivityEvent:
    created_at: float = 0.0
    kind: str = ""
    title: str = ""
    detail: str = ""


@dataclass(slots=True)
class HomeSnapshot:
    bot_active: bool = False
    guilds: int = 0
    members: int | None = None
    discord_latency_ms: float | None = None
    commands_24h: int = 0
    errors_24h: int = 0
    open_tickets: int = 0
    pihole_active: bool = False
    pihole_queries: int | None = None
    pihole_blocked: int | None = None
    pihole_percent: float | None = None
    pihole_clients: int | None = None
    iface: str = ""
    lan_ip: str = ""
    rx_bps: float = 0.0
    tx_bps: float = 0.0
    link_mbps: int = 0
    git_branch: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    git_ahead: int = 0
    git_behind: int = 0
    disk_percent: float = 0.0
    db_mb: float = 0.0
    logs_mb: float = 0.0
    backups: int = 0
    backups_mb: float = 0.0
    tailscale_active: bool = False
    tailscale_ip: str = ""
    ssh_active: bool = False
    public_ip: str = ""
    internet_online: bool = False
    ping_ms: float | None = None
    dns_ms: float | None = None
    events: list[ActivityEvent] = field(default_factory=list)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True, timeout=1.0)
    con.row_factory = sqlite3.Row
    return con


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


def load_mesh_snapshot() -> MeshSnapshot:
    raw = _read_json(MESH_STATE_PATH)
    message = raw.get("last_message") if isinstance(raw.get("last_message"), dict) else {}
    local = raw.get("local") if isinstance(raw.get("local"), dict) else {}
    return MeshSnapshot(connected=bool(raw.get("connected")), nodes_total=_as_int(raw.get("nodes_total")), nodes_10m=_as_int(raw.get("nodes_active_10m")), nodes_60m=_as_int(raw.get("nodes_active_60m")), rx_packets=_as_int(raw.get("rx_packets")), last_packet_at=float(raw.get("last_packet_at") or 0), last_rssi=_as_float(raw.get("last_rssi")), last_snr=_as_float(raw.get("last_snr")), last_from=str(raw.get("last_from") or ""), local_name=str(local.get("name") or ""), local_id=str(local.get("id") or ""), last_message_text=str(message.get("text") or ""), last_message_from=str(message.get("from") or ""), last_message_at=float(message.get("received_at") or 0))


def load_voice_event() -> VoiceEvent:
    raw = _read_json(VOICE_EVENT_PATH)
    return VoiceEvent(text=str(raw.get("text") or ""), action=str(raw.get("action") or ""), unit=str(raw.get("unit") or ""), status=str(raw.get("status") or ""), speech=str(raw.get("speech") or ""), created_at=float(raw.get("created_at") or 0))


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
    lines, current = [], ""
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


def _clock(timestamp: float) -> str:
    return "--:--" if timestamp <= 0 else time.strftime("%H:%M", time.localtime(timestamp))


def _service_active(name: str) -> bool:
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.7, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _run(args: list[str], *, cwd: Path | None = None, timeout: float = 2.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, cwd=str(cwd) if cwd else None, text=True, capture_output=True, timeout=timeout, check=False)
        return int(proc.returncode), (proc.stdout or proc.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def _header(draw: ImageDraw.ImageDraw, title: str, status: str = "") -> None:
    draw.text((2, 2), title, font=FONT_SMALL, fill=255)
    if status:
        width = draw.textbbox((0, 0), status, font=FONT_TINY)[2]
        draw.text((126 - width, 3), status, font=FONT_TINY, fill=255)
    draw.line((0, 13, 127, 13), fill=255)


def _fmt_rate(value: float) -> str:
    value = max(0.0, float(value))
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f}M"
    if value >= 1024:
        return f"{value / 1024:.0f}K"
    return f"{value:.0f}B"


def _fmt_mb(value: float) -> str:
    return f"{value / 1024:.1f}G" if value >= 1024 else f"{value:.0f}M" if value >= 100 else f"{value:.1f}M"


def _parse_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        try:
            return time.mktime(time.strptime(text[:19], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return 0.0


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    try:
        return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except sqlite3.Error:
        return False


def _read_discord_stats() -> dict[str, Any]:
    result = {"bot_active": _service_active("raspberry-bot.service"), "guilds": 0, "members": None, "latency_ms": None, "commands_24h": 0, "errors_24h": 0, "open_tickets": 0}
    try:
        with _connect() as con:
            if _table_exists(con, "dashboard_runtime_state"):
                rows = con.execute("SELECT state_json FROM dashboard_runtime_state").fetchall()
                result["guilds"] = len(rows)
                for row in rows:
                    try:
                        runtime = json.loads(row["state_json"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    bot = runtime.get("bot") if isinstance(runtime, dict) else None
                    if isinstance(bot, dict):
                        result["guilds"] = max(result["guilds"], _as_int(bot.get("guilds"))) if bot.get("guilds") is not None else result["guilds"]
                        result["members"] = _as_int(bot.get("members")) if bot.get("members") is not None else result["members"]
                        result["latency_ms"] = _as_float(bot.get("latency_ms")) if bot.get("latency_ms") is not None else result["latency_ms"]
            if _table_exists(con, "command_analytics"):
                row = con.execute("SELECT COUNT(*) uses, COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) errors FROM command_analytics WHERE created_at>=datetime('now','-24 hours')").fetchone()
                if row:
                    result["commands_24h"], result["errors_24h"] = _as_int(row["uses"]), _as_int(row["errors"])
            if _table_exists(con, "tickets"):
                row = con.execute("SELECT COUNT(*) count FROM tickets WHERE status!='closed'").fetchone()
                result["open_tickets"] = _as_int(row["count"] if row else 0)
    except (sqlite3.Error, OSError):
        pass
    return result


def _read_pihole_stats() -> dict[str, Any]:
    result = {"active": _service_active("pihole-FTL.service"), "queries": None, "blocked": None, "percent": None, "clients": None}
    code, text = _run(["pihole", "api", "stats/summary"], timeout=4)
    if code != 0 or not text:
        return result
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1] if start >= 0 and end > start else text)
    except (ValueError, TypeError, json.JSONDecodeError):
        return result
    if isinstance(data, dict):
        queries = data.get("queries") if isinstance(data.get("queries"), dict) else {}
        clients = data.get("clients") if isinstance(data.get("clients"), dict) else {}
        result["queries"] = _as_int(queries.get("total")) if queries.get("total") is not None else None
        result["blocked"] = _as_int(queries.get("blocked")) if queries.get("blocked") is not None else None
        result["percent"] = _as_float(queries.get("percent_blocked"))
        result["clients"] = _as_int(clients.get("active")) if clients.get("active") is not None else None
    return result


def _active_interface() -> tuple[str, int, int, int, str]:
    stats, counters, addresses = psutil.net_if_stats(), psutil.net_io_counters(pernic=True), psutil.net_if_addrs()
    candidates = []
    for name, stat in stats.items():
        if name == "lo" or not stat.isup or name not in counters:
            continue
        priority = 0 if name.startswith("eth") else 1 if name.startswith("en") else 2 if name.startswith("wl") else 3
        candidates.append((priority, name, stat))
    if not candidates:
        return "", 0, 0, 0, ""
    _, name, stat = sorted(candidates)[0]
    counter = counters[name]
    ipv4 = ""
    for addr in addresses.get(name, []):
        if getattr(addr, "family", None) == socket.AF_INET and addr.address:
            ipv4 = str(addr.address)
            break
    return name, int(counter.bytes_recv), int(counter.bytes_sent), int(stat.speed or 0), ipv4


def _git_stats() -> dict[str, Any]:
    out = {"branch": "", "sha": "", "dirty": False, "ahead": 0, "behind": 0}
    code, text = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT)
    if code == 0:
        out["branch"] = text.splitlines()[0].strip() if text else ""
    code, text = _run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT)
    if code == 0:
        out["sha"] = text.splitlines()[0].strip() if text else ""
    code, text = _run(["git", "status", "--porcelain"], cwd=REPO_ROOT)
    out["dirty"] = bool(text.strip()) if code == 0 else False
    code, text = _run(["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=REPO_ROOT)
    if code == 0 and len(text.split()) >= 2:
        parts = text.split()
        out["ahead"], out["behind"] = _as_int(parts[0]), _as_int(parts[1])
    return out


def _du_mb(path: Path) -> float:
    code, text = _run(["du", "-sb", str(path)], timeout=3)
    if code == 0 and text:
        try:
            return int(text.split()[0]) / 1024 / 1024
        except (ValueError, IndexError):
            pass
    return 0.0


def _storage_stats() -> dict[str, Any]:
    disk = psutil.disk_usage("/")
    db_mb = DATABASE_PATH.stat().st_size / 1024 / 1024 if DATABASE_PATH.exists() else 0.0
    backups_dir = REPO_ROOT / "data" / "backups"
    backup_files = []
    if backups_dir.exists():
        try:
            backup_files = [item for item in backups_dir.iterdir() if item.is_file()]
        except OSError:
            pass
    backup_mb = 0.0
    for item in backup_files:
        try:
            backup_mb += item.stat().st_size / 1024 / 1024
        except OSError:
            pass
    return {"disk_percent": float(disk.percent), "db_mb": db_mb, "logs_mb": _du_mb(Path("/var/log")), "backups": len(backup_files), "backups_mb": backup_mb}


def _remote_stats(public_ip: str) -> dict[str, Any]:
    tailscale_active = _service_active("tailscaled.service")
    tailscale_ip = ""
    if tailscale_active:
        code, text = _run(["tailscale", "ip", "-4"], timeout=2)
        tailscale_ip = text.splitlines()[0].strip() if code == 0 and text else ""
    return {"tailscale_active": tailscale_active, "tailscale_ip": tailscale_ip, "ssh_active": _service_active("ssh.service") or _service_active("sshd.service"), "public_ip": public_ip}


def _read_public_ip() -> str:
    if not PUBLIC_IP_ENABLED:
        return ""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=2.0) as response:
            value = response.read(64).decode("ascii", errors="ignore").strip()
        ipaddress.ip_address(value)
        return value
    except Exception:
        return ""


def _internet_stats() -> dict[str, Any]:
    ping_ms = None
    code, text = _run(["ping", "-c", "1", "-W", "1", "1.1.1.1"], timeout=2.5)
    if code == 0:
        match = re.search(r"time[=<]([0-9.]+)\s*ms", text)
        ping_ms = _as_float(match.group(1)) if match else None
    started = time.monotonic()
    dns_code, _ = _run(["getent", "hosts", "example.com"], timeout=2.0)
    dns_ms = (time.monotonic() - started) * 1000 if dns_code == 0 else None
    return {"online": code == 0 or dns_code == 0, "ping_ms": ping_ms, "dns_ms": dns_ms}


def _activity_events(mesh: MeshSnapshot, voice: VoiceEvent) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []
    try:
        with _connect() as con:
            if _table_exists(con, "dashboard_activity"):
                for row in con.execute("SELECT created_at,kind,title,detail FROM dashboard_activity ORDER BY id DESC LIMIT 5").fetchall():
                    events.append(ActivityEvent(_parse_epoch(row["created_at"]), str(row["kind"] or "ACT"), str(row["title"] or ""), str(row["detail"] or "")))
            if _table_exists(con, "bot_audit_log"):
                for row in con.execute("SELECT created_at,action,target_type,target_id FROM bot_audit_log ORDER BY id DESC LIMIT 5").fetchall():
                    detail = " ".join(str(v) for v in (row["target_type"], row["target_id"]) if v)
                    events.append(ActivityEvent(_parse_epoch(row["created_at"]), "audit", str(row["action"] or "Audit"), detail))
            if _table_exists(con, "command_analytics"):
                for row in con.execute("SELECT created_at,command_name,success FROM command_analytics ORDER BY id DESC LIMIT 4").fetchall():
                    events.append(ActivityEvent(_parse_epoch(row["created_at"]), "cmd", f"/{row['command_name']}", "ok" if _as_int(row["success"]) else "error"))
    except (sqlite3.Error, OSError):
        pass
    if voice.created_at:
        events.append(ActivityEvent(voice.created_at, "voice", voice.action or voice.text or "Voice", voice.status or ""))
    if mesh.last_packet_at:
        events.append(ActivityEvent(mesh.last_packet_at, "mesh", "Mesh RX", mesh.last_from or "packet"))
    events.sort(key=lambda item: item.created_at, reverse=True)
    return events[:8]


def render_message(snap: MeshSnapshot) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "LORA MESSAGE", _ago(snap.last_message_at))
    draw.text((2, 17), _truncate(draw, snap.last_message_from or "Unbekannt", FONT_TINY, 124), font=FONT_TINY, fill=255)
    for idx, line in enumerate(_wrap(draw, snap.last_message_text or "(leer)", FONT_SMALL, 124, 3)):
        draw.text((2, 27 + idx * 11), line, font=FONT_SMALL, fill=255)
    return image.rotate(180) if ROTATION == 180 else image


def render_voice(event: VoiceEvent) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "VOICE", (event.status or "VOICE").upper()[:10])
    target = event.unit or event.action or event.text or "Command"
    for idx, line in enumerate(_wrap(draw, target, FONT_SMALL, 124, 2)):
        draw.text((2, 18 + idx * 11), line, font=FONT_SMALL, fill=255)
    if event.action and event.unit:
        draw.text((2, 43), _truncate(draw, event.action.upper(), FONT_TINY, 70), font=FONT_TINY, fill=255)
    draw.text((95, 52), _ago(event.created_at), font=FONT_TINY, fill=255)
    return image.rotate(180) if ROTATION == 180 else image


def render_alert(title: str, detail: str) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    _header(draw, "ALERT", "!")
    draw.text((2, 18), _truncate(draw, title, FONT_MEDIUM, 124), font=FONT_MEDIUM, fill=255)
    for idx, line in enumerate(_wrap(draw, detail, FONT_SMALL, 124, 2)):
        draw.text((2, 35 + idx * 11), line, font=FONT_SMALL, fill=255)
    return image.rotate(180) if ROTATION == 180 else image


def render_mesh_page(page: str, snap: MeshSnapshot) -> Image.Image:
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
    return image.rotate(180) if ROTATION == 180 else image


def render_home_page(page: str, snap: HomeSnapshot) -> Image.Image:
    image = Image.new("1", (128, 64), 0)
    draw = ImageDraw.Draw(image)
    if page == "discord":
        _header(draw, "DISCORD BOT", "ON" if snap.bot_active else "OFF")
        draw.text((2, 18), f"Guilds {snap.guilds}", font=FONT_SMALL, fill=255)
        draw.text((67, 18), f"Cmd24 {snap.commands_24h}", font=FONT_SMALL, fill=255)
        latency = "--" if snap.discord_latency_ms is None else f"{snap.discord_latency_ms:.0f}ms"
        draw.text((2, 31), f"Ping {latency}", font=FONT_SMALL, fill=255)
        draw.text((67, 31), f"Err24 {snap.errors_24h}", font=FONT_SMALL, fill=255)
        draw.text((2, 46), f"Tickets {snap.open_tickets}", font=FONT_SMALL, fill=255)
        if snap.members is not None:
            draw.text((67, 46), f"Users {snap.members}", font=FONT_SMALL, fill=255)
    elif page == "pihole":
        _header(draw, "PI-HOLE", "ON" if snap.pihole_active else "OFF")
        q = "--" if snap.pihole_queries is None else str(snap.pihole_queries)
        b = "--" if snap.pihole_blocked is None else str(snap.pihole_blocked)
        pct = "--" if snap.pihole_percent is None else f"{snap.pihole_percent:.1f}%"
        draw.text((2, 18), f"Queries {q}", font=FONT_SMALL, fill=255)
        draw.text((2, 31), f"Blocked {b}", font=FONT_SMALL, fill=255)
        draw.text((2, 44), f"Rate {pct}", font=FONT_SMALL, fill=255)
        if snap.pihole_clients is not None:
            draw.text((81, 44), f"C {snap.pihole_clients}", font=FONT_TINY, fill=255)
    elif page == "traffic":
        _header(draw, "NETWORK", snap.iface or "--")
        draw.text((2, 18), f"RX  {_fmt_rate(snap.rx_bps)}/s", font=FONT_MEDIUM, fill=255)
        draw.text((2, 34), f"TX  {_fmt_rate(snap.tx_bps)}/s", font=FONT_MEDIUM, fill=255)
        footer = f"{snap.link_mbps}M" if snap.link_mbps else "LINK --"
        draw.text((2, 52), _truncate(draw, snap.lan_ip or "--", FONT_TINY, 88), font=FONT_TINY, fill=255)
        fw = draw.textbbox((0, 0), footer, font=FONT_TINY)[2]
        draw.text((126 - fw, 52), footer, font=FONT_TINY, fill=255)
    elif page == "git":
        sync = "DIRTY" if snap.git_dirty else "SYNC" if snap.git_behind == 0 and snap.git_ahead == 0 else "DIFF"
        _header(draw, "GITHUB / GIT", sync)
        draw.text((2, 18), _truncate(draw, snap.git_branch or "--", FONT_MEDIUM, 124), font=FONT_MEDIUM, fill=255)
        draw.text((2, 34), f"Commit {snap.git_sha or '--'}", font=FONT_SMALL, fill=255)
        draw.text((2, 48), f"Ahead {snap.git_ahead}", font=FONT_TINY, fill=255)
        draw.text((65, 48), f"Behind {snap.git_behind}", font=FONT_TINY, fill=255)
    elif page == "storage":
        _header(draw, "STORAGE / DB")
        draw.text((2, 17), f"Disk {snap.disk_percent:.0f}%", font=FONT_SMALL, fill=255)
        draw.text((67, 17), f"DB {_fmt_mb(snap.db_mb)}", font=FONT_SMALL, fill=255)
        draw.text((2, 31), f"Logs {_fmt_mb(snap.logs_mb)}", font=FONT_SMALL, fill=255)
        draw.text((2, 45), f"Backups {snap.backups}", font=FONT_SMALL, fill=255)
        draw.text((78, 45), _fmt_mb(snap.backups_mb), font=FONT_TINY, fill=255)
    elif page == "remote":
        _header(draw, "REMOTE ACCESS")
        draw.text((2, 17), f"TAIL {'ON' if snap.tailscale_active else 'OFF'}", font=FONT_SMALL, fill=255)
        draw.text((67, 17), f"SSH {'ON' if snap.ssh_active else 'OFF'}", font=FONT_SMALL, fill=255)
        draw.text((2, 31), _truncate(draw, snap.tailscale_ip or "Tailscale --", FONT_TINY, 124), font=FONT_TINY, fill=255)
        draw.text((2, 43), _truncate(draw, f"PUB {snap.public_ip}" if snap.public_ip else "PUB --", FONT_TINY, 124), font=FONT_TINY, fill=255)
        draw.text((2, 53), _truncate(draw, f"LAN {snap.lan_ip or '--'}", FONT_TINY, 124), font=FONT_TINY, fill=255)
    elif page == "internet":
        _header(draw, "INTERNET", "ONLINE" if snap.internet_online else "OFFLINE")
        ping = "--" if snap.ping_ms is None else f"{snap.ping_ms:.0f} ms"
        dns = "--" if snap.dns_ms is None else f"{snap.dns_ms:.0f} ms"
        draw.text((2, 20), "PING", font=FONT_TINY, fill=255)
        draw.text((39, 18), ping, font=FONT_MEDIUM, fill=255)
        draw.text((2, 39), "DNS", font=FONT_TINY, fill=255)
        draw.text((39, 37), dns, font=FONT_MEDIUM, fill=255)
    elif page == "activity":
        _header(draw, "HOMEPI LIVE", f"{len(snap.events)} EVT")
        y = 17
        for event in snap.events[:3]:
            draw.text((2, y), _truncate(draw, f"{_clock(event.created_at)} {event.title}", FONT_TINY, 124), font=FONT_TINY, fill=255)
            y += 13
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
        self.mesh = MeshSnapshot()
        self.home = HomeSnapshot()
        self.voice = VoiceEvent()
        self.page_index = 0
        self.last_page_switch = 0.0
        self.last_refresh = 0.0
        self.last_preview = 0.0
        self.last_status = 0.0
        self.current_page = MESH_PAGES[0]
        self.profile = "mesh"
        self._cache: dict[str, tuple[float, Any]] = {}
        self._net_prev: tuple[float, int, int] | None = None
        self._last_alert_key = ""
        self._last_alert_at = 0.0

    def _cached(self, key: str, ttl: float, producer) -> Any:
        now = time.monotonic()
        existing = self._cache.get(key)
        if existing and now - existing[0] < ttl:
            return existing[1]
        try:
            value = producer()
        except Exception as exc:
            log.debug("%s collection failed: %s", key, exc)
            value = existing[1] if existing else None
        self._cache[key] = (now, value)
        return value

    def _refresh_home(self) -> None:
        discord = self._cached("discord", 10, _read_discord_stats) or {}
        pihole = self._cached("pihole", 30, _read_pihole_stats) or {}
        git = self._cached("git", 30, _git_stats) or {}
        storage = self._cached("storage", 60, _storage_stats) or {}
        internet = self._cached("internet", 30, _internet_stats) or {}
        public_ip = self._cached("public_ip", 600, _read_public_ip) or ""
        remote = self._cached("remote", 15, lambda: _remote_stats(str(public_ip))) or {}
        iface, rx, tx, speed, lan_ip = _active_interface()
        now = time.monotonic()
        rx_bps = tx_bps = 0.0
        if self._net_prev is not None:
            previous_at, previous_rx, previous_tx = self._net_prev
            elapsed = max(0.1, now - previous_at)
            rx_bps = max(0.0, (rx - previous_rx) / elapsed)
            tx_bps = max(0.0, (tx - previous_tx) / elapsed)
        self._net_prev = (now, rx, tx)
        events = _activity_events(self.mesh, self.voice)
        self.home = HomeSnapshot(bot_active=bool(discord.get("bot_active")), guilds=_as_int(discord.get("guilds")), members=discord.get("members"), discord_latency_ms=_as_float(discord.get("latency_ms")), commands_24h=_as_int(discord.get("commands_24h")), errors_24h=_as_int(discord.get("errors_24h")), open_tickets=_as_int(discord.get("open_tickets")), pihole_active=bool(pihole.get("active")), pihole_queries=pihole.get("queries"), pihole_blocked=pihole.get("blocked"), pihole_percent=_as_float(pihole.get("percent")), pihole_clients=pihole.get("clients"), iface=iface, lan_ip=lan_ip, rx_bps=rx_bps, tx_bps=tx_bps, link_mbps=speed, git_branch=str(git.get("branch") or ""), git_sha=str(git.get("sha") or ""), git_dirty=bool(git.get("dirty")), git_ahead=_as_int(git.get("ahead")), git_behind=_as_int(git.get("behind")), disk_percent=float(storage.get("disk_percent") or 0.0), db_mb=float(storage.get("db_mb") or 0.0), logs_mb=float(storage.get("logs_mb") or 0.0), backups=_as_int(storage.get("backups")), backups_mb=float(storage.get("backups_mb") or 0.0), tailscale_active=bool(remote.get("tailscale_active")), tailscale_ip=str(remote.get("tailscale_ip") or ""), ssh_active=bool(remote.get("ssh_active")), public_ip=str(remote.get("public_ip") or ""), internet_online=bool(internet.get("online")), ping_ms=_as_float(internet.get("ping_ms")), dns_ms=_as_float(internet.get("dns_ms")), events=events)

    def _alert(self) -> tuple[str, str] | None:
        candidates = []
        if not self.home.bot_active:
            candidates.append(("BOT OFFLINE", "raspberry-bot.service"))
        if not self.home.internet_online:
            candidates.append(("INTERNET OFFLINE", "Ping und DNS fehlgeschlagen"))
        if self.home.disk_percent >= 90:
            candidates.append(("DISK FAST VOLL", f"{self.home.disk_percent:.0f}% belegt"))
        if self.home.errors_24h >= 5:
            candidates.append(("BOT FEHLER", f"{self.home.errors_24h} Fehler / 24h"))
        if self.home.git_behind > 0:
            candidates.append(("UPDATE", f"{self.home.git_behind} Commits hinter main"))
        if not candidates:
            self._last_alert_key = ""
            return None
        title, detail = candidates[0]
        key = f"{title}|{detail}"
        now = time.monotonic()
        if key != self._last_alert_key:
            self._last_alert_key = key
            self._last_alert_at = now
        return (title, detail) if now - self._last_alert_at <= ALERT_SECONDS else None

    def _page(self, now: float) -> str:
        if self._alert():
            return "alert"
        if self.voice.created_at and time.time() - self.voice.created_at <= VOICE_SECONDS:
            return "voice"
        if self.mesh.last_message_text and time.time() - self.mesh.last_message_at <= MESSAGE_SECONDS:
            return "message"
        pages = MESH_PAGES if self.mesh.connected else HOME_PAGES
        profile = "mesh" if self.mesh.connected else "homepi"
        if profile != self.profile:
            self.profile = profile
            self.page_index = 0
            self.last_page_switch = now
        if self.last_page_switch == 0:
            self.last_page_switch = now
        elif now - self.last_page_switch >= PAGE_SECONDS:
            self.page_index = (self.page_index + 1) % len(pages)
            self.last_page_switch = now
        return pages[self.page_index % len(pages)]

    def _render(self) -> Image.Image:
        if self.current_page == "message":
            return render_message(self.mesh)
        if self.current_page == "voice":
            return render_voice(self.voice)
        if self.current_page == "alert":
            return render_alert(*(self._alert() or ("HOMEPI", "Status geändert")))
        if self.current_page in MESH_PAGES:
            return render_mesh_page(self.current_page, self.mesh)
        return render_home_page(self.current_page, self.home)

    def _write_status(self, now: float) -> None:
        if now - self.last_status < 5:
            return
        payload = {"ok": True, "mode": "hardware" if self.hardware.connected else "standby", "profile": self.profile, "hardware_connected": self.hardware.connected, "hardware_optional": ALLOW_MISSING, "hardware_error": self.hardware.last_error or None, "i2c_bus": I2C_BUS, "i2c_address": I2C_ADDRESS, "current_page": self.current_page, "mesh_pages": list(MESH_PAGES), "home_pages": list(HOME_PAGES), "meshtastic_connected": self.mesh.connected, "nodes_total": self.mesh.nodes_total, "bot_active": self.home.bot_active, "internet_online": self.home.internet_online, "git_behind": self.home.git_behind, "voice_event_at": self.voice.created_at or None, "preview_path": str(PREVIEW_PATH) if WRITE_PREVIEW else None, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        try:
            STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_PATH.with_suffix(STATUS_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATUS_PATH)
            self.last_status = now
        except OSError as exc:
            log.debug("Status write failed: %s", exc)

    def run(self) -> None:
        self.mesh = load_mesh_snapshot()
        self.voice = load_voice_event()
        self._refresh_home()
        self.hardware.ensure(force=True)
        if not self.hardware.connected and not ALLOW_MISSING:
            raise RuntimeError(f"OLED2 required but unavailable: {self.hardware.last_error}")
        while True:
            try:
                now = time.monotonic()
                if now - self.last_refresh >= REFRESH_SECONDS:
                    self.mesh = load_mesh_snapshot()
                    self.voice = load_voice_event()
                    self._refresh_home()
                    self.last_refresh = now
                self.hardware.ensure()
                self.current_page = self._page(now)
                image = self._render()
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
    print(json.dumps({"service_ready": True, "hardware_connected": connected, "hardware_optional": ALLOW_MISSING, "hardware_error": hardware.last_error or None, "i2c_bus": I2C_BUS, "i2c_address": I2C_ADDRESS, "meshtastic_state": str(MESH_STATE_PATH), "voice_event": str(VOICE_EVENT_PATH), "database": str(DATABASE_PATH), "mesh_pages": list(MESH_PAGES), "home_pages": list(HOME_PAGES), "preview_path": str(PREVIEW_PATH)}, ensure_ascii=False, indent=2))
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
