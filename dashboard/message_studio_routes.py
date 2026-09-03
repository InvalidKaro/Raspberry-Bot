from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from aiohttp import web

from .services.discord_service import DiscordServiceError

FIXED_GUILD_ID = 1162733312226361454
TEXT_CHANNEL_TYPES = {0, 5, 10, 11, 12}


def _db_path(config) -> Path:
    path = Path(config.database_path)
    return path if path.is_absolute() else Path(config.repo_path) / path


def _snowflake(value: object, name: str) -> int:
    raw = str(value or "").strip()
    if not raw.isdigit():
        raise ValueError(f"{name} fehlt oder ist ungültig.")
    return int(raw)


def _color(value: object) -> int:
    raw = str(value or "5865F2").strip().replace("#", "")
    try:
        number = int(raw, 16)
    except ValueError:
        number = 0x5865F2
    return max(0, min(0xFFFFFF, number))


def _embed_payload(raw: object) -> dict[str, Any] | None:
    data = raw if isinstance(raw, dict) else {}
    title = str(data.get("title") or "").strip()[:256]
    description = str(data.get("description") or "").strip()[:4096]
    footer = str(data.get("footer") or "").strip()[:2048]
    image = str(data.get("image") or "").strip()
    if image and not image.startswith("https://"):
        raise ValueError("Bild-URL muss mit https:// beginnen.")
    if not any((title, description, footer, image)):
        return None
    embed: dict[str, Any] = {"color": _color(data.get("color"))}
    if title:
        embed["title"] = title
    if description:
        embed["description"] = description
    if footer:
        embed["footer"] = {"text": footer}
    if image:
        embed["image"] = {"url": image}
    return embed


def _components(raw: object) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("Buttons müssen als JSON-Array gesendet werden.")
    buttons: list[dict[str, Any]] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "Link").strip()[:80] or "Link"
        url = str(item.get("url") or "").strip()
        if not url.startswith("https://"):
            raise ValueError(f"Button „{label}“ benötigt eine HTTPS-URL.")
        buttons.append({"type": 2, "style": 5, "label": label, "url": url})
    return [{"type": 1, "components": buttons}] if buttons else []


async def api_message_send(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        guild_id = _snowflake(data.get("guild_id"), "Guild-ID")
        channel_id = _snowflake(data.get("channel_id"), "Channel-ID")
        if guild_id != FIXED_GUILD_ID:
            raise ValueError("Dashboard Pro ist auf die konfigurierte Guild begrenzt.")

        content = str(data.get("content") or "").strip()[:2000]
        embed = _embed_payload(data.get("embed"))
        components = _components(data.get("buttons"))
        if not content and not embed:
            raise ValueError("Die Nachricht ist leer.")

        discord = request.app["discord"]
        channels = await asyncio.wait_for(discord.channels_detailed(guild_id), timeout=5.0)
        channel = next((row for row in channels if str(row.get("id")) == str(channel_id)), None)
        if channel is None:
            raise ValueError("Der gewählte Channel gehört nicht zur Dashboard-Guild oder wurde gelöscht.")
        if int(channel.get("type", -1)) not in TEXT_CHANNEL_TYPES:
            raise ValueError("Der gewählte Channel unterstützt keine normalen Nachrichten.")

        payload: dict[str, Any] = {
            "allowed_mentions": {"parse": []},
        }
        if content:
            payload["content"] = content
        if embed:
            payload["embeds"] = [embed]
        if components:
            payload["components"] = components

        result = await asyncio.wait_for(
            discord._request("POST", f"/channels/{channel_id}/messages", payload=payload),
            timeout=8.0,
        )
        message_id = str(result.get("id") or "")
        return web.json_response(
            {
                "ok": True,
                "message": "Nachricht direkt über Discord gesendet.",
                "message_id": message_id,
                "channel_id": str(channel_id),
                "guild_id": str(guild_id),
                "discord_url": f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}" if message_id else None,
            }
        )
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "message": "Discord hat beim Senden nicht rechtzeitig geantwortet."}, status=504)
    except (ValueError, json.JSONDecodeError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    except DiscordServiceError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=502)
    except Exception as exc:
        return web.json_response({"ok": False, "message": f"{type(exc).__name__}: {exc}"}, status=500)


async def api_message_status(request: web.Request) -> web.Response:
    try:
        guild_id = _snowflake(request.query.get("guild_id"), "Guild-ID")
        if guild_id != FIXED_GUILD_ID:
            raise ValueError("Dashboard Pro ist auf die konfigurierte Guild begrenzt.")
        config = request.app["config"]
        db_path = _db_path(config)

        def read_rows() -> list[dict[str, Any]]:
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    """SELECT id,channel_id,send_at,status,result,created_at,processed_at
                       FROM dashboard_scheduled_messages
                       WHERE guild_id=?
                       ORDER BY id DESC LIMIT 12""",
                    (guild_id,),
                ).fetchall()
                return [
                    {
                        **dict(row),
                        "id": str(row["id"]),
                        "channel_id": str(row["channel_id"]),
                    }
                    for row in rows
                ]
            finally:
                con.close()

        rows = await asyncio.to_thread(read_rows)
        return web.json_response({"ok": True, "messages": rows})
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    except sqlite3.Error as exc:
        return web.json_response({"ok": False, "message": f"SQLite: {exc}"}, status=500)


def register_message_studio_routes(app: web.Application) -> None:
    app.router.add_post("/api/ops/messages/send", api_message_send)
    app.router.add_get("/api/ops/messages/status", api_message_status)
