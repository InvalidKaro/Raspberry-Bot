from __future__ import annotations

import asyncio
import ipaddress
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

AMBIENT_CATALOG = [
    {"key": "rain", "label": "Regen am Fenster", "icon": "🌧️", "description": "Mehrschichtiger Regen, Tropfen und tiefer Raum-Rumble."},
    {"key": "storm", "label": "Gewitter", "icon": "⛈️", "description": "Dichter Regen, Donnerteppich und langsame Druckwellen."},
    {"key": "fireplace", "label": "Kamin", "icon": "🔥", "description": "Glut, Knistern und wechselnde Crackle-Layer."},
    {"key": "forest", "label": "Wald", "icon": "🌲", "description": "Blätterwind mit Vogel- und Insekten-Tönen."},
    {"key": "cafe", "label": "Café", "icon": "☕", "description": "Gedämpfter Raum, Murmelteppich und Geschirr-Höhen."},
    {"key": "ocean", "label": "Ozean", "icon": "🌊", "description": "Langsame Brandung mit Luft- und Gischt-Layer."},
    {"key": "train", "label": "Nachtzug", "icon": "🚆", "description": "Schienen-Rhythmus, Motor-Rumble und Fahrtgeräusch."},
    {"key": "night", "label": "Sommernacht", "icon": "🌙", "description": "Nachtwind mit Grillen-/Insekten-Tönen."},
    {"key": "spaceship", "label": "Raumschiff", "icon": "🛰️", "description": "Maschinen-Drones, Lüftung und Elektronik-Hum."},
    {"key": "fan", "label": "Ventilator", "icon": "🌀", "description": "Luftstrom mit Motor-Grundton und leichtem Puls."},
    {"key": "city", "label": "Stadt bei Nacht", "icon": "🌆", "description": "Verkehr, diffuse Straße und entfernte Motoren."},
]


def _db_path(config) -> Path:
    configured = Path(config.database_path)
    if configured.is_absolute():
        return configured
    return Path(config.repo_path) / configured


