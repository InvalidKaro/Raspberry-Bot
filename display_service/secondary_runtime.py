from __future__ import annotations

import os
import sqlite3
from typing import Any

from . import secondary as core


def _unexpected_errors_24h() -> int:
    """Count only unexpected bot exceptions, not normal command failures.

    command_analytics marks expected failures such as permission/check/cooldown
    errors as unsuccessful too. Display 2 should not present those as bot
    crashes. dashboard_error_events is written only for unexpected command
    exceptions and is therefore the better health metric.
    """
    try:
        with core._connect() as con:
            if not core._table_exists(con, "dashboard_error_events"):
                return 0
            row = con.execute(
                "SELECT COUNT(*) count FROM dashboard_error_events "
                "WHERE created_at>=datetime('now','-24 hours')"
            ).fetchone()
            return core._as_int(row["count"] if row else 0)
    except (sqlite3.Error, OSError):
        return 0


_base_read_discord_stats = core._read_discord_stats


def _read_discord_stats() -> dict[str, Any]:
    stats = _base_read_discord_stats()
    stats["errors_24h"] = _unexpected_errors_24h()
    return stats


# Runtime policy for Display 2. Keep user overrides working, while making the
# default rotation a little slower and easier to read on the 128x64 OLED.
core.PAGE_SECONDS = max(2, min(30, int(os.getenv("DISPLAY2_PAGE_SECONDS", "8"))))
core._read_discord_stats = _read_discord_stats


def main() -> None:
    core.main()


if __name__ == "__main__":
    main()
