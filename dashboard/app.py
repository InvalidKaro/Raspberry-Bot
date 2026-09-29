from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from aiohttp import web

from . import app_legacy
from .config import DashboardConfig
from .meshtastic_routes import register_meshtastic_routes
from .services.database_admin_service import DatabaseAdminService
from .services.maintenance_center import MaintenanceCenter
from .services.system_service import bot_action, get_status
from .services.commands import run_command

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


async def control_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "control.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def database_admin_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "database_admin.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def tools_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "tools.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def _dashboard_db_path(config: DashboardConfig) -> Path:
    configured = Path(config.database_path)
    if configured.is_absolute():
        return configured
    return Path(config.repo_path) / configured


async def api_control_center(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    db_path = _dashboard_db_path(config)

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


async def api_control_history(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    db_path = _dashboard_db_path(config)
    try:
        limit = max(12, min(160, int(request.query.get("limit", "80"))))
    except ValueError:
        limit = 80

    def read_history() -> list[dict]:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT recorded_at,
                          ROUND(AVG(cpu_percent), 1) AS cpu_percent,
                          ROUND(AVG(ram_percent), 1) AS ram_percent,
                          ROUND(AVG(temperature), 1) AS temperature
                   FROM system_snapshots_v4
                   GROUP BY recorded_at
                   ORDER BY recorded_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]
        finally:
            con.close()

    try:
        history = await asyncio.to_thread(read_history)
    except sqlite3.Error as exc:
        return web.json_response({"ok": False, "message": str(exc), "history": []}, status=400)
    return web.json_response({"ok": True, "history": history, "interval_seconds": 90})


async def api_control_personnel(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    db_path = _dashboard_db_path(config)

    def read_personnel() -> dict:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT pm.id, pm.display_name,
                          COALESCE(SUM(pr.inductions), 0) AS inductions,
                          COALESCE(SUM(pr.bwg), 0) AS bwg,
                          COALESCE(SUM(pr.inductions), 0) + COALESCE(SUM(pr.bwg), 0) AS activity
                   FROM personnel_members pm
                   LEFT JOIN personnel_records pr ON pr.personnel_id = pm.id
                   WHERE pm.active = 1
                   GROUP BY pm.id, pm.display_name
                   ORDER BY activity DESC, pm.display_name COLLATE NOCASE
                   LIMIT 30"""
            ).fetchall()
            result = [dict(row) for row in rows]
            return {
                "rows": result,
                "total_e": sum(int(row["inductions"]) for row in result),
                "total_b": sum(int(row["bwg"]) for row in result),
            }
        finally:
            con.close()

    try:
        data = await asyncio.to_thread(read_personnel)
    except sqlite3.Error as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    return web.json_response({"ok": True, **data})


async def api_control_personnel_report(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    db_path = _dashboard_db_path(config)
    report_format = request.query.get("format", "overview")
    if report_format not in {"overview", "chart"}:
        return web.json_response({"ok": False, "message": "Unsupported report format."}, status=400)

    def read_rows() -> list[dict]:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT pm.display_name,
                          COALESCE(SUM(pr.inductions), 0) AS inductions,
                          COALESCE(SUM(pr.bwg), 0) AS bwg,
                          COALESCE(SUM(pr.inductions), 0) + COALESCE(SUM(pr.bwg), 0) AS activity
                   FROM personnel_members pm
                   LEFT JOIN personnel_records pr ON pr.personnel_id = pm.id
                   WHERE pm.active = 1
                   GROUP BY pm.id, pm.display_name
                   ORDER BY activity DESC, pm.display_name COLLATE NOCASE"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            con.close()

    try:
        rows = await asyncio.to_thread(read_rows)
        from services.personnel_export import render_personnel_chart, render_personnel_png
        if report_format == "chart":
            data = await asyncio.to_thread(render_personnel_chart, "MD Personalabteilung • Aktivitätsdiagramm", rows)
            filename = "perso-diagramm.png"
        else:
            data = await asyncio.to_thread(render_personnel_png, "MD Personalabteilung • Statistik", rows)
            filename = "perso-statistik.png"
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=500)

    return web.Response(
        body=data,
        content_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


async def api_cogs(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    text = (Path(config.repo_path) / "bot.py").read_text(encoding="utf-8")
    extensions = re.findall(r'"((?:cogs|tasks)\.[^"]+)"', text)
    return web.json_response({"ok": True, "extensions": extensions})


def _enqueue_dashboard_command(db_path: Path, action: str, payload: dict) -> int:
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


async def api_cog_action(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    if action not in {"reload", "load", "unload", "sync"}:
        return web.json_response({"ok": False, "message": "Unsupported action"}, status=400)
    data = await request.json()
    payload = {} if action == "sync" else {"extension": str(data.get("extension", ""))}
    config: DashboardConfig = request.app["config"]
    command_id = await asyncio.to_thread(
        _enqueue_dashboard_command,
        _dashboard_db_path(config),
        action,
        payload,
    )
    return web.json_response({"ok": True, "command_id": command_id, "message": "Queued for bot process"})


async def api_maintenance_action(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    if action not in {"cache-clear", "gc", "database-optimize"}:
        return web.json_response({"ok": False, "message": "Unsupported maintenance action"}, status=400)
    config: DashboardConfig = request.app["config"]
    command_id = await asyncio.to_thread(
        _enqueue_dashboard_command,
        _dashboard_db_path(config),
        action,
        {},
    )
    audit = request.app.get("audit")
    if audit is not None:
        audit.record(f"control.maintenance.{action}", ok=True, detail=f"queued #{command_id}")
    return web.json_response({"ok": True, "command_id": command_id, "message": "Queued for bot process"})


async def api_dashboard_command(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    db_path = _dashboard_db_path(config)
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


async def _restart_dashboard_later() -> None:
    await asyncio.sleep(0.7)
    await bot_action("raspberry-dashboard", "restart")


async def api_control_system_action(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    if action not in {"pull", "restart-bot", "restart-dashboard", "update-all"}:
        return web.json_response({"ok": False, "message": "Unsupported control action."}, status=400)

    config: DashboardConfig = request.app["config"]
    audit = request.app.get("audit")

    try:
        if action == "pull":
            result = await request.app["git"].pull()
            if audit is not None:
                audit.record("control.git.pull", ok=bool(result.get("ok")), detail=str(result.get("message", "")))
            return web.json_response(result, status=200 if result.get("ok") else 409)

        if action == "restart-bot":
            result = await bot_action(config.bot_service, "restart")
            if audit is not None:
                audit.record("control.bot.restart", ok=bool(result.get("ok")), detail=str(result.get("message", "")))
            return web.json_response(result, status=200 if result.get("ok") else 500)

        if action == "restart-dashboard":
            if audit is not None:
                audit.record("control.dashboard.restart", ok=True, detail="Dashboard restart requested")
            asyncio.create_task(_restart_dashboard_later(), name="dashboard-self-restart")
            return web.json_response({"ok": True, "message": "Dashboard restart scheduled.", "dashboard_restarting": True})

        pull = await request.app["git"].pull()
        if not pull.get("ok"):
            if audit is not None:
                audit.record("control.update_all", ok=False, detail=f"git pull failed: {pull.get('message', '')}")
            return web.json_response({"ok": False, "message": f"Git pull failed: {pull.get('message', '')}"}, status=409)

        bot = await bot_action(config.bot_service, "restart")
        if not bot.get("ok"):
            if audit is not None:
                audit.record("control.update_all", ok=False, detail=f"bot restart failed: {bot.get('message', '')}")
            return web.json_response({"ok": False, "message": f"Pull completed, but bot restart failed: {bot.get('message', '')}"}, status=500)

        if audit is not None:
            audit.record("control.update_all", ok=True, detail="git pull + bot restart + dashboard restart")
        asyncio.create_task(_restart_dashboard_later(), name="dashboard-update-restart")
        return web.json_response({
            "ok": True,
            "message": "Git pull completed. Bot restarted. Dashboard restart scheduled.",
            "dashboard_restarting": True,
        })
    except Exception as exc:
        if audit is not None:
            audit.record(f"control.{action}", ok=False, detail=f"{type(exc).__name__}: {exc}")
        return web.json_response({"ok": False, "message": f"{type(exc).__name__}: {exc}"}, status=500)


def _db_audit(request: web.Request, action: str, table: str, result: dict) -> None:
    audit = request.app.get("audit")
    if audit is not None:
        audit.record(
            f"database.{action}",
            ok=bool(result.get("ok")),
            detail=f"table={table} {result.get('message', '')}",
        )


async def api_database_admin_metadata(request: web.Request) -> web.Response:
    try:
        result = await request.app["database_admin"].metadata(request.match_info["table"])
        return web.json_response(result)
    except (ValueError, OSError, sqlite3.Error) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_database_admin_insert(request: web.Request) -> web.Response:
    table = request.match_info["table"]
    try:
        data = await request.json()
        result = await request.app["database_admin"].insert(table, data.get("values", {}))
        _db_audit(request, "insert", table, result)
        return web.json_response(result)
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = {"ok": False, "message": str(exc)}
        _db_audit(request, "insert", table, result)
        return web.json_response(result, status=400)


async def api_database_admin_update(request: web.Request) -> web.Response:
    table = request.match_info["table"]
    try:
        data = await request.json()
        result = await request.app["database_admin"].update(
            table,
            data.get("key", {}),
            data.get("values", {}),
            expected=data.get("expected"),
        )
        _db_audit(request, "update", table, result)
        return web.json_response(result)
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = {"ok": False, "message": str(exc)}
        _db_audit(request, "update", table, result)
        return web.json_response(result, status=400)


async def api_database_admin_delete(request: web.Request) -> web.Response:
    table = request.match_info["table"]
    try:
        data = await request.json()
        if str(data.get("confirm", "")) != "DELETE":
            return web.json_response({"ok": False, "message": "Type DELETE exactly to confirm."}, status=400)
        result = await request.app["database_admin"].delete(
            table,
            data.get("key", {}),
            expected=data.get("expected"),
        )
        _db_audit(request, "delete", table, result)
        return web.json_response(result)
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = {"ok": False, "message": str(exc)}
        _db_audit(request, "delete", table, result)
        return web.json_response(result, status=400)



async def api_tools_diagnostics(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    system_task = get_status(config.bot_service, request.app["system_sampler"])
    checks_task = request.app["maintenance"].diagnostic_checks()
    system, diagnostics = await asyncio.gather(system_task, checks_task)
    return web.json_response({"ok": bool(diagnostics.get("ok")), "system": system, "diagnostics": diagnostics})


async def api_tools_network(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        result = await request.app["maintenance"].network_test(
            str(data.get("kind", "")),
            str(data.get("target", "")),
        )
    except (ValueError, OSError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    audit = request.app.get("audit")
    if audit is not None:
        audit.record(
            f"tools.network.{result.get('kind', 'unknown')}",
            ok=bool(result.get("ok")),
            detail=str(result.get("target", "")),
        )
    return web.json_response(result, status=200 if result.get("ok") else 400)


async def api_tools_updates(request: web.Request) -> web.Response:
    result = await request.app["maintenance"].cached_updates()
    return web.json_response(result, status=200 if result.get("available") else 503)


async def api_tools_schedule(request: web.Request) -> web.Response:
    maintenance: MaintenanceCenter = request.app["maintenance"]
    if request.method == "GET":
        return web.json_response({"ok": True, "schedule": maintenance.status()})
    try:
        data = await request.json()
        schedule = maintenance.configure(data)
    except (ValueError, TypeError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)
    audit = request.app.get("audit")
    if audit is not None:
        audit.record(
            "tools.backup.schedule",
            ok=True,
            detail=f"enabled={schedule.get('enabled')} frequency={schedule.get('frequency')} time={schedule.get('time')}",
        )
    return web.json_response({"ok": True, "schedule": schedule})


async def api_tools_backup_now(request: web.Request) -> web.Response:
    result = await request.app["maintenance"].run_backup(automatic=False)
    audit = request.app.get("audit")
    if audit is not None:
        audit.record("tools.backup.run", ok=bool(result.get("ok")), detail=str(result.get("message", "")))
    return web.json_response(result, status=200 if result.get("ok") else 500)


async def api_tools_service_action(request: web.Request) -> web.Response:
    service_key = request.match_info["service"]
    action = request.match_info["action"]
    config: DashboardConfig = request.app["config"]
    services = {
        "bot": config.bot_service,
        "pihole": "pihole-FTL",
        "tailscale": "tailscaled",
        "dashboard": "raspberry-dashboard",
    }
    if service_key not in services or action not in {"start", "stop", "restart"}:
        return web.json_response({"ok": False, "message": "Unsupported service action."}, status=400)
    if service_key == "dashboard":
        if action != "restart":
            return web.json_response({"ok": False, "message": "Dashboard only supports restart from this page."}, status=400)
        audit = request.app.get("audit")
        if audit is not None:
            audit.record("tools.service.dashboard.restart", ok=True, detail="Dashboard restart requested")
        asyncio.create_task(_restart_dashboard_later(), name="tools-dashboard-self-restart")
        return web.json_response({"ok": True, "message": "Dashboard restart scheduled.", "dashboard_restarting": True})

    result = await run_command(["sudo", "-n", "systemctl", action, services[service_key]], timeout=20)
    payload = {
        "ok": result.ok,
        "message": result.stdout or result.stderr or f"{services[service_key]} {action} completed.",
        "service": service_key,
        "action": action,
    }
    audit = request.app.get("audit")
    if audit is not None:
        audit.record(f"tools.service.{service_key}.{action}", ok=result.ok, detail=payload["message"])
    return web.json_response(payload, status=200 if result.ok else 500)


async def api_tools_support_bundle(request: web.Request) -> web.StreamResponse:
    config: DashboardConfig = request.app["config"]
    system_task = get_status(config.bot_service, request.app["system_sampler"])
    checks_task = request.app["maintenance"].diagnostic_checks()
    updates_task = request.app["maintenance"].cached_updates()
    git_task = request.app["git"].status()
    system, diagnostics, updates, git = await asyncio.gather(
        system_task, checks_task, updates_task, git_task
    )

    safe_system_keys = {
        "hostname", "lan_ip", "os_name", "kernel", "architecture", "cpu_count",
        "cpu_frequency_mhz", "cpu_percent", "cpu_average_30s", "cpu_average_5m",
        "temperature_c", "memory_percent", "memory_used_mb", "memory_total_mb",
        "memory_available_mb", "swap_percent", "disk_percent", "disk_used_gb",
        "disk_total_gb", "disk_free_gb", "uptime_seconds", "load_average",
        "network_rx_rate_bps", "network_tx_rate_bps", "bot_active",
    }
    safe_system = {key: system.get(key) for key in sorted(safe_system_keys)}
    safe_system["services"] = [
        {
            "name": row.get("name"),
            "active": row.get("active"),
            "sub": row.get("sub"),
            "memory_mb": row.get("memory_mb"),
        }
        for row in system.get("services", [])
    ]
    safe_system["pihole"] = {
        key: (system.get("pihole") or {}).get(key)
        for key in ("installed", "active", "blocking", "api_available")
    }
    safe_system["tailscale"] = {
        key: (system.get("tailscale") or {}).get(key)
        for key in ("installed", "online")
    }

    bundle = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "dashboard_version": "3.3.2",
        "system": safe_system,
        "diagnostics": diagnostics,
        "git": {
            "ok": git.get("ok"),
            "branch": git.get("branch"),
            "dirty": git.get("dirty"),
            "ahead": git.get("ahead"),
            "behind": git.get("behind"),
            "change_count": len(git.get("changes") or []),
            "last_commit": git.get("last_commit"),
        },
        "cached_updates": {
            "ok": updates.get("ok"),
            "available": updates.get("available"),
            "count": updates.get("count", 0),
            "packages": [row.get("package") for row in updates.get("updates", [])[:100]],
        },
        "backup_schedule": request.app["maintenance"].status(),
    }
    body = json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8")
    filename = datetime.now().strftime("homepi-support-%Y%m%d-%H%M%S.json")
    return web.Response(
        body=body,
        content_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )



def create_app(config: DashboardConfig | None = None) -> web.Application:
    app = app_legacy.create_app(config)
    register_meshtastic_routes(app)
    app["database_admin"] = DatabaseAdminService(app["config"].database_path)
    state_dir = Path.home() / ".local" / "state" / "homepi-dashboard"
    app["maintenance"] = MaintenanceCenter(app["backups"], app["backups"].database_path, state_dir)

    async def _start_maintenance(application: web.Application) -> None:
        await application["maintenance"].start()

    async def _stop_maintenance(application: web.Application) -> None:
        await application["maintenance"].stop()

    app.on_startup.append(_start_maintenance)
    app.on_cleanup.append(_stop_maintenance)
    app.router.add_get("/control", control_page)
    app.router.add_get("/database-admin", database_admin_page)
    app.router.add_get("/tools", tools_page)
    app.router.add_get("/api/tools/diagnostics", api_tools_diagnostics)
    app.router.add_post("/api/tools/network", api_tools_network)
    app.router.add_get("/api/tools/updates", api_tools_updates)
    app.router.add_get("/api/tools/schedule", api_tools_schedule)
    app.router.add_post("/api/tools/schedule", api_tools_schedule)
    app.router.add_post("/api/tools/backup-now", api_tools_backup_now)
    app.router.add_post("/api/tools/service/{service}/{action}", api_tools_service_action)
    app.router.add_get("/api/tools/support-bundle", api_tools_support_bundle)
    app.router.add_get("/api/control-center", api_control_center)
    app.router.add_get("/api/control/history", api_control_history)
    app.router.add_get("/api/control/personnel", api_control_personnel)
    app.router.add_get("/api/control/personnel/report", api_control_personnel_report)
    app.router.add_post("/api/control/maintenance/{action}", api_maintenance_action)
    app.router.add_get("/api/cogs", api_cogs)
    app.router.add_post("/api/cogs/{action}", api_cog_action)
    app.router.add_get("/api/dashboard-command/{id}", api_dashboard_command)
    app.router.add_post("/api/control/system/{action}", api_control_system_action)
    app.router.add_get("/api/database/admin/{table}/metadata", api_database_admin_metadata)
    app.router.add_post("/api/database/admin/{table}/insert", api_database_admin_insert)
    app.router.add_patch("/api/database/admin/{table}/update", api_database_admin_update)
    app.router.add_delete("/api/database/admin/{table}/delete", api_database_admin_delete)
    return app


def main() -> None:
    config = DashboardConfig.from_env()
    web.run_app(create_app(config), host=config.host, port=config.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
