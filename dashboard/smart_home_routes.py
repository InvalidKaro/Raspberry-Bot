from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from aiohttp import web

SMART_HOME_GUILD_ID = 1162733312226361454
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
ALLOWED_ACTIONS = {
    "smart-home-snapshot",
    "smart-home-scan",
    "smart-home-climate-refresh",
    "smart-home-preset",
    "smart-home-power",
    "smart-home-brightness",
    "smart-home-color",
}
ALLOWED_PRESETS = {"on", "off", "night", "gaming"}


def _db_path(config: Any) -> Path:
    path = Path(config.database_path)
    if path.is_absolute():
        return path
    return Path(config.repo_path) / path


def _connect(config: Any) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(config), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


async def smart_home_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "smart_home.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_smart_home_command(request: web.Request) -> web.Response:
    data = await request.json()
    action = str(data.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        return web.json_response({"ok": False, "message": "Unsupported smart-home action."}, status=400)

    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "message": "payload must be an object."}, status=400)

    if action == "smart-home-preset":
        preset = str(payload.get("preset") or "").strip().lower()
        if preset not in ALLOWED_PRESETS:
            return web.json_response({"ok": False, "message": "Unsupported preset."}, status=400)
        payload["preset"] = preset

    if action == "smart-home-brightness":
        try:
            payload["value"] = max(1, min(100, int(payload.get("value", 50))))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "message": "Brightness must be 1-100."}, status=400)

    if action == "smart-home-color":
        try:
            for key in ("r", "g", "b"):
                payload[key] = max(0, min(255, int(payload.get(key, 0))))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "message": "RGB values must be 0-255."}, status=400)

    config = request.app["config"]

    def enqueue() -> int:
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

    command_id = await asyncio.to_thread(enqueue)
    audit = request.app.get("audit")
    if audit is not None:
        audit.record(
            f"smart-home.queue.{action.removeprefix('smart-home-')}",
            ok=True,
            detail=f"command #{command_id}",
        )
    return web.json_response({"ok": True, "command_id": command_id})


