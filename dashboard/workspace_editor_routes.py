from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class ResourceSpec:
    table: str
    title: str
    group: str
    fixed: dict[str, object] = field(default_factory=dict)
    can_create: bool = True
    can_update: bool = True
    can_delete: bool = True
    description: str = ""


# Only these tables are reachable from the Workspace manager. The request never
# supplies a raw table name, keeping SQL identifiers strictly allow-listed.
RESOURCES: dict[str, ResourceSpec] = {
    "templates": ResourceSpec("content_templates", "Templates", "Creator", description="Gespeicherte Nachrichten- und Embed-Vorlagen."),
    "forms": ResourceSpec("forms", "Formulare", "Creator", description="Formular-Definitionen. Fragen können als JSON-Liste oder mit | getrennt eingegeben werden."),
    "form-responses": ResourceSpec("form_responses", "Formularantworten", "Creator", can_create=False, can_update=False, description="Gespeicherte Antworten; Löschen bleibt für Aufräumarbeiten möglich."),
    "panels": ResourceSpec("panel_messages", "Button-/RoleSelect-Panels", "Creator", description="Gespeicherte Panel-Köpfe. Discord-Nachrichten-IDs werden automatisch vom Bot gesetzt, wenn ein Panel über Discord veröffentlicht wurde."),
    "panel-actions": ResourceSpec("panel_actions", "Panel-Aktionen / RoleSelects", "Creator", description="Buttons, Links, Info-Aktionen und RoleSelect-Definitionen."),
    "planner": ResourceSpec("planner_entries", "Wochenplaner", "Organisation", description="Termine des allgemeinen Wochenplaners."),
    "tasks": ResourceSpec("workspace_tasks", "Aufgabenboard", "Organisation", description="Interne Aufgaben mit Status und Fälligkeit."),
    "events": ResourceSpec("workspace_events", "Events", "Organisation", description="Events und Termine."),
    "rsvps": ResourceSpec("event_rsvps", "Event-Zusagen", "Organisation", can_create=False, description="Zu-/Absagen zu Events."),
    "reminders": ResourceSpec("reminders", "Reminder Hub", "Organisation", description="Erinnerungen. delivered=0 bedeutet noch offen."),
    "trainings": ResourceSpec("training_library", "Schulungsbibliothek", "Wissen", description="Schulungsinhalte und Materiallinks."),
    "wiki": ResourceSpec("knowledge_entries", "Wiki", "Wissen", fixed={"kind": "wiki"}, description="Interne Wiki-Seiten."),
    "faq": ResourceSpec("knowledge_entries", "FAQ", "Wissen", fixed={"kind": "faq"}, description="FAQ-Fragen und Antworten."),
    "med": ResourceSpec("knowledge_entries", "Wissen / Medikamente", "Wissen", fixed={"kind": "med"}, description="Medikamenten- und Wissenseinträge."),
    "knowledge": ResourceSpec("knowledge_entries", "Alle Wissenseinträge", "Wissen", description="Gesamte Knowledge-Datenbank inklusive kind."),
    "quiz": ResourceSpec("quiz_questions", "Prüfungsfragen", "Wissen", description="Fragenpool mit Antwort und Erklärung."),
    "achievements": ResourceSpec("achievements", "Achievements", "Community", description="Eigene Achievements und XP-Schwellen."),
    "quotes": ResourceSpec("quotes", "Quotes", "Community", description="Gespeicherte Zitate."),
    "giveaways": ResourceSpec("giveaways", "Giveaways", "Community", description="Gewinnspiele und deren Status."),
    "giveaway-entries": ResourceSpec("giveaway_entries", "Giveaway-Teilnahmen", "Community", can_create=False, description="Teilnahmen an Gewinnspielen."),
    "commands": ResourceSpec("custom_commands", "Custom Commands", "Automation", description="Dashboard-/Discord-Custom-Commands."),
    "automations": ResourceSpec("automation_jobs", "Scheduler / Automationen", "Automation", description="Zeitgesteuerte Nachrichten, Templates und Webhooks."),
    "webhooks": ResourceSpec("webhook_endpoints", "Webhook Hub", "Automation", description="Gespeicherte Webhook-Endpunkte."),
}

