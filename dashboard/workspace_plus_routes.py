from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from aiohttp import web

from services.smart_search import rank_candidates

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
        if (
            key.endswith("_id")
            or key in {"guild_id", "user_id", "channel_id", "role_id"}
        ) and data[key] is not None:
            data[key] = str(data[key])
    return data


def _guild_id(raw: object) -> int:
    value = str(raw or "").strip()
    if not value.isdigit():
        raise ValueError("Guild-ID fehlt oder ist ungültig.")
    return int(value)


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


def _search_candidates(
    con: sqlite3.Connection, guild_id: int, query: str
) -> list[dict]:
    like = f"%{query.strip()}%"
    result: list[dict] = []

    for row in con.execute(
        """
        SELECT kind,title,entry_key AS key,content,COALESCE(tags,'') AS tags,'' AS category
        FROM knowledge_entries
        WHERE guild_id=? AND (
            lower(title) LIKE lower(?) OR lower(entry_key) LIKE lower(?) OR
            lower(content) LIKE lower(?) OR lower(COALESCE(tags,'')) LIKE lower(?)
        )
        ORDER BY updated_at DESC
        LIMIT 80
        """,
        (guild_id, like, like, like, like),
    ).fetchall():
        result.append(dict(row))

    for row in con.execute(
        """
        SELECT 'training' AS kind,title,CAST(id AS TEXT) AS key,content,'' AS tags,category
        FROM training_library
        WHERE guild_id=? AND (
            lower(title) LIKE lower(?) OR lower(content) LIKE lower(?) OR lower(category) LIKE lower(?)
        )
        ORDER BY updated_at DESC
        LIMIT 60
        """,
        (guild_id, like, like, like),
    ).fetchall():
        result.append(dict(row))

    for row in con.execute(
        """
        SELECT 'quiz' AS kind,question AS title,CAST(id AS TEXT) AS key,
               answer || CASE WHEN COALESCE(explanation,'')='' THEN '' ELSE ' · ' || explanation END AS content,
               '' AS tags,category
        FROM quiz_questions
        WHERE guild_id=? AND (
            lower(question) LIKE lower(?) OR lower(answer) LIKE lower(?) OR lower(category) LIKE lower(?)
        )
        ORDER BY id DESC
        LIMIT 50
        """,
        (guild_id, like, like, like),
    ).fetchall():
        result.append(dict(row))

    for row in con.execute(
        """
        SELECT 'template' AS kind,title,name AS key,body AS content,'' AS tags,'' AS category
        FROM content_templates
        WHERE guild_id=? AND (
            lower(title) LIKE lower(?) OR lower(name) LIKE lower(?) OR lower(body) LIKE lower(?)
        )
        ORDER BY updated_at DESC
        LIMIT 50
        """,
        (guild_id, like, like, like),
    ).fetchall():
        result.append(dict(row))

    for row in con.execute(
        """
        SELECT 'form' AS kind,title,name AS key,questions_json AS content,'' AS tags,'' AS category
        FROM forms
        WHERE guild_id=? AND (
            lower(title) LIKE lower(?) OR lower(name) LIKE lower(?) OR lower(questions_json) LIKE lower(?)
        )
        ORDER BY updated_at DESC
        LIMIT 50
        """,
        (guild_id, like, like, like),
    ).fetchall():
        result.append(dict(row))

    for row in con.execute(
        """
        SELECT 'command' AS kind,'!' || name AS title,name AS key,response AS content,'' AS tags,'' AS category
        FROM custom_commands
        WHERE guild_id=? AND enabled=1 AND (
            lower(name) LIKE lower(?) OR lower(response) LIKE lower(?)
        )
        ORDER BY updated_at DESC
        LIMIT 50
        """,
        (guild_id, like, like),
    ).fetchall():
        result.append(dict(row))

    return result


async def workspace_studio_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "workspace_studio.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_workspace_guilds(request: web.Request) -> web.Response:
    config = request.app["config"]

    def read() -> list[str]:
        con = _connect(config)
        try:
            rows = con.execute(
                """
                SELECT DISTINCT CAST(guild_id AS TEXT) guild_id FROM (
                    SELECT guild_id FROM guild_settings
                    UNION SELECT guild_id FROM knowledge_entries
                    UNION SELECT guild_id FROM workspace_tasks
                    UNION SELECT guild_id FROM content_templates
                )
                WHERE guild_id IS NOT NULL
                ORDER BY guild_id
                """
            ).fetchall()
            return [str(row["guild_id"]) for row in rows]
        finally:
            con.close()

    return web.json_response({"ok": True, "guilds": await asyncio.to_thread(read)})


