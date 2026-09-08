from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

REPO_ROOT = Path(os.getenv("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot"))
BLACKBOX_DB_PATH = Path(os.getenv("HOMEPI_BLACKBOX_DB", str(REPO_ROOT / "data" / "homepi_blackbox.sqlite3")))
STATE_PATH = Path(os.getenv("HOMEPI_INTELLIGENCE_STATE", str(REPO_ROOT / "data" / "homepi_intelligence.json")))
BOT_DB_PATH = Path(
    os.getenv("BOT_DATABASE_PATH")
    or os.getenv("DATABASE_PATH")
    or str(REPO_ROOT / "data" / "bot.sqlite3")
)
MESH_STATE_PATH = Path(os.getenv("MESHTASTIC_STATE_PATH", str(REPO_ROOT / "data" / "meshtastic_state.json")))
POLL_SECONDS = max(5, int(os.getenv("HOMEPI_POLL_SECONDS", "15")))
SAMPLE_SECONDS = max(30, int(os.getenv("HOMEPI_SAMPLE_SECONDS", "300")))
WARNING_SECONDS = max(60, int(os.getenv("HOMEPI_WARNING_SECONDS", "300")))
RETENTION_DAYS = max(30, int(os.getenv("HOMEPI_BLACKBOX_RETENTION_DAYS", "365")))
WARNING_ARS = os.getenv("HOMEPI_WARNING_ARS", "").strip()
WARNING_LABEL = os.getenv("HOMEPI_WARNING_LABEL", "").strip()
WARNING_ALERT_LEVEL = max(1, min(4, int(os.getenv("HOMEPI_WARNING_ALERT_LEVEL", "3"))))
WATCH_SERVICES = tuple(
    item.strip()
    for item in os.getenv(
        "HOMEPI_WATCH_SERVICES",
        "raspberry-bot.service,raspberry-dashboard.service,raspberry-display.service,"
        "raspberry-display2.service,raspberry-meshtastic.service,pihole-FTL.service,"
        "tailscaled.service,ssh.service",
    ).split(",")
    if item.strip()
)

_SEVERITY_RANK = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
_SEVERITY_LABEL = {1: "Hinweis", 2: "Warnung", 3: "Ernst", 4: "Extrem"}
_MAC_RE = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")


def _now() -> float:
    return time.time()


