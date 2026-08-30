from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _read_env_file_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip().upper() == key.upper():
            return value.strip().strip('"').strip("'") or None
    return None


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
    sample_interval_seconds: int

    @classmethod
    def from_env(cls) -> "DashboardConfig":
        repo_path = Path(_env("BOT_REPO_PATH", "/home/stefano/services/Raspberry-Bot")).expanduser().resolve()

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

        explicit_db = os.getenv("BOT_DATABASE_PATH", "").strip()
        bot_db_setting = _read_env_file_value(bot_env_path, "DATABASE_PATH")
        db_raw = explicit_db or bot_db_setting or "data/bot.sqlite3"
        database_path = Path(db_raw).expanduser()
        if not database_path.is_absolute():
            database_path = repo_path / database_path
        database_path = database_path.resolve()

        try:
            sample_interval_seconds = max(10, min(30, int(_env("DASHBOARD_SAMPLE_INTERVAL", "15"))))
        except ValueError:
            sample_interval_seconds = 15

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
            sample_interval_seconds=sample_interval_seconds,
        )
