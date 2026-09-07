from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from aiohttp import web


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _file_age(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


async def meshtastic_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "meshtastic.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_meshtastic(request: web.Request) -> web.Response:
    config = request.app["config"]
    repo_root = Path(config.repo_path)
    state_path = repo_root / "data" / "meshtastic_state.json"
    display_path = repo_root / "data" / "display2_status.json"

    state, display2, state_age, display_age = await asyncio.gather(
        asyncio.to_thread(_safe_json, state_path),
        asyncio.to_thread(_safe_json, display_path),
        asyncio.to_thread(_file_age, state_path),
        asyncio.to_thread(_file_age, display_path),
    )

    return web.json_response(
        {
            "ok": True,
            "state": state,
            "display2": display2,
            "state_age_seconds": state_age,
            "display2_age_seconds": display_age,
            "state_file_exists": state_age is not None,
            "display2_file_exists": display_age is not None,
        },
        headers={"Cache-Control": "no-store"},
    )


def register_meshtastic_routes(app: web.Application) -> None:
    app.router.add_get("/meshtastic", meshtastic_page)
    app.router.add_get("/api/meshtastic", api_meshtastic)