async def api_smart_home_command_status(request: web.Request) -> web.Response:
    try:
        command_id = int(request.match_info["id"])
    except ValueError:
        return web.json_response({"ok": False, "message": "Invalid command id."}, status=400)

    config = request.app["config"]

    def read() -> dict[str, Any] | None:
        con = _connect(config)
        try:
            row = con.execute(
                """SELECT id,action,status,result,created_at,processed_at
                   FROM dashboard_commands
                   WHERE id=? AND action LIKE 'smart-home-%'""",
                (command_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            con.close()

    row = await asyncio.to_thread(read)
    if row is None:
        return web.json_response({"ok": False, "message": "Command not found."}, status=404)

    parsed = None
    if row.get("result"):
        try:
            parsed = json.loads(str(row["result"]))
        except json.JSONDecodeError:
            parsed = None
    row["parsed_result"] = parsed
    return web.json_response({"ok": True, "command": row})


async def api_smart_home_alerts(request: web.Request) -> web.Response:
    config = request.app["config"]

    if request.method == "GET":
        def read() -> dict[str, Any] | None:
            con = _connect(config)
            try:
                row = con.execute(
                    """SELECT guild_id,channel_id,enabled,temp_min,temp_max,humidity_min,
                              humidity_max,cooldown_minutes,last_fired_at,last_reason,updated_at
                       FROM smart_home_alert_config WHERE guild_id=?""",
                    (SMART_HOME_GUILD_ID,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                con.close()

        row = await asyncio.to_thread(read)
        return web.json_response({"ok": True, "config": row or {
            "guild_id": SMART_HOME_GUILD_ID,
            "channel_id": None,
            "enabled": 0,
            "temp_min": 17.0,
            "temp_max": 27.0,
            "humidity_min": 35.0,
            "humidity_max": 65.0,
            "cooldown_minutes": 60,
            "last_fired_at": None,
            "last_reason": None,
        }})

    data = await request.json()

    def optional_float(name: str, default: float | None) -> float | None:
        value = data.get(name, default)
        if value in ("", None):
            return None
        return float(value)

    try:
        channel_id = int(data["channel_id"]) if data.get("channel_id") else None
        temp_min = optional_float("temp_min", 17.0)
        temp_max = optional_float("temp_max", 27.0)
        humidity_min = optional_float("humidity_min", 35.0)
        humidity_max = optional_float("humidity_max", 65.0)
        cooldown = max(5, min(1440, int(data.get("cooldown_minutes", 60))))
    except (TypeError, ValueError, KeyError) as exc:
        return web.json_response({"ok": False, "message": f"Invalid alert config: {exc}"}, status=400)

    if temp_min is not None and temp_max is not None and temp_min >= temp_max:
        return web.json_response({"ok": False, "message": "temp_min must be below temp_max."}, status=400)
    if humidity_min is not None and humidity_max is not None and humidity_min >= humidity_max:
        return web.json_response({"ok": False, "message": "humidity_min must be below humidity_max."}, status=400)

    def write() -> None:
        con = _connect(config)
        try:
            con.execute(
                """INSERT INTO smart_home_alert_config(
                       guild_id,channel_id,enabled,temp_min,temp_max,humidity_min,
                       humidity_max,cooldown_minutes,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(guild_id) DO UPDATE SET
                       channel_id=excluded.channel_id,
                       enabled=excluded.enabled,
                       temp_min=excluded.temp_min,
                       temp_max=excluded.temp_max,
                       humidity_min=excluded.humidity_min,
                       humidity_max=excluded.humidity_max,
                       cooldown_minutes=excluded.cooldown_minutes,
                       updated_at=CURRENT_TIMESTAMP""",
                (
                    SMART_HOME_GUILD_ID,
                    channel_id,
                    int(bool(data.get("enabled"))),
                    temp_min,
                    temp_max,
                    humidity_min,
                    humidity_max,
                    cooldown,
                ),
            )
            con.commit()
        finally:
            con.close()

    await asyncio.to_thread(write)
    audit = request.app.get("audit")
    if audit is not None:
        audit.record(
            "smart-home.alerts.save",
            ok=True,
            detail=f"enabled={bool(data.get('enabled'))} channel={channel_id}",
        )
    return web.json_response({"ok": True})


async def api_smart_home_schedules(request: web.Request) -> web.Response:
    config = request.app["config"]

    if request.method == "GET":
        def read() -> list[dict[str, Any]]:
            con = _connect(config)
            try:
                rows = con.execute(
                    """SELECT id,name,device_selector,preset,run_time,weekdays,
                              notify_channel_id,enabled,last_run_key,last_result,
                              created_at,updated_at
                       FROM smart_home_schedules
                       WHERE guild_id=?
                       ORDER BY run_time,name,id""",
                    (SMART_HOME_GUILD_ID,),
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                con.close()

        return web.json_response({"ok": True, "schedules": await asyncio.to_thread(read)})

    data = await request.json()
    action = str(data.get("action") or "save").lower()

    if action == "delete":
        try:
            schedule_id = int(data.get("id"))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "message": "Invalid schedule id."}, status=400)

        def delete() -> int:
            con = _connect(config)
            try:
                cur = con.execute(
                    "DELETE FROM smart_home_schedules WHERE id=? AND guild_id=?",
                    (schedule_id, SMART_HOME_GUILD_ID),
                )
                con.commit()
                return int(cur.rowcount)
            finally:
                con.close()

        removed = await asyncio.to_thread(delete)
        return web.json_response({"ok": bool(removed), "deleted": removed})

    try:
        schedule_id = int(data.get("id") or 0)
        name = str(data.get("name") or "Smart-Home Szene").strip()[:80]
        selector = str(data.get("device_selector") or "all").strip()[:160]
        preset = str(data.get("preset") or "off").strip().lower()
        run_time = str(data.get("run_time") or "23:30").strip()
        weekdays_raw = data.get("weekdays", [0, 1, 2, 3, 4, 5, 6])
        weekdays = sorted({int(value) for value in weekdays_raw if 0 <= int(value) <= 6})
        notify_channel_id = int(data["notify_channel_id"]) if data.get("notify_channel_id") else None
    except (TypeError, ValueError) as exc:
        return web.json_response({"ok": False, "message": f"Invalid schedule: {exc}"}, status=400)

    if preset not in ALLOWED_PRESETS:
        return web.json_response({"ok": False, "message": "Unsupported preset."}, status=400)
    if not __import__("re").fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", run_time):
        return web.json_response({"ok": False, "message": "run_time must use HH:MM."}, status=400)
    if not weekdays:
        return web.json_response({"ok": False, "message": "Select at least one weekday."}, status=400)

    weekdays_text = ",".join(str(value) for value in weekdays)

    def save() -> int:
        con = _connect(config)
        try:
            if schedule_id:
                con.execute(
                    """UPDATE smart_home_schedules
                       SET name=?,device_selector=?,preset=?,run_time=?,weekdays=?,
                           notify_channel_id=?,enabled=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND guild_id=?""",
                    (
                        name,
                        selector,
                        preset,
                        run_time,
                        weekdays_text,
                        notify_channel_id,
                        int(bool(data.get("enabled", True))),
                        schedule_id,
                        SMART_HOME_GUILD_ID,
                    ),
                )
                result_id = schedule_id
            else:
                cur = con.execute(
                    """INSERT INTO smart_home_schedules(
                           guild_id,name,device_selector,preset,run_time,weekdays,
                           notify_channel_id,enabled
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        SMART_HOME_GUILD_ID,
                        name,
                        selector,
                        preset,
                        run_time,
                        weekdays_text,
                        notify_channel_id,
                        int(bool(data.get("enabled", True))),
                    ),
                )
                result_id = int(cur.lastrowid)
            con.commit()
            return result_id
        finally:
            con.close()

    result_id = await asyncio.to_thread(save)
    audit = request.app.get("audit")
    if audit is not None:
        audit.record("smart-home.schedule.save", ok=True, detail=f"id={result_id} name={name}")
    return web.json_response({"ok": True, "id": result_id})


async def api_smart_home_history(request: web.Request) -> web.Response:
    config = request.app["config"]
    try:
        hours = max(1, min(720, int(request.query.get("hours", "24"))))
    except ValueError:
        hours = 24

    def read() -> list[dict[str, Any]]:
        con = _connect(config)
        try:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='govee_climate_history'"
            ).fetchone()
            if not exists:
                return []
            rows = con.execute(
                """SELECT model,recorded_at,temperature_c,humidity_percent,battery_percent
                   FROM govee_climate_history
                   WHERE recorded_at>=datetime('now',?)
                   ORDER BY recorded_at ASC""",
                (f"-{hours} hours",),
            ).fetchall()
            result = [dict(row) for row in rows]
            if len(result) <= 500:
                return result
            step = max(1, len(result) // 500)
            sampled = result[::step]
            if sampled[-1] != result[-1]:
                sampled.append(result[-1])
            return sampled[:501]
        finally:
            con.close()

    rows = await asyncio.to_thread(read)
    temperatures = [float(row["temperature_c"]) for row in rows if row["temperature_c"] is not None]
    humidities = [float(row["humidity_percent"]) for row in rows if row["humidity_percent"] is not None]

    def stats(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        return {
            "min": round(min(values), 1),
            "avg": round(sum(values) / len(values), 1),
            "max": round(max(values), 1),
        }

    return web.json_response({
        "ok": True,
        "hours": hours,
        "samples": rows,
        "stats": {
            "temperature": stats(temperatures),
            "humidity": stats(humidities),
        },
    })


async def api_smart_home_recent_commands(request: web.Request) -> web.Response:
    config = request.app["config"]

    def read() -> list[dict[str, Any]]:
        con = _connect(config)
        try:
            rows = con.execute(
                """SELECT id,action,status,result,created_at,processed_at
                   FROM dashboard_commands
                   WHERE action LIKE 'smart-home-%'
                   ORDER BY id DESC LIMIT 30"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            con.close()

    return web.json_response({"ok": True, "commands": await asyncio.to_thread(read)})


def register_smart_home_routes(app: web.Application) -> None:
    app.router.add_get("/smart-home", smart_home_page)
    app.router.add_post("/api/smart-home/command", api_smart_home_command)
    app.router.add_get("/api/smart-home/command/{id}", api_smart_home_command_status)
    app.router.add_get("/api/smart-home/alerts", api_smart_home_alerts)
    app.router.add_post("/api/smart-home/alerts", api_smart_home_alerts)
    app.router.add_get("/api/smart-home/schedules", api_smart_home_schedules)
    app.router.add_post("/api/smart-home/schedules", api_smart_home_schedules)
    app.router.add_get("/api/smart-home/history", api_smart_home_history)
    app.router.add_get("/api/smart-home/recent", api_smart_home_recent_commands)
