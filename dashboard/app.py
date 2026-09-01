from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path

from aiohttp import web

from . import app_legacy
from .config import DashboardConfig

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


async def control_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "control.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_control_center(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    db_path = Path(config.repo_path) / "data" / "bot.sqlite3"

    def read_overview() -> dict:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                """SELECT
                (SELECT COUNT(*) FROM tickets) tickets,
                (SELECT COUNT(*) FROM tickets WHERE status='open') open_tickets,
                (SELECT COUNT(*) FROM personnel_members WHERE active=1) personnel,
                (SELECT COALESCE(SUM(inductions),0) FROM personnel_records) inductions,
                (SELECT COALESCE(SUM(bwg),0) FROM personnel_records) bwg,
                (SELECT COUNT(*) FROM moderation_cases) mod_cases,
                (SELECT COUNT(*) FROM command_analytics WHERE success=0 AND created_at>=datetime('now','-24 hours')) errors_24h"""
            ).fetchone()
            return dict(row)
        finally:
            con.close()

    overview = await asyncio.to_thread(read_overview)
    backups = list((Path(config.repo_path) / "data" / "backups").glob("*.sqlite3"))
    return web.json_response({"ok": True, "overview": overview, "backups": len(backups)})


async def api_cogs(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    text = (Path(config.repo_path) / "bot.py").read_text(encoding="utf-8")
    extensions = re.findall(r'"((?:cogs|tasks)\.[^"]+)"', text)
    return web.json_response({"ok": True, "extensions": extensions})


async def api_cog_action(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    if action not in {"reload", "load", "unload", "sync"}:
        return web.json_response({"ok": False, "message": "Unsupported action"}, status=400)
    data = await request.json()
    payload = {} if action == "sync" else {"extension": str(data.get("extension", ""))}
    config: DashboardConfig = request.app["config"]
    db_path = Path(config.repo_path) / "data" / "bot.sqlite3"

    def enqueue() -> int:
        con = sqlite3.connect(db_path)
        try:
            cur = con.execute(
                "INSERT INTO dashboard_commands(action,payload_json) VALUES(?,?)",
                (action, json.dumps(payload)),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()

    command_id = await asyncio.to_thread(enqueue)
    return web.json_response({"ok": True, "command_id": command_id, "message": "Queued for bot process"})


async def api_dashboard_command(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    db_path = Path(config.repo_path) / "data" / "bot.sqlite3"
    command_id = int(request.match_info["id"])

    def read() -> dict | None:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("SELECT * FROM dashboard_commands WHERE id=?", (command_id,)).fetchone()
            return dict(row) if row else None
        finally:
            con.close()

    row = await asyncio.to_thread(read)
    return web.json_response({"ok": bool(row), "command": row})


def create_app(config: DashboardConfig | None = None) -> web.Application:
    app = app_legacy.create_app(config)
    app.router.add_get("/control", control_page)
    app.router.add_get("/api/control-center", api_control_center)
    app.router.add_get("/api/cogs", api_cogs)
    app.router.add_post("/api/cogs/{action}", api_cog_action)
    app.router.add_get("/api/dashboard-command/{id}", api_dashboard_command)
    return app


def main() -> None:
    config = DashboardConfig.from_env()
    web.run_app(create_app(config), host=config.host, port=config.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
