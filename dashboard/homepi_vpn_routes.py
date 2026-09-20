"""Authenticated dashboard routes; inherited auth and CSRF middleware."""
from __future__ import annotations

from pathlib import Path
from aiohttp import web
from services.homepi_vpn import status

TEMPLATES = Path(__file__).resolve().parent / "templates"


async def page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATES / "vpn.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_status(_: web.Request) -> web.Response:
    return web.json_response(await status(), headers={"Cache-Control": "no-store"})


def register_vpn_routes(app: web.Application) -> None:
    app.router.add_get("/vpn", page)
    app.router.add_get("/api/vpn/status", api_status)
