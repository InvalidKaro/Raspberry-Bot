from __future__ import annotations

import asyncio
import os
import subprocess
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

import psutil

from services.pihole import collect_pihole_stats


@dataclass(slots=True)
class SystemMetrics:
    cpu_percent: float
    cpu_average_30s: float
    cpu_average_5m: float
    sample_interval_seconds: int
    sample_age_seconds: float
    bot_cpu_percent: float
    dashboard_cpu_percent: float | None
    temperature: float | None
    ram_percent: float
    ram_used: int
    ram_total: int
    ram_available: int
    swap_percent: float
    swap_used: int
    swap_total: int
    disk_percent: float
    disk_used: int
    disk_total: int
    load_1m: float
    load_5m: float
    load_15m: float
    network_rx: int
    network_tx: int
    network_rx_rate: float
    network_tx_rate: float
    bot_memory: int
    dashboard_memory: int | None
    uptime_seconds: int
    throttled_flags: int
    cpu_frequency_mhz: float | None
    pihole_active: bool
    pihole_blocking: bool | None


def _read_temperature() -> float | None:
    for path in (
        Path("/sys/class/thermal/thermal_zone0/temp"),
        Path("/sys/class/hwmon/hwmon0/temp1_input"),
    ):
        try:
            value = float(path.read_text(encoding="utf-8").strip())
            if value > 1000:
                value /= 1000.0
            if -20 <= value <= 130:
                return value
        except (OSError, ValueError):
            continue
    return None