async def api_workspace_search(request: web.Request) -> web.Response:
    config = request.app["config"]
    query = str(request.query.get("q", "")).strip()
    try:
        guild_id = _guild_id(request.query.get("guild_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    if len(query) < 2:
        return web.json_response(
            {"ok": True, "query": query, "results": [], "suggestions": []}
        )

    def read() -> list[dict]:
        con = _connect(config)
        try:
            return rank_candidates(
                _search_candidates(con, guild_id, query), query, limit=20
            )
        finally:
            con.close()

    rows = await asyncio.to_thread(read)
    suggestions = [
        {
            "label": f"[{row['kind']}] {row['title']}",
            "value": str(row.get("key") or row["title"]),
        }
        for row in rows[:12]
    ]
    return web.json_response(
        {"ok": True, "query": query, "results": rows, "suggestions": suggestions}
    )


async def api_workspace_catalog(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        guild_id = _guild_id(request.query.get("guild_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    def read() -> dict:
        con = _connect(config)
        try:
            queries = {
                "templates": "SELECT id,name,title,body,color,updated_at FROM content_templates WHERE guild_id=? ORDER BY updated_at DESC LIMIT 30",
                "forms": "SELECT id,name,title,questions_json,updated_at FROM forms WHERE guild_id=? ORDER BY updated_at DESC LIMIT 30",
                "trainings": "SELECT id,title,category,content,source_url,updated_at FROM training_library WHERE guild_id=? ORDER BY updated_at DESC LIMIT 30",
                "tasks": "SELECT id,title,details,status,assigned_to,due_at,updated_at FROM workspace_tasks WHERE guild_id=? ORDER BY updated_at DESC LIMIT 30",
                "events": "SELECT id,title,description,starts_at,channel_id FROM workspace_events WHERE guild_id=? ORDER BY starts_at DESC LIMIT 30",
                "commands": "SELECT name,response,enabled,updated_at FROM custom_commands WHERE guild_id=? ORDER BY updated_at DESC LIMIT 30",
                "quiz": "SELECT id,category,question,answer,explanation FROM quiz_questions WHERE guild_id=? ORDER BY id DESC LIMIT 30",
            }
            return {
                key: [
                    _json_row(row)
                    for row in con.execute(sql, (guild_id,)).fetchall()
                ]
                for key, sql in queries.items()
            }
        finally:
            con.close()

    return web.json_response(
        {"ok": True, "guild_id": str(guild_id), **await asyncio.to_thread(read)}
    )


def _valid_url(value: object) -> str:
    raw = str(value or "").strip()
    if raw and not raw.startswith(("http://", "https://")):
        raise ValueError("Bild-URLs müssen mit http:// oder https:// beginnen.")
    return raw


async def api_workspace_embed_send(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    channel_id = str(data.get("channel_id", "")).strip()
    if not channel_id.isdigit():
        return web.json_response(
            {"ok": False, "message": "Channel-ID fehlt oder ist ungültig."},
            status=400,
        )

    title = str(data.get("title", "")).strip()[:256]
    text = str(data.get("text", "")).strip()[:4096]
    if not title and not text:
        return web.json_response(
            {"ok": False, "message": "Titel oder Beschreibung ist erforderlich."},
            status=400,
        )

    try:
        thumbnail = _valid_url(data.get("thumbnail"))
        image = _valid_url(data.get("image"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    raw_fields = data.get("fields") or []
    fields: list[dict] = []
    if isinstance(raw_fields, list):
        for item in raw_fields[:25]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:256]
            value = str(item.get("value", "")).strip()[:1024]
            if name and value:
                fields.append(
                    {
                        "name": name,
                        "value": value,
                        "inline": bool(item.get("inline")),
                    }
                )

    payload = {
        "channel_id": channel_id,
        "title": title,
        "text": text,
        "color": str(data.get("color", ""))[:20],
        "author": str(data.get("author", ""))[:256],
        "footer": str(data.get("footer", ""))[:2048],
        "thumbnail": thumbnail,
        "image": image,
        "fields": fields,
    }
    command_id = await asyncio.to_thread(
        _enqueue, config, "send-embed-v2", payload
    )
    return web.json_response({"ok": True, "command_id": command_id})


def register_workspace_plus_routes(app: web.Application) -> None:
    # Dashboard-only routes. Public /api/v1 endpoints were intentionally removed.
    app.router.add_get("/workspace/studio", workspace_studio_page)
    app.router.add_get("/api/workspace/guilds", api_workspace_guilds)
    app.router.add_get("/api/workspace/search", api_workspace_search)
    app.router.add_get("/api/workspace/catalog", api_workspace_catalog)
    app.router.add_post("/api/workspace/embed-send", api_workspace_embed_send)
