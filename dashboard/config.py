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
    bot_env_path: Path
    database_path: Path
    log_lines: int

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        repo_path = Path(
            _env("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot")
        ).expanduser().resolve()

        try:
            port = max(1, min(65535, int(_env("DASHBOARD_PORT", "8080"))))
        except ValueError:
            port = 8080

        try:
            log_lines = max(20, min(500, int(_env("DASHBOARD_LOG_LINES", "150"))))
        except ValueError:
            log_lines = 150

        token = _env("DASHBOARD_TOKEN", "")
        secret = _env("DASHBOARD_SECRET", "")
        if len(token) < 12:
            raise RuntimeError("DASHBOARD_TOKEN must contain at least 12 characters.")
        if len(secret) < 24:
            raise RuntimeError("DASHBOARD_SECRET must contain at least 24 characters.")

        bot_env_path = Path(_env("BOT_ENV_PATH", str(repo_path / ".env"))).expanduser().resolve()
        database_path = Path(
            _env("BOT_DATABASE_PATH", str(repo_path / "data" / "bot.sqlite3"))
        ).expanduser().resolve()

        return cls(
            host=_env("DASHBOARD_HOST", "0.0.0.0"),
            port=port,
            dashboard_token=token,
            dashboard_secret=secret,
            bot_service=_env("BOT_SERVICE_NAME", "raspberry-bot"),
            repo_path=repo_path,
            bot_env_path=bot_env_path,
            database_path=database_path,
            log_lines=log_lines,
        )