def _read_throttled_flags() -> int:
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        value = result.stdout.strip().split("=", 1)[-1]
        return int(value, 16) if value.startswith("0x") else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _find_dashboard_process() -> psutil.Process | None:
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "dashboard.app" in cmdline or "raspberry-dashboard" in cmdline:
                return psutil.Process(int(proc.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
            continue
    return None


def _load_average() -> tuple[float, float, float]:
    try:
        values = os.getloadavg()
        return float(values[0]), float(values[1]), float(values[2])
    except (AttributeError, OSError):
        return 0.0, 0.0, 0.0


class SystemMetricsSampler:
    """Low-overhead system sampler.

    CPU percentages are calculated between background samples instead of during a
    Discord command. This prevents the monitoring request itself from inflating
    the number shown to the user.
    """

    def __init__(self, interval_seconds: int = 15) -> None:
        self.interval_seconds = max(10, min(30, int(interval_seconds)))
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._latest: SystemMetrics | None = None
        self._history: deque[float] = deque(maxlen=max(20, int(300 / self.interval_seconds) + 2))
        self._self_process = psutil.Process()
        self._dashboard_process: psutil.Process | None = None
        self._previous_net: tuple[float, int, int] | None = None
        self._throttled_flags = 0
        self._sample_counter = 0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # Prime psutil counters. The first non-blocking cpu_percent call is not a
        # useful measurement because there is no previous sample yet.
        psutil.cpu_percent(interval=None)
        self._self_process.cpu_percent(interval=None)
        self._dashboard_process = _find_dashboard_process()
        if self._dashboard_process is not None:
            try:
                self._dashboard_process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._dashboard_process = None
        net = psutil.net_io_counters()
        self._previous_net = (time.monotonic(), int(net.bytes_recv), int(net.bytes_sent))
        await self._sample(initial=True)
        self._task = asyncio.create_task(self._run(), name="system-metrics-sampler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            await self._sample()

    async def _sample(self, *, initial: bool = False) -> None:
        async with self._lock:
            now = time.monotonic()
            cpu = 0.0 if initial else float(psutil.cpu_percent(interval=None))
            if not initial:
                self._history.append(cpu)

            try:
                bot_cpu = 0.0 if initial else float(self._self_process.cpu_percent(interval=None))
                bot_memory = int(self._self_process.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                bot_cpu = 0.0
                bot_memory = 0

            if self._dashboard_process is None or not self._dashboard_process.is_running():
                self._dashboard_process = _find_dashboard_process()
                if self._dashboard_process is not None:
                    try:
                        self._dashboard_process.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        self._dashboard_process = None

            dashboard_cpu: float | None = None
            dashboard_memory: int | None = None
            if self._dashboard_process is not None:
                try:
                    dashboard_cpu = 0.0 if initial else float(self._dashboard_process.cpu_percent(interval=None))
                    dashboard_memory = int(self._dashboard_process.memory_info().rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    self._dashboard_process = None

            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_usage("/")
            network = psutil.net_io_counters()
            load_1m, load_5m, load_15m = _load_average()
            frequency = psutil.cpu_freq()

            rx_rate = tx_rate = 0.0
            if self._previous_net is not None:
                previous_at, previous_rx, previous_tx = self._previous_net
                elapsed = max(now - previous_at, 0.001)
                rx_rate = max((int(network.bytes_recv) - previous_rx) / elapsed, 0.0)
                tx_rate = max((int(network.bytes_sent) - previous_tx) / elapsed, 0.0)
            self._previous_net = (now, int(network.bytes_recv), int(network.bytes_sent))

            self._sample_counter += 1
            # vcgencmd launches a process, so refresh it only about once a minute.
            if initial or self._sample_counter % max(1, round(60 / self.interval_seconds)) == 0:
                self._throttled_flags = await asyncio.to_thread(_read_throttled_flags)

            samples_30s = max(1, round(30 / self.interval_seconds))
            history = list(self._history)
            avg_30 = sum(history[-samples_30s:]) / len(history[-samples_30s:]) if history else cpu
            avg_5m = sum(history) / len(history) if history else cpu

            self._latest = SystemMetrics(
                cpu_percent=cpu,
                cpu_average_30s=avg_30,
                cpu_average_5m=avg_5m,
                sample_interval_seconds=self.interval_seconds,
                sample_age_seconds=0.0,
                bot_cpu_percent=bot_cpu,
                dashboard_cpu_percent=dashboard_cpu,
                temperature=_read_temperature(),
                ram_percent=float(memory.percent),
                ram_used=int(memory.used),
                ram_total=int(memory.total),
                ram_available=int(memory.available),
                swap_percent=float(swap.percent),
                swap_used=int(swap.used),
                swap_total=int(swap.total),
                disk_percent=float(disk.percent),
                disk_used=int(disk.used),
                disk_total=int(disk.total),
                load_1m=load_1m,
                load_5m=load_5m,
                load_15m=load_15m,
                network_rx=int(network.bytes_recv),
                network_tx=int(network.bytes_sent),
                network_rx_rate=rx_rate,
                network_tx_rate=tx_rate,
                bot_memory=bot_memory,
                dashboard_memory=dashboard_memory,
                uptime_seconds=max(int(time.time() - psutil.boot_time()), 0),
                throttled_flags=self._throttled_flags,
                cpu_frequency_mhz=float(frequency.current) if frequency else None,
                pihole_active=False,
                pihole_blocking=None,
            )
            self._latest_at = now

    async def get(self) -> SystemMetrics:
        if self._task is None:
            await self.start()
        async with self._lock:
            if self._latest is None:
                raise RuntimeError("System metrics sampler has no sample yet")
            age = max(time.monotonic() - getattr(self, "_latest_at", time.monotonic()), 0.0)
            return replace(self._latest, sample_age_seconds=age)


def _collect_blocking() -> SystemMetrics:
    # Fallback path only; normal bot operation uses SystemMetricsSampler.
    cpu = float(psutil.cpu_percent(interval=1.0))
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    network = psutil.net_io_counters()
    process = psutil.Process()
    load_1m, load_5m, load_15m = _load_average()
    frequency = psutil.cpu_freq()
    dashboard = _find_dashboard_process()
    return SystemMetrics(
        cpu_percent=cpu,
        cpu_average_30s=cpu,
        cpu_average_5m=cpu,
        sample_interval_seconds=1,
        sample_age_seconds=0.0,
        bot_cpu_percent=0.0,
        dashboard_cpu_percent=None,
        temperature=_read_temperature(),
        ram_percent=float(memory.percent),
        ram_used=int(memory.used),
        ram_total=int(memory.total),
        ram_available=int(memory.available),
        swap_percent=float(swap.percent),
        swap_used=int(swap.used),
        swap_total=int(swap.total),
        disk_percent=float(disk.percent),
        disk_used=int(disk.used),
        disk_total=int(disk.total),
        load_1m=load_1m,
        load_5m=load_5m,
        load_15m=load_15m,
        network_rx=int(network.bytes_recv),
        network_tx=int(network.bytes_sent),
        network_rx_rate=0.0,
        network_tx_rate=0.0,
        bot_memory=int(process.memory_info().rss),
        dashboard_memory=(int(dashboard.memory_info().rss) if dashboard is not None else None),
        uptime_seconds=max(int(time.time() - psutil.boot_time()), 0),
        throttled_flags=_read_throttled_flags(),
        cpu_frequency_mhz=float(frequency.current) if frequency else None,
        pihole_active=False,
        pihole_blocking=None,
    )


async def collect_system_metrics(bot: object | None = None) -> SystemMetrics:
    sampler = getattr(bot, "system_metrics", None) if bot is not None else None
    if isinstance(sampler, SystemMetricsSampler):
        metrics = await sampler.get()
    else:
        metrics = await asyncio.to_thread(_collect_blocking)

    pihole = await collect_pihole_stats()
    return replace(
        metrics,
        pihole_active=pihole.active,
        pihole_blocking=pihole.blocking,
    )


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
