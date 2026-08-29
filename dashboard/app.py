from __future__ import annotations

import hashlib
import hmac
import mimetypes
from pathlib import Path

from aiohttp import web

from .config import DashboardConfig
from .services.config_service import BotConfigService
from .services.deploy_service import DeployService
from .services.discord_service import DiscordService, DiscordServiceError
from .services.editor_service import EditorError, EditorService
from .services.git_service import GitService
from .services.system_service import bot_action, bot_logs, get_status

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


@web.middleware
async def security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
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
    return web.json_response({"ok": True, "version": 2})


async def login_page(request: web.Request) -> web.Response:
    if _authenticated(request):
        raise web.HTTPFound("/")
    return web.Response(text=(TEMPLATE_DIR / "login.html").read_text(encoding="utf-8"), content_type="text/html")


async def login(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    data = await request.post()
    if not hmac.compare_digest(str(data.get("token", "")), config.dashboard_token):
        html = (TEMPLATE_DIR / "login.html").read_text(encoding="utf-8").replace(
            "<!--ERROR-->", '<p class="login-error">Wrong dashboard password.</p>'
        )
        return web.Response(text=html, content_type="text/html", status=401)
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


async def api_bootstrap(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    return web.json_response({"ok": True, "csrf": _csrf_value(config), "bot_service": config.bot_service, "version": 2})


async def api_status(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    system = await get_status(config.bot_service)
    git = await request.app["git"].status()
    return web.json_response({"ok": True, "system": system, "git": git})


async def api_logs(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    result = await bot_logs(config.bot_service, config.log_lines)
    return web.json_response(result, status=200 if result["ok"] else 500)


async def api_bot_action(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    result = await bot_action(config.bot_service, request.match_info["action"])
    return web.json_response(result, status=200 if result["ok"] else 500)


async def api_git_status(request: web.Request) -> web.Response:
    return web.json_response(await request.app["git"].status())


async def api_git_diff(request: web.Request) -> web.Response:
    return web.json_response(await request.app["git"].diff())


async def api_git_action(request: web.Request) -> web.Response:
    git: GitService = request.app["git"]
    action = request.match_info["action"]
    if action == "pull":
        result = await git.pull()
    elif action == "push":
        result = await git.push()
    else:
        return web.json_response({"ok": False, "message": "Unsupported Git action."}, status=400)
    return web.json_response(result, status=200 if result["ok"] else 409)


async def api_git_commit(request: web.Request) -> web.Response:
    data = await request.json()
    result = await request.app["git"].commit(str(data.get("message", "")), list(data.get("paths", [])))
    return web.json_response(result, status=200 if result["ok"] else 409)


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
    return web.json_response(result, status=200 if result["ok"] else 422)


async def api_editor_save(request: web.Request) -> web.Response:
    data = await request.json()
    try:
        result = request.app["editor"].save(str(data.get("path", "")), str(data.get("content", "")))
        return web.json_response(result, status=200 if result["ok"] else 422)
    except EditorError as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_discord_guilds(request: web.Request) -> web.Response:
    try:
        guild_ids = await request.app["bot_config"].list_guild_ids()
        guilds = await request.app["discord"].guilds(guild_ids)
        return web.json_response({"ok": True, "guilds": guilds})
    except (DiscordServiceError, OSError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=502)


async def api_discord_resources(request: web.Request) -> web.Response:
    try:
        guild_id = int(request.match_info["guild_id"])
        channels, roles = await __import__('asyncio').gather(
            request.app["discord"].channels(guild_id),
            request.app["discord"].roles(guild_id),
        )
        return web.json_response({"ok": True, "channels": channels, "roles": roles})
    except (ValueError, DiscordServiceError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_bot_config_get(request: web.Request) -> web.Response:
    try:
        guild_id = int(request.match_info["guild_id"])
        return web.json_response({"ok": True, "config": await request.app["bot_config"].get(guild_id)})
    except (ValueError, OSError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_bot_config_save(request: web.Request) -> web.Response:
    try:
        guild_id = int(request.match_info["guild_id"])
        data = await request.json()
        updated = await request.app["bot_config"].update(guild_id, data)
        restart = await bot_action(request.app["config"].bot_service, "restart")
        ok = bool(restart["ok"])
        return web.json_response({"ok": ok, "config": updated, "message": "Configuration saved and bot restarted." if ok else "Configuration saved, but bot restart failed: " + restart["message"]}, status=200 if ok else 500)
    except (ValueError, OSError) as exc:
        return web.json_response({"ok": False, "message": str(exc)}, status=400)


async def api_deploy(request: web.Request) -> web.Response:
    result = await request.app["deploy"].deploy()
    return web.json_response(result, status=200 if result["ok"] else 409)


async def api_rollback(request: web.Request) -> web.Response:
    result = await request.app["deploy"].rollback()
    return web.json_response(result, status=200 if result["ok"] else 409)


def create_app(config: DashboardConfig | None = None) -> web.Application:
    config = config or DashboardConfig.from_env()
    app = web.Application(middlewares=[security_headers, auth_middleware], client_max_size=1024 * 1024)
    app["config"] = config
    app["git"] = GitService(config.repo_path)
    app["editor"] = EditorService(config.repo_path)
    app["discord"] = DiscordService(config.bot_env_path)
    app["bot_config"] = BotConfigService(config.database_path)
    app["deploy"] = DeployService(config.repo_path, config.bot_service)

    app.router.add_get("/health", health)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_post("/logout", logout)
    app.router.add_get("/", index)
    app.router.add_get("/static/{name}", static_file)

    app.router.add_get("/api/bootstrap", api_bootstrap)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_post("/api/bot/{action}", api_bot_action)
    app.router.add_get("/api/git/status", api_git_status)
    app.router.add_get("/api/git/diff", api_git_diff)
    app.router.add_post("/api/git/commit", api_git_commit)
    app.router.add_post("/api/git/{action}", api_git_action)
    app.router.add_get("/api/editor/files", api_editor_files)
    app.router.add_get("/api/editor/read", api_editor_read)
    app.router.add_post("/api/editor/validate", api_editor_validate)
    app.router.add_post("/api/editor/save", api_editor_save)
    app.router.add_get("/api/discord/guilds", api_discord_guilds)
    app.router.add_get("/api/discord/guilds/{guild_id}", api_discord_resources)
    app.router.add_get("/api/config/{guild_id}", api_bot_config_get)
    app.router.add_post("/api/config/{guild_id}", api_bot_config_save)
    app.router.add_post("/api/deploy", api_deploy)
    app.router.add_post("/api/rollback", api_rollback)
    return app


def main() -> None:
    config = DashboardConfig.from_env()
    web.run_app(create_app(config), host=config.host, port=config.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
