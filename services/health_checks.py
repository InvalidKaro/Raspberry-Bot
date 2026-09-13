from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

from dashboard.services.commands import run_command


@dataclass(frozen=True, slots=True)
class HealthResult:
    name: str
    status: str
    latency_ms: float
    last_check: float
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "online"

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


HEALTH_SERVICES: dict[str, str] = {
    "bot": "raspberry-bot",
    "dashboard": "raspberry-dashboard",
    "radar": "homepi-flight-radar",
    "mesh": "raspberry-meshtastic",
    "display": "raspberry-display",
    "display2": "raspberry-display2",
    "intelligence": "raspberry-intelligence",
    "pihole": "pihole-FTL",
}


def _now() -> float:
    return time.time()


def _status(active: str, sub: str) -> str:
    if active == "active" and sub in {"running", "exited", "listening"}:
        return "online"
    if active in {"activating", "reloading"}:
        return "degraded"
    return "offline"


async def check_systemd_service(name: str, unit: str) -> HealthResult:
    started = time.perf_counter()
    result = await run_command(
        [
            "systemctl",
            "show",
            unit,
            "--property=LoadState,ActiveState,SubState",
            "--no-pager",
        ],
        timeout=5,
    )
    latency = (time.perf_counter() - started) * 1000
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value

    load = values.get("LoadState", "not-found")
    active = values.get("ActiveState", "unknown")
    sub = values.get("SubState", "unknown")
    status = _status(active, sub) if load == "loaded" else "offline"
    message = f"{unit}: {active}/{sub}" if load == "loaded" else f"{unit}: not found"
    if not result.ok and not result.stdout:
        message = (result.stderr or message).strip()[-300:]
    return HealthResult(name, status, round(latency, 1), _now(), message)


async def check_database(path: str | Path) -> HealthResult:
    db_path = Path(path)
    started = time.perf_counter()

    def probe() -> tuple[str, str]:
        if not db_path.is_file():
            return "offline", f"Database not found: {db_path}"
        try:
            with sqlite3.connect(db_path, timeout=2.0) as con:
                row = con.execute("PRAGMA quick_check(1)").fetchone()
            check = str(row[0]) if row else "unknown"
            return ("online", "SQLite quick_check: ok") if check == "ok" else ("degraded", f"SQLite quick_check: {check}")
        except sqlite3.Error as exc:
            return "offline", f"SQLite {type(exc).__name__}: {exc}"

    status, message = await asyncio.to_thread(probe)
    latency = (time.perf_counter() - started) * 1000
    return HealthResult("database", status, round(latency, 1), _now(), message[:300])


async def check_all(database_path: str | Path | None = None) -> list[HealthResult]:
    checks: list[Awaitable[HealthResult]] = [
        check_systemd_service(name, unit) for name, unit in HEALTH_SERVICES.items()
    ]
    if database_path is not None:
        checks.append(check_database(database_path))
    return list(await asyncio.gather(*checks))


def summarize(results: list[HealthResult]) -> dict[str, object]:
    counts = {"online": 0, "degraded": 0, "offline": 0}
    for result in results:
        counts[result.status if result.status in counts else "offline"] += 1
    if counts["offline"]:
        overall = "offline"
    elif counts["degraded"]:
        overall = "degraded"
    else:
        overall = "online"
    return {
        "status": overall,
        "counts": counts,
        "last_check": max((item.last_check for item in results), default=_now()),
        "checks": [item.as_dict() for item in results],
    }
