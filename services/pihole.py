from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass


@dataclass(slots=True)
class PiHoleStats:
    installed: bool = False
    active: bool = False
    blocking: bool | None = None
    api_available: bool = False
    total_queries: int | None = None
    blocked_queries: int | None = None
    percent_blocked: float | None = None
    unique_domains: int | None = None
    forwarded_queries: int | None = None
    cached_queries: int | None = None
    total_clients: int | None = None
    active_clients: int | None = None
    domains_blocked: int | None = None
    core_version: str | None = None
    web_version: str | None = None
    ftl_version: str | None = None
    raw_status: str = ""
    permission_limited: bool = False
    permission_detail: str | None = None


_CACHE: PiHoleStats | None = None
_CACHE_AT = 0.0
_CACHE_TTL = 30.0
_CACHE_LOCK = asyncio.Lock()


def _run(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        text = (result.stdout or result.stderr or "").strip()
        return int(result.returncode), text
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested(data: dict, *path: str) -> object | None:
    value: object = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _parse_versions(text: str) -> tuple[str | None, str | None, str | None]:
    core = web = ftl = None
    for line in text.splitlines():
        lower = line.lower()
        match = re.search(r"\bv?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][\w.-]+)?)\b", line)
        if not match:
            continue
        version = match.group(1)
        if "ftl" in lower:
            ftl = version
        elif "web" in lower:
            web = version
        elif "pi-hole" in lower or "core" in lower:
            core = version
    return core, web, ftl


def _systemctl_active(service: str) -> bool:
    binary = shutil.which("systemctl")
    if not binary:
        return False
    code, text = _run([binary, "is-active", service], timeout=3)
    return code == 0 and text.strip() == "active"


def _collect_sync() -> PiHoleStats:
    binary = shutil.which("pihole")
    if not binary:
        return PiHoleStats(installed=False)

    config_path = "/etc/pihole/pihole.toml"
    if os.path.exists(config_path) and not os.access(config_path, os.R_OK):
        # Pi-hole v6 CLI reads pihole.toml. Calling the CLI without read access
        # can cause one journal line per statistics refresh, so degrade quietly
        # to FTL service status until the bot user has the required access.
        return PiHoleStats(
            installed=True,
            active=_systemctl_active("pihole-FTL"),
            api_available=False,
            permission_limited=True,
            permission_detail=f"Bot user cannot read {config_path}",
            raw_status="Pi-hole CLI skipped to avoid permission-error log spam.",
        )

    status_code, status_text = _run([binary, "status"], timeout=5)
    status_lower = status_text.lower()
    if "permission denied" in status_lower and "pihole.toml" in status_lower:
        return PiHoleStats(
            installed=True,
            active=_systemctl_active("pihole-FTL"),
            api_available=False,
            permission_limited=True,
            permission_detail="Pi-hole CLI cannot read pihole.toml",
            raw_status=status_text[-1600:],
        )

    enabled = "blocking is enabled" in status_lower or "blocking enabled" in status_lower
    disabled = "blocking is disabled" in status_lower or "blocking disabled" in status_lower
    active = status_code == 0 and (
        "listening" in status_lower
        or "ftl is listening" in status_lower
        or enabled
        or disabled
    )
    if not active:
        active = _systemctl_active("pihole-FTL")

    stats = PiHoleStats(
        installed=True,
        active=active,
        blocking=True if enabled else False if disabled else None,
        raw_status=status_text[-1600:],
    )

    version_code, version_text = _run([binary, "-v"], timeout=5)
    if version_code == 0:
        stats.core_version, stats.web_version, stats.ftl_version = _parse_versions(version_text)

    summary_code, summary_text = _run([binary, "api", "stats/summary"], timeout=6)
    if "permission denied" in summary_text.lower() and "pihole.toml" in summary_text.lower():
        stats.permission_limited = True
        stats.permission_detail = "Pi-hole statistics API is unavailable to the bot user"
        return stats

    summary = _extract_json(summary_text) if summary_code == 0 else {}
    if summary:
        stats.api_available = True
        stats.total_queries = _as_int(_nested(summary, "queries", "total"))
        stats.blocked_queries = _as_int(_nested(summary, "queries", "blocked"))
        stats.percent_blocked = _as_float(_nested(summary, "queries", "percent_blocked"))
        stats.unique_domains = _as_int(_nested(summary, "queries", "unique_domains"))
        stats.forwarded_queries = _as_int(_nested(summary, "queries", "forwarded"))
        stats.cached_queries = _as_int(_nested(summary, "queries", "cached"))
        stats.total_clients = _as_int(_nested(summary, "clients", "total"))
        stats.active_clients = _as_int(_nested(summary, "clients", "active"))
        stats.domains_blocked = (
            _as_int(_nested(summary, "gravity", "domains_being_blocked"))
            or _as_int(summary.get("domains_being_blocked"))
        )

    blocking_code, blocking_text = _run([binary, "api", "dns/blocking"], timeout=5)
    blocking = _extract_json(blocking_text) if blocking_code == 0 else {}
    if blocking:
        stats.api_available = True
        value = blocking.get("blocking")
        if isinstance(value, bool):
            stats.blocking = value
        elif isinstance(value, str):
            stats.blocking = value.lower() in {"enabled", "enable", "true", "on"}

    return stats


async def collect_pihole_stats(*, force: bool = False) -> PiHoleStats:
    global _CACHE, _CACHE_AT

    now = time.monotonic()
    if not force and _CACHE is not None and now - _CACHE_AT < _CACHE_TTL:
        return _CACHE

    async with _CACHE_LOCK:
        now = time.monotonic()
        if not force and _CACHE is not None and now - _CACHE_AT < _CACHE_TTL:
            return _CACHE
        _CACHE = await asyncio.to_thread(_collect_sync)
        _CACHE_AT = time.monotonic()
        return _CACHE
