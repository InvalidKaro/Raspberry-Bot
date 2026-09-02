from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path

from aiohttp import web

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _db_path(config) -> Path:
    configured = Path(config.database_path)
    if configured.is_absolute():
        return configured
    return Path(config.repo_path) / configured


def _connect(config) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(config))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _json_row(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    for key in list(data):
        if key.endswith("_id") and data[key] is not None:
            data[key] = str(data[key])
    return data


def _enqueue(config, action: str, payload: dict) -> int:
    con = _connect(config)
    try:
        cur = con.execute(
            "INSERT INTO dashboard_commands(action,payload_json) VALUES(?,?)",
            (action, json.dumps(payload, ensure_ascii=False)),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


async def workspace_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "workspace.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_workspace_summary(request: web.Request) -> web.Response:
    config = request.app["config"]

    def read() -> dict:
        con = _connect(config)
        try:
            counts = con.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM workspace_tasks WHERE status!='done') tasks,
                    (SELECT COUNT(*) FROM workspace_events WHERE starts_at>=datetime('now')) events,
                    (SELECT COUNT(*) FROM knowledge_entries) knowledge,
                    (SELECT COUNT(*) FROM automation_jobs WHERE enabled=1) jobs,
                    (SELECT COUNT(*) FROM content_templates) templates,
                    (SELECT COUNT(*) FROM forms) forms
                """
            ).fetchone()
            commands = con.execute(
                "SELECT guild_id,name,enabled FROM custom_commands ORDER BY updated_at DESC LIMIT 30"
            ).fetchall()
            settings = con.execute(
                "SELECT guild_id,welcome_channel_id,general_log_channel_id,auto_role_id,welcome_message FROM guild_settings ORDER BY guild_id LIMIT 20"
            ).fetchall()
            plugin_rows = con.execute(
                "SELECT extension,enabled FROM plugin_state ORDER BY extension"
            ).fetchall()
            audit = con.execute(
                "SELECT created_at,'audit' type,action name,'ok' status FROM bot_audit_log ORDER BY id DESC LIMIT 25"
            ).fetchall()
            analytics = con.execute(
                "SELECT created_at,'command' type,command_name name,CASE WHEN success=1 THEN 'ok' ELSE COALESCE(error_type,'error') END status FROM command_analytics ORDER BY id DESC LIMIT 25"
            ).fetchall()
            console = sorted(
                [_json_row(x) for x in list(audit) + list(analytics)],
                key=lambda x: str(x.get("created_at") or ""),
                reverse=True,
            )[:40]
            return {
                "counts": dict(counts),
                "commands": [_json_row(r) for r in commands],
                "guild_settings": [_json_row(r) for r in settings],
                "plugin_rows": [_json_row(r) for r in plugin_rows],
                "console": console,
            }
        finally:
            con.close()

    data = await asyncio.to_thread(read)
    bot_text = (Path(config.repo_path) / "bot.py").read_text(encoding="utf-8")
    extensions = [
        item
        for item in re.findall(r'"(cogs\.[^"]+)"', bot_text)
        if item.startswith("cogs.")
    ]
    state = {r["extension"]: bool(r["enabled"]) for r in data.pop("plugin_rows")}
    plugins = [
        {"extension": ext, "enabled": state.get(ext, True)}
        for ext in sorted(set(extensions) | set(state))
    ]
    return web.json_response({"ok": True, **data, "plugins": plugins})


async def api_workspace_composer(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    channel_id = str(data.get("channel_id", "")).strip()
    text = str(data.get("text", "")).strip()
    title = str(data.get("title", "")).strip()
    color = str(data.get("color", "")).strip()
    if not channel_id.isdigit() or not text:
        return web.json_response(
            {"ok": False, "message": "Channel-ID und Nachricht sind erforderlich."},
            status=400,
        )
    action = "send-embed" if title else "send-message"
    payload = {"channel_id": channel_id, "text": text}
    if title:
        payload.update({"title": title, "color": color})
    command_id = await asyncio.to_thread(_enqueue, config, action, payload)
    return web.json_response({"ok": True, "command_id": command_id})


async def api_workspace_custom_command(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    guild_raw = str(data.get("guild_id", "")).strip()
    name = str(data.get("name", "")).strip().lower().removeprefix("!")
    response = str(data.get("response", "")).strip()
    if not guild_raw.isdigit() or not name or not response:
        return web.json_response(
            {"ok": False, "message": "Guild-ID, Name und Antwort sind erforderlich."},
            status=400,
        )
    if not name.replace("_", "").replace("-", "").isalnum():
        return web.json_response(
            {"ok": False, "message": "Ungültiger Command-Name."},
            status=400,
        )

    def write() -> None:
        con = _connect(config)
        try:
            con.execute(
                """
                INSERT INTO custom_commands(guild_id,name,response,created_by)
                VALUES(?,?,?,0)
                ON CONFLICT(guild_id,name) DO UPDATE SET
                    response=excluded.response,
                    enabled=1,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(guild_raw), name, response),
            )
            con.commit()
        finally:
            con.close()

    await asyncio.to_thread(write)
    return web.json_response({"ok": True})


def _optional_snowflake(value) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError(f"Ungültige Discord-ID: {raw}")
    return int(raw)


async def api_workspace_config(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    guild_raw = str(data.get("guild_id", "")).strip()
    if not guild_raw.isdigit():
        return web.json_response(
            {"ok": False, "message": "Guild-ID fehlt/ungültig."}, status=400
        )
    try:
        welcome = _optional_snowflake(data.get("welcome_channel_id"))
        log_channel = _optional_snowflake(data.get("general_log_channel_id"))
        role = _optional_snowflake(data.get("auto_role_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    message = str(data.get("welcome_message", ""))

    def write() -> None:
        con = _connect(config)
        try:
            con.execute(
                """
                INSERT INTO guild_settings(
                    guild_id,welcome_channel_id,general_log_channel_id,auto_role_id,welcome_message
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    welcome_channel_id=excluded.welcome_channel_id,
                    general_log_channel_id=excluded.general_log_channel_id,
                    auto_role_id=excluded.auto_role_id,
                    welcome_message=excluded.welcome_message,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(guild_raw), welcome, log_channel, role, message),
            )
            con.commit()
        finally:
            con.close()

    await asyncio.to_thread(write)
    return web.json_response({"ok": True})


async def api_workspace_plugin(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    extension = str(data.get("extension", "")).strip()
    enabled = bool(data.get("enabled"))
    if not extension.startswith("cogs.") or not all(
        part.replace("_", "").isalnum() for part in extension.split(".")
    ):
        return web.json_response(
            {"ok": False, "message": "Ungültige Extension."}, status=400
        )
    if extension == "cogs.management.automation_suite" and not enabled:
        return web.json_response(
            {"ok": False, "message": "Automation Suite kann nicht deaktiviert werden."},
            status=400,
        )
    command_id = await asyncio.to_thread(
        _enqueue,
        config,
        "plugin-toggle",
        {"extension": extension, "enabled": enabled},
    )
    return web.json_response({"ok": True, "command_id": command_id})


def register_workspace_routes(app: web.Application) -> None:
    # Internal dashboard routes only. No public /api/v1 surface is exposed.
    app.router.add_get("/workspace", workspace_page)
    app.router.add_get("/api/workspace/summary", api_workspace_summary)
    app.router.add_post("/api/workspace/composer", api_workspace_composer)
    app.router.add_post("/api/workspace/custom-command", api_workspace_custom_command)
    app.router.add_post("/api/workspace/config", api_workspace_config)
    app.router.add_post("/api/workspace/plugin", api_workspace_plugin)