AUTO_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "delivered_at",
    "ended_at",
    "last_run_at",
    "last_status",
    "last_error",
    "processed_at",
}
IDENTITY_FIELDS = {"created_by", "updated_by"}
BOOLEAN_FIELDS = {"enabled", "active", "delivered"}
ID_FIELDS = {
    "guild_id",
    "user_id",
    "channel_id",
    "role_id",
    "message_id",
    "author_id",
    "assigned_to",
    "created_by",
    "updated_by",
    "winner_id",
    "opener_id",
}


def _db_path(config) -> Path:
    configured = Path(config.database_path)
    if configured.is_absolute():
        return configured
    return Path(config.repo_path) / configured


def _connect(config) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(config))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _quote_ident(name: str) -> str:
    # Identifiers only originate from PRAGMA or the static RESOURCES map.
    return '"' + name.replace('"', '""') + '"'


def _table_info(con: sqlite3.Connection, table: str) -> list[dict]:
    rows = con.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    return [dict(row) for row in rows]


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _json_safe(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    for key, value in list(data.items()):
        if value is not None and (key.endswith("_id") or key in ID_FIELDS):
            data[key] = str(value)
    return data


def _field_kind(name: str, sql_type: str) -> str:
    upper = (sql_type or "").upper()
    if name in BOOLEAN_FIELDS:
        return "boolean"
    if name.endswith("_json") or name in {"value"}:
        return "json" if name.endswith("_json") else "text"
    if name in ID_FIELDS or name.endswith("_id"):
        return "snowflake"
    if name in {"content", "body", "details", "description", "explanation", "message", "questions_json", "response", "rules_text"}:
        return "textarea"
    if name in {"due_at", "starts_at", "run_at", "ends_at"}:
        return "datetime"
    if name in {"event_date"}:
        return "date"
    if name in {"start_time"}:
        return "time"
    if "INT" in upper:
        return "integer"
    if any(part in upper for part in ("REAL", "FLOA", "DOUB")):
        return "number"
    return "text"


def _field_meta(info: dict, spec: ResourceSpec) -> dict:
    name = str(info["name"])
    readonly = bool(info.get("pk")) or name in AUTO_FIELDS or name in IDENTITY_FIELDS or name == "guild_id" or name in spec.fixed
    required = bool(info.get("notnull")) and info.get("dflt_value") is None and not readonly
    return {
        "name": name,
        "sql_type": str(info.get("type") or "TEXT"),
        "required": required,
        "readonly": readonly,
        "default": info.get("dflt_value"),
        "kind": _field_kind(name, str(info.get("type") or "")),
    }


def _parse_guild(raw: object) -> int:
    value = str(raw or "").strip()
    if not value.isdigit():
        raise ValueError("Server auswählen.")
    return int(value)


def _coerce(name: str, sql_type: str, value: object, *, required: bool) -> object:
    if value is None:
        if required:
            raise ValueError(f"{name} ist erforderlich.")
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            if required:
                raise ValueError(f"{name} ist erforderlich.")
            return None
    if name in BOOLEAN_FIELDS:
        if isinstance(value, bool):
            return int(value)
        raw = str(value).lower()
        if raw in {"1", "true", "yes", "on", "ja"}:
            return 1
        if raw in {"0", "false", "no", "off", "nein"}:
            return 0
        raise ValueError(f"{name}: true/false erwartet.")
    if name.endswith("_json"):
        raw = str(value)
        if name == "questions_json" and not raw.lstrip().startswith(("[", "{")):
            parsed = [item.strip() for item in raw.split("|") if item.strip()]
            return json.dumps(parsed, ensure_ascii=False)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name}: ungültiges JSON ({exc.msg}).") from exc
        return json.dumps(parsed, ensure_ascii=False)
    if name == "color" and isinstance(value, str) and value.startswith("#"):
        try:
            return int(value[1:], 16)
        except ValueError as exc:
            raise ValueError("color: ungültiger Hex-Wert.") from exc
    upper = (sql_type or "").upper()
    if "INT" in upper:
        try:
            return int(str(value))
        except ValueError as exc:
            raise ValueError(f"{name}: ganze Zahl erwartet.") from exc
    if any(part in upper for part in ("REAL", "FLOA", "DOUB")):
        try:
            return float(str(value))
        except ValueError as exc:
            raise ValueError(f"{name}: Zahl erwartet.") from exc
    return str(value)


