from __future__ import annotations

import asyncio
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .commands import run_command


class BackupService:
    NAME_RE = re.compile(r"^bot-\d{8}-\d{6}\.sqlite3$")

    def __init__(self, database_path: Path, bot_service: str, state_dir: Path) -> None:
        self.database_path = database_path
        self.bot_service = bot_service
        self.backup_dir = state_dir / "backups"

    async def _backup_sync(self, target: Path) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True, timeout=5)
        dest = sqlite3.connect(target, timeout=5)
        try:
            source.backup(dest)
            dest.execute("PRAGMA integrity_check")
            dest.commit()
        finally:
            dest.close()
            source.close()

    async def create(self) -> dict:
        if not self.database_path.is_file():
            return {"ok": False, "message": f"Database not found: {self.database_path}"}
        name = datetime.now(UTC).strftime("bot-%Y%m%d-%H%M%S.sqlite3")
        target = self.backup_dir / name
        try:
            await asyncio.to_thread(self._backup_sync_blocking, target)
        except (OSError, sqlite3.Error) as exc:
            target.unlink(missing_ok=True)
            return {"ok": False, "message": f"Backup failed: {exc}"}
        return {"ok": True, "message": f"Created database backup {name}.", "name": name}

    def _backup_sync_blocking(self, target: Path) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True, timeout=5)
        dest = sqlite3.connect(target, timeout=5)
        try:
            source.backup(dest)
            row = dest.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                raise sqlite3.DatabaseError("integrity_check did not return ok")
            dest.commit()
        finally:
            dest.close()
            source.close()

    def list(self) -> list[dict]:
        if not self.backup_dir.is_dir():
            return []
        rows = []
        for path in sorted(self.backup_dir.glob("bot-*.sqlite3"), reverse=True)[:80]:
            if not self.NAME_RE.fullmatch(path.name):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append({"name": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime)})
        return rows

    def path(self, name: str) -> Path:
        name = str(name or "")
        if not self.NAME_RE.fullmatch(name):
            raise ValueError("Invalid backup name.")
        path = (self.backup_dir / name).resolve()
        path.relative_to(self.backup_dir.resolve())
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    async def restore(self, name: str) -> dict:
        try:
            source = self.path(name)
        except (ValueError, FileNotFoundError) as exc:
            return {"ok": False, "message": f"Backup not found: {exc}"}
        stop = await run_command(["sudo", "-n", "systemctl", "stop", self.bot_service], timeout=20)
        if not stop.ok:
            return {"ok": False, "message": stop.stderr or stop.stdout or "Could not stop bot before restore."}
        safety_name = datetime.now(UTC).strftime("bot-%Y%m%d-%H%M%S.sqlite3")
        safety = self.backup_dir / safety_name
        try:
            if self.database_path.is_file():
                shutil.copy2(self.database_path, safety)
            shutil.copy2(source, self.database_path)
        except OSError as exc:
            await run_command(["sudo", "-n", "systemctl", "start", self.bot_service], timeout=20)
            return {"ok": False, "message": f"Restore copy failed: {exc}"}
        start = await run_command(["sudo", "-n", "systemctl", "start", self.bot_service], timeout=20)
        return {
            "ok": start.ok,
            "message": f"Restored {name}; pre-restore safety copy: {safety_name}." if start.ok else (start.stderr or start.stdout or "Database restored, but bot failed to start."),
        }

    def delete(self, name: str) -> dict:
        try:
            path = self.path(name)
            path.unlink()
        except (ValueError, FileNotFoundError, OSError) as exc:
            return {"ok": False, "message": f"Could not delete backup: {exc}"}
        return {"ok": True, "message": f"Deleted backup {name}."}
