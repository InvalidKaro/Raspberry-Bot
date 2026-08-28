from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: str = "INFO", directory: str | Path = "logs") -> None:
    log_dir = Path(directory)
    log_dir.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(formatter)

    normal_log = RotatingFileHandler(
        log_dir / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    normal_log.setLevel(numeric_level)
    normal_log.setFormatter(formatter)

    error_log = RotatingFileHandler(
        log_dir / "errors.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    error_log.setLevel(logging.ERROR)
    error_log.setFormatter(formatter)

    root.addHandler(console)
    root.addHandler(normal_log)
    root.addHandler(error_log)

    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