def _backup_database(config, reason: str) -> str:
    source = _db_path(config)
    folder = source.parent / "dashboard-edit-backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = folder / f"before-workspace-{reason}-{stamp}.sqlite3"
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    backups = sorted(folder.glob("before-workspace-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[30:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target.name


def _where_for_spec(spec: ResourceSpec, guild_id: int | None, columns: set[str]) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if "guild_id" in columns:
        if guild_id is None:
            raise ValueError("Server auswählen.")
        clauses.append('"guild_id"=?')
        params.append(guild_id)
    for name, value in spec.fixed.items():
        if name in columns:
            clauses.append(f"{_quote_ident(name)}=?")
            params.append(value)
    return clauses, params


async def workspace_manage_page(_: web.Request) -> web.Response:
    return web.Response(
        text=(TEMPLATE_DIR / "workspace_manage.html").read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_manage_resources(request: web.Request) -> web.Response:
    config = request.app["config"]

    def read() -> list[dict]:
        con = _connect(config)
        try:
            result = []
            for key, spec in RESOURCES.items():
                available = _table_exists(con, spec.table)
                result.append(
                    {
                        "key": key,
                        "title": spec.title,
                        "group": spec.group,
                        "description": spec.description,
                        "available": available,
                        "can_create": spec.can_create,
                        "can_update": spec.can_update,
                        "can_delete": spec.can_delete,
                    }
                )
            return result
        finally:
            con.close()

    return web.json_response({"ok": True, "resources": await asyncio.to_thread(read)})


async def api_manage_meta(request: web.Request) -> web.Response:
    key = request.match_info["resource"]
    spec = RESOURCES.get(key)
    if spec is None:
        raise web.HTTPNotFound()
    config = request.app["config"]

    def read() -> dict:
        con = _connect(config)
        try:
            if not _table_exists(con, spec.table):
                raise ValueError(f"Tabelle {spec.table} ist noch nicht vorhanden. Bot einmal starten.")
            info = _table_info(con, spec.table)
            return {
                "key": key,
                "title": spec.title,
                "description": spec.description,
                "fields": [_field_meta(item, spec) for item in info],
                "can_create": spec.can_create,
                "can_update": spec.can_update,
                "can_delete": spec.can_delete,
            }
        finally:
            con.close()

    try:
        return web.json_response({"ok": True, **await asyncio.to_thread(read)})
    except ValueError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_manage_list(request: web.Request) -> web.Response:
    key = request.match_info["resource"]
    spec = RESOURCES.get(key)
    if spec is None:
        raise web.HTTPNotFound()
    config = request.app["config"]
    query = str(request.query.get("q", "")).strip()
    guild_raw = request.query.get("guild_id")

    def read() -> list[dict]:
        con = _connect(config)
        try:
            if not _table_exists(con, spec.table):
                return []
            info = _table_info(con, spec.table)
            columns = {str(item["name"]) for item in info}
            guild_id = _parse_guild(guild_raw) if "guild_id" in columns else None
            clauses, params = _where_for_spec(spec, guild_id, columns)
            if query:
                searchable = [
                    str(item["name"])
                    for item in info
                    if str(item["name"]) not in {"guild_id", "created_by", "updated_by"}
                ][:12]
                if searchable:
                    q_clause = " OR ".join(f"CAST({_quote_ident(name)} AS TEXT) LIKE ?" for name in searchable)
                    clauses.append(f"({q_clause})")
                    params.extend([f"%{query}%"] * len(searchable))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            sql = f"SELECT rowid AS __rowid__, * FROM {_quote_ident(spec.table)}{where} ORDER BY rowid DESC LIMIT 150"
            return [_json_safe(row) for row in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    try:
        rows = await asyncio.to_thread(read)
        return web.json_response({"ok": True, "rows": rows})
    except (ValueError, sqlite3.Error) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


def _prepare_values(con: sqlite3.Connection, spec: ResourceSpec, values: dict, guild_id: int | None, *, creating: bool) -> tuple[dict[str, object], set[str]]:
    info = _table_info(con, spec.table)
    columns = {str(item["name"]) for item in info}
    meta = {str(item["name"]): _field_meta(item, spec) for item in info}
    output: dict[str, object] = {}
    if "guild_id" in columns:
        if guild_id is None:
            raise ValueError("Server auswählen.")
        output["guild_id"] = guild_id
    for name, fixed_value in spec.fixed.items():
        if name in columns:
            output[name] = fixed_value
    if creating:
        if "created_by" in columns:
            output["created_by"] = 0
        if "updated_by" in columns:
            output["updated_by"] = 0
    for name, raw in values.items():
        if name not in meta or meta[name]["readonly"]:
            continue
        output[name] = _coerce(name, meta[name]["sql_type"], raw, required=bool(meta[name]["required"]))
    if creating:
        for name, item in meta.items():
            if item["required"] and name not in output:
                raise ValueError(f"{name} ist erforderlich.")
    return output, columns


async def api_manage_create(request: web.Request) -> web.Response:
    key = request.match_info["resource"]
    spec = RESOURCES.get(key)
    if spec is None:
        raise web.HTTPNotFound()
    if not spec.can_create:
        return web.json_response({"ok": False, "message": "Erstellen ist für diesen Bereich deaktiviert."}, status=405)
    config = request.app["config"]
    data = await request.json()
    values = data.get("values") if isinstance(data.get("values"), dict) else {}

    def write() -> dict:
        con = _connect(config)
        try:
            if not _table_exists(con, spec.table):
                raise ValueError(f"Tabelle {spec.table} fehlt.")
            info = _table_info(con, spec.table)
            has_guild = any(str(item["name"]) == "guild_id" for item in info)
            guild_id = _parse_guild(data.get("guild_id")) if has_guild else None
            prepared, _ = _prepare_values(con, spec, values, guild_id, creating=True)
            if not prepared:
                raise ValueError("Keine speicherbaren Felder übergeben.")
            backup = _backup_database(config, f"create-{key}")
            names = list(prepared)
            sql = f"INSERT INTO {_quote_ident(spec.table)} ({','.join(_quote_ident(n) for n in names)}) VALUES ({','.join('?' for _ in names)})"
            cur = con.execute(sql, [prepared[name] for name in names])
            con.commit()
            return {"rowid": int(cur.lastrowid), "backup": backup}
        finally:
            con.close()

    try:
        result = await asyncio.to_thread(write)
        return web.json_response({"ok": True, **result})
    except (ValueError, sqlite3.IntegrityError, sqlite3.Error) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_manage_update(request: web.Request) -> web.Response:
    key = request.match_info["resource"]
    spec = RESOURCES.get(key)
    if spec is None:
        raise web.HTTPNotFound()
    if not spec.can_update:
        return web.json_response({"ok": False, "message": "Bearbeiten ist für diesen Bereich deaktiviert."}, status=405)
    config = request.app["config"]
    try:
        rowid = int(request.match_info["rowid"])
    except ValueError:
        raise web.HTTPNotFound()
    data = await request.json()
    values = data.get("values") if isinstance(data.get("values"), dict) else {}

    def write() -> dict:
        con = _connect(config)
        try:
            if not _table_exists(con, spec.table):
                raise ValueError(f"Tabelle {spec.table} fehlt.")
            info = _table_info(con, spec.table)
            columns = {str(item["name"]) for item in info}
            guild_id = _parse_guild(data.get("guild_id")) if "guild_id" in columns else None
            prepared, columns = _prepare_values(con, spec, values, guild_id, creating=False)
            prepared.pop("guild_id", None)
            for fixed_name in spec.fixed:
                prepared.pop(fixed_name, None)
            if not prepared:
                raise ValueError("Keine Änderungen erkannt.")
            backup = _backup_database(config, f"update-{key}")
            assignments = [f"{_quote_ident(name)}=?" for name in prepared]
            params: list[object] = [prepared[name] for name in prepared]
            if "updated_at" in columns:
                assignments.append('"updated_at"=CURRENT_TIMESTAMP')
            clauses = ["rowid=?"]
            params.append(rowid)
            scope, scope_params = _where_for_spec(spec, guild_id, columns)
            clauses.extend(scope)
            params.extend(scope_params)
            sql = f"UPDATE {_quote_ident(spec.table)} SET {','.join(assignments)} WHERE {' AND '.join(clauses)}"
            cur = con.execute(sql, params)
            if cur.rowcount != 1:
                raise ValueError("Eintrag nicht gefunden oder gehört zu einem anderen Server.")
            con.commit()
            return {"backup": backup}
        finally:
            con.close()

    try:
        result = await asyncio.to_thread(write)
        return web.json_response({"ok": True, **result})
    except (ValueError, sqlite3.IntegrityError, sqlite3.Error) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_manage_delete(request: web.Request) -> web.Response:
    key = request.match_info["resource"]
    spec = RESOURCES.get(key)
    if spec is None:
        raise web.HTTPNotFound()
    if not spec.can_delete:
        return web.json_response({"ok": False, "message": "Löschen ist für diesen Bereich deaktiviert."}, status=405)
    config = request.app["config"]
    try:
        rowid = int(request.match_info["rowid"])
    except ValueError:
        raise web.HTTPNotFound()
    data = await request.json()
    if str(data.get("confirm", "")) != "DELETE":
        return web.json_response({"ok": False, "message": "DELETE-Bestätigung fehlt."}, status=400)

    def write() -> dict:
        con = _connect(config)
        try:
            if not _table_exists(con, spec.table):
                raise ValueError(f"Tabelle {spec.table} fehlt.")
            columns = {str(item["name"]) for item in _table_info(con, spec.table)}
            guild_id = _parse_guild(data.get("guild_id")) if "guild_id" in columns else None
            backup = _backup_database(config, f"delete-{key}")
            clauses = ["rowid=?"]
            params: list[object] = [rowid]
            scope, scope_params = _where_for_spec(spec, guild_id, columns)
            clauses.extend(scope)
            params.extend(scope_params)
            cur = con.execute(
                f"DELETE FROM {_quote_ident(spec.table)} WHERE {' AND '.join(clauses)}",
                params,
            )
            if cur.rowcount != 1:
                raise ValueError("Eintrag nicht gefunden oder gehört zu einem anderen Server.")
            con.commit()
            return {"backup": backup}
        finally:
            con.close()

    try:
        result = await asyncio.to_thread(write)
        return web.json_response({"ok": True, **result})
    except (ValueError, sqlite3.IntegrityError, sqlite3.Error) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


def register_workspace_editor_routes(app: web.Application) -> None:
    app.router.add_get("/workspace/manage", workspace_manage_page)
    app.router.add_get("/api/workspace/manage/resources", api_manage_resources)
    app.router.add_get("/api/workspace/manage/{resource}/meta", api_manage_meta)
    app.router.add_get("/api/workspace/manage/{resource}", api_manage_list)
    app.router.add_post("/api/workspace/manage/{resource}", api_manage_create)
    app.router.add_patch("/api/workspace/manage/{resource}/{rowid}", api_manage_update)
    app.router.add_delete("/api/workspace/manage/{resource}/{rowid}", api_manage_delete)
