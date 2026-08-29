from __future__ import annotations

import hashlib
import hmac
import mimetypes
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web

from .config import DashboardConfig
from .services.audit_service import AuditService
from .services.backup_service import BackupService
from .services.config_service import BotConfigService
from .services.data_service import BotDataService
from .services.deploy_service import DeployService
from .services.discord_service import DiscordService, DiscordServiceError
from .services.editor_service import EditorError, EditorService
from .services.git_service import GitService
from .services.project_service import ProjectService
from .services.system_service import bot_action, bot_logs, get_status, service_logs, system_power_action

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"


def _signature(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _session_value(config: DashboardConfig) -> str:
    return _signature(config.dashboard_secret, f"session:{config.dashboard_token}")


def _csrf_value(config: DashboardConfig) -> str:
    return _signature(config.dashboard_secret, f"csrf:{config.dashboard_token}")


def _authenticated(request: web.Request) -> bool:
    config: DashboardConfig = request.app["config"]
    actual = request.cookies.get("dashboard_session", "")
    expected = _session_value(config)
    return bool(actual) and hmac.compare_digest(actual, expected)


class LoginLimiter:
    def __init__(self) -> None:
        self.failures: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=12))

    def _key(self, request: web.Request) -> str:
        peer = request.transport.get_extra_info("peername") if request.transport else None
        return str(peer[0] if isinstance(peer, tuple) and peer else request.remote or "unknown")

    def allowed(self, request: web.Request) -> bool:
        key = self._key(request)
        now = time.monotonic()
        rows = self.failures[key]
        while rows and now - rows[0] > 600:
            rows.popleft()
        return len(rows) < 6

    def fail(self, request: web.Request) -> None:
        self.failures[self._key(request)].append(time.monotonic())

    def clear(self, request: web.Request) -> None:
        self.failures.pop(self._key(request), None)


@web.middleware
async def security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        "Cache-Control": "no-store",
    })
    return response


@web.middleware
async def auth_middleware(request: web.Request, handler):
    public = {"/login", "/health", "/static/style.css", "/static/app.js"}
    if request.path in public:
        return await handler(request)
    if not _authenticated(request):
        if request.path.startswith("/api/"):
            return web.json_response({"ok": False, "message": "Authentication required."}, status=401)
        raise web.HTTPFound("/login")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        config: DashboardConfig = request.app["config"]
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, _csrf_value(config)):
            return web.json_response({"ok": False, "message": "Invalid CSRF token."}, status=403)
    return await handler(request)


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": 3})


async def login_page(request: web.Request) -> web.Response:
    if _authenticated(request):
        raise web.HTTPFound("/")
    return web.Response(text=(TEMPLATE_DIR / "login.html").read_text(encoding="utf-8"), content_type="text/html")


async def login(request: web.Request) -> web.Response:
    limiter: LoginLimiter = request.app["login_limiter"]
    if not limiter.allowed(request):
        return web.Response(text="Too many failed logins. Try again later.", status=429, content_type="text/plain")
    config: DashboardConfig = request.app["config"]
    data = await request.post()
    if not hmac.compare_digest(str(data.get("token", "")), config.dashboard_token):
        limiter.fail(request)
        html = (TEMPLATE_DIR / "login.html").read_text(encoding="utf-8").replace(
            "<!--ERROR-->", '<p class="login-error">Wrong dashboard password.</p>'
        )
        return web.Response(text=html, content_type="text/html", status=401)
    limiter.clear(request)
    response = web.HTTPFound("/")
    response.set_cookie("dashboard_session", _session_value(config), httponly=True, samesite="Strict", max_age=43200, path="/")
    return response


async def logout(_: web.Request) -> web.Response:
    response = web.json_response({"ok": True})
    response.del_cookie("dashboard_session", path="/")
    return response


async def index(_: web.Request) -> web.Response:
    return web.Response(text=(TEMPLATE_DIR / "index.html").read_text(encoding="utf-8"), content_type="text/html")