def _iso(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp or _now(), tz=timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _db() -> sqlite3.Connection:
    BLACKBOX_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BLACKBOX_DB_PATH, timeout=3.0)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _db() as con:
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_homepi_events_time ON events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_homepi_events_kind_time ON events(kind,created_at DESC);
            CREATE TABLE IF NOT EXISTS samples(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                temperature REAL,
                cpu_percent REAL NOT NULL,
                ram_percent REAL NOT NULL,
                disk_percent REAL NOT NULL,
                internet_online INTEGER NOT NULL,
                score INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_homepi_samples_time ON samples(created_at DESC);
            CREATE TABLE IF NOT EXISTS known_devices(
                mac TEXT PRIMARY KEY,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                ip TEXT NOT NULL DEFAULT '',
                iface TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def _meta_get(key: str, default: str = "") -> str:
    try:
        with _db() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else default
    except sqlite3.Error:
        return default


def _meta_set(key: str, value: Any) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def log_event(kind: str, title: str, detail: str = "", severity: str = "info", *, timestamp: float | None = None) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO events(created_at,kind,severity,title,detail) VALUES(?,?,?,?,?)",
            (timestamp or _now(), kind, severity, title[:160], detail[:1200]),
        )


def recent_events(hours: float = 24.0, limit: int = 20) -> list[dict[str, Any]]:
    cutoff = _now() - max(0.1, hours) * 3600
    try:
        with _db() as con:
            rows = con.execute(
                "SELECT id,created_at,kind,severity,title,detail FROM events WHERE created_at>=? ORDER BY id DESC LIMIT ?",
                (cutoff, max(1, min(200, limit))),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def blackbox_summary(hours: float = 24.0) -> dict[str, Any]:
    cutoff = _now() - max(0.1, hours) * 3600
    result: dict[str, Any] = {
        "hours": hours,
        "events": 0,
        "internet_outages": 0,
        "service_crashes": 0,
        "bot_errors": 0,
        "new_devices": 0,
        "reboots": 0,
        "max_temperature": None,
        "latest": [],
    }
    try:
        with _db() as con:
            rows = con.execute(
                "SELECT kind,COUNT(*) count FROM events WHERE created_at>=? GROUP BY kind",
                (cutoff,),
            ).fetchall()
            counts = {str(row["kind"]): int(row["count"] or 0) for row in rows}
            result["events"] = sum(counts.values())
            result["internet_outages"] = counts.get("internet_down", 0)
            result["service_crashes"] = counts.get("service_down", 0)
            result["bot_errors"] = counts.get("bot_error", 0)
            result["new_devices"] = counts.get("new_device", 0)
            result["reboots"] = counts.get("reboot", 0)
            row = con.execute("SELECT MAX(temperature) max_temp FROM samples WHERE created_at>=?", (cutoff,)).fetchone()
            result["max_temperature"] = float(row["max_temp"]) if row and row["max_temp"] is not None else None
            latest = con.execute(
                "SELECT created_at,kind,severity,title,detail FROM events WHERE created_at>=? ORDER BY id DESC LIMIT 5",
                (cutoff,),
            ).fetchall()
            result["latest"] = [dict(row) for row in latest]
    except sqlite3.Error:
        pass
    return result


def _temperature() -> float | None:
    for path in (Path("/sys/class/thermal/thermal_zone0/temp"), Path("/sys/class/hwmon/hwmon0/temp1_input")):
        try:
            value = float(path.read_text(encoding="utf-8").strip())
            return value / 1000.0 if value > 1000 else value
        except (OSError, ValueError):
            continue
    return None


def _probe_internet() -> dict[str, Any]:
    started = time.monotonic()
    tcp_ok = False
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=1.5):
            tcp_ok = True
    except OSError:
        pass
    latency_ms = (time.monotonic() - started) * 1000 if tcp_ok else None
    dns_started = time.monotonic()
    dns_ok = False
    try:
        socket.getaddrinfo("example.com", 443, type=socket.SOCK_STREAM)
        dns_ok = True
    except OSError:
        pass
    dns_ms = (time.monotonic() - dns_started) * 1000 if dns_ok else None
    return {"online": bool(tcp_ok or dns_ok), "tcp_ok": tcp_ok, "dns_ok": dns_ok, "latency_ms": latency_ms, "dns_ms": dns_ms}


def _service_state(name: str) -> str:
    try:
        proc = subprocess.run(
            ["systemctl", "show", name, "--property=LoadState", "--property=ActiveState"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    values: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if values.get("LoadState") in {"not-found", "masked"}:
        return "not-found" if values.get("LoadState") == "not-found" else "masked"
    return values.get("ActiveState") or "unknown"


def _services() -> dict[str, str]:
    return {name: _service_state(name) for name in WATCH_SERVICES}


def _mesh_connected() -> bool:
    return bool(_read_json(MESH_STATE_PATH).get("connected"))


def _neighbors() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    try:
        proc = subprocess.run(["ip", "-j", "neigh", "show"], capture_output=True, text=True, timeout=2, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            raw = json.loads(proc.stdout)
            if isinstance(raw, list):
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    mac = str(entry.get("lladdr") or "").lower()
                    state = str(entry.get("state") or "").upper()
                    if not _MAC_RE.fullmatch(mac) or state in {"FAILED", "INCOMPLETE"}:
                        continue
                    items.append({"mac": mac, "ip": str(entry.get("dst") or ""), "iface": str(entry.get("dev") or "")})
                return items
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
        pass
    try:
        proc = subprocess.run(["ip", "neigh", "show"], capture_output=True, text=True, timeout=2, check=False)
        for line in proc.stdout.splitlines():
            match = _MAC_RE.search(line)
            if not match or "FAILED" in line or "INCOMPLETE" in line:
                continue
            parts = line.split()
            ip = parts[0] if parts else ""
            iface = parts[parts.index("dev") + 1] if "dev" in parts and parts.index("dev") + 1 < len(parts) else ""
            items.append({"mac": match.group(0).lower(), "ip": ip, "iface": iface})
    except (OSError, subprocess.SubprocessError):
        pass
    return items


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return f"boot-{int(psutil.boot_time())}"


def _parse_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_nina_payload(payload: Any, *, now: float | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    current = now or _now()
    warnings: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        outer_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        data = outer_payload.get("data") if isinstance(outer_payload.get("data"), dict) else {}
        if data.get("valid") is False:
            continue
        msg_type = str(data.get("msgType") or "Alert")
        if msg_type.lower() == "cancel":
            continue
        expires_at = _parse_timestamp(item.get("expires"))
        if expires_at and expires_at < current:
            continue
        i18n = item.get("i18nTitle") if isinstance(item.get("i18nTitle"), dict) else {}
        headline = str(data.get("headline") or i18n.get("de") or "Amtliche Warnung").strip()
        severity = str(data.get("severity") or "Minor").strip()
        rank = _SEVERITY_RANK.get(severity.lower(), 1)
        warnings.append(
            {
                "id": str(item.get("id") or outer_payload.get("id") or ""),
                "headline": headline,
                "provider": str(data.get("provider") or "NINA").strip(),
                "severity": severity,
                "severity_rank": rank,
                "severity_label": _SEVERITY_LABEL.get(rank, "Hinweis"),
                "urgency": str(data.get("urgency") or "").strip(),
                "sent": str(item.get("sent") or ""),
                "onset": str(item.get("onset") or ""),
                "expires": str(item.get("expires") or ""),
            }
        )
    warnings.sort(key=lambda item: (int(item.get("severity_rank") or 0), str(item.get("sent") or "")), reverse=True)
    return warnings


def _fetch_warnings() -> dict[str, Any]:
    if not WARNING_ARS:
        return {
            "configured": False,
            "region": WARNING_LABEL,
            "ars": "",
            "active": 0,
            "items": [],
            "error": "HOMEPI_WARNING_ARS ist nicht gesetzt",
            "alert_threshold_rank": WARNING_ALERT_LEVEL,
            "updated_at": _iso(),
        }
    if not re.fullmatch(r"\d{12}", WARNING_ARS):
        return {
            "configured": False,
            "region": WARNING_LABEL,
            "ars": WARNING_ARS,
            "active": 0,
            "items": [],
            "error": "HOMEPI_WARNING_ARS muss 12 Ziffern haben",
            "alert_threshold_rank": WARNING_ALERT_LEVEL,
            "updated_at": _iso(),
        }
    url = f"https://warnung.bund.de/api31/dashboard/{WARNING_ARS}.json"
    request = urllib.request.Request(url, headers={"User-Agent": "HomePi-Warnzentrale/1.0"})
    with urllib.request.urlopen(request, timeout=8.0) as response:
        raw = json.loads(response.read(2_000_000).decode("utf-8"))
    items = parse_nina_payload(raw)
    return {
        "configured": True,
        "region": WARNING_LABEL,
        "ars": WARNING_ARS,
        "active": len(items),
        "items": items[:20],
        "error": "",
        "alert_threshold_rank": WARNING_ALERT_LEVEL,
        "updated_at": _iso(),
    }


def calculate_score(metrics: dict[str, Any], services: dict[str, str], blackbox: dict[str, Any]) -> dict[str, Any]:
    cpu = float(metrics.get("cpu_percent") or 0.0)
    ram = float(metrics.get("ram_percent") or 0.0)
    disk = float(metrics.get("disk_percent") or 0.0)
    temp = metrics.get("temperature")
    temp_value = float(temp) if temp is not None else None

    system = 100.0
    system -= max(0.0, cpu - 75.0) * 0.6
    system -= max(0.0, ram - 75.0) * 0.8
    system -= max(0.0, disk - 80.0) * 1.5
    if temp_value is not None:
        system -= max(0.0, temp_value - 60.0) * 2.0
    system = max(0.0, min(100.0, system))

    online = bool(metrics.get("internet_online"))
    network = 100.0 if online else 15.0
    latency = metrics.get("latency_ms")
    if online and latency is not None:
        network -= max(0.0, float(latency) - 80.0) * 0.25
    if online and not bool(metrics.get("dns_ok", True)):
        network -= 35.0
    network = max(0.0, min(100.0, network))

    installed = [state for state in services.values() if state != "not-found"]
    active = sum(state == "active" for state in installed)
    service_score = 100.0 if not installed else 100.0 * active / len(installed)

    stability = 100.0
    stability -= int(blackbox.get("internet_outages") or 0) * 15.0
    stability -= int(blackbox.get("service_crashes") or 0) * 12.0
    stability -= int(blackbox.get("bot_errors") or 0) * 8.0
    stability -= int(blackbox.get("reboots") or 0) * 5.0
    stability = max(0.0, min(100.0, stability))

    overall = round(system * 0.25 + network * 0.25 + service_score * 0.30 + stability * 0.20)
    status = "GREEN" if overall >= 90 else "YELLOW" if overall >= 75 else "ORANGE" if overall >= 55 else "RED"
    return {
        "overall": int(overall),
        "status": status,
        "system": round(system),
        "network": round(network),
        "services": round(service_score),
        "stability": round(stability),
        "services_active": active,
        "services_total": len(installed),
    }


def read_state() -> dict[str, Any]:
    return _read_json(STATE_PATH)


def _event_transition(key: str, current: str, *, down_kind: str, up_kind: str, down_title: str, up_title: str, detail: str = "") -> None:
    previous = _meta_get(key)
    if previous and previous != current:
        if current in {"active", "online", "connected"}:
            log_event(up_kind, up_title, detail, "info")
        elif previous in {"active", "online", "connected"}:
            log_event(down_kind, down_title, detail, "warning")
    _meta_set(key, current)


def _record_boot() -> None:
    current = _boot_id()
    previous = _meta_get("boot_id")
    if previous != current:
        boot_time = psutil.boot_time()
        log_event("reboot", "HomePi gestartet", f"Bootzeit {_iso(boot_time)}", "info", timestamp=max(boot_time, _now() - 300))
        _meta_set("boot_id", current)


def _record_internet(internet: dict[str, Any]) -> None:
    current = "online" if internet.get("online") else "offline"
    previous = _meta_get("internet_state")
    now = _now()
    if previous and previous != current:
        if current == "offline":
            _meta_set("internet_down_since", now)
            log_event("internet_down", "Internet ausgefallen", "TCP und DNS nicht erreichbar", "warning")
        else:
            started = float(_meta_get("internet_down_since", "0") or 0)
            duration = max(0, int(now - started)) if started else 0
            log_event("internet_up", "Internet wieder online", f"Ausfall {duration} Sekunden" if duration else "Verbindung wiederhergestellt", "info")
            _meta_set("internet_down_since", "0")
    _meta_set("internet_state", current)


def _record_services(states: dict[str, str]) -> None:
    for name, current in states.items():
        key = f"service:{name}"
        previous = _meta_get(key)
        if previous and previous != current:
            if current == "active":
                log_event("service_up", f"{name} wieder aktiv", f"vorher {previous}", "info")
            elif previous == "active" and current not in {"not-found", "unknown"}:
                severity = "critical" if current == "failed" else "warning"
                log_event("service_down", f"{name} nicht aktiv", f"Status {current}", severity)
        _meta_set(key, current)


def _record_mesh(connected: bool) -> None:
    current = "connected" if connected else "disconnected"
    previous = _meta_get("mesh_state")
    if previous and previous != current:
        if connected:
            log_event("mesh_connected", "Meshtastic verbunden", "USB/Radio-Verbindung aktiv", "info")
        else:
            log_event("mesh_disconnected", "Meshtastic getrennt", "Radio-Verbindung verloren", "warning")
    _meta_set("mesh_state", current)


def _record_neighbors(items: list[dict[str, str]]) -> None:
    now = _now()
    try:
        with _db() as con:
            existing_count = int(con.execute("SELECT COUNT(*) count FROM known_devices").fetchone()["count"] or 0)
            baseline = existing_count == 0 and not _meta_get("neighbors_initialized")
            for item in items:
                mac = item["mac"]
                row = con.execute("SELECT mac FROM known_devices WHERE mac=?", (mac,)).fetchone()
                if row:
                    con.execute("UPDATE known_devices SET last_seen=?,ip=?,iface=? WHERE mac=?", (now, item["ip"], item["iface"], mac))
                else:
                    con.execute(
                        "INSERT INTO known_devices(mac,first_seen,last_seen,ip,iface) VALUES(?,?,?,?,?)",
                        (mac, now, now, item["ip"], item["iface"]),
                    )
                    if not baseline:
                        con.execute(
                            "INSERT INTO events(created_at,kind,severity,title,detail) VALUES(?,?,?,?,?)",
                            (now, "new_device", "info", "Neues Netzwerkgerät", f"{item['ip']} {mac} über {item['iface']}".strip()),
                        )
            if baseline:
                con.execute(
                    "INSERT INTO meta(key,value) VALUES('neighbors_initialized','1') ON CONFLICT(key) DO UPDATE SET value='1'"
                )
    except sqlite3.Error:
        pass


def _record_bot_errors() -> None:
    if not BOT_DB_PATH.exists():
        return
    try:
        con = sqlite3.connect(f"file:{BOT_DB_PATH}?mode=ro", uri=True, timeout=1.5)
        con.row_factory = sqlite3.Row
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dashboard_error_events'").fetchone()
        if not exists:
            con.close()
            return
        max_row = con.execute("SELECT MAX(id) max_id FROM dashboard_error_events").fetchone()
        max_id = int(max_row["max_id"] or 0) if max_row else 0
        previous = int(_meta_get("bot_error_id", "0") or 0)
        if previous == 0:
            _meta_set("bot_error_id", max_id)
            con.close()
            return
        rows = con.execute(
            "SELECT id,source,error_type,message,command_name FROM dashboard_error_events WHERE id>? ORDER BY id ASC LIMIT 50",
            (previous,),
        ).fetchall()
        con.close()
        for row in rows:
            label = str(row["error_type"] or "Botfehler")
            command = str(row["command_name"] or "").strip()
            detail = str(row["message"] or "").strip()
            if command:
                detail = f"/{command}: {detail}".strip()
            log_event("bot_error", f"Bot-Fehler: {label}", detail, "warning")
        if max_id > previous:
            _meta_set("bot_error_id", max_id)
    except (sqlite3.Error, OSError, ValueError):
        return


def _record_temperature_state(temp: float | None) -> None:
    if temp is None:
        return
    previous = _meta_get("temperature_state", "normal")
    current = "high" if temp >= 70 else "normal" if temp < 65 else previous
    if previous != current:
        if current == "high":
            log_event("temperature_high", "Temperatur erhöht", f"CPU {temp:.1f} °C", "warning")
        else:
            log_event("temperature_normal", "Temperatur normalisiert", f"CPU {temp:.1f} °C", "info")
    _meta_set("temperature_state", current)


def _record_warning_changes(warnings: dict[str, Any]) -> None:
    if not warnings.get("configured") or warnings.get("error"):
        return
    items = warnings.get("items") if isinstance(warnings.get("items"), list) else []
    current_ids = [str(item.get("id") or "") for item in items if isinstance(item, dict) and item.get("id")]
    previous_raw = _meta_get("warning_ids", "[]")
    try:
        previous_ids = set(json.loads(previous_raw))
    except (ValueError, TypeError, json.JSONDecodeError):
        previous_ids = set()
    current_set = set(current_ids)
    if previous_ids:
        for item in items:
            if not isinstance(item, dict) or str(item.get("id") or "") in previous_ids:
                continue
            rank = int(item.get("severity_rank") or 1)
            severity = "critical" if rank >= 4 else "warning" if rank >= 2 else "info"
            log_event("warning", "Neue amtliche Warnung", str(item.get("headline") or "Warnung"), severity)
        for removed in previous_ids - current_set:
            log_event("warning_clear", "Warnung nicht mehr aktiv", removed, "info")
    _meta_set("warning_ids", json.dumps(current_ids))


def _cleanup() -> None:
    cutoff = _now() - RETENTION_DAYS * 86400
    try:
        with _db() as con:
            con.execute("DELETE FROM samples WHERE created_at<?", (cutoff,))
    except sqlite3.Error:
        pass


class Collector:
    def __init__(self) -> None:
        self.last_sample = 0.0
        self.last_warning = 0.0
        previous = read_state().get("warnings")
        self.warnings: dict[str, Any] = previous if isinstance(previous, dict) else {
            "configured": bool(WARNING_ARS),
            "region": WARNING_LABEL,
            "ars": WARNING_ARS,
            "active": 0,
            "items": [],
            "error": "",
            "alert_threshold_rank": WARNING_ALERT_LEVEL,
        }

    def _warnings(self, now: float) -> dict[str, Any]:
        if now - self.last_warning < WARNING_SECONDS:
            return self.warnings
        self.last_warning = now
        try:
            self.warnings = _fetch_warnings()
            _record_warning_changes(self.warnings)
        except Exception as exc:
            cached = dict(self.warnings)
            cached["error"] = f"{type(exc).__name__}: {exc}"
            cached["stale"] = True
            cached["alert_threshold_rank"] = WARNING_ALERT_LEVEL
            self.warnings = cached
        return self.warnings

    def collect(self) -> dict[str, Any]:
        now = _now()
        temp = _temperature()
        internet = _probe_internet()
        services = _services()
        mesh = _mesh_connected()

        _record_boot()
        _record_internet(internet)
        _record_services(services)
        _record_mesh(mesh)
        _record_neighbors(_neighbors())
        _record_bot_errors()
        _record_temperature_state(temp)

        metrics = {
            "temperature": temp,
            "cpu_percent": float(psutil.cpu_percent(interval=None)),
            "ram_percent": float(psutil.virtual_memory().percent),
            "disk_percent": float(psutil.disk_usage("/").percent),
            "internet_online": bool(internet.get("online")),
            "latency_ms": internet.get("latency_ms"),
            "dns_ms": internet.get("dns_ms"),
            "dns_ok": bool(internet.get("dns_ok")),
        }
        blackbox = blackbox_summary(24)
        score = calculate_score(metrics, services, blackbox)
        warnings = self._warnings(now)

        if now - self.last_sample >= SAMPLE_SECONDS:
            with _db() as con:
                con.execute(
                    "INSERT INTO samples(created_at,temperature,cpu_percent,ram_percent,disk_percent,internet_online,score) VALUES(?,?,?,?,?,?,?)",
                    (
                        now,
                        temp,
                        metrics["cpu_percent"],
                        metrics["ram_percent"],
                        metrics["disk_percent"],
                        1 if metrics["internet_online"] else 0,
                        score["overall"],
                    ),
                )
            self.last_sample = now
            _cleanup()
            blackbox = blackbox_summary(24)
            score = calculate_score(metrics, services, blackbox)

        state = {
            "updated_at": _iso(now),
            "updated_at_epoch": now,
            "boot_id": _boot_id(),
            "metrics": metrics,
            "services": services,
            "mesh_connected": mesh,
            "blackbox": blackbox,
            "score": score,
            "warnings": warnings,
        }
        _atomic_json(STATE_PATH, state)
        return state


def voice_blackbox(hours: float = 24.0) -> tuple[str, dict[str, Any]]:
    summary = blackbox_summary(hours)
    max_temp = summary.get("max_temperature")
    if int(summary.get("events") or 0) == 0:
        temp_text = f" Die höchste gemessene Temperatur lag bei {float(max_temp):.1f} Grad.".replace(".", ",") if max_temp is not None else ""
        return f"Blackbox geprüft. In den letzten {int(hours)} Stunden gab es keine protokollierten Auffälligkeiten.{temp_text}", summary
    parts = []
    if summary.get("internet_outages"):
        parts.append(f"{summary['internet_outages']} Internet-Ausfall" + ("" if summary["internet_outages"] == 1 else "e"))
    if summary.get("service_crashes"):
        parts.append(f"{summary['service_crashes']} Dienst-Ausfall" + ("" if summary["service_crashes"] == 1 else "e"))
    if summary.get("bot_errors"):
        parts.append(f"{summary['bot_errors']} Bot-Fehler")
    if summary.get("new_devices"):
        parts.append(f"{summary['new_devices']} neues Netzwerkgerät" + ("" if summary["new_devices"] == 1 else "e"))
    if summary.get("reboots"):
        parts.append(f"{summary['reboots']} Neustart" + ("" if summary["reboots"] == 1 else "s"))
    detail = ", ".join(parts) if parts else f"{summary['events']} Ereignisse"
    latest = summary.get("latest") or []
    latest_text = ""
    if latest:
        latest_text = f" Das letzte Ereignis war: {latest[0].get('title', 'unbekannt')}."
    return f"Blackbox-Auswertung abgeschlossen. In den letzten {int(hours)} Stunden: {detail}.{latest_text}", summary


def voice_score() -> tuple[str, dict[str, Any]]:
    state = read_state()
    score = state.get("score") if isinstance(state.get("score"), dict) else {}
    if not score:
        return "Der Server Score ist noch nicht verfügbar. Die Blackbox muss dafür einmal Daten gesammelt haben.", {}
    status_de = {"GREEN": "Grün", "YELLOW": "Gelb", "ORANGE": "Orange", "RED": "Rot"}.get(str(score.get("status")), str(score.get("status") or "unbekannt"))
    speech = (
        f"Server Score {int(score.get('overall') or 0)} von 100, Status {status_de}. "
        f"System {int(score.get('system') or 0)}, Netzwerk {int(score.get('network') or 0)}, "
        f"Dienste {int(score.get('services') or 0)} und Stabilität {int(score.get('stability') or 0)}."
    )
    return speech, score


def voice_warnings() -> tuple[str, dict[str, Any]]:
    state = read_state()
    warnings = state.get("warnings") if isinstance(state.get("warnings"), dict) else {}
    if not warnings.get("configured"):
        return (
            "Die Warnzentrale ist eingebaut, aber noch keiner Region zugeordnet. Dafür muss einmal der zwölfstellige Regionalschlüssel in HOMEPI_WARNING_ARS gesetzt werden.",
            warnings,
        )
    items = warnings.get("items") if isinstance(warnings.get("items"), list) else []
    if not items:
        stale = " Die letzte Abfrage ist allerdings fehlgeschlagen." if warnings.get("error") else ""
        return f"Warnzentrale geprüft. Für die konfigurierte Region liegen aktuell keine aktiven Warnungen vor.{stale}", warnings
    top = items[0]
    count = len(items)
    provider = str(top.get("provider") or "amtlicher Stelle")
    level = str(top.get("severity_label") or top.get("severity") or "Warnung")
    headline = str(top.get("headline") or "Amtliche Warnung")
    region = f" für {warnings.get('region')}" if warnings.get("region") else ""
    speech = f"Warnzentrale: {count} aktive {'Warnung' if count == 1 else 'Warnungen'}{region}. Höchste Stufe: {level}. {headline}, Quelle {provider}."
    return speech, warnings


def run_daemon() -> None:
    init_db()
    collector = Collector()
    while True:
        try:
            collector.collect()
        except KeyboardInterrupt:
            return
        except Exception as exc:
            try:
                log_event("collector_error", "Blackbox-Collector Fehler", f"{type(exc).__name__}: {exc}", "warning")
            except Exception:
                pass
        time.sleep(POLL_SECONDS)


def check() -> dict[str, Any]:
    init_db()
    state = read_state()
    return {
        "ready": True,
        "blackbox_db": str(BLACKBOX_DB_PATH),
        "state_path": str(STATE_PATH),
        "warning_ars_configured": bool(WARNING_ARS),
        "warning_ars_valid": bool(re.fullmatch(r"\d{12}", WARNING_ARS)) if WARNING_ARS else False,
        "watch_services": list(WATCH_SERVICES),
        "state_available": bool(state),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="HomePi Blackbox, Server Score and Warnzentrale")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check(), ensure_ascii=False, indent=2))
        return
    init_db()
    if args.once:
        print(json.dumps(Collector().collect(), ensure_ascii=False, indent=2))
        return
    run_daemon()


if __name__ == "__main__":
    main()
