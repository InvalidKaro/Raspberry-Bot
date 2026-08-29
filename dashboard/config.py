from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    host: str
    port: int
    dashboard_token: str
    dashboard_secret: str
    bot_service: str
    repo_path: Path
    log_lines: int

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        try:
            port = max(1, min(65535, int(_env("DASHBOARD_PORT", "8080"))))
        except ValueError:
            port = 8080

        try:
            log_lines = max(20, min(500, int(_env("DASHBOARD_LOG_LINES", "120"))))
        except ValueError:
            log_lines = 120

        token = _env("DASHBOARD_TOKEN", "")
        secret = _env("DASHBOARD_SECRET", "")
        if len(token) < 12:
            raise RuntimeError("DASHBOARD_TOKEN missing/too short (minimum 12 characters).")
        if len(secret) < 24:
            raise RuntimeError("DASHBOARD_SECRET missing/too short (minimum 24 characters).")

        return cls(
            host=_env("DASHBOARD_HOST", "0.0.0.0"),
            port=port,
            dashboard_token=token,
            dashboard_secret=secret,
            bot_service=_env("BOT_SERVICE_NAME", "raspberry-bot"),
            repo_path=Path(_env("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot")).expanduser().resolve(),
            log_lines=log_lines,
        )
