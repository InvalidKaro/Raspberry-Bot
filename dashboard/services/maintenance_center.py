from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import sqlite3
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .backup_service import BackupService
from .commands import run_command


class MaintenanceCenter:
    """Persistent HomePi maintenance features.

    The scheduler only manages backups it created itself. Manual backups are
    never deleted by automatic retention.
    """

    TARGET_RE = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
    DEFAULT_SCHEDULE = {
        "enabled": False,
        "frequency": "daily",
        "time": "03:30",
        "weekday": 6,
        "retention": 7,
        "next_run_at": None,
        "last_run_at": None,
        "last_result": None,
        "automatic_backups": [],
    }

    def __init__(
        self,
        backups: BackupService,
        database_path: Path,
        state_dir: Path,
    ) -> None:
        self.backups = backups
        self.database_path = Path(database_path)
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "maintenance.json"
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _read(self) -> dict[str, Any]:
        state = dict(self.DEFAULT_SCHEDULE)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state.update(raw)
        except (OSError, json.JSONDecodeError):
            pass
        state["automatic_backups"] = [
            str(name) for name in state.get("automatic_backups", []) if isinstance(name, str)
        ][-100:]
        return state

    def _write(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    @staticmethod
    def _local_now() -> datetime:
        return datetime.now().astimezone()

    @staticmethod
    def _parse_time(value: str) -> tuple[int, int]:
        match = re.fullmatch(r"(\d{2}):(\d{2})", str(value or ""))
        if not match:
            raise ValueError("Time must use HH:MM.")
        hour, minute = int(match.group(1)), int(match.group(2))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Invalid schedule time.")
        return hour, minute

    def _next_run(self, state: dict[str, Any], now: datetime | None = None) -> str | None:
        if not state.get("enabled"):
            return None
        now = now or self._local_now()
        hour, minute = self._parse_time(str(state.get("time") or "03:30"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        frequency = str(state.get("frequency") or "daily")
        if frequency == "weekly":
            weekday = max(0, min(6, int(state.get("weekday", 6))))
            days = (weekday - now.weekday()) % 7
            candidate = candidate + timedelta(days=days)
            if candidate <= now:
                candidate += timedelta(days=7)
        else:
            if candidate <= now:
                candidate += timedelta(days=1)
        return candidate.isoformat()

    def status(self) -> dict[str, Any]:
        state = self._read()
        if state.get("enabled") and not state.get("next_run_at"):
            state["next_run_at"] = self._next_run(state)
            self._write(state)
        return state

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._read()
        frequency = str(payload.get("frequency", state.get("frequency", "daily"))).lower()
        if frequency not in {"daily", "weekly"}:
            raise ValueError("Frequency must be daily or weekly.")
        schedule_time = str(payload.get("time", state.get("time", "03:30")))
        self._parse_time(schedule_time)
        weekday = int(payload.get("weekday", state.get("weekday", 6)))
        if not 0 <= weekday <= 6:
            raise ValueError("Weekday must be between 0 and 6.")
        retention = int(payload.get("retention", state.get("retention", 7)))
        if not 1 <= retention <= 50:
            raise ValueError("Retention must be between 1 and 50 automatic backups.")

        state.update(
            {
                "enabled": bool(payload.get("enabled", False)),
                "frequency": frequency,
                "time": schedule_time,
                "weekday": weekday,
                "retention": retention,
            }
        )
        state["next_run_at"] = self._next_run(state)
        self._write(state)
        return state

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="homepi-maintenance-scheduler")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _loop(self) -> None:
        while True:
            try:
                await self._run_if_due()
            except Exception:
                # Maintenance must never take down the dashboard.
                pass
            await asyncio.sleep(60)

    async def _run_if_due(self) -> None:
        state = self._read()
        if not state.get("enabled"):
            return
        raw = state.get("next_run_at")
        if not raw:
            state["next_run_at"] = self._next_run(state)
            self._write(state)
            return
        try:
            due = datetime.fromisoformat(str(raw))
        except ValueError:
            state["next_run_at"] = self._next_run(state)
            self._write(state)
            return
        if due.tzinfo is None:
            due = due.replace(tzinfo=self._local_now().tzinfo)
        if self._local_now() >= due:
            await self.run_backup(automatic=True)

    async def run_backup(self, *, automatic: bool) -> dict[str, Any]:
        async with self._lock:
            result = await self.backups.create()
            state = self._read()
            now = self._local_now()
            state["last_run_at"] = now.isoformat()
            state["last_result"] = {
                "ok": bool(result.get("ok")),
                "message": str(result.get("message", "")),
                "automatic": automatic,
            }
            if result.get("ok") and result.get("name") and automatic:
                names = list(state.get("automatic_backups", []))
                names.append(str(result["name"]))
                existing = {row["name"] for row in self.backups.list()}
                names = [name for name in names if name in existing]
                retention = max(1, min(50, int(state.get("retention", 7))))
                remove = names[:-retention]
                names = names[-retention:]
                for name in remove:
                    self.backups.delete(name)
                state["automatic_backups"] = names
            if automatic:
                state["next_run_at"] = self._next_run(state, now + timedelta(seconds=1))
            self._write(state)
            return {**result, "schedule": state}

    async def database_check(self) -> dict[str, Any]:
        path = self.database_path

        def check() -> dict[str, Any]:
            if not path.is_file():
                return {"ok": False, "quick_check": "database missing", "foreign_key_issues": None}
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=8)
            try:
                quick_rows = con.execute("PRAGMA quick_check").fetchall()
                foreign_rows = con.execute("PRAGMA foreign_key_check").fetchmany(50)
                quick = [str(row[0]) for row in quick_rows]
                return {
                    "ok": quick == ["ok"] and not foreign_rows,
                    "quick_check": quick,
                    "foreign_key_issues": len(foreign_rows),
                    "size_bytes": path.stat().st_size,
                }
            finally:
                con.close()

        try:
            return await asyncio.to_thread(check)
        except (OSError, sqlite3.Error) as exc:
            return {"ok": False, "quick_check": [str(exc)], "foreign_key_issues": None}

    async def cached_updates(self) -> dict[str, Any]:
        if not shutil.which("apt"):
            return {"ok": False, "available": False, "updates": [], "message": "apt is not installed."}
        result = await run_command(["apt", "list", "--upgradable"], timeout=25)
        lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and not line.lower().startswith("listing")
        ]
        rows = []
        for line in lines[:100]:
            package = line.split("/", 1)[0]
            rows.append({"package": package, "detail": line})
        return {
            "ok": result.ok,
            "available": True,
            "count": len(lines),
            "updates": rows,
            "message": result.stderr or "Uses the local APT package index; no packages were installed.",
        }

    async def network_test(self, kind: str, target: str) -> dict[str, Any]:
        target = str(target or "").strip()
        if not self.TARGET_RE.fullmatch(target):
            raise ValueError("Target may only contain hostname/IP characters.")

        if kind == "dns":
            def resolve() -> list[str]:
                addresses = {
                    row[4][0]
                    for row in socket.getaddrinfo(target, None, type=socket.SOCK_STREAM)
                    if row and row[4]
                }
                return sorted(addresses)

            try:
                addresses = await asyncio.wait_for(asyncio.to_thread(resolve), timeout=8)
                return {"ok": bool(addresses), "kind": "dns", "target": target, "addresses": addresses}
            except (OSError, TimeoutError) as exc:
                return {"ok": False, "kind": "dns", "target": target, "message": str(exc)}

        if kind == "ping":
            if not shutil.which("ping"):
                return {"ok": False, "kind": "ping", "target": target, "message": "ping is not installed."}
            result = await run_command(["ping", "-c", "3", "-W", "2", target], timeout=10)
            return {
                "ok": result.ok,
                "kind": "ping",
                "target": target,
                "output": result.stdout or result.stderr,
            }

        raise ValueError("Unsupported network test.")

    async def diagnostic_checks(self) -> dict[str, Any]:
        db_task = self.database_check()
        dns_task = self.network_test("dns", "example.com")
        route_task = run_command(["ip", "route", "show", "default"], timeout=5)
        db, dns, route = await asyncio.gather(db_task, dns_task, route_task)

        writable = False
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            probe = self.state_dir / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            writable = True
        except OSError:
            writable = False

        checks = [
            {"key": "database", "label": "SQLite integrity", "ok": bool(db.get("ok")), "detail": str(db.get("quick_check"))},
            {"key": "dns", "label": "DNS resolution", "ok": bool(dns.get("ok")), "detail": ", ".join(dns.get("addresses", [])) or dns.get("message", "")},
            {"key": "route", "label": "Default route", "ok": bool(route.ok and route.stdout), "detail": route.stdout or route.stderr or "No default route"},
            {"key": "state", "label": "Dashboard state storage", "ok": writable, "detail": str(self.state_dir)},
        ]
        return {
            "ok": all(item["ok"] for item in checks),
            "checks": checks,
            "database": db,
        }
