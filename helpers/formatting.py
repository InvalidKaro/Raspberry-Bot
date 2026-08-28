from __future__ import annotations

import re


def human_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def human_duration(seconds: int | float) -> str:
    total = max(int(seconds), 0)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def slugify_channel_name(value: str, fallback: str = "ticket") -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9äöüß-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return (value or fallback)[:60]
