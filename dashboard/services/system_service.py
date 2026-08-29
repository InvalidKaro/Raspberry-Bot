from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import psutil

from .commands import run_command


def _temperature_c() -> float | None:
    for path in (Path("/sys/class/thermal/thermal_zone0/temp"), Path("/sys/class/hwmon/hwmon0/temp1_input")):
        try:
            value = float(path.read_text(encoding="utf-8").strip())
            if value > 1000:
                value /= 1000.0
            if -20 <= value <= 130:
                return round(value, 1)
        except (OSError, ValueError):
            continue
    return None


def _uptime_seconds() -> int:
    return max(0, int(time.time() - psutil.boot_time()))


def _load_average() -> list[float]:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (AttributeError, OSError):
        return [0.0, 0.0, 0.0]


async def _service_state(name: str) -> dict:
    result = await run_command([
        "systemctl", "show", name,
        "--property=LoadState,ActiveState,SubState,MainPID,MemoryCurrent,ActiveEnterTimestamp",
        "--no-pager",
    ], timeout=6)
    data = {"name": name, "load": "not-found", "active": "unknown", "sub": "unknown", "pid": 0, "memory_mb": None, "since": ""}
    if not result.ok and not result.stdout:
        return data
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "LoadState": data["load"] = value
        elif key == "ActiveState": data["active"] = value
        elif key == "SubState": data["sub"] = value
        elif key == "MainPID":
            try: data["pid"] = int(value)
            except ValueError: pass
        elif key == "MemoryCurrent":
            try: data["memory_mb"] = round(int(value) / 1024 / 1024, 1)
            except ValueError: pass
        elif key == "ActiveEnterTimestamp": data["since"] = value
    return data


async def _pihole_status() -> dict:
    pihole = shutil.which("pihole")
    if not pihole:
        return {"installed": False, "active": False, "blocking": None}
    result = await run_command([pihole, "status"], timeout=8)
    combined = f"{result.stdout}\n{result.stderr}".lower()
    enabled = "blocking is enabled" in combined or "blocking enabled" in combined
    disabled = "blocking is disabled" in combined or "blocking disabled" in combined
    return {
        "installed": True,
        "active": result.ok and (enabled or "listening" in combined),
        "blocking": True if enabled else False if disabled else None,
        "raw": (result.stdout or result.stderr)[-1200:],
    }


async def _tailscale_status() -> dict:
    tailscale = shutil.which("tailscale")
    if not tailscale:
        return {"installed": False, "online": False, "ip": None, "hostname": None, "peers": []}
    result, ip_result = await asyncio.gather(
        run_command([tailscale, "status", "--json"], timeout=8),
        run_command([tailscale, "ip", "-4"], timeout=5),
    )
    info = {"installed": True, "online": False, "ip": ip_result.stdout or None, "hostname": None, "peers": []}
    if not result.ok:
        return info
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return info
    self_data = data.get("Self") or {}
    info["online"] = bool(self_data.get("Online"))
    info["hostname"] = self_data.get("HostName") or self_data.get("DNSName")
    peers = []
    raw_peers = data.get("Peer") or {}
    if isinstance(raw_peers, dict):
        for peer in raw_peers.values():
            if not isinstance(peer, dict):
                continue
            tailscale_ips = peer.get("TailscaleIPs") or []
            peers.append({
                "name": peer.get("HostName") or peer.get("DNSName") or "peer",
                "ip": tailscale_ips[0] if tailscale_ips else None,
                "online": bool(peer.get("Online")),
                "os": peer.get("OS") or "",
            })
    info["peers"] = sorted(peers, key=lambda x: (not x["online"], str(x["name"]).lower()))[:30]
    return info


def _top_processes(limit: int = 12) -> list[dict]:
    rows = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "cpu_percent"]):
        try:
            info = proc.info
            rss = info["memory_info"].rss if info.get("memory_info") else 0
            rows.append({
                "pid": int(info["pid"]),
                "name": str(info.get("name") or "process")[:60],
                "user": str(info.get("username") or "")[:40],
                "memory_mb": round(rss / 1024 / 1024, 1),
                "cpu_percent": round(float(info.get("cpu_percent") or 0), 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    rows.sort(key=lambda r: (r["memory_mb"], r["cpu_percent"]), reverse=True)
    return rows[: max(1, min(30, limit))]


async def get_status(bot_service: str) -> dict:
    cpu_task = asyncio.to_thread(psutil.cpu_percent, 0.25)
    pihole_task = _pihole_status()
    tailscale_task = _tailscale_status()
    services_task = asyncio.gather(
        _service_state(bot_service),
        _service_state("raspberry-dashboard"),
        _service_state("pihole-FTL"),
        _service_state("tailscaled"),
    )
    processes_task = asyncio.to_thread(_top_processes, 12)
    cpu, pihole, tailscale, services, processes = await asyncio.gather(
        cpu_task, pihole_task, tailscale_task, services_task, processes_task
    )
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    bot_state = next((row for row in services if row["name"] == bot_service), None) or {}
    return {
        "cpu_percent": round(float(cpu), 1),
        "temperature_c": _temperature_c(),
        "memory_percent": round(float(vm.percent), 1),
        "memory_used_mb": round(vm.used / 1024 / 1024),
        "memory_total_mb": round(vm.total / 1024 / 1024),
        "memory_available_mb": round(vm.available / 1024 / 1024),
        "disk_percent": round(float(disk.percent), 1),
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "uptime_seconds": _uptime_seconds(),
        "load_average": _load_average(),
        "network_rx_mb": round(net.bytes_recv / 1024**2, 1),
        "network_tx_mb": round(net.bytes_sent / 1024**2, 1),
        "bot_active": bot_state.get("active") == "active",
        "pihole": pihole,
        "tailscale": tailscale,
        "services": services,
        "processes": processes,
    }


async def bot_action(bot_service: str, action: str) -> dict:
    if action not in {"start", "stop", "restart"}:
        return {"ok": False, "message": "Unsupported bot action."}
    result = await run_command(["sudo", "-n", "systemctl", action, bot_service], timeout=20)
    return {"ok": result.ok, "message": result.stdout or result.stderr or f"{bot_service} {action} completed."}


async def system_power_action(action: str) -> dict:
    if action not in {"reboot", "poweroff"}:
        return {"ok": False, "message": "Unsupported system power action."}
    result = await run_command(["sudo", "-n", "systemctl", action], timeout=8)
    return {"ok": result.ok, "message": result.stdout or result.stderr or f"System {action} requested."}


async def bot_logs(bot_service: str, lines: int) -> dict:
    result = await run_command([
        "journalctl", "-u", bot_service, "-n", str(lines), "--no-pager", "--output=short-iso"
    ], timeout=10)
    return {"ok": result.ok, "logs": result.stdout if result.ok else result.stderr}


async def service_logs(service: str, lines: int = 100) -> dict:
    allowed = {"raspberry-bot", "raspberry-dashboard", "pihole-FTL", "tailscaled"}
    if service not in allowed:
        return {"ok": False, "logs": "Service is not allowed in the dashboard log viewer."}
    result = await run_command([
        "journalctl", "-u", service, "-n", str(max(20, min(300, lines))), "--no-pager", "--output=short-iso"
    ], timeout=10)
    return {"ok": result.ok, "logs": result.stdout if result.ok else result.stderr}
