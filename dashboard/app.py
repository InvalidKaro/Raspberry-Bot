from __future__ import annotations

import hashlib
import hmac
import mimetypes
from pathlib import Path

from aiohttp import web

from .config import DashboardConfig
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
    return bool(actual) and hmac.compare_digest(actual, _session_value(config))


@web.middleware
async def security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    return response


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in {"/login", "/health", "/static/style.css", "/static/app.js"}:
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
    return web.json_response({"ok": True})


async def login_page(request: web.Request) -> web.Response:
    if _authenticated(request):
        raise web.HTTPFound("/")
    return web.Response(text=(TEMPLATE_DIR / "login.html").read_text(), content_type="text/html")


async def login(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    data = await request.post()
    token = str(data.get("token", ""))
    if not hmac.compare_digest(token, config.dashboard_token):
        html = (TEMPLATE_DIR / "login.html").read_text().replace(
            "<!--ERROR-->", '<p class="login-error">Wrong dashboard password.</p>'
        )
        return web.Response(text=html, content_type="text/html", status=401)
    response = web.HTTPFound("/")
    response.set_cookie("dashboard_session", _session_value(config), httponly=True, samesite="Strict", max_age=43200, path="/")
    return response


async def logout(_: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie("dashboard_session", path="/")
    return response


async def index(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    html = (TEMPLATE_DIR / "index.html").read_text()
    html = html.replace("__DASHBOARD_CSRF__", _csrf_value(config))
    html = html.replace("__BOT_SERVICE__", config.bot_service)
    return web.Response(text=html, content_type="text/html")


async def static_file(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if "/" in name or "\\" in name or name.startswith("."):
        raise web.HTTPNotFound()
    path = STATIC_DIR / name
    if not path.is_file():
        raise web.HTTPNotFound()
    content_type, _ = mimetypes.guess_type(path.name)
    return web.FileResponse(path, headers={"Content-Type": content_type or "application/octet-stream"})


async def api_status(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    system = await get_status(config.bot_service)
    git = await request.app["git"].status()
    return web.json_response({"ok": True, "system": system, "git": git})


async def api_bot_action(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    result = await bot_action(config.bot_service, request.match_info["action"])
    return web.json_response(result, status=200 if result["ok"] else 500)


async def api_logs(request: web.Request) -> web.Response:
    config: DashboardConfig = request.app["config"]
    result = await bot_logs(config.bot_service, config.log_lines)
    return web.json_response(result, status=200 if result["ok"] else 500)


async def api_git_action(request: web.Request) -> web.Response:
    git: GitService = request.app["git"]
    action = request.match_info["action"]
    if action == "pull":
        result = await git.pull()
    elif action == "push":
        result = await git.push()
    else:
        return web.json_response({"ok": False, "message": "Unsupported Git action."}, status=400)
    return web.json_response(result, status=200 if result["ok"] else 500)


def create_app(config: DashboardConfig | None = None) -> web.Application:
    config = config or DashboardConfig.from_env()
    app = web.Application(middlewares=[security_headers, auth_middleware], client_max_size=1024 * 1024)
    app["config"] = config
    app["git"] = GitService(config.repo_path)
    app.router.add_get("/health", health)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_post("/logout", logout)
    app.router.add_get("/", index)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_post("/api/bot/{action}", api_bot_action)
    app.router.add_post("/api/git/{action}", api_git_action)
    return app


def main() -> None:
    config = DashboardConfig.from_env()
    web.run_app(create_app(config), host=config.host, port=config.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