def _connect(config) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(config))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(con)
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS voice_radio_stations (guild_id INTEGER NOT NULL,name TEXT NOT NULL,stream_url TEXT NOT NULL,genre TEXT,homepage TEXT,created_by INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,name))""")
    con.execute("""CREATE TABLE IF NOT EXISTS voice_ambient_sources (guild_id INTEGER NOT NULL,name TEXT NOT NULL,audio_url TEXT NOT NULL,category TEXT,created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,name))""")
    con.execute("""CREATE TABLE IF NOT EXISTS voice_playback_history (id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,source_name TEXT,started_by INTEGER NOT NULL,started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    con.commit()


def _guild_id(value: object) -> int:
    raw = str(value or "").strip()
    if not raw.isdigit():
        raise ValueError("Guild-ID fehlt oder ist ungültig.")
    return int(raw)


def _channel_id(value: object) -> int:
    raw = str(value or "").strip()
    if not raw.isdigit():
        raise ValueError("Voice-Channel-ID fehlt oder ist ungültig.")
    return int(raw)


def _clean_name(value: object) -> str:
    raw = " ".join(str(value or "").strip().split())[:48]
    if not raw or not all(ch.isalnum() or ch in " -_().&+" for ch in raw):
        raise ValueError("Ungültiger Name. Erlaubt: Buchstaben, Zahlen, Leerzeichen und - _ ( ) . & +")
    return raw


def _safe_https(value: object, *, optional: bool = False) -> str:
    raw = str(value or "").strip()
    if optional and not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Es sind nur öffentliche HTTPS-URLs erlaubt.")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Lokale Ziele sind nicht erlaubt.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return raw
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast:
        raise ValueError("Private/lokale IP-Adressen sind nicht erlaubt.")
    return raw


def _clamp(value: object, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


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


async def media_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "media.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_media_state(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        guild_id = _guild_id(request.query.get("guild_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    def read() -> dict:
        con = _connect(config)
        try:
            stations = [dict(row) for row in con.execute(
                "SELECT name,stream_url,COALESCE(genre,'') genre,COALESCE(homepage,'') homepage,enabled,updated_at FROM voice_radio_stations WHERE guild_id=? ORDER BY name COLLATE NOCASE",
                (guild_id,),
            ).fetchall()]
            ambient_sources = [dict(row) for row in con.execute(
                "SELECT name,audio_url,COALESCE(category,'Custom') category,updated_at FROM voice_ambient_sources WHERE guild_id=? ORDER BY category,name COLLATE NOCASE",
                (guild_id,),
            ).fetchall()]
            history = [dict(row) for row in con.execute(
                "SELECT kind,title,source_name,started_by,started_at FROM voice_playback_history WHERE guild_id=? ORDER BY id DESC LIMIT 30",
                (guild_id,),
            ).fetchall()]
            queue = [dict(row) for row in con.execute(
                "SELECT id,action,status,result,created_at,processed_at FROM dashboard_commands WHERE action LIKE 'media-%' ORDER BY id DESC LIMIT 20"
            ).fetchall()]
            return {"stations": stations, "ambient_sources": ambient_sources, "history": history, "queue": queue}
        finally:
            con.close()

    data = await asyncio.to_thread(read)
    return web.json_response({"ok": True, "guild_id": str(guild_id), "ambient_catalog": AMBIENT_CATALOG, **data})


async def api_media_station(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    try:
        guild_id = _guild_id(data.get("guild_id"))
        name = _clean_name(data.get("name"))
        action = str(data.get("action", "save")).strip().lower()
        if action not in {"save", "delete"}:
            raise ValueError("Unbekannte Sender-Aktion.")
        if action == "save":
            stream_url = _safe_https(data.get("stream_url"))
            homepage = _safe_https(data.get("homepage"), optional=True)
            genre = str(data.get("genre", "")).strip()[:60]
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    def write() -> None:
        con = _connect(config)
        try:
            if action == "delete":
                con.execute("DELETE FROM voice_radio_favorites WHERE guild_id=? AND lower(station_name)=lower(?)", (guild_id, name))
                con.execute("DELETE FROM voice_radio_stations WHERE guild_id=? AND lower(name)=lower(?)", (guild_id, name))
            else:
                con.execute(
                    """INSERT INTO voice_radio_stations(guild_id,name,stream_url,genre,homepage,created_by) VALUES(?,?,?,?,?,0)
                    ON CONFLICT(guild_id,name) DO UPDATE SET stream_url=excluded.stream_url,genre=excluded.genre,homepage=excluded.homepage,enabled=1,updated_at=CURRENT_TIMESTAMP""",
                    (guild_id, name, stream_url, genre, homepage),
                )
            con.commit()
        finally:
            con.close()

    await asyncio.to_thread(write)
    return web.json_response({"ok": True, "message": "Sender gespeichert." if action == "save" else "Sender gelöscht."})


async def api_media_ambient_source(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    try:
        guild_id = _guild_id(data.get("guild_id"))
        name = _clean_name(data.get("name"))
        action = str(data.get("action", "save")).strip().lower()
        if action not in {"save", "delete"}:
            raise ValueError("Unbekannte Ambient-Aktion.")
        if action == "save":
            audio_url = _safe_https(data.get("audio_url"))
            category = str(data.get("category", "Custom")).strip()[:40] or "Custom"
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    def write() -> None:
        con = _connect(config)
        try:
            if action == "delete":
                con.execute("DELETE FROM voice_ambient_sources WHERE guild_id=? AND lower(name)=lower(?)", (guild_id, name))
            else:
                con.execute(
                    """INSERT INTO voice_ambient_sources(guild_id,name,audio_url,category,created_by) VALUES(?,?,?,?,0)
                    ON CONFLICT(guild_id,name) DO UPDATE SET audio_url=excluded.audio_url,category=excluded.category,updated_at=CURRENT_TIMESTAMP""",
                    (guild_id, name, audio_url, category),
                )
            con.commit()
        finally:
            con.close()

    await asyncio.to_thread(write)
    return web.json_response({"ok": True, "message": "Ambient-Quelle gespeichert." if action == "save" else "Ambient-Quelle gelöscht."})


async def api_media_action(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    try:
        guild_id = _guild_id(data.get("guild_id"))
        action = str(data.get("action", "")).strip().lower()
        payload: dict = {"guild_id": str(guild_id)}
        if action in {"radio-play", "ambient-play", "ambient-source-play"}:
            payload["channel_id"] = str(_channel_id(data.get("channel_id")))
            payload["volume"] = _clamp(data.get("volume"), 10, 120, 65)
        if action == "radio-play":
            payload["station"] = _clean_name(data.get("station"))
        elif action == "ambient-play":
            scene = str(data.get("scene", "")).strip().lower()
            if scene not in {item["key"] for item in AMBIENT_CATALOG}:
                raise ValueError("Ambient-Szene ist ungültig.")
            payload["scene"] = scene
            payload["minutes"] = _clamp(data.get("minutes"), 0, 480, 0)
        elif action == "ambient-source-play":
            payload["source"] = _clean_name(data.get("source"))
            payload["minutes"] = _clamp(data.get("minutes"), 0, 480, 0)
        elif action == "volume":
            payload["volume"] = _clamp(data.get("volume"), 10, 120, 65)
        elif action not in {"stop", "disconnect"}:
            raise ValueError("Unbekannte Media-Aktion.")
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    command_id = await asyncio.to_thread(_enqueue, config, f"media-{action}", payload)
    return web.json_response({"ok": True, "command_id": command_id, "message": "Aktion an den Bot übergeben."})


def register_media_routes(app: web.Application) -> None:
    app.router.add_get("/media", media_page)
    app.router.add_get("/api/media/state", api_media_state)
    app.router.add_post("/api/media/station", api_media_station)
    app.router.add_post("/api/media/ambient-source", api_media_ambient_source)
    app.router.add_post("/api/media/action", api_media_action)
