from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path


class AuditService:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "audit.jsonl"
        self.max_bytes = 2 * 1024 * 1024

    def _ensure(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def record(self, action: str, *, ok: bool, detail: str = "") -> None:
        self._ensure()
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                rotated = self.state_dir / "audit.previous.jsonl"
                rotated.unlink(missing_ok=True)
                self.path.replace(rotated)
            row = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "action": str(action)[:80],
                "ok": bool(ok),
                "detail": str(detail).replace("\x00", "")[:1000],
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            # Audit failures must never break the control panel action itself.
            return

    def recent(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(300, int(limit)))
        if not self.path.is_file():
            return []
        rows: deque[str] = deque(maxlen=limit)
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    rows.append(line)
        except OSError:
            return []
        result: list[dict] = []
        for line in reversed(rows):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result
