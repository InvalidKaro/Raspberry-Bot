from __future__ import annotations
from datetime import UTC, datetime
from pathlib import Path
import asyncio, shutil, sqlite3

class BackupService:
    def __init__(self, database, backup_dir: Path | str = "data/backups") -> None:
        self.database = database
        self.backup_dir = Path(backup_dir)

    async def create(self, *, kind: str = "manual", created_by: int | None = None) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"bot-{kind}-{stamp}.sqlite3"
        source = Path(self.database.path)

        def _copy() -> None:
            src = sqlite3.connect(source)
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

        await asyncio.to_thread(_copy)
        await self.database.execute(
            "INSERT INTO backup_history(file_name, kind, size_bytes, created_by) VALUES (?, ?, ?, ?)",
            (target.name, kind, target.stat().st_size, created_by),
        )
        await self.rotate()
        return target

    async def rotate(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        daily = sorted(self.backup_dir.glob("bot-daily-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        weekly = sorted(self.backup_dir.glob("bot-weekly-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        manual = sorted(self.backup_dir.glob("bot-manual-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in daily[7:] + weekly[4:] + manual[10:]:
            path.unlink(missing_ok=True)

    async def list(self) -> list[Path]:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        return sorted(self.backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)

    async def restore(self, file_name: str) -> None:
        safe = Path(file_name).name
        source = self.backup_dir / safe
        if not source.is_file():
            raise FileNotFoundError(safe)
        await self.database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        await self.database.connection.commit()
        shutil.copy2(source, self.database.path)
