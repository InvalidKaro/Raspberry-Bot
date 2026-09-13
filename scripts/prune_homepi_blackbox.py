#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path


def _default_database() -> Path:
    repo_root = Path(os.getenv("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot"))
    return Path(os.getenv("HOMEPI_BLACKBOX_DB", str(repo_root / "data" / "homepi_blackbox.sqlite3")))


def _retention_days() -> int:
    try:
        return max(30, min(3650, int(os.getenv("HOMEPI_BLACKBOX_RETENTION_DAYS", "365"))))
    except ValueError:
        return 365


def prune(database: Path, retention_days: int, *, dry_run: bool = False) -> dict[str, int]:
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    if not database.is_file():
        return {"events": 0, "samples": 0}

    cutoff = time.time() - retention_days * 86400
    uri = f"file:{database}?mode={'ro' if dry_run else 'rw'}"
    with sqlite3.connect(uri, uri=True, timeout=5.0) as con:
        tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('events','samples')")
        }
        counts: dict[str, int] = {"events": 0, "samples": 0}
        for table in ("events", "samples"):
            if table not in tables:
                continue
            if dry_run:
                row = con.execute(f"SELECT COUNT(*) FROM {table} WHERE created_at < ?", (cutoff,)).fetchone()
                counts[table] = int(row[0] if row else 0)
            else:
                cur = con.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
                counts[table] = max(0, int(cur.rowcount if cur.rowcount is not None else 0))
        if not dry_run:
            con.commit()
            con.execute("PRAGMA wal_checkpoint(PASSIVE)")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune old HomePi Blackbox rows without VACUUM write amplification.")
    parser.add_argument("--database", type=Path, default=_default_database())
    parser.add_argument("--retention-days", type=int, default=_retention_days())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    counts = prune(args.database.expanduser().resolve(), args.retention_days, dry_run=args.dry_run)
    mode = "would prune" if args.dry_run else "pruned"
    print(f"Blackbox retention: {mode} events={counts['events']} samples={counts['samples']} ({args.retention_days} days)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