async def static_file(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if "/" in name or "\\" in name or name.startswith("."):
        raise web.HTTPNotFound()
    path = STATIC_DIR / name
    if not path.is_file():
        raise web.HTTPNotFound()
    content_type, _ = mimetypes.guess_type(path.name)
    return web.FileResponse(path, headers={"Content-Type": content_type or "application/octet-stream"})


def _json_result(result: dict, fail_status: int = 409) -> web.Response:
    return web.json_response(result, status=200 if result.get("ok") else fail_status)


def _audit(request: web.Request, action: str, result: dict) -> None:
    request.app["audit"].record(action, ok=bool(result.get("ok")), detail=str(result.get("message", "")))


async def api_bootstrap(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    return web.json_response({"ok": True, "csrf": _csrf_value(config), "bot_service": config.bot_service, "version": 3})


async def api_status(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    system = await get_status(config.bot_service)
    git = await request.app["git"].status()
    return web.json_response({"ok": True, "system": system, "git": git})


async def api_logs(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    return _json_result(await bot_logs(config.bot_service, config.log_lines), 500)


async def api_service_logs(request: web.Request) -> web.Response:
    return _json_result(await service_logs(request.match_info["service"], request.app["config"].log_lines), 400)


async def api_bot_action(request: web.Request) -> web.Response:
    result = await bot_action(request.app["config"].bot_service, request.match_info["action"])
    _audit(request, f"bot.{request.match_info['action']}", result)
    return _json_result(result, 500)


async def api_power(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    data = await request.json()
    expected = "REBOOT" if action == "reboot" else "SHUTDOWN" if action == "poweroff" else ""
    if str(data.get("confirm", "")) != expected:
        return web.json_response({"ok": False, "message": f"Type {expected or 'the required confirmation'} exactly."}, status=400)
    result = await system_power_action(action)
    _audit(request, f"system.{action}", result)
    return _json_result(result, 500)


async def api_git_status(request: web.Request) -> web.Response:
    return web.json_response(await request.app["git"].status())


async def api_git_diff(request: web.Request) -> web.Response:
    return web.json_response(await request.app["git"].diff(request.query.get("path") or None))


async def api_git_action(request: web.Request) -> web.Response:
    git: GitService = request.app["git"]
    action = request.match_info["action"]
    if action == "pull": result = await git.pull()
    elif action == "push": result = await git.push()
    else: return web.json_response({"ok": False, "message": "Unsupported Git action."}, status=400)
    _audit(request, f"git.{action}", result)
    return _json_result(result)


async def api_git_commit(request: web.Request) -> web.Response:
    data = await request.json()
    result = await request.app["git"].commit(str(data.get("message", "")), list(data.get("paths", [])) or None)
    _audit(request, "git.commit", result)
    return _json_result(result)


async def api_git_paths(request: web.Request) -> web.Response:
    data = await request.json()
    action = request.match_info["action"]
    git: GitService = request.app["git"]
    paths = list(data.get("paths", []))
    if action == "stage": result = await git.stage(paths)
    elif action == "unstage": result = await git.unstage(paths)
    elif action == "discard": result = await git.discard(paths)
    else: return web.json_response({"ok": False, "message": "Unsupported Git path action."}, status=400)
    _audit(request, f"git.{action}", result)
    return _json_result(result)


async def api_git_branches(request: web.Request) -> web.Response:
    return web.json_response(await request.app["git"].branches())


async def api_git_branch(request: web.Request) -> web.Response:
    data = await request.json()
    action = request.match_info["action"]
    if action == "create": result = await request.app["git"].create_branch(str(data.get("name", "")))
    elif action == "switch": result = await request.app["git"].switch_branch(str(data.get("name", "")))
    else: return web.json_response({"ok": False, "message": "Unsupported branch action."}, status=400)
    _audit(request, f"git.branch.{action}", result)
    return _json_result(result)


async def api_git_history(request: web.Request) -> web.Response:
    return web.json_response(await request.app["git"].history(40))


async def api_editor_files(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "files": request.app["editor"].list_files()})


async def api_editor_read(request: web.Request) -> web.Response:
    try:
        data = request.app["editor"].read(request.query.get("path", ""))
        return web.json_response({"ok": True, **data})
    except EditorError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_editor_validate(request: web.Request) -> web.Response:
    data = await request.json()
    result = request.app["editor"].validate(str(data.get("path", "")), str(data.get("content", "")))
    return _json_result(result, 422)


async def api_editor_save(request: web.Request) -> web.Response:
    data = await request.json()
    try:
        result = request.app["editor"].save(str(data.get("path", "")), str(data.get("content", "")))
    except EditorError as exc:
        result = {"ok": False, "message": str(exc)}
    _audit(request, "editor.save", result)
    return _json_result(result, 422 if not result.get("ok") else 200)


async def api_editor_create(request: web.Request) -> web.Response:
    data = await request.json()
    try:
        if data.get("kind") == "dir": result = request.app["editor"].create_directory(str(data.get("path", "")))
        else: result = request.app["editor"].create_file(str(data.get("path", "")), str(data.get("content", "")))
    except EditorError as exc:
        result = {"ok": False, "message": str(exc)}
    _audit(request, "editor.create", result)
    return _json_result(result, 400)


async def api_editor_rename(request: web.Request) -> web.Response:
    data = await request.json()
    try: result = request.app["editor"].rename(str(data.get("old_path", "")), str(data.get("new_path", "")))
    except EditorError as exc: result = {"ok": False, "message": str(exc)}
    _audit(request, "editor.rename", result)
    return _json_result(result, 400)


async def api_editor_delete(request: web.Request) -> web.Response:
    data = await request.json()
    try: result = request.app["editor"].delete(str(data.get("path", "")), recursive=bool(data.get("recursive", False)))
    except EditorError as exc: result = {"ok": False, "message": str(exc)}
    _audit(request, "editor.delete", result)
    return _json_result(result, 400)


async def api_editor_search(request: web.Request) -> web.Response:
    try: return web.json_response(request.app["editor"].search(request.query.get("q", "")))
    except EditorError as exc: return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_discord_guilds(request: web.Request) -> web.Response:
    try:
        guild_ids = await request.app["bot_config"].list_guild_ids()
        return web.json_response({"ok": True, "guilds": await request.app["discord"].guilds(guild_ids)})
    except (DiscordServiceError, OSError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=502)


async def api_discord_resources(request: web.Request) -> web.Response:
    import asyncio
    try:
        guild_id = int(request.match_info["guild_id"])
        channels, roles = await asyncio.gather(request.app["discord"].channels(guild_id), request.app["discord"].roles(guild_id))
        return web.json_response({"ok": True, "channels": channels, "roles": roles})
    except (ValueError, DiscordServiceError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_bot_config_get(request: web.Request) -> web.Response:
    try: return web.json_response({"ok": True, "config": await request.app["bot_config"].get(int(request.match_info["guild_id"]))})
    except (ValueError, OSError) as exc: return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_bot_config_save(request: web.Request) -> web.Response:
    try:
        guild_id = int(request.match_info["guild_id"])
        updated = await request.app["bot_config"].update(guild_id, await request.json())
        restart = await bot_action(request.app["config"].bot_service, "restart")
        ok = bool(restart["ok"])
        result = {"ok": ok, "config": updated, "message": "Configuration saved and bot restarted." if ok else "Configuration saved, but bot restart failed: " + restart["message"]}
    except (ValueError, OSError) as exc:
        result = {"ok": False, "message": str(exc)}
    _audit(request, "bot.config.save", result)
    return _json_result(result, 500)


async def api_deploy(request: web.Request) -> web.Response:
    result = await request.app["deploy"].deploy(); _audit(request, "deploy", result); return _json_result(result)


async def api_rollback(request: web.Request) -> web.Response:
    result = await request.app["deploy"].rollback(); _audit(request, "deploy.rollback", result); return _json_result(result)


async def api_requirements(request: web.Request) -> web.Response:
    result = await request.app["deploy"].install_requirements(); _audit(request, "requirements.install", result); return _json_result(result)


async def api_data(request: web.Request) -> web.Response:
    return web.json_response(await request.app["data"].overview())


async def api_metrics(request: web.Request) -> web.Response:
    try: hours = int(request.query.get("hours", "24"))
    except ValueError: hours = 24
    return web.json_response(await request.app["data"].metrics(hours))


async def api_project(request: web.Request) -> web.Response:
    return web.json_response(request.app["project"].overview())


async def api_backups(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "backups": request.app["backups"].list()})


async def api_backup_create(request: web.Request) -> web.Response:
    result = await request.app["backups"].create(); _audit(request, "backup.create", result); return _json_result(result)


async def api_backup_restore(request: web.Request) -> web.Response:
    data = await request.json()
    if str(data.get("confirm", "")) != "RESTORE":
        return web.json_response({"ok": False, "message": "Type RESTORE exactly."}, status=400)
    result = await request.app["backups"].restore(str(data.get("name", ""))); _audit(request, "backup.restore", result); return _json_result(result, 500)


async def api_backup_delete(request: web.Request) -> web.Response:
    data = await request.json(); result = request.app["backups"].delete(str(data.get("name", ""))); _audit(request, "backup.delete", result); return _json_result(result, 400)


async def api_backup_download(request: web.Request) -> web.StreamResponse:
    try: path = request.app["backups"].path(request.match_info["name"])
    except (ValueError, FileNotFoundError): raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


async def api_audit(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "events": request.app["audit"].recent(120)})


def create_app(config: DashboardConfig | None = None) -> web.Application:
    config = config or DashboardConfig.from_env()
    state_dir = Path.home() / ".local" / "state" / "homepi-dashboard"
    app = web.Application(middlewares=[security_headers, auth_middleware], client_max_size=2 * 1024 * 1024)
    app["config"] = config
    app["git"] = GitService(config.repo_path)
    app["editor"] = EditorService(config.repo_path)
    app["discord"] = DiscordService(config.bot_env_path)
    app["bot_config"] = BotConfigService(config.database_path)
    app["deploy"] = DeployService(config.repo_path, config.bot_service)
    app["data"] = BotDataService(config.database_path)
    app["project"] = ProjectService(config.repo_path)
    app["audit"] = AuditService(state_dir)
    app["backups"] = BackupService(config.database_path, config.bot_service, state_dir)
    app["login_limiter"] = LoginLimiter()

    app.router.add_get("/health", health)
    app.router.add_get("/login", login_page); app.router.add_post("/login", login); app.router.add_post("/logout", logout)
    app.router.add_get("/", index); app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/api/bootstrap", api_bootstrap); app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/logs", api_logs); app.router.add_get("/api/logs/{service}", api_service_logs)
    app.router.add_post("/api/bot/{action}", api_bot_action); app.router.add_post("/api/system/{action}", api_power)
    app.router.add_get("/api/git/status", api_git_status); app.router.add_get("/api/git/diff", api_git_diff)
    app.router.add_post("/api/git/commit", api_git_commit); app.router.add_post("/api/git/{action:pull|push}", api_git_action)
    app.router.add_post("/api/git/paths/{action:stage|unstage|discard}", api_git_paths)
    app.router.add_get("/api/git/branches", api_git_branches); app.router.add_post("/api/git/branch/{action:create|switch}", api_git_branch); app.router.add_get("/api/git/history", api_git_history)
    app.router.add_get("/api/editor/files", api_editor_files); app.router.add_get("/api/editor/read", api_editor_read); app.router.add_get("/api/editor/search", api_editor_search)
    app.router.add_post("/api/editor/validate", api_editor_validate); app.router.add_post("/api/editor/save", api_editor_save); app.router.add_post("/api/editor/create", api_editor_create); app.router.add_post("/api/editor/rename", api_editor_rename); app.router.add_post("/api/editor/delete", api_editor_delete)
    app.router.add_get("/api/discord/guilds", api_discord_guilds); app.router.add_get("/api/discord/guilds/{guild_id}", api_discord_resources)
    app.router.add_get("/api/config/{guild_id}", api_bot_config_get); app.router.add_post("/api/config/{guild_id}", api_bot_config_save)
    app.router.add_post("/api/deploy", api_deploy); app.router.add_post("/api/rollback", api_rollback); app.router.add_post("/api/requirements/install", api_requirements)
    app.router.add_get("/api/data", api_data); app.router.add_get("/api/data/metrics", api_metrics); app.router.add_get("/api/project", api_project)
    app.router.add_get("/api/backups", api_backups); app.router.add_post("/api/backups/create", api_backup_create); app.router.add_post("/api/backups/restore", api_backup_restore); app.router.add_post("/api/backups/delete", api_backup_delete); app.router.add_get("/api/backups/download/{name}", api_backup_download)
    app.router.add_get("/api/audit", api_audit)
    return app


def main() -> None:
    config = DashboardConfig.from_env()
    web.run_app(create_app(config), host=config.host, port=config.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
