"""Read-only WireGuard profile inventory and status. No network mutations."""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

PROFILE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")
INTERFACE_NAME = re.compile(r"^[a-zA-Z0-9_=+.-]{1,15}$")


def profile_directory() -> Path:
    return Path(os.environ.get("HOMEPI_VPN_PROFILE_DIR", "/etc/wireguard")).resolve()


def profiles() -> list[str]:
    directory = profile_directory()
    if not directory.is_dir():
        return []
    result = []
    for item in directory.iterdir():
        if item.is_symlink() or not item.is_file() or item.suffix != ".conf":
            continue
        if PROFILE_NAME.fullmatch(item.stem) and INTERFACE_NAME.fullmatch(item.stem):
            result.append(item.stem)
    return sorted(result)


async def _run(*args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
        return proc.returncode or 0, out.decode("utf-8", "replace").strip()
    except (OSError, asyncio.TimeoutError):
        return 1, ""


async def status() -> dict:
    available = profiles()
    code, output = await _run("wg", "show", "interfaces")
    active = output.split() if code == 0 else []
    # Do not expose WireGuard public keys, peer addresses or private config content.
    return {
        "ok": True,
        "mode": "read-only",
        "profiles": available,
        "active_interfaces": active,
        "active_profiles": [name for name in available if name in active],
        "message": "VPN switching is disabled until routing, firewall and rollback are verified.",
    }
