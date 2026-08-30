from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections import deque
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
        if key == "LoadState":
            data["load"] = value
        elif key == "ActiveState":
            data["active"] = value
        elif key == "SubState":
            data["sub"] = value
        elif key == "MainPID":
            try:
                data["pid"] = int(value)
            except ValueError:
                pass
        elif key == "MemoryCurrent":
            try:
                data["memory_mb"] = round(int(value) / 1024 / 1024, 1)
            except ValueError:
                pass
        elif key == "ActiveEnterTimestamp":
            data["since"] = value
    return data


async def _service_process(name: str) -> psutil.Process | None:
    state = await _service_state(name)
    pid = int(state.get("pid") or 0)
    if pid <= 0:
        return None
    try:
        return psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


class DashboardSystemSampler:
    """Background sampler so HTTP requests do not measure their own CPU spike."""

    def __init__(self, bot_service: str, interval_seconds: int = 15) -> None:
        self.bot_service = bot_service
        self.interval_seconds = max(10, min(30, int(interval_seconds)))
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._latest: dict | None = None
        self._latest_at = 0.0
        self._history: deque[float] = deque(maxlen=max(20, int(300 / self.interval_seconds) + 2))
        self._dashboard_process = psutil.Process()
        self._bot_process: psutil.Process | None = None
        self._previous_net: tuple[float, int, int] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        psutil.cpu_percent(interval=None)
        self._dashboard_process.cpu_percent(interval=None)
        self._bot_process = await _service_process(self.bot_service)
        if self._bot_process is not None:
            try:
                self._bot_process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._bot_process = None
        net = psutil.net_io_counters()
        self._previous_net = (time.monotonic(), int(net.bytes_recv), int(net.bytes_sent))
        await self._sample(initial=True)
        self._task = asyncio.create_task(self._run(), name="dashboard-system-sampler")

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

            dashboard_cpu = 0.0
            dashboard_memory_mb = 0.0
            try:
                dashboard_cpu = 0.0 if initial else float(self._dashboard_process.cpu_percent(interval=None))
                dashboard_memory_mb = round(self._dashboard_process.memory_info().rss / 1024 / 1024, 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                pass

            if self._bot_process is None or not self._bot_process.is_running():
                self._bot_process = await _service_process(self.bot_service)
                if self._bot_process is not None:
                    try:
                        self._bot_process.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        self._bot_process = None

            bot_cpu: float | None = None
            bot_memory_mb: float | None = None
            if self._bot_process is not None:
                try:
                    bot_cpu = 0.0 if initial else float(self._bot_process.cpu_percent(interval=None))
                    bot_memory_mb = round(self._bot_process.memory_info().rss / 1024 / 1024, 1)
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    self._bot_process = None

            net = psutil.net_io_counters()
            rx_rate = tx_rate = 0.0
            if self._previous_net is not None:
                previous_at, previous_rx, previous_tx = self._previous_net
                elapsed = max(now - previous_at, 0.001)
                rx_rate = max((int(net.bytes_recv) - previous_rx) / elapsed, 0.0)
                tx_rate = max((int(net.bytes_sent) - previous_tx) / elapsed, 0.0)
            self._previous_net = (now, int(net.bytes_recv), int(net.bytes_sent))

            history = list(self._history)
            count_30 = max(1, round(30 / self.interval_seconds))
            recent = history[-count_30:]
            avg_30 = sum(recent) / len(recent) if recent else cpu
            avg_5m = sum(history) / len(history) if history else cpu

            self._latest = {
                "cpu_percent": round(cpu, 1),
                "cpu_average_30s": round(avg_30, 1),
                "cpu_average_5m": round(avg_5m, 1),
                "sample_interval_seconds": self.interval_seconds,
                "dashboard_cpu_percent": round(dashboard_cpu, 1),
                "dashboard_memory_mb": dashboard_memory_mb,
                "bot_cpu_percent": round(bot_cpu, 1) if bot_cpu is not None else None,
                "bot_memory_mb": bot_memory_mb,
                "network_rx_rate_bps": round(rx_rate, 1),
                "network_tx_rate_bps": round(tx_rate, 1),
            }
            self._latest_at = now

    async def snapshot(self) -> dict:
        if self._task is None:
            await self.start()
        async with self._lock:
            data = dict(self._latest or {})
            data["sample_age_seconds"] = round(max(time.monotonic() - self._latest_at, 0.0), 1)
            return data


def _extract_json(text: str) -> dict:
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def _version_value(text: str, keyword: str) -> str | None:
    for line in text.splitlines():
        if keyword.lower() not in line.lower():
            continue
        match = re.search(r"\bv?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][\w.-]+)?)\b", line)
        if match:
            return match.group(1)
    return None


async def _pihole_status() -> dict:
    pihole = shutil.which("pihole")
    if not pihole:
        return {"installed": False, "active": False, "blocking": None}

    status_result, stats_result, blocking_result, version_result = await asyncio.gather(
        run_command([pihole, "status"], timeout=8),
        run_command([pihole, "api", "stats/summary"], timeout=8),
        run_command([pihole, "api", "dns/blocking"], timeout=6),
        run_command([pihole, "-v"], timeout=6),
    )
    combined = f"{status_result.stdout}\n{status_result.stderr}".lower()
    enabled = "blocking is enabled" in combined or "blocking enabled" in combined
    disabled = "blocking is disabled" in combined or "blocking disabled" in combined

    summary = _extract_json(stats_result.stdout) if stats_result.ok else {}
    blocking_data = _extract_json(blocking_result.stdout) if blocking_result.ok else {}
    blocking_value = blocking_data.get("blocking")
    if isinstance(blocking_value, bool):
        blocking = blocking_value
    elif isinstance(blocking_value, str):
        blocking = blocking_value.lower() in {"enabled", "enable", "true", "on"}
    else:
        blocking = True if enabled else False if disabled else None

    queries = summary.get("queries") if isinstance(summary.get("queries"), dict) else {}
    clients = summary.get("clients") if isinstance(summary.get("clients"), dict) else {}
    gravity = summary.get("gravity") if isinstance(summary.get("gravity"), dict) else {}

    return {
        "installed": True,
        "active": status_result.ok and (enabled or disabled or "listening" in combined),
        "blocking": blocking,
        "api_available": bool(summary),
        "queries_total": queries.get("total"),
        "queries_blocked": queries.get("blocked"),
        "percent_blocked": queries.get("percent_blocked"),
        "queries_cached": queries.get("cached"),
        "queries_forwarded": queries.get("forwarded"),
        "unique_domains": queries.get("unique_domains"),
        "clients_total": clients.get("total"),
        "clients_active": clients.get("active"),
        "domains_blocked": gravity.get("domains_being_blocked") or summary.get("domains_being_blocked"),
        "core_version": _version_value(version_result.stdout, "Pi-hole") if version_result.ok else None,
        "web_version": _version_value(version_result.stdout, "web") if version_result.ok else None,
        "ftl_version": _version_value(version_result.stdout, "FTL") if version_result.ok else None,
        "raw": (status_result.stdout or status_result.stderr)[-1200:],
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
    processes: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "username"]):
        try:
            proc.cpu_percent(interval=None)
            processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    # Sleep inside the worker thread, not the event loop. This yields a real
    # per-process CPU interval without affecting the cached system CPU sample.
    time.sleep(0.25)
    rows: list[dict] = []
    for proc in processes:
        try:
            rss = proc.memory_info().rss
            rows.append({
                "pid": int(proc.pid),
                "name": str(proc.name() or "process")[:60],
                "user": str(proc.username() or "")[:40],
                "memory_mb": round(rss / 1024 / 1024, 1),
                "cpu_percent": round(float(proc.cpu_percent(interval=None)), 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    rows.sort(key=lambda r: (r["memory_mb"], r["cpu_percent"]), reverse=True)
    return rows[: max(1, min(30, limit))]


async def get_status(bot_service: str, sampler: DashboardSystemSampler | None = None) -> dict:
    sample = await sampler.snapshot() if sampler is not None else {
        "cpu_percent": round(float(psutil.cpu_percent(interval=None)), 1),
        "cpu_average_30s": None,
        "cpu_average_5m": None,
        "sample_interval_seconds": None,
        "sample_age_seconds": None,
        "dashboard_cpu_percent": None,
        "dashboard_memory_mb": None,
        "bot_cpu_percent": None,
        "bot_memory_mb": None,
        "network_rx_rate_bps": 0.0,
        "network_tx_rate_bps": 0.0,
    }

    pihole_task = _pihole_status()
    tailscale_task = _tailscale_status()
    services_task = asyncio.gather(
        _service_state(bot_service),
        _service_state("raspberry-dashboard"),
        _service_state("pihole-FTL"),
        _service_state("tailscaled"),
    )
    processes_task = asyncio.to_thread(_top_processes, 12)
    pihole, tailscale, services, processes = await asyncio.gather(
        pihole_task, tailscale_task, services_task, processes_task
    )

    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    bot_state = next((row for row in services if row["name"] == bot_service), None) or {}
    return {
        **sample,
        "temperature_c": _temperature_c(),
        "memory_percent": round(float(vm.percent), 1),
        "memory_used_mb": round(vm.used / 1024 / 1024),
        "memory_total_mb": round(vm.total / 1024 / 1024),
        "memory_available_mb": round(vm.available / 1024 / 1024),
        "swap_percent": round(float(swap.percent), 1),
        "swap_used_mb": round(swap.used / 1024 / 1024),
        "swap_total_mb": round(swap.total / 1024 / 1024),
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
