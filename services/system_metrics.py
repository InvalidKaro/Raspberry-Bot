from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(slots=True)
class SystemMetrics:
    cpu_percent: float
    temperature: float | None
    ram_percent: float
    ram_used: int
    ram_total: int
    disk_percent: float
    disk_used: int
    disk_total: int
    load_1m: float
    load_5m: float
    load_15m: float
    network_rx: int
    network_tx: int
    bot_memory: int
    uptime_seconds: int
    throttled_flags: int
    cpu_frequency_mhz: float | None
    pihole_active: bool


def _read_temperature() -> float | None:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return int(path.read_text(encoding="utf-8").strip()) / 1000
    except (OSError, ValueError):
        return None


def _read_throttled_flags() -> int:
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2, check=False
        )
        value = result.stdout.strip().split("=", 1)[-1]
        return int(value, 16) if value.startswith("0x") else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _pihole_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "pihole-FTL"], capture_output=True, text=True, timeout=2, check=False
        )
        return result.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _collect_blocking() -> SystemMetrics:
    cpu = psutil.cpu_percent(interval=0.15)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    network = psutil.net_io_counters()
    process_memory = psutil.Process().memory_info().rss
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    frequency = psutil.cpu_freq()
    return SystemMetrics(
        cpu_percent=cpu,
        temperature=_read_temperature(),
        ram_percent=float(memory.percent),
        ram_used=int(memory.used),
        ram_total=int(memory.total),
        disk_percent=float(disk.percent),
        disk_used=int(disk.used),
        disk_total=int(disk.total),
        load_1m=float(load[0]),
        load_5m=float(load[1]),
        load_15m=float(load[2]),
        network_rx=int(network.bytes_recv),
        network_tx=int(network.bytes_sent),
        bot_memory=int(process_memory),
        uptime_seconds=max(int(time.time() - psutil.boot_time()), 0),
        throttled_flags=_read_throttled_flags(),
        cpu_frequency_mhz=float(frequency.current) if frequency else None,
        pihole_active=_pihole_active(),
    )


async def collect_system_metrics() -> SystemMetrics:
    return await asyncio.to_thread(_collect_blocking)


def throttling_labels(flags: int) -> list[str]:
    labels: list[str] = []
    mapping = {
        0: "Undervoltage now",
        1: "Frequency capped now",
        2: "Throttled now",
        3: "Soft temperature limit now",
        16: "Undervoltage occurred",
        17: "Frequency cap occurred",
        18: "Throttling occurred",
        19: "Soft temperature limit occurred",
    }
    for bit, label in mapping.items():
        if flags & (1 << bit):
            labels.append(label)
    return labels
