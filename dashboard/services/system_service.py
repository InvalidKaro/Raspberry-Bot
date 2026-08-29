from __future__ import annotations

import asyncio
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
            pass
    return None


def _uptime_seconds() -> int:
    return max(0, int(time.time() - psutil.boot_time()))


def _load_average() -> list[float]:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (AttributeError, OSError):
        return [0.0, 0.0, 0.0]


async def _service_active(name: str) -> bool:
    result = await run_command(["systemctl", "is-active", "--quiet", name], timeout=5)
    return result.ok


async def _pihole_status() -> dict:
    pihole = shutil.which("pihole")
    if not pihole:
        return {"installed": False, "active": False, "blocking": None}
    result = await run_command([pihole, "status"], timeout=8)
    text = f"{result.stdout}\n{result.stderr}".lower()
    blocking = None
    if "blocking is enabled" in text or "blocking enabled" in text:
        blocking = True
    elif "blocking is disabled" in text or "blocking disabled" in text:
        blocking = False
    return {"installed": True, "active": result.ok, "blocking": blocking}


async def get_status(bot_service: str) -> dict:
    cpu_task = asyncio.to_thread(psutil.cpu_percent, 0.25)
    cpu, bot_active, pihole = await asyncio.gather(cpu_task, _service_active(bot_service), _pihole_status())
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    return {
        "cpu_percent": round(float(cpu), 1),
        "temperature_c": _temperature_c(),
        "memory_percent": round(float(vm.percent), 1),
        "memory_used_mb": round(vm.used / 1024 / 1024),
        "memory_total_mb": round(vm.total / 1024 / 1024),
        "disk_percent": round(float(disk.percent), 1),
        "disk_used_gb": round(disk.used / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "uptime_seconds": _uptime_seconds(),
        "load_average": _load_average(),
        "network_rx_mb": round(net.bytes_recv / 1024**2, 1),
        "network_tx_mb": round(net.bytes_sent / 1024**2, 1),
        "bot_active": bool(bot_active),
        "pihole": pihole,
    }


async def bot_action(bot_service: str, action: str) -> dict:
    if action not in {"start", "stop", "restart"}:
        return {"ok": False, "message": "Unsupported bot action."}
    result = await run_command(["sudo", "-n", "systemctl", action, bot_service], timeout=20)
    return {"ok": result.ok, "message": result.stdout or result.stderr or f"{bot_service} {action} completed."}


async def bot_logs(bot_service: str, lines: int) -> dict:
    result = await run_command([
        "journalctl", "-u", bot_service, "-n", str(lines), "--no-pager", "--output=short-iso"
    ], timeout=10)
    return {"ok": result.ok, "logs": result.stdout if result.ok else result.stderr}
