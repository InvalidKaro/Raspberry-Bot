from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import re
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiohttp import web

from .services.discord_service import DiscordServiceError
from .services.system_service import get_status

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_activity(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,kind TEXT NOT NULL,title TEXT NOT NULL,
    detail TEXT,actor_id INTEGER,target_id TEXT,source TEXT,metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dashboard_activity_guild_time ON dashboard_activity(guild_id,created_at);
CREATE TABLE IF NOT EXISTS dashboard_error_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,source TEXT NOT NULL,error_type TEXT NOT NULL,
    message TEXT,traceback TEXT,command_name TEXT,commit_sha TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_runtime_state(
    guild_id INTEGER PRIMARY KEY,state_json TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_notification_rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,metric TEXT NOT NULL,
    operator TEXT NOT NULL DEFAULT '>',threshold REAL NOT NULL,duration_seconds INTEGER NOT NULL DEFAULT 0,
    cooldown_seconds INTEGER NOT NULL DEFAULT 1800,channel_id INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
    last_fired_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_workflows(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,trigger_json TEXT NOT NULL,
    steps_json TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_workflow_state(
    workflow_id INTEGER PRIMARY KEY,cursor_json TEXT,last_run_at TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_workflow_runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,workflow_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,status TEXT NOT NULL,
    detail TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_scheduled_messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,channel_id INTEGER NOT NULL,send_at TEXT NOT NULL,
    content TEXT,embed_json TEXT,buttons_json TEXT,status TEXT NOT NULL DEFAULT 'pending',result TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,processed_at TEXT
);
CREATE TABLE IF NOT EXISTS system_snapshots_hourly(
    guild_id INTEGER NOT NULL,hour TEXT NOT NULL,cpu_avg REAL,cpu_peak REAL,ram_avg REAL,ram_peak REAL,
    temperature_avg REAL,temperature_peak REAL,disk_avg REAL,load_avg REAL,pihole_ok INTEGER,tailscale_ok INTEGER,
    samples INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(guild_id,hour)
);
CREATE TABLE IF NOT EXISTS dashboard_widget_layout(
    guild_id INTEGER PRIMARY KEY,layout_json TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_feature_flags(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL DEFAULT 0,
    feature_key TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id,user_id,feature_key)
);
CREATE TABLE IF NOT EXISTS dashboard_org_nodes(
    guild_id INTEGER NOT NULL,node_key TEXT NOT NULL,parent_key TEXT,label TEXT NOT NULL,role_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'team',position INTEGER NOT NULL DEFAULT 0,metadata_json TEXT,
    PRIMARY KEY(guild_id,node_key)
);
CREATE TABLE IF NOT EXISTS dashboard_ticket_board(
    ticket_id INTEGER PRIMARY KEY,lane TEXT NOT NULL DEFAULT 'new',position INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_panel_versions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,panel_id INTEGER NOT NULL,snapshot_json TEXT NOT NULL,
    note TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_gpio_devices(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,pin INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'led',active_high INTEGER NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 0,
    config_json TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(guild_id,name)
);
CREATE TABLE IF NOT EXISTS dashboard_display_layout(
    guild_id INTEGER PRIMARY KEY,layout_json TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_changes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,entity_type TEXT NOT NULL,entity_key TEXT NOT NULL,
    before_json TEXT,after_json TEXT,reversible INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dashboard_changes_time ON dashboard_changes(created_at);
"""

DEFAULT_WIDGETS = [
    {"key": "health", "title": "Health Score", "w": 1},
    {"key": "system", "title": "CPU / RAM / Temp", "w": 2},
    {"key": "voice", "title": "Voice & YouTube", "w": 1},
    {"key": "tickets", "title": "Tickets", "w": 1},
    {"key": "tasks", "title": "Tasks", "w": 1},
    {"key": "commands", "title": "Commands", "w": 1},
    {"key": "pihole", "title": "Pi-hole", "w": 1},
    {"key": "briefing", "title": "Daily Briefing", "w": 2},
]

FEATURE_CATALOG = [
    ("server-map", "Live Server Map", "/ops#discord"),
    ("analytics", "Command Analytics 2.0", "/ops#analytics"),
    ("activity", "Live Activity Feed", "/ops#activity"),
    ("media", "Media Center 2.0", "/ops#media"),
    ("now-playing", "Now Playing Fullscreen", "/now-playing"),
    ("widgets", "Widget Dashboard", "/ops#overview"),
    ("history", "Raspberry Pi History", "/ops#history"),
    ("incidents", "Incident Center", "/ops#incidents"),
    ("errors", "Error Explorer", "/ops#errors"),
    ("topology", "Dependency Map / Service Topology", "/ops#topology"),
    ("deploy", "Deployment Center / Rollback", "/ops#deploy"),
    ("lab", "Feature Lab", "/ops#lab"),
    ("plugins", "Plugin Manager 2.0", "/ops#plugins"),
    ("profiler", "Resource Profiler", "/ops#profiler"),
    ("database", "Database Insights / Timeline", "/ops#database"),
    ("backups", "Backup Center", "/ops#backups"),
    ("permissions", "Permission Visualizer", "/ops#permissions"),
    ("member", "Member 360° View", "/ops#member"),
    ("org", "Org Chart Editor", "/ops#org"),
    ("tickets", "Ticket Operations Board", "/ops#tickets"),
    ("calendar", "Operations Calendar", "/ops#calendar"),
    ("workflows", "Visual Workflow / Automation Builder", "/ops#workflows"),
    ("messages", "Message Studio 2.0 / Discord Live Preview", "/ops#messages"),
    ("panels", "Panel Gallery", "/ops#panels"),
    ("status", "Public Status Page", "/status"),
    ("display", "Pi Display Designer", "/ops#display"),
    ("gpio", "GPIO Designer", "/ops#gpio"),
    ("thermal", "Thermal Dashboard", "/ops#history"),
    ("network", "Network Center", "/ops#network"),
    ("notifications", "Notification Rules", "/ops#notifications"),
    ("briefing", "Daily Control Briefing", "/ops#overview"),
    ("records", "Records Page", "/ops#records"),
    ("timemachine", "Time Machine", "/ops#timemachine"),
]

PERMISSION_BITS = {
    "Administrator": 3,
    "Manage Channels": 4,
    "Manage Server": 5,
    "View Audit Log": 7,
    "View Channel": 10,
    "Send Messages": 11,
    "Manage Messages": 13,
    "Read Message History": 16,
    "Connect": 20,
    "Speak": 21,
    "Mute Members": 22,
    "Deafen Members": 23,
    "Move Members": 24,
    "Manage Roles": 28,
    "Manage Webhooks": 29,
    "Use Application Commands": 31,
    "Manage Events": 33,
    "Manage Threads": 34,
    "Moderate Members": 40,
    "Use Soundboard": 42,
    "Send Voice Messages": 46,
}


def _db_path(config) -> Path:
    path = Path(config.database_path)
    return path if path.is_absolute() else Path(config.repo_path) / path


def _connect(config) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(config))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ensure_schema(config) -> None:
    con = _connect(config)
    try:
        con.executescript(OPS_SCHEMA)
        con.commit()
    finally:
        con.close()


def _guild(value: object) -> int:
    raw = str(value or "").strip()
    if not raw.isdigit():
        raise ValueError("Guild-ID fehlt oder ist ungültig.")
    return int(raw)


def _int(value: object, default: int = 0, low: int | None = None, high: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def _json_load(value: object, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (ValueError, TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _rows(rows) -> list[dict]:
    return [_json_safe(dict(row)) for row in rows]


def _json_safe(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v, key) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v, key) for v in value]
    if isinstance(value, int) and (key == "id" or key.endswith("_id") or abs(value) > 9_000_000_000_000_000):
        return str(value)
    return value


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Unsafe SQLite identifier")
    return '"' + name + '"'


def _enqueue(config, action: str, payload: dict[str, Any]) -> int:
    con = _connect(config)
    try:
        cur = con.execute(
            "INSERT INTO dashboard_commands(action,payload_json) VALUES(?,?)",
            (action, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def _record_change(
    con: sqlite3.Connection,
    guild_id: int | None,
    entity_type: str,
    entity_key: str,
    before: Any,
    after: Any,
    *,
    reversible: bool,
) -> int:
    cur = con.execute(
        """INSERT INTO dashboard_changes(guild_id,entity_type,entity_key,before_json,after_json,reversible)
        VALUES(?,?,?,?,?,?)""",
        (
            guild_id,
            entity_type[:80],
            entity_key[:180],
            json.dumps(before, ensure_ascii=False, separators=(",", ":")) if before is not None else None,
            json.dumps(after, ensure_ascii=False, separators=(",", ":")) if after is not None else None,
            int(reversible),
        ),
    )
    return int(cur.lastrowid)


def _youtube_video_id(url: object) -> str | None:
    raw = str(url or "")
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host.endswith("youtu.be"):
        value = parsed.path.strip("/").split("/")[0]
        return value if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", value or "") else None
    if "youtube.com" in host:
        value = (parse_qs(parsed.query).get("v") or [""])[0]
        return value if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", value or "") else None
    return None


def _health_score(system: dict[str, Any], counts: dict[str, Any]) -> tuple[int, list[str]]:
    score = 100
    notes: list[str] = []
    if not system.get("bot_active"):
        score -= 35
        notes.append("Bot service offline")
    temp = system.get("temperature_c")
    if isinstance(temp, (int, float)):
        if temp >= 80:
            score -= 20
            notes.append("CPU temperature critical")
        elif temp >= 70:
            score -= 8
            notes.append("CPU temperature elevated")
    ram = float(system.get("memory_percent") or 0)
    if ram >= 90:
        score -= 15
        notes.append("RAM pressure critical")
    elif ram >= 80:
        score -= 6
        notes.append("RAM pressure elevated")
    disk = float(system.get("disk_percent") or 0)
    if disk >= 95:
        score -= 15
        notes.append("Disk almost full")
    elif disk >= 85:
        score -= 5
        notes.append("Disk usage elevated")
    errors = int(counts.get("errors_today") or 0)
    if errors >= 20:
        score -= 12
        notes.append("High command error count")
    elif errors >= 5:
        score -= 5
        notes.append("Several command errors today")
    failed = int(counts.get("failed_dashboard_commands") or 0)
    if failed >= 5:
        score -= 8
        notes.append("Dashboard command failures")
    pihole = system.get("pihole") or {}
    if pihole.get("installed") and not pihole.get("active"):
        score -= 4
        notes.append("Pi-hole inactive")
    return max(0, min(100, score)), notes or ["All monitored core checks look normal"]


def _read_counts(config, guild_id: int | None = None) -> dict[str, Any]:
    con = _connect(config)
    try:
        where = "" if guild_id is None else " AND guild_id=?"
        params: tuple[Any, ...] = () if guild_id is None else (guild_id,)
        def count(sql: str, args: tuple[Any, ...] = ()) -> int:
            row = con.execute(sql, args).fetchone()
            return int(row[0] if row else 0)
        result = {
            "commands_today": count("SELECT COUNT(*) FROM command_analytics WHERE created_at>=date('now')" + where, params),
            "errors_today": count("SELECT COUNT(*) FROM command_analytics WHERE success=0 AND created_at>=date('now')" + where, params),
            "open_tickets": count("SELECT COUNT(*) FROM tickets WHERE status!='closed'" + where, params),
            "open_tasks": count("SELECT COUNT(*) FROM workspace_tasks WHERE status NOT IN ('done','closed','completed')" + where, params),
            "failed_dashboard_commands": count("SELECT COUNT(*) FROM dashboard_commands WHERE status='failed' AND created_at>=datetime('now','-24 hours')"),
            "pending_automations": count("SELECT COUNT(*) FROM dashboard_scheduled_messages WHERE status='pending'"),
        }
        return result
    finally:
        con.close()


async def ops_page(_: web.Request) -> web.Response:
    return web.Response(text=(TEMPLATE_DIR / "ops.html").read_text(encoding="utf-8"), content_type="text/html", headers={"Cache-Control": "no-store"})


async def now_playing_page(_: web.Request) -> web.Response:
    return web.Response(text=(TEMPLATE_DIR / "now_playing.html").read_text(encoding="utf-8"), content_type="text/html", headers={"Cache-Control": "no-store"})


async def public_status_page(_: web.Request) -> web.Response:
    return web.Response(text=(TEMPLATE_DIR / "status.html").read_text(encoding="utf-8"), content_type="text/html", headers={"Cache-Control": "no-store"})


async def api_ops_summary(request: web.Request) -> web.Response:
    config = request.app["config"]
    guild_raw = request.query.get("guild_id")
    guild_id = int(guild_raw) if guild_raw and guild_raw.isdigit() else None
    system_task = get_status(config.bot_service, request.app["system_sampler"])
    git_task = request.app["git"].status()
    counts_task = asyncio.to_thread(_read_counts, config, guild_id)
    system, git, counts = await asyncio.gather(system_task, git_task, counts_task)
    score, health_notes = _health_score(system, counts)
    briefing = [
        f"{counts['commands_today']} Commands heute, {counts['errors_today']} davon fehlgeschlagen.",
        f"{counts['open_tickets']} offene Tickets und {counts['open_tasks']} offene Workspace-Tasks.",
        f"CPU {system.get('cpu_percent', 0)} %, RAM {system.get('memory_percent', 0)} %, Temperatur {system.get('temperature_c', '—')} °C.",
        f"Bot {'online' if system.get('bot_active') else 'offline'} · Pi-hole {'aktiv' if (system.get('pihole') or {}).get('active') else 'inaktiv/nicht vorhanden'}.",
    ]
    return web.json_response(_json_safe({
        "ok": True,
        "health": {"score": score, "notes": health_notes},
        "system": system,
        "git": git,
        "counts": counts,
        "briefing": briefing,
    }))


async def api_ops_analytics(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        guild_id = _guild(request.query.get("guild_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    days = _int(request.query.get("days"), 7, 1, 90)

    def read() -> dict[str, Any]:
        con = _connect(config)
        try:
            since = f"-{days} days"
            heat = _rows(con.execute(
                """SELECT CAST(strftime('%w',created_at) AS INTEGER) weekday,
                    CAST(strftime('%H',created_at) AS INTEGER) hour,COUNT(*) count,
                    SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) errors,
                    ROUND(AVG(COALESCE(duration_ms,0)),1) avg_ms
                FROM command_analytics WHERE guild_id=? AND created_at>=datetime('now',?)
                GROUP BY weekday,hour ORDER BY weekday,hour""",
                (guild_id, since),
            ).fetchall())
            top = _rows(con.execute(
                """SELECT command_name,COUNT(*) count,
                    SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) errors,
                    ROUND(AVG(duration_ms),1) avg_ms,ROUND(MAX(duration_ms),1) max_ms
                FROM command_analytics WHERE guild_id=? AND created_at>=datetime('now',?)
                GROUP BY command_name ORDER BY count DESC LIMIT 25""",
                (guild_id, since),
            ).fetchall())
            users = _rows(con.execute(
                "SELECT user_id,COUNT(*) count FROM command_analytics WHERE guild_id=? AND created_at>=datetime('now',?) GROUP BY user_id ORDER BY count DESC LIMIT 20",
                (guild_id, since),
            ).fetchall())
            guilds = _rows(con.execute(
                "SELECT guild_id,COUNT(*) count FROM command_analytics WHERE created_at>=datetime('now',?) GROUP BY guild_id ORDER BY count DESC LIMIT 20",
                (since,),
            ).fetchall())
            now = con.execute("SELECT COUNT(*) c,SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) e FROM command_analytics WHERE guild_id=? AND created_at>=datetime('now','-24 hours')", (guild_id,)).fetchone()
            prev = con.execute("SELECT COUNT(*) c,SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) e FROM command_analytics WHERE guild_id=? AND created_at>=datetime('now','-48 hours') AND created_at<datetime('now','-24 hours')", (guild_id,)).fetchone()
            return {
                "heatmap": heat,
                "top": top,
                "users": users,
                "guilds": guilds,
                "compare": {"current": {"count": now["c"], "errors": now["e"] or 0}, "previous": {"count": prev["c"], "errors": prev["e"] or 0}},
            }
        finally:
            con.close()

    data = await asyncio.to_thread(read)
    return web.json_response({"ok": True, "days": days, **data})


async def api_ops_activity(request: web.Request) -> web.Response:
    config = request.app["config"]
    guild_raw = request.query.get("guild_id")
    guild_id = int(guild_raw) if guild_raw and guild_raw.isdigit() else None
    after_id = _int(request.query.get("after_id"), 0, 0)
    limit = _int(request.query.get("limit"), 100, 1, 250)

    def read() -> list[dict]:
        con = _connect(config)
        try:
            sql = "SELECT * FROM dashboard_activity WHERE id>?"
            params: list[Any] = [after_id]
            if guild_id is not None:
                sql += " AND guild_id=?"
                params.append(guild_id)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = con.execute(sql, tuple(params)).fetchall()
            return list(reversed(_rows(rows)))
        finally:
            con.close()

    return web.json_response({"ok": True, "events": await asyncio.to_thread(read)})


async def api_ops_live(request: web.Request) -> web.StreamResponse:
    config = request.app["config"]
    guild_raw = request.query.get("guild_id")
    guild_id = int(guild_raw) if guild_raw and guild_raw.isdigit() else None
    last_id = _int(request.query.get("after_id"), 0, 0)
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    heartbeat = 0
    try:
        while True:
            def read_new() -> list[dict]:
                con = _connect(config)
                try:
                    sql = "SELECT * FROM dashboard_activity WHERE id>?"
                    params: list[Any] = [last_id]
                    if guild_id is not None:
                        sql += " AND guild_id=?"
                        params.append(guild_id)
                    sql += " ORDER BY id LIMIT 50"
                    return _rows(con.execute(sql, tuple(params)).fetchall())
                finally:
                    con.close()
            events = await asyncio.to_thread(read_new)
            if events:
                for event in events:
                    last_id = max(last_id, int(event["id"]))
                    await response.write(f"id: {last_id}\nevent: activity\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                heartbeat = 0
            else:
                heartbeat += 1
                if heartbeat >= 5:
                    await response.write(b": heartbeat\n\n")
                    heartbeat = 0
            await asyncio.sleep(2)
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    return response


async def api_ops_server_map(request: web.Request) -> web.Response:
    try:
        guild_id = _guild(request.query.get("guild_id"))
        discord = request.app["discord"]
        guild, channels, roles, members = await asyncio.gather(
            discord.guild(guild_id),
            discord.channels_detailed(guild_id),
            discord.roles_detailed(guild_id),
            discord.members(guild_id, limit=1000),
        )
    except (ValueError, DiscordServiceError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    bots = [member for member in members if member.get("bot")]
    categories = [item for item in channels if item["type"] == 4]
    children: dict[str, list[dict]] = {item["id"]: [] for item in categories}
    uncategorized: list[dict] = []
    for channel in channels:
        if channel["type"] == 4:
            continue
        parent = channel.get("parent_id")
        if parent and parent in children:
            children[parent].append(channel)
        else:
            uncategorized.append(channel)
    tree = [{**category, "children": children.get(category["id"], [])} for category in categories]
    return web.json_response(_json_safe({"ok": True, "guild": guild, "tree": tree, "uncategorized": uncategorized, "roles": roles, "bots": bots, "member_count_loaded": len(members)}))


async def api_ops_discord_edit(request: web.Request) -> web.Response:
    data = await request.json()
    try:
        guild_id = _guild(data.get("guild_id"))
        kind = str(data.get("kind", "")).lower()
        target_id = _int(data.get("id"), 0, 1)
        discord = request.app["discord"]
        if kind == "channel":
            channels = await discord.channels_detailed(guild_id)
            before = next((row for row in channels if row["id"] == str(target_id)), None)
            after = await discord.patch_channel(target_id, data.get("changes") or {})
        elif kind == "role":
            roles = await discord.roles_detailed(guild_id)
            before = next((row for row in roles if row["id"] == str(target_id)), None)
            after = await discord.patch_role(guild_id, target_id, data.get("changes") or {})
        else:
            raise ValueError("kind muss channel oder role sein")
    except (ValueError, DiscordServiceError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    def log_change() -> None:
        con = _connect(request.app["config"])
        try:
            _record_change(con, guild_id, f"discord_{kind}", str(target_id), before, after, reversible=False)
            con.execute(
                "INSERT INTO dashboard_activity(guild_id,kind,title,detail,target_id,source) VALUES(?,?,?,?,?,?)",
                (guild_id, "dashboard_edit", f"{kind.title()} geändert", str(after.get("name") or target_id), str(target_id), "dashboard.server-map"),
            )
            con.commit()
        finally:
            con.close()
    await asyncio.to_thread(log_change)
    return web.json_response({"ok": True, "result": after})


async def api_ops_member_search(request: web.Request) -> web.Response:
    try:
        guild_id = _guild(request.query.get("guild_id"))
        query = str(request.query.get("q", ""))
        members = await request.app["discord"].search_members(guild_id, query, limit=60)
        return web.json_response({"ok": True, "members": members})
    except (ValueError, DiscordServiceError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_ops_member(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        guild_id = _guild(request.query.get("guild_id"))
        user_id = _int(request.query.get("user_id"), 0, 1)
        member, roles = await asyncio.gather(
            request.app["discord"].member(guild_id, user_id),
            request.app["discord"].roles_detailed(guild_id),
        )
    except (ValueError, DiscordServiceError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    def read() -> dict[str, Any]:
        con = _connect(config)
        try:
            xp = con.execute("SELECT * FROM xp_profiles WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
            achievements = con.execute(
                """SELECT a.title,a.description,ua.unlocked_at FROM user_achievements ua
                JOIN achievements a ON a.id=ua.achievement_id WHERE ua.guild_id=? AND ua.user_id=? ORDER BY ua.unlocked_at DESC LIMIT 20""",
                (guild_id, user_id),
            ).fetchall()
            tasks = con.execute(
                "SELECT id,title,status,due_at FROM workspace_tasks WHERE guild_id=? AND assigned_to=? AND status NOT IN ('done','closed','completed') ORDER BY due_at LIMIT 20",
                (guild_id, user_id),
            ).fetchall()
            tickets = con.execute(
                "SELECT id,subject,status,priority,claimed_by,created_at FROM tickets WHERE guild_id=? AND opener_id=? ORDER BY id DESC LIMIT 20",
                (guild_id, user_id),
            ).fetchall()
            mod = con.execute(
                "SELECT id,action,reason,active,created_at,expires_at FROM moderation_cases WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 20",
                (guild_id, user_id),
            ).fetchall()
            commands = con.execute(
                "SELECT COUNT(*) count,MAX(created_at) last_used,SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) errors FROM command_analytics WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            ).fetchone()
            return {
                "xp": _json_safe(dict(xp)) if xp else None,
                "achievements": _rows(achievements),
                "tasks": _rows(tasks),
                "tickets": _rows(tickets),
                "moderation": _rows(mod),
                "commands": _json_safe(dict(commands)) if commands else {"count": 0, "errors": 0},
            }
        finally:
            con.close()
    data = await asyncio.to_thread(read)
    role_by_id = {row["id"]: row for row in roles}
    member["role_details"] = [role_by_id[rid] for rid in member.get("roles", []) if rid in role_by_id]
    return web.json_response(_json_safe({"ok": True, "member": member, **data}))


def _permission_value(roles: list[dict], member_roles: list[str], guild_id: int) -> tuple[int, list[str]]:
    selected = [row for row in roles if row["id"] == str(guild_id) or row["id"] in member_roles]
    value = 0
    names: list[str] = []
    for role in selected:
        try:
            value |= int(role.get("permissions") or 0)
        except (TypeError, ValueError):
            continue
        names.append(str(role.get("name") or role["id"]))
    return value, names


def _apply_channel_overwrites(base: int, channel: dict, guild_id: int, member_id: int, role_ids: list[str]) -> tuple[int, list[str]]:
    if base & (1 << 3):
        return (1 << 53) - 1, ["Administrator bypasses channel overwrites"]
    reasons: list[str] = []
    overwrites = channel.get("permission_overwrites") or []
    everyone = next((row for row in overwrites if row["type"] == 0 and row["id"] == str(guild_id)), None)
    if everyone:
        deny, allow = int(everyone["deny"]), int(everyone["allow"])
        base = (base & ~deny) | allow
        reasons.append("@everyone channel overwrite applied")
    role_deny = role_allow = 0
    for row in overwrites:
        if row["type"] == 0 and row["id"] in role_ids:
            role_deny |= int(row["deny"])
            role_allow |= int(row["allow"])
    if role_deny or role_allow:
        base = (base & ~role_deny) | role_allow
        reasons.append("Role channel overwrites applied")
    member = next((row for row in overwrites if row["type"] == 1 and row["id"] == str(member_id)), None)
    if member:
        deny, allow = int(member["deny"]), int(member["allow"])
        base = (base & ~deny) | allow
        reasons.append("Member-specific channel overwrite applied")
    return base, reasons


async def api_ops_permissions(request: web.Request) -> web.Response:
    try:
        guild_id = _guild(request.query.get("guild_id"))
        user_id = _int(request.query.get("user_id"), 0, 1)
        channel_id = _int(request.query.get("channel_id"), 0, 1)
        discord = request.app["discord"]
        member, roles, channels = await asyncio.gather(discord.member(guild_id, user_id), discord.roles_detailed(guild_id), discord.channels_detailed(guild_id))
        channel = next((row for row in channels if row["id"] == str(channel_id)), None)
        if channel is None:
            raise ValueError("Channel nicht gefunden")
    except (ValueError, DiscordServiceError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    base, role_names = _permission_value(roles, member.get("roles", []), guild_id)
    final, reasons = _apply_channel_overwrites(base, channel, guild_id, user_id, member.get("roles", []))
    permissions = [{"name": name, "granted": bool(final & (1 << bit)), "bit": bit} for name, bit in PERMISSION_BITS.items()]
    return web.json_response({"ok": True, "member": member, "channel": {"id": channel["id"], "name": channel["name"]}, "roles": role_names, "reasons": reasons, "permissions": permissions, "raw": str(final)})


async def api_ops_media(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        guild_id = _guild(request.query.get("guild_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)

    def read() -> dict[str, Any]:
        con = _connect(config)
        try:
            runtime_row = con.execute("SELECT state_json,updated_at FROM dashboard_runtime_state WHERE guild_id=?", (guild_id,)).fetchone()
            runtime = _json_load(runtime_row["state_json"] if runtime_row else "{}", {})
            if runtime_row:
                runtime["updated_at"] = runtime_row["updated_at"]
            history = _rows(con.execute("SELECT kind,title,source_name,started_by,started_at FROM voice_playback_history WHERE guild_id=? ORDER BY id DESC LIMIT 50", (guild_id,)).fetchall()) if _table_exists(con, "voice_playback_history") else []
            mods = _rows(con.execute("SELECT user_id,created_at FROM youtube_queue_mods WHERE guild_id=? ORDER BY created_at", (guild_id,)).fetchall()) if _table_exists(con, "youtube_queue_mods") else []
            commands = _rows(con.execute("SELECT id,action,status,result,created_at,processed_at FROM dashboard_commands WHERE action LIKE 'ops-youtube-%' ORDER BY id DESC LIMIT 20").fetchall())
            current = ((runtime.get("youtube") or {}).get("current") or {})
            video_id = _youtube_video_id(current.get("url"))
            runtime["youtube_thumbnail"] = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None
            return {"runtime": runtime, "history": history, "mods": mods, "commands": commands}
        finally:
            con.close()
    return web.json_response({"ok": True, **await asyncio.to_thread(read)})


async def api_ops_media_action(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.json()
    try:
        guild_id = _guild(data.get("guild_id"))
        action = str(data.get("action", "")).strip().lower()
        allowed = {"play", "add", "skip", "stop", "pause", "resume", "volume", "reorder", "mod", "clear"}
        if action not in allowed:
            raise ValueError("Unbekannte YouTube-Aktion")
        payload: dict[str, Any] = {"guild_id": str(guild_id)}
        if action == "play":
            payload["channel_id"] = str(_int(data.get("channel_id"), 0, 1))
            payload["query"] = str(data.get("query", "")).strip()[:500]
            payload["volume"] = _int(data.get("volume"), 65, 10, 120)
            if not payload["query"]:
                raise ValueError("Suchbegriff oder YouTube-Link fehlt")
        elif action == "add":
            payload["query"] = str(data.get("query", "")).strip()[:500]
            payload["requested_by"] = str(data.get("requested_by") or "0")
            if not payload["query"]:
                raise ValueError("Suchbegriff oder YouTube-Link fehlt")
        elif action == "volume":
            payload["volume"] = _int(data.get("volume"), 65, 10, 120)
        elif action == "reorder":
            payload["from"] = _int(data.get("from"), 0, 0, 24)
            payload["to"] = _int(data.get("to"), 0, 0, 24)
        elif action == "mod":
            payload["user_id"] = str(_int(data.get("user_id"), 0, 1))
            payload["enabled"] = bool(data.get("enabled", True))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    command_id = await asyncio.to_thread(_enqueue, config, f"ops-youtube-{action}", payload)
    return web.json_response({"ok": True, "command_id": command_id, "message": "Aktion an den Bot übergeben."})


async def api_ops_widgets(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        guild_id = _guild(request.query.get("guild_id") if request.method == "GET" else (await request.json()).get("guild_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    if request.method == "GET":
        def read():
            con = _connect(config)
            try:
                row = con.execute("SELECT layout_json,updated_at FROM dashboard_widget_layout WHERE guild_id=?", (guild_id,)).fetchone()
                return {"layout": _json_load(row["layout_json"], DEFAULT_WIDGETS) if row else DEFAULT_WIDGETS, "updated_at": row["updated_at"] if row else None}
            finally:
                con.close()
        return web.json_response({"ok": True, **await asyncio.to_thread(read)})
    data = await request.json() if False else None
    # request body was consumed above only when POST; aiohttp caches JSON, so read it again safely.
    body = await request.json()
    layout = body.get("layout")
    if not isinstance(layout, list) or len(layout) > 30:
        return web.json_response({"ok": False, "message": "Ungültiges Widget-Layout."}, status=400)
    def write():
        con = _connect(config)
        try:
            old = con.execute("SELECT layout_json FROM dashboard_widget_layout WHERE guild_id=?", (guild_id,)).fetchone()
            before = _json_load(old["layout_json"], None) if old else None
            con.execute("INSERT INTO dashboard_widget_layout(guild_id,layout_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id) DO UPDATE SET layout_json=excluded.layout_json,updated_at=CURRENT_TIMESTAMP", (guild_id, json.dumps(layout, ensure_ascii=False)))
            _record_change(con, guild_id, "widget", str(guild_id), before, layout, reversible=True)
            con.commit()
        finally:
            con.close()
    await asyncio.to_thread(write)
    return web.json_response({"ok": True, "layout": layout})


async def api_ops_history(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        guild_id = _guild(request.query.get("guild_id"))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    hours = _int(request.query.get("hours"), 24, 1, 1440)

    def read() -> list[dict]:
        con = _connect(config)
        try:
            rows: list[dict] = []
            if hours > 192 and _table_exists(con, "system_snapshots_hourly"):
                hourly = con.execute(
                    """SELECT hour recorded_at,cpu_avg cpu_percent,cpu_peak,ram_avg ram_percent,ram_peak,
                    temperature_avg temperature,temperature_peak,disk_avg disk_percent,load_avg load_1m,pihole_ok,tailscale_ok,samples
                    FROM system_snapshots_hourly WHERE guild_id=? AND hour>=datetime('now',?) ORDER BY hour""",
                    (guild_id, f"-{hours} hours"),
                ).fetchall()
                rows.extend(_rows(hourly))
                raw_since = "-8 days"
            else:
                raw_since = f"-{hours} hours"
            raw = con.execute(
                "SELECT recorded_at,cpu_percent,ram_percent,temperature,disk_percent,pihole_ok,tailscale_ok,extra_json FROM system_snapshots_v4 WHERE guild_id=? AND recorded_at>=datetime('now',?) ORDER BY recorded_at",
                (guild_id, raw_since),
            ).fetchall()
            for row in raw:
                item = dict(row)
                extra = _json_load(item.pop("extra_json", "{}"), {})
                item["load_1m"] = extra.get("load")
                rows.append(_json_safe(item))
            rows.sort(key=lambda x: str(x.get("recorded_at", "")))
            if len(rows) > 1400:
                step = max(1, math.ceil(len(rows) / 1400))
                rows = rows[::step]
            return rows
        finally:
            con.close()
    points = await asyncio.to_thread(read)
    return web.json_response({"ok": True, "hours": hours, "points": points})


async def api_ops_incidents(request: web.Request) -> web.Response:
    config = request.app["config"]
    guild_raw = request.query.get("guild_id")
    guild_id = int(guild_raw) if guild_raw and guild_raw.isdigit() else None
    days = _int(request.query.get("days"), 7, 1, 60)
    def read():
        con = _connect(config)
        try:
            params: list[Any] = [f"-{days} days"]
            guild_clause = ""
            if guild_id is not None:
                guild_clause = " AND guild_id=?"
                params.append(guild_id)
            groups = con.execute(
                """SELECT COALESCE(error_type,'Unknown') error_type,command_name,COUNT(*) count,MAX(created_at) last_seen,
                ROUND(AVG(duration_ms),1) avg_ms FROM command_analytics
                WHERE success=0 AND created_at>=datetime('now',?)""" + guild_clause + " GROUP BY error_type,command_name ORDER BY count DESC LIMIT 60",
                tuple(params),
            ).fetchall()
            failed = con.execute("SELECT action,result,COUNT(*) count,MAX(created_at) last_seen FROM dashboard_commands WHERE status='failed' AND created_at>=datetime('now',?) GROUP BY action,result ORDER BY count DESC LIMIT 40", (f"-{days} days",)).fetchall()
            return {"command_incidents": _rows(groups), "dashboard_incidents": _rows(failed)}
        finally:
            con.close()
    return web.json_response({"ok": True, **await asyncio.to_thread(read)})


async def api_ops_errors(request: web.Request) -> web.Response:
    config = request.app["config"]
    guild_raw = request.query.get("guild_id")
    guild_id = int(guild_raw) if guild_raw and guild_raw.isdigit() else None
    days = _int(request.query.get("days"), 7, 1, 60)
    def read():
        con = _connect(config)
        try:
            sql = "SELECT * FROM dashboard_error_events WHERE created_at>=datetime('now',?)"
            params: list[Any] = [f"-{days} days"]
            if guild_id is not None:
                sql += " AND (guild_id=? OR guild_id IS NULL)"
                params.append(guild_id)
            sql += " ORDER BY id DESC LIMIT 200"
            events = _rows(con.execute(sql, tuple(params)).fetchall())
            analytics_sql = "SELECT command_name,error_type,created_at,user_id,guild_id FROM command_analytics WHERE success=0 AND created_at>=datetime('now',?)"
            analytics_params: list[Any] = [f"-{days} days"]
            if guild_id is not None:
                analytics_sql += " AND guild_id=?"
                analytics_params.append(guild_id)
            analytics_sql += " ORDER BY id DESC LIMIT 200"
            failed = _rows(con.execute("SELECT id,action,result,created_at FROM dashboard_commands WHERE status='failed' AND created_at>=datetime('now',?) ORDER BY id DESC LIMIT 100", (f"-{days} days",)).fetchall())
            return {"events": events, "command_errors": _rows(con.execute(analytics_sql, tuple(analytics_params)).fetchall()), "dashboard_errors": failed}
        finally:
            con.close()
    return web.json_response({"ok": True, **await asyncio.to_thread(read)})


async def api_ops_topology(request: web.Request) -> web.Response:
    config = request.app["config"]
    system = await get_status(config.bot_service, request.app["system_sampler"])
    db_ok = True
    try:
        con = _connect(config); con.execute("SELECT 1").fetchone(); con.close()
    except sqlite3.Error:
        db_ok = False
    nodes = [
        {"id": "pi", "label": "Raspberry Pi", "status": "ok"},
        {"id": "bot", "label": "Discord Bot", "status": "ok" if system.get("bot_active") else "down", "parent": "pi"},
        {"id": "sqlite", "label": "SQLite", "status": "ok" if db_ok else "down", "parent": "bot"},
        {"id": "discord", "label": "Discord Gateway/API", "status": "ok" if system.get("bot_active") else "unknown", "parent": "bot"},
        {"id": "ffmpeg", "label": "FFmpeg", "status": "ok" if shutil.which("ffmpeg") else "down", "parent": "bot"},
        {"id": "ytdlp", "label": "yt-dlp", "status": "ok" if importlib.util.find_spec("yt_dlp") else "down", "parent": "bot"},
        {"id": "pihole", "label": "Pi-hole", "status": "ok" if (system.get("pihole") or {}).get("active") else "warn", "parent": "pi"},
        {"id": "tailscale", "label": "Tailscale", "status": "ok" if (system.get("tailscale") or {}).get("active") else "warn", "parent": "pi"},
        {"id": "dashboard", "label": "Dashboard", "status": "ok", "parent": "pi"},
    ]
    return web.json_response({"ok": True, "nodes": nodes})


async def api_ops_deploy(request: web.Request) -> web.Response:
    action = str((await request.json()).get("action", "")).strip().lower()
    if action not in {"deploy", "rollback", "backup", "requirements"}:
        return web.json_response({"ok": False, "message": "Unbekannte Deployment-Aktion."}, status=400)
    backup_result = None
    if action in {"deploy", "rollback"}:
        backup_result = await request.app["backups"].create()
        if not backup_result.get("ok"):
            return web.json_response({"ok": False, "message": "Pre-deploy backup failed; deployment aborted.", "backup": backup_result}, status=409)
    if action == "deploy":
        result = await request.app["deploy"].deploy()
    elif action == "rollback":
        result = await request.app["deploy"].rollback()
    elif action == "requirements":
        result = await request.app["deploy"].install_requirements()
    else:
        result = await request.app["backups"].create()
    request.app["audit"].record(f"ops.deploy.{action}", ok=bool(result.get("ok")), detail=str(result.get("message", "")))
    return web.json_response({"ok": bool(result.get("ok")), "result": result, "backup": backup_result}, status=200 if result.get("ok") else 500)


async def api_ops_features(request: web.Request) -> web.Response:
    config = request.app["config"]
    if request.method == "GET":
        try:
            guild_id = _guild(request.query.get("guild_id"))
        except ValueError as exc:
            return web.json_response({"ok": False, "message": str(exc)}, status=400)
        def read():
            con = _connect(config)
            try:
                return _rows(con.execute("SELECT * FROM dashboard_feature_flags WHERE guild_id=? ORDER BY feature_key,user_id", (guild_id,)).fetchall())
            finally: con.close()
        return web.json_response({"ok": True, "flags": await asyncio.to_thread(read), "catalog": [{"key": k, "title": t} for k,t,_ in FEATURE_CATALOG]})
    data = await request.json()
    try:
        guild_id = _guild(data.get("guild_id"))
        user_id = _int(data.get("user_id"), 0, 0)
        key = re.sub(r"[^a-z0-9_.-]+", "-", str(data.get("feature_key", "")).lower()).strip("-")[:80]
        if not key:
            raise ValueError("Feature-Key fehlt")
        enabled = bool(data.get("enabled", True))
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    def write():
        con = _connect(config)
        try:
            old = con.execute("SELECT enabled FROM dashboard_feature_flags WHERE guild_id=? AND user_id=? AND feature_key=?", (guild_id,user_id,key)).fetchone()
            before = {"enabled": bool(old["enabled"])} if old else None
            con.execute("INSERT INTO dashboard_feature_flags(guild_id,user_id,feature_key,enabled,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id,user_id,feature_key) DO UPDATE SET enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP", (guild_id,user_id,key,int(enabled)))
            _record_change(con,guild_id,"feature",f"{guild_id}:{user_id}:{key}",before,{"enabled":enabled},reversible=True)
            con.commit()
        finally: con.close()
    await asyncio.to_thread(write)
    return web.json_response({"ok": True, "feature_key": key, "enabled": enabled})


def _extensions(repo_path: Path) -> list[str]:
    try:
        text = (repo_path / "bot.py").read_text(encoding="utf-8")
    except OSError:
        return []
    match = re.search(r"EXTENSIONS:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\((.*?)\)\s*\n\n", text, re.S)
    block = match.group(1) if match else text
    return list(dict.fromkeys(re.findall(r'"((?:cogs|tasks)\.[A-Za-z0-9_.]+)"', block)))


async def api_ops_plugins(request: web.Request) -> web.Response:
    config = request.app["config"]
    if request.method == "POST":
        data = await request.json()
        extension = str(data.get("extension", "")).strip()
        action = str(data.get("action", "reload")).strip().lower()
        known = _extensions(Path(config.repo_path))
        if extension not in known or not extension.startswith("cogs."):
            return web.json_response({"ok": False, "message": "Unbekannte oder nicht schaltbare Extension."}, status=400)
        if action == "reload":
            command_id = await asyncio.to_thread(_enqueue, config, "reload", {"extension": extension})
        elif action in {"enable", "disable"}:
            command_id = await asyncio.to_thread(_enqueue, config, "plugin-toggle", {"extension": extension, "enabled": action == "enable"})
        else:
            return web.json_response({"ok": False, "message": "Aktion muss reload/enable/disable sein."}, status=400)
        return web.json_response({"ok": True, "command_id": command_id})

    def read():
        con = _connect(config)
        try:
            state_rows = {row["extension"]: dict(row) for row in con.execute("SELECT * FROM plugin_state").fetchall()} if _table_exists(con,"plugin_state") else {}
            recent = {row["action"]: dict(row) for row in con.execute("SELECT action,status,result,created_at FROM dashboard_commands WHERE action IN ('reload','plugin-toggle') ORDER BY id DESC LIMIT 50").fetchall()}
            rows = []
            for extension in _extensions(Path(config.repo_path)):
                state = state_rows.get(extension)
                lowered = extension.lower()
                load = "high" if any(x in lowered for x in ("voice","youtube","system_monitor")) else "medium" if any(x in lowered for x in ("automation","workspace","telemetry","games")) else "low"
                rows.append({"extension": extension, "enabled": bool(state["enabled"]) if state else True, "updated_at": state.get("updated_at") if state else None, "load_estimate": load, "kind": "task" if extension.startswith("tasks.") else "cog"})
            return rows
        finally: con.close()
    return web.json_response({"ok": True, "plugins": await asyncio.to_thread(read)})


async def api_ops_profiler(request: web.Request) -> web.Response:
    config = request.app["config"]
    system = await get_status(config.bot_service, request.app["system_sampler"])
    def read():
        con = _connect(config)
        try:
            queue = con.execute("SELECT status,COUNT(*) count FROM dashboard_commands GROUP BY status").fetchall()
            activity = con.execute("SELECT source,COUNT(*) count FROM dashboard_activity WHERE created_at>=datetime('now','-24 hours') GROUP BY source ORDER BY count DESC LIMIT 30").fetchall()
            db = _db_path(config)
            return {"dashboard_queue": _rows(queue), "activity_by_source": _rows(activity), "database_size_bytes": db.stat().st_size if db.exists() else 0}
        finally: con.close()
    return web.json_response(_json_safe({"ok": True, "system": {"bot_cpu_percent": system.get("bot_cpu_percent"), "bot_memory_mb": system.get("bot_memory_mb"), "dashboard_cpu_percent": system.get("dashboard_cpu_percent"), "dashboard_memory_mb": system.get("dashboard_memory_mb"), "processes": system.get("processes", [])}, **await asyncio.to_thread(read)}))


async def api_ops_database(request: web.Request) -> web.Response:
    config = request.app["config"]
    def read():
        con = _connect(config)
        try:
            names = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()][:120]
            size_map: dict[str,int] = {}
            try:
                for row in con.execute("SELECT name,SUM(pgsize) bytes FROM dbstat GROUP BY name").fetchall():
                    size_map[str(row["name"])] = int(row["bytes"] or 0)
            except sqlite3.Error:
                pass
            tables = []
            for name in names:
                try:
                    count = int(con.execute(f"SELECT COUNT(*) FROM {_ident(name)}").fetchone()[0])
                except sqlite3.Error:
                    count = -1
                index_count = int(con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND tbl_name=?", (name,)).fetchone()[0])
                tables.append({"name": name, "rows": count, "bytes": size_map.get(name), "indexes": index_count})
            tables.sort(key=lambda row: ((row["bytes"] or 0), row["rows"]), reverse=True)
            backups = _rows(con.execute("SELECT * FROM backup_history ORDER BY id DESC LIMIT 30").fetchall()) if _table_exists(con,"backup_history") else []
            path = _db_path(config)
            return {"file_size_bytes": path.stat().st_size if path.exists() else 0, "tables": tables, "backups": backups}
        finally: con.close()
    return web.json_response({"ok": True, **await asyncio.to_thread(read)})


async def api_ops_timeline(request: web.Request) -> web.Response:
    config = request.app["config"]
    if request.method == "GET":
        guild_raw = request.query.get("guild_id")
        guild_id = int(guild_raw) if guild_raw and guild_raw.isdigit() else None
        def read():
            con = _connect(config)
            try:
                sql = "SELECT * FROM dashboard_changes"
                params: tuple[Any,...] = ()
                if guild_id is not None:
                    sql += " WHERE guild_id=? OR guild_id IS NULL"; params=(guild_id,)
                changes = _rows(con.execute(sql + " ORDER BY id DESC LIMIT 200", params).fetchall())
                audits = _rows(con.execute("SELECT id,guild_id,actor_id,action,target_type,target_id,before_json,after_json,created_at FROM bot_audit_log ORDER BY id DESC LIMIT 120").fetchall()) if _table_exists(con,"bot_audit_log") else []
                return {"changes": changes, "audit": audits}
            finally: con.close()
        return web.json_response({"ok": True, **await asyncio.to_thread(read)})
    data = await request.json()
    change_id = _int(data.get("change_id"),0,1)
    def undo():
        con = _connect(config)
        try:
            row = con.execute("SELECT * FROM dashboard_changes WHERE id=?", (change_id,)).fetchone()
            if not row or not int(row["reversible"]):
                raise ValueError("Diese Änderung unterstützt kein sicheres Undo.")
            before = _json_load(row["before_json"], None)
            entity = str(row["entity_type"])
            key = str(row["entity_key"])
            if entity == "widget":
                guild_id=int(key)
                if before is None: con.execute("DELETE FROM dashboard_widget_layout WHERE guild_id=?",(guild_id,))
                else: con.execute("INSERT INTO dashboard_widget_layout(guild_id,layout_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id) DO UPDATE SET layout_json=excluded.layout_json,updated_at=CURRENT_TIMESTAMP",(guild_id,json.dumps(before,ensure_ascii=False)))
            elif entity == "feature":
                guild_s,user_s,feature = key.split(":",2); gid,uid=int(guild_s),int(user_s)
                if before is None: con.execute("DELETE FROM dashboard_feature_flags WHERE guild_id=? AND user_id=? AND feature_key=?",(gid,uid,feature))
                else: con.execute("INSERT INTO dashboard_feature_flags(guild_id,user_id,feature_key,enabled,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id,user_id,feature_key) DO UPDATE SET enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP",(gid,uid,feature,int(bool(before.get('enabled')))))
            elif entity == "display":
                gid=int(key)
                if before is None: con.execute("DELETE FROM dashboard_display_layout WHERE guild_id=?",(gid,))
                else: con.execute("INSERT INTO dashboard_display_layout(guild_id,layout_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id) DO UPDATE SET layout_json=excluded.layout_json,updated_at=CURRENT_TIMESTAMP",(gid,json.dumps(before,ensure_ascii=False)))
            elif entity == "ticket_lane":
                tid=int(key)
                if before is None: con.execute("DELETE FROM dashboard_ticket_board WHERE ticket_id=?",(tid,))
                else: con.execute("INSERT INTO dashboard_ticket_board(ticket_id,lane,position,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(ticket_id) DO UPDATE SET lane=excluded.lane,position=excluded.position,updated_at=CURRENT_TIMESTAMP",(tid,before.get('lane','new'),int(before.get('position',0))))
            elif entity == "org":
                gid=int(key); con.execute("DELETE FROM dashboard_org_nodes WHERE guild_id=?",(gid,))
                for node in before or []:
                    con.execute("INSERT INTO dashboard_org_nodes(guild_id,node_key,parent_key,label,role_id,kind,position,metadata_json) VALUES(?,?,?,?,?,?,?,?)",(gid,node['node_key'],node.get('parent_key'),node['label'],node.get('role_id'),node.get('kind','team'),int(node.get('position',0)),json.dumps(node.get('metadata',{}),ensure_ascii=False)))
            else:
                raise ValueError("Undo für diesen Änderungstyp ist nicht implementiert.")
            con.execute("UPDATE dashboard_changes SET reversible=0 WHERE id=?",(change_id,))
            con.commit(); return {"entity_type":entity,"entity_key":key}
        finally: con.close()
    try:
        result=await asyncio.to_thread(undo)
    except ValueError as exc:
        return web.json_response({"ok":False,"message":str(exc)},status=400)
    return web.json_response({"ok":True,"restored":result})


async def api_ops_org(request: web.Request) -> web.Response:
    config=request.app["config"]
    if request.method=="GET":
        try: guild_id=_guild(request.query.get("guild_id"))
        except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
        def read():
            con=_connect(config)
            try:
                result=[]
                for row in con.execute("SELECT * FROM dashboard_org_nodes WHERE guild_id=? ORDER BY position,label",(guild_id,)).fetchall():
                    item=dict(row); item["metadata"]=_json_load(item.pop("metadata_json",None),{}); result.append(_json_safe(item))
                return result
            finally: con.close()
        return web.json_response({"ok":True,"nodes":await asyncio.to_thread(read)})
    data=await request.json()
    try:
        guild_id=_guild(data.get("guild_id")); nodes=data.get("nodes")
        if not isinstance(nodes,list) or len(nodes)>150: raise ValueError("Ungültiger Org-Chart")
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    def write():
        con=_connect(config)
        try:
            old=[]
            for row in con.execute("SELECT * FROM dashboard_org_nodes WHERE guild_id=? ORDER BY position,label",(guild_id,)).fetchall():
                item=dict(row); item["metadata"]=_json_load(item.pop("metadata_json",None),{}); old.append(item)
            con.execute("DELETE FROM dashboard_org_nodes WHERE guild_id=?",(guild_id,))
            for idx,node in enumerate(nodes):
                key=re.sub(r"[^a-zA-Z0-9_.-]+","-",str(node.get("node_key") or f"node-{idx}"))[:80]
                label=str(node.get("label") or "Bereich").strip()[:120]
                role_raw=str(node.get("role_id") or ""); role_id=int(role_raw) if role_raw.isdigit() else None
                con.execute("INSERT INTO dashboard_org_nodes(guild_id,node_key,parent_key,label,role_id,kind,position,metadata_json) VALUES(?,?,?,?,?,?,?,?)",(guild_id,key,str(node.get("parent_key") or "")[:80] or None,label,role_id,str(node.get("kind") or "team")[:30],idx,json.dumps(node.get("metadata") or {},ensure_ascii=False)))
            _record_change(con,guild_id,"org",str(guild_id),old,nodes,reversible=True); con.commit()
        finally: con.close()
    await asyncio.to_thread(write)
    return web.json_response({"ok":True,"nodes":nodes})


async def api_ops_tickets(request: web.Request) -> web.Response:
    config=request.app["config"]
    if request.method=="GET":
        try: guild_id=_guild(request.query.get("guild_id"))
        except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
        def read():
            con=_connect(config)
            try:
                rows=con.execute("""SELECT t.id,t.channel_id,t.opener_id,t.subject,t.priority,t.status,t.claimed_by,t.created_at,t.updated_at,
                    b.lane,b.position FROM tickets t LEFT JOIN dashboard_ticket_board b ON b.ticket_id=t.id
                    WHERE t.guild_id=? ORDER BY CASE WHEN t.status='closed' THEN 1 ELSE 0 END,t.priority DESC,t.id DESC LIMIT 300""",(guild_id,)).fetchall()
                result=[]
                for row in rows:
                    item=dict(row)
                    if item.get("status")=="closed": item["lane"]="done"
                    elif not item.get("lane"): item["lane"]="claimed" if item.get("claimed_by") else "new"
                    result.append(_json_safe(item))
                return result
            finally: con.close()
        return web.json_response({"ok":True,"tickets":await asyncio.to_thread(read)})
    data=await request.json()
    try:
        ticket_id=_int(data.get("ticket_id"),0,1); lane=str(data.get("lane","")).lower()
        if lane not in {"new","claimed","waiting","done"}: raise ValueError("Ungültige Board-Spalte")
        position=_int(data.get("position"),0,0,10000)
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    def write():
        con=_connect(config)
        try:
            ticket=con.execute("SELECT guild_id,status FROM tickets WHERE id=?",(ticket_id,)).fetchone()
            if not ticket: raise ValueError("Ticket nicht gefunden")
            old=con.execute("SELECT lane,position FROM dashboard_ticket_board WHERE ticket_id=?",(ticket_id,)).fetchone(); before=dict(old) if old else None
            con.execute("INSERT INTO dashboard_ticket_board(ticket_id,lane,position,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(ticket_id) DO UPDATE SET lane=excluded.lane,position=excluded.position,updated_at=CURRENT_TIMESTAMP",(ticket_id,lane,position))
            _record_change(con,int(ticket["guild_id"]),"ticket_lane",str(ticket_id),before,{"lane":lane,"position":position},reversible=True); con.commit()
            return bool(lane=="done" and ticket["status"]!="closed")
        finally: con.close()
    try: warning=await asyncio.to_thread(write)
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=404)
    return web.json_response({"ok":True,"warning":"Board-Spalte ist erledigt, das Discord-Ticket selbst wurde nicht geschlossen." if warning else None})


async def api_ops_calendar(request: web.Request) -> web.Response:
    config=request.app["config"]
    try: guild_id=_guild(request.query.get("guild_id"))
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    days=_int(request.query.get("days"),45,1,180)
    def read():
        con=_connect(config)
        try:
            events=[]
            if _table_exists(con,"planner_entries"):
                for row in con.execute("SELECT id,event_date,start_time,title,owner_text,category FROM planner_entries WHERE guild_id=? AND event_date BETWEEN date('now','-7 days') AND date('now',?) ORDER BY event_date,start_time",(guild_id,f"+{days} days")).fetchall():
                    item=dict(row); item.update({"source":"planner","start":f"{row['event_date']}T{row['start_time']}"}); events.append(_json_safe(item))
            if _table_exists(con,"workspace_events"):
                for row in con.execute("SELECT id,title,description,starts_at,channel_id FROM workspace_events WHERE guild_id=? AND starts_at BETWEEN datetime('now','-7 days') AND datetime('now',?) ORDER BY starts_at",(guild_id,f"+{days} days")).fetchall():
                    item=dict(row); item.update({"source":"workspace","start":row['starts_at']}); events.append(_json_safe(item))
            if _table_exists(con,"reminders"):
                for row in con.execute("SELECT id,message,due_at,user_id,channel_id,delivered FROM reminders WHERE guild_id=? AND due_at BETWEEN datetime('now','-7 days') AND datetime('now',?) ORDER BY due_at",(guild_id,f"+{days} days")).fetchall():
                    item=dict(row); item.update({"title":row['message'],"source":"reminder","start":row['due_at']}); events.append(_json_safe(item))
            if _table_exists(con,"md_weekly_drafts") and _table_exists(con,"md_weekly_entries"):
                try:
                    md=con.execute("""SELECT e.id,d.week_start,e.day_index,e.time_text,e.title,e.owner_text,e.kind
                        FROM md_weekly_entries e JOIN md_weekly_drafts d ON d.id=e.draft_id
                        WHERE d.guild_id=? AND date(d.week_start)>=date('now','-14 days') ORDER BY d.week_start,e.day_index,e.sort_time""",(guild_id,)).fetchall()
                    for row in md:
                        start_date=(datetime.fromisoformat(str(row['week_start']))+timedelta(days=int(row['day_index']))).date().isoformat()
                        item=dict(row); item.update({"source":"mdplan","start":start_date,"title":row['title']}); events.append(_json_safe(item))
                except (sqlite3.Error,ValueError): pass
            events.sort(key=lambda x:str(x.get("start",""))); return events
        finally: con.close()
    return web.json_response({"ok":True,"events":await asyncio.to_thread(read)})


async def api_ops_workflows(request: web.Request) -> web.Response:
    config=request.app["config"]
    if request.method=="GET":
        try: guild_id=_guild(request.query.get("guild_id"))
        except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
        def read():
            con=_connect(config)
            try:
                rows=[]
                for row in con.execute("SELECT * FROM dashboard_workflows WHERE guild_id=? ORDER BY id DESC",(guild_id,)).fetchall():
                    item=dict(row); item["trigger"]=_json_load(item.pop("trigger_json"),{}); item["steps"]=_json_load(item.pop("steps_json"),[])
                    item["runs"]=_rows(con.execute("SELECT status,detail,created_at FROM dashboard_workflow_runs WHERE workflow_id=? ORDER BY id DESC LIMIT 5",(row['id'],)).fetchall()); rows.append(_json_safe(item))
                return rows
            finally: con.close()
        return web.json_response({"ok":True,"workflows":await asyncio.to_thread(read)})
    data=await request.json()
    try:
        guild_id=_guild(data.get("guild_id")); action=str(data.get("action","save")).lower(); workflow_id=_int(data.get("id"),0,0)
        if action not in {"save","delete","toggle"}: raise ValueError("Ungültige Workflow-Aktion")
        trigger=data.get("trigger") or {}; steps=data.get("steps") or []
        if action=="save" and (not isinstance(trigger,dict) or not isinstance(steps,list) or len(steps)>12): raise ValueError("Trigger/Steps ungültig")
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    def write():
        con=_connect(config)
        try:
            old=dict(con.execute("SELECT * FROM dashboard_workflows WHERE id=? AND guild_id=?",(workflow_id,guild_id)).fetchone() or {}) if workflow_id else None
            if action=="delete": con.execute("DELETE FROM dashboard_workflows WHERE id=? AND guild_id=?",(workflow_id,guild_id)); result_id=workflow_id
            elif action=="toggle": con.execute("UPDATE dashboard_workflows SET enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?",(int(bool(data.get('enabled',True))),workflow_id,guild_id)); result_id=workflow_id
            elif workflow_id:
                con.execute("UPDATE dashboard_workflows SET name=?,trigger_json=?,steps_json=?,enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?",(str(data.get('name') or 'Workflow')[:120],json.dumps(trigger,ensure_ascii=False),json.dumps(steps,ensure_ascii=False),int(bool(data.get('enabled',True))),workflow_id,guild_id)); result_id=workflow_id
            else:
                cur=con.execute("INSERT INTO dashboard_workflows(guild_id,name,trigger_json,steps_json,enabled) VALUES(?,?,?,?,?)",(guild_id,str(data.get('name') or 'Workflow')[:120],json.dumps(trigger,ensure_ascii=False),json.dumps(steps,ensure_ascii=False),int(bool(data.get('enabled',True))))); result_id=int(cur.lastrowid)
            con.execute("DELETE FROM dashboard_workflow_state WHERE workflow_id=?",(result_id,)); con.commit(); return result_id
        finally: con.close()
    result_id=await asyncio.to_thread(write); return web.json_response({"ok":True,"id":str(result_id)})


async def api_ops_messages(request: web.Request) -> web.Response:
    config=request.app["config"]; data=await request.json()
    try:
        guild_id=_guild(data.get("guild_id")); channel_id=_int(data.get("channel_id"),0,1)
        send_at=str(data.get("send_at") or "").strip(); content=str(data.get("content") or "")[:1900]
        embed=data.get("embed") or {}; buttons=data.get("buttons") or []
        if not isinstance(embed,dict) or not isinstance(buttons,list) or len(buttons)>5: raise ValueError("Embed/Buttons ungültig")
        if not content and not embed: raise ValueError("Nachricht ist leer")
        for button in buttons:
            if button.get("url") and not str(button["url"]).startswith("https://"): raise ValueError("Link-Buttons benötigen HTTPS")
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    def write():
        con=_connect(config)
        try:
            if send_at:
                try: parsed=datetime.fromisoformat(send_at.replace('Z','+00:00'))
                except ValueError: raise ValueError("send_at ist kein gültiges ISO-Datum")
                if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=UTC)
                send_sql=parsed.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')
            else: send_sql=datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
            cur=con.execute("INSERT INTO dashboard_scheduled_messages(guild_id,channel_id,send_at,content,embed_json,buttons_json) VALUES(?,?,?,?,?,?)",(guild_id,channel_id,send_sql,content,json.dumps(embed,ensure_ascii=False),json.dumps(buttons,ensure_ascii=False))); con.commit(); return int(cur.lastrowid)
        finally: con.close()
    try: message_id=await asyncio.to_thread(write)
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    return web.json_response({"ok":True,"id":str(message_id),"message":"Nachricht geplant; der Bot verarbeitet fällige Nachrichten innerhalb von ca. 30 Sekunden."})


async def api_ops_panels(request: web.Request) -> web.Response:
    config=request.app["config"]
    try: guild_id=_guild(request.query.get("guild_id"))
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    def read():
        con=_connect(config)
        try:
            panels=[]
            for row in con.execute("SELECT * FROM panel_messages WHERE guild_id=? ORDER BY id DESC LIMIT 100",(guild_id,)).fetchall():
                item=dict(row); item["actions"]=_rows(con.execute("SELECT * FROM panel_actions WHERE panel_id=? ORDER BY position,id",(row['id'],)).fetchall()); item["versions"]=_rows(con.execute("SELECT id,note,created_at FROM dashboard_panel_versions WHERE panel_id=? ORDER BY id DESC LIMIT 10",(row['id'],)).fetchall()); panels.append(_json_safe(item))
            return panels
        finally: con.close()
    return web.json_response({"ok":True,"panels":await asyncio.to_thread(read)})


async def api_ops_panel_snapshot(request: web.Request) -> web.Response:
    config=request.app["config"]; data=await request.json(); panel_id=_int(data.get("panel_id"),0,1)
    def write():
        con=_connect(config)
        try:
            panel=con.execute("SELECT * FROM panel_messages WHERE id=?",(panel_id,)).fetchone()
            if not panel: raise ValueError("Panel nicht gefunden")
            snapshot={"panel":dict(panel),"actions":[dict(row) for row in con.execute("SELECT * FROM panel_actions WHERE panel_id=? ORDER BY position,id",(panel_id,)).fetchall()]}
            cur=con.execute("INSERT INTO dashboard_panel_versions(panel_id,snapshot_json,note) VALUES(?,?,?)",(panel_id,json.dumps(snapshot,ensure_ascii=False),str(data.get('note') or '')[:200])); con.commit(); return int(cur.lastrowid)
        finally: con.close()
    try: vid=await asyncio.to_thread(write)
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=404)
    return web.json_response({"ok":True,"version_id":str(vid)})


async def api_ops_display(request: web.Request) -> web.Response:
    config=request.app["config"]
    if request.method=="GET":
        try: guild_id=_guild(request.query.get("guild_id"))
        except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
        def read():
            con=_connect(config)
            try:
                row=con.execute("SELECT layout_json,updated_at FROM dashboard_display_layout WHERE guild_id=?",(guild_id,)).fetchone()
                default={"rotation":0,"refresh_seconds":10,"widgets":["clock","temperature","ram","nowplaying","pihole"]}
                return {"layout":_json_load(row['layout_json'],default) if row else default,"updated_at":row['updated_at'] if row else None}
            finally: con.close()
        return web.json_response({"ok":True,**await asyncio.to_thread(read)})
    data=await request.json()
    try: guild_id=_guild(data.get("guild_id")); layout=data.get("layout");
    except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
    if not isinstance(layout,dict): return web.json_response({"ok":False,"message":"Layout muss ein Objekt sein."},status=400)
    def write():
        con=_connect(config)
        try:
            old=con.execute("SELECT layout_json FROM dashboard_display_layout WHERE guild_id=?",(guild_id,)).fetchone(); before=_json_load(old['layout_json'],None) if old else None
            con.execute("INSERT INTO dashboard_display_layout(guild_id,layout_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id) DO UPDATE SET layout_json=excluded.layout_json,updated_at=CURRENT_TIMESTAMP",(guild_id,json.dumps(layout,ensure_ascii=False))); _record_change(con,guild_id,"display",str(guild_id),before,layout,reversible=True); con.commit()
        finally: con.close()
    await asyncio.to_thread(write); return web.json_response({"ok":True,"layout":layout})


async def api_ops_gpio(request: web.Request) -> web.Response:
    config=request.app["config"]
    if request.method=="GET":
        try: guild_id=_guild(request.query.get("guild_id"))
        except ValueError as exc: return web.json_response({"ok":False,"message":str(exc)},status=400)
        def read():
            con=_connect(config)
            try:
                rows=[]
                for row in con.execute("SELECT * FROM dashboard_gpio_devices WHERE guild_id=? ORDER BY pin,name",(guild_id,)).fetchall():
                    item=dict(row); item['config']=_json_load(item.pop('config_json',None),{}); rows.append(_json_safe(item))
                return rows
            finally: con.close()
        return web.json_response({"ok":True,"devices":await asyncio.to_thread(read),"note":"Designer-Konfiguration. Physische Ausgänge werden nur über explizit unterstützte Hardware-Cogs geschaltet."})
    data=await request.json()
    try:
        guild_id=_guild(data.get('guild_id')); action=str(data.get('action','save')).lower(); device_id=_int(data.get('id'),0,0)
        if action not in {'save','delete'}: raise ValueError('Ungültige GPIO-Aktion')
        pin=_int(data.get('pin'),18,0,27); kind=str(data.get('kind') or 'led').lower()[:30]; name=str(data.get('name') or f'GPIO {pin}').strip()[:80]
    except ValueError as exc: return web.json_response({'ok':False,'message':str(exc)},status=400)
    def write():
        con=_connect(config)
        try:
            if action=='delete': con.execute('DELETE FROM dashboard_gpio_devices WHERE id=? AND guild_id=?',(device_id,guild_id)); rid=device_id
            elif device_id:
                con.execute('UPDATE dashboard_gpio_devices SET name=?,pin=?,kind=?,active_high=?,enabled=?,config_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?',(name,pin,kind,int(bool(data.get('active_high',True))),int(bool(data.get('enabled',False))),json.dumps(data.get('config') or {},ensure_ascii=False),device_id,guild_id)); rid=device_id
            else:
                cur=con.execute('INSERT INTO dashboard_gpio_devices(guild_id,name,pin,kind,active_high,enabled,config_json) VALUES(?,?,?,?,?,?,?)',(guild_id,name,pin,kind,int(bool(data.get('active_high',True))),int(bool(data.get('enabled',False))),json.dumps(data.get('config') or {},ensure_ascii=False))); rid=int(cur.lastrowid)
            con.commit(); return rid
        finally: con.close()
    rid=await asyncio.to_thread(write); return web.json_response({'ok':True,'id':str(rid)})


async def api_ops_network(request: web.Request) -> web.Response:
    config=request.app["config"]; system=await get_status(config.bot_service,request.app["system_sampler"])
    return web.json_response(_json_safe({"ok":True,"network":{"rx_rate_bps":system.get('network_rx_rate_bps'),"tx_rate_bps":system.get('network_tx_rate_bps'),"rx_mb":system.get('network_rx_mb'),"tx_mb":system.get('network_tx_mb')},"pihole":system.get('pihole'),"tailscale":system.get('tailscale'),"services":system.get('services')}))


async def api_ops_notifications(request: web.Request) -> web.Response:
    config=request.app['config']
    if request.method=='GET':
        try: guild_id=_guild(request.query.get('guild_id'))
        except ValueError as exc: return web.json_response({'ok':False,'message':str(exc)},status=400)
        def read():
            con=_connect(config)
            try: return _rows(con.execute('SELECT * FROM dashboard_notification_rules WHERE guild_id=? ORDER BY id DESC',(guild_id,)).fetchall())
            finally: con.close()
        return web.json_response({'ok':True,'rules':await asyncio.to_thread(read),'metrics':['cpu','ram','temperature','disk','load','command_errors_5m','open_tickets','voice_sessions']})
    data=await request.json()
    try:
        guild_id=_guild(data.get('guild_id')); action=str(data.get('action','save')).lower(); rule_id=_int(data.get('id'),0,0)
        if action not in {'save','delete'}: raise ValueError('Ungültige Regel-Aktion')
    except ValueError as exc: return web.json_response({'ok':False,'message':str(exc)},status=400)
    def write():
        con=_connect(config)
        try:
            if action=='delete': con.execute('DELETE FROM dashboard_notification_rules WHERE id=? AND guild_id=?',(rule_id,guild_id)); rid=rule_id
            else:
                values=(str(data.get('name') or 'Monitoring-Regel')[:100],str(data.get('metric') or 'ram')[:40],str(data.get('operator') or '>')[:2],float(data.get('threshold') or 85),_int(data.get('duration_seconds'),0,0,86400),_int(data.get('cooldown_seconds'),1800,60,604800),_int(data.get('channel_id'),0,1),int(bool(data.get('enabled',True))))
                if rule_id:
                    con.execute('UPDATE dashboard_notification_rules SET name=?,metric=?,operator=?,threshold=?,duration_seconds=?,cooldown_seconds=?,channel_id=?,enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?',values+(rule_id,guild_id)); rid=rule_id
                else:
                    cur=con.execute('INSERT INTO dashboard_notification_rules(guild_id,name,metric,operator,threshold,duration_seconds,cooldown_seconds,channel_id,enabled) VALUES(?,?,?,?,?,?,?,?,?)',(guild_id,)+values); rid=int(cur.lastrowid)
            con.commit(); return rid
        finally: con.close()
    try: rid=await asyncio.to_thread(write)
    except (ValueError,sqlite3.Error) as exc: return web.json_response({'ok':False,'message':str(exc)},status=400)
    return web.json_response({'ok':True,'id':str(rid)})


async def api_ops_records(request: web.Request) -> web.Response:
    config=request.app['config']; guild_raw=request.query.get('guild_id'); guild_id=int(guild_raw) if guild_raw and guild_raw.isdigit() else None
    def read():
        con=_connect(config)
        try:
            where='' if guild_id is None else ' WHERE guild_id=?'; params=() if guild_id is None else (guild_id,)
            max_temp=con.execute('SELECT MAX(temperature) value,recorded_at FROM system_snapshots_v4'+where+' ORDER BY temperature DESC LIMIT 1',params).fetchone() if _table_exists(con,'system_snapshots_v4') else None
            top_command=con.execute('SELECT command_name,COUNT(*) count FROM command_analytics'+where+' GROUP BY command_name ORDER BY count DESC LIMIT 1',params).fetchone()
            active_day=con.execute("SELECT date(created_at) day,COUNT(*) count FROM command_analytics"+where+" GROUP BY date(created_at) ORDER BY count DESC LIMIT 1",params).fetchone()
            fastest=con.execute('SELECT command_name,ROUND(AVG(duration_ms),1) avg_ms,COUNT(*) count FROM command_analytics'+where+(' AND duration_ms IS NOT NULL' if where else ' WHERE duration_ms IS NOT NULL')+' GROUP BY command_name HAVING count>=3 ORDER BY avg_ms ASC LIMIT 1',params).fetchone()
            largest_queue=con.execute("SELECT MAX(CAST(json_extract(state_json,'$.youtube.queue') IS NOT NULL AS INTEGER)) FROM dashboard_runtime_state").fetchone()
            return {'max_temperature':_json_safe(dict(max_temp)) if max_temp else None,'top_command':dict(top_command) if top_command else None,'most_active_day':dict(active_day) if active_day else None,'fastest_command':dict(fastest) if fastest else None}
        finally: con.close()
    system=await get_status(config.bot_service,request.app['system_sampler']); return web.json_response(_json_safe({'ok':True,'uptime_seconds':system.get('uptime_seconds'),**await asyncio.to_thread(read)}))


async def api_ops_timemachine(request: web.Request) -> web.Response:
    config=request.app['config']
    try: guild_id=_guild(request.query.get('guild_id')); at=str(request.query.get('at') or '').strip(); parsed=datetime.fromisoformat(at.replace('Z','+00:00'))
    except (ValueError,TypeError) as exc: return web.json_response({'ok':False,'message':'guild_id und gültiger ISO-Zeitpunkt erforderlich.'},status=400)
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=UTC)
    target=parsed.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')
    def read():
        con=_connect(config)
        try:
            snap=con.execute("SELECT * FROM system_snapshots_v4 WHERE guild_id=? ORDER BY ABS(strftime('%s',recorded_at)-strftime('%s',?)) LIMIT 1",(guild_id,target)).fetchone()
            hourly=con.execute("SELECT * FROM system_snapshots_hourly WHERE guild_id=? ORDER BY ABS(strftime('%s',hour)-strftime('%s',?)) LIMIT 1",(guild_id,target)).fetchone() if _table_exists(con,'system_snapshots_hourly') else None
            activity=_rows(con.execute("SELECT * FROM dashboard_activity WHERE guild_id=? AND created_at BETWEEN datetime(?,'-30 minutes') AND datetime(?,'+30 minutes') ORDER BY created_at LIMIT 100",(guild_id,target,target)).fetchall())
            audit=_rows(con.execute("SELECT id,action,target_type,target_id,before_json,after_json,created_at FROM bot_audit_log WHERE guild_id=? AND created_at<=? ORDER BY id DESC LIMIT 30",(guild_id,target)).fetchall()) if _table_exists(con,'bot_audit_log') else []
            return {'snapshot':_json_safe(dict(snap)) if snap else (_json_safe(dict(hourly)) if hourly else None),'activity':activity,'recent_config_changes':audit}
        finally: con.close()
    return web.json_response({'ok':True,'at':target,**await asyncio.to_thread(read)})


async def api_ops_search(request: web.Request) -> web.Response:
    q=' '.join(str(request.query.get('q') or '').lower().split())
    if not q: return web.json_response({'ok':True,'results':[]})
    results=[]
    aliases={
        'ban':['member','moderation','server-map'],'person bannen':['member'],'radio':['media'],'youtube':['media'],'welcome':['server-map','workflows'],
        'backup':['backups','deploy'],'fehler':['errors','incidents'],'ram':['history','profiler'],'ticket':['tickets'],'rolle':['permissions','org','server-map'],
        'embed':['messages','panels'],'gpio':['gpio'],'display':['display'],'automatisierung':['workflows'],'workflow':['workflows']
    }
    expanded={q}
    for key,values in aliases.items():
        if key in q: expanded.update(values)
    for key,title,path in FEATURE_CATALOG:
        hay=f'{key} {title}'.lower()
        score=sum(1 for term in expanded if term in hay or hay in term)
        if score: results.append({'key':key,'title':title,'path':path,'score':score})
    results.sort(key=lambda x:(-x['score'],x['title'])); return web.json_response({'ok':True,'results':results[:12]})


async def api_public_status(request: web.Request) -> web.Response:
    config=request.app['config']; system=await get_status(config.bot_service,request.app['system_sampler'])
    services=[]
    for row in system.get('services') or []:
        if row.get('name') in {config.bot_service,'raspberry-dashboard','pihole-FTL'}:
            services.append({'name':row.get('name'),'online':row.get('active')=='active'})
    return web.json_response({'ok':True,'status':'operational' if system.get('bot_active') else 'degraded','bot_online':bool(system.get('bot_active')),'services':services,'uptime_seconds':system.get('uptime_seconds'),'updated_at':datetime.now(UTC).replace(microsecond=0).isoformat()})


def register_ops_routes(app: web.Application) -> None:
    _ensure_schema(app['config'])
    app.router.add_get('/ops',ops_page)
    app.router.add_get('/now-playing',now_playing_page)
    app.router.add_get('/status',public_status_page)
    app.router.add_get('/api/public/status',api_public_status)
    app.router.add_get('/api/ops/summary',api_ops_summary)
    app.router.add_get('/api/ops/analytics',api_ops_analytics)
    app.router.add_get('/api/ops/activity',api_ops_activity)
    app.router.add_get('/api/ops/live',api_ops_live)
    app.router.add_get('/api/ops/server-map',api_ops_server_map)
    app.router.add_post('/api/ops/discord/edit',api_ops_discord_edit)
    app.router.add_get('/api/ops/member/search',api_ops_member_search)
    app.router.add_get('/api/ops/member',api_ops_member)
    app.router.add_get('/api/ops/permissions',api_ops_permissions)
    app.router.add_get('/api/ops/media',api_ops_media)
    app.router.add_post('/api/ops/media/action',api_ops_media_action)
    app.router.add_get('/api/ops/widgets',api_ops_widgets)
    app.router.add_post('/api/ops/widgets',api_ops_widgets)
    app.router.add_get('/api/ops/history',api_ops_history)
    app.router.add_get('/api/ops/incidents',api_ops_incidents)
    app.router.add_get('/api/ops/errors',api_ops_errors)
    app.router.add_get('/api/ops/topology',api_ops_topology)
    app.router.add_post('/api/ops/deploy',api_ops_deploy)
    app.router.add_get('/api/ops/features',api_ops_features)
    app.router.add_post('/api/ops/features',api_ops_features)
    app.router.add_get('/api/ops/plugins',api_ops_plugins)
    app.router.add_post('/api/ops/plugins',api_ops_plugins)
    app.router.add_get('/api/ops/profiler',api_ops_profiler)
    app.router.add_get('/api/ops/database',api_ops_database)
    app.router.add_get('/api/ops/timeline',api_ops_timeline)
    app.router.add_post('/api/ops/timeline/undo',api_ops_timeline)
    app.router.add_get('/api/ops/org',api_ops_org)
    app.router.add_post('/api/ops/org',api_ops_org)
    app.router.add_get('/api/ops/tickets',api_ops_tickets)
    app.router.add_post('/api/ops/tickets',api_ops_tickets)
    app.router.add_get('/api/ops/calendar',api_ops_calendar)
    app.router.add_get('/api/ops/workflows',api_ops_workflows)
    app.router.add_post('/api/ops/workflows',api_ops_workflows)
    app.router.add_post('/api/ops/messages',api_ops_messages)
    app.router.add_get('/api/ops/panels',api_ops_panels)
    app.router.add_post('/api/ops/panels/snapshot',api_ops_panel_snapshot)
    app.router.add_get('/api/ops/display',api_ops_display)
    app.router.add_post('/api/ops/display',api_ops_display)
    app.router.add_get('/api/ops/gpio',api_ops_gpio)
    app.router.add_post('/api/ops/gpio',api_ops_gpio)
    app.router.add_get('/api/ops/network',api_ops_network)
    app.router.add_get('/api/ops/notifications',api_ops_notifications)
    app.router.add_post('/api/ops/notifications',api_ops_notifications)
    app.router.add_get('/api/ops/records',api_ops_records)
    app.router.add_get('/api/ops/timemachine',api_ops_timemachine)
    app.router.add_get('/api/ops/search',api_ops_search)
