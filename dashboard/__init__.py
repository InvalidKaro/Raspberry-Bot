"""Dashboard package bootstrap.

The legacy dashboard owns authentication/middleware and the main aiohttp app.
Workspace Suite, Media Hub and Dashboard Pro extend that app here so
``dashboard/app.py`` can stay focused on the existing Control Center while the
project remains modular.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

from aiohttp import web

from . import app_legacy as _app_legacy
from .media_routes import register_media_routes
from .ops_routes import register_ops_routes
from .services.discord_service import DiscordServiceError
from .workspace_editor_routes import register_workspace_editor_routes
from .workspace_plus_routes import register_workspace_plus_routes
from .workspace_routes import register_workspace_routes


_HOME_NAV_INJECT = r"""
<style>
.homepi-nav-hub{position:relative;display:inline-flex}.homepi-nav-menu{position:absolute;right:0;top:calc(100% + 9px);z-index:1000;width:270px;padding:8px;background:#11151c;border:1px solid #252c38;border-radius:13px;box-shadow:0 18px 45px rgba(0,0,0,.42);display:none}.homepi-nav-hub.open .homepi-nav-menu{display:grid;gap:5px}.homepi-nav-menu a{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:40px;padding:8px 10px;border-radius:9px;color:#f4f6fa;text-decoration:none;font-size:13px}.homepi-nav-menu a:hover{background:#181324;color:#e4d8ff}.homepi-nav-menu small{color:#8e99aa;font-size:10px}.homepi-nav-title{padding:6px 10px 4px;color:#8e99aa;font-size:10px;letter-spacing:.14em;font-weight:800}.homepi-nav-launch{background:#8b5cf6;border-color:#8b5cf6;color:white}.homepi-nav-launch:hover{background:#6d43d5}.homepi-nav-menu a.pro{background:linear-gradient(90deg,rgba(139,92,246,.16),rgba(53,194,255,.08));border:1px solid #40345c}@media(max-width:620px){.homepi-nav-hub{flex:1}.homepi-nav-launch{width:100%}.homepi-nav-menu{position:fixed;left:13px;right:13px;top:auto;bottom:13px;width:auto}}
</style>
<script>
(()=>{
  const top=document.querySelector('.top-actions');
  if(!top||document.getElementById('homepi-nav-hub'))return;
  const hub=document.createElement('div');
  hub.id='homepi-nav-hub';
  hub.className='homepi-nav-hub';
  hub.innerHTML=`<button type="button" class="homepi-nav-launch" aria-expanded="false">Navigation ▾</button>
    <div class="homepi-nav-menu" role="menu">
      <div class="homepi-nav-title">HOMEPI PAGES</div>
      <a href="/">Dashboard <small>:8080</small></a>
      <a class="pro" href="/ops">Dashboard Pro <small>Operations</small></a>
      <a href="/now-playing">Now Playing <small>Fullscreen</small></a>
      <a href="/control">Control Center <small>System</small></a>
      <a href="/media">Media Hub <small>Voice · Radio</small></a>
      <a href="/workspace">Workspace <small>Tools</small></a>
      <a href="/workspace/manage">Data Manager <small>CRUD</small></a>
      <a href="/workspace/studio">Workspace Studio <small>Search · Embeds</small></a>
      <a href="/database-admin">Database Admin <small>SQLite</small></a>
      <a href="/status" target="_blank">Public Status <small>Sanitized</small></a>
    </div>`;
  const refresh=document.getElementById('refresh-button');
  top.insertBefore(hub,refresh||null);
  const button=hub.querySelector('.homepi-nav-launch');
  button.addEventListener('click',event=>{
    event.stopPropagation();
    const open=hub.classList.toggle('open');
    button.setAttribute('aria-expanded',String(open));
  });
  document.addEventListener('click',event=>{
    if(!hub.contains(event.target)){
      hub.classList.remove('open');
      button.setAttribute('aria-expanded','false');
    }
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'){
      hub.classList.remove('open');
      button.setAttribute('aria-expanded','false');
    }
  });
})();
</script>
"""


def _dashboard_db_path(config) -> Path:
    path = Path(config.database_path)
    return path if path.is_absolute() else Path(config.repo_path) / path


def _select_ops_guild_id(config) -> int | None:
    """Pick one Dashboard Pro guild without asking Discord for every guild.

    An explicit DASHBOARD_PRO_GUILD_ID wins. Otherwise prefer the guild that
    currently has an active voice/YouTube session, then the most recently active
    telemetry guild. This keeps Dashboard Pro deliberately single-guild for now.
    """

    explicit = os.getenv("DASHBOARD_PRO_GUILD_ID", "").strip()
    if explicit.isdigit() and int(explicit) > 0:
        return int(explicit)

    try:
        con = sqlite3.connect(_dashboard_db_path(config), timeout=1.0)
    except sqlite3.Error:
        return None

    try:
        queries = [
            """
            SELECT r.guild_id
            FROM dashboard_runtime_state r
            WHERE r.guild_id IS NOT NULL
            ORDER BY
              CASE
                WHEN r.state_json LIKE '%\"connected\":true%'
                  OR r.state_json LIKE '%\"active\":true%'
                THEN 0 ELSE 1
              END,
              COALESCE(
                (SELECT MAX(a.id) FROM dashboard_activity a WHERE a.guild_id = r.guild_id),
                0
              ) DESC,
              r.updated_at DESC
            LIMIT 1
            """,
            """
            SELECT guild_id
            FROM dashboard_activity
            WHERE guild_id IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            """
            SELECT guild_id
            FROM guild_settings
            WHERE guild_id IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 1
            """,
        ]
        for query in queries:
            try:
                row = con.execute(query).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0] is not None:
                value = int(row[0])
                if value > 0:
                    return value
    finally:
        con.close()
    return None


async def _ops_single_guild_response(request: web.Request) -> web.Response:
    """Fast single-guild bootstrap used only by Dashboard Pro."""

    guild_id = await asyncio.to_thread(_select_ops_guild_id, request.app["config"])
    if guild_id is None:
        return web.json_response(
            {
                "ok": True,
                "guilds": [],
                "single_guild": True,
                "message": "No Dashboard Pro guild is available yet.",
            }
        )

    # One direct Discord lookup gives the proper name/counts. It is optional:
    # Dashboard Pro still opens from SQLite immediately if Discord is slow.
    try:
        guild = await asyncio.wait_for(request.app["discord"].guild(guild_id), timeout=2.5)
    except (DiscordServiceError, asyncio.TimeoutError, OSError):
        guild = {
            "id": str(guild_id),
            "name": f"Server {guild_id}",
            "icon": None,
            "owner_id": "",
            "member_count": 0,
            "presence_count": 0,
            "description": None,
            "features": [],
        }

    return web.json_response({"ok": True, "guilds": [guild], "single_guild": True})


def _patch_ops_html(text: str) -> str:
    """Make /ops single-guild and non-blocking without changing other pages."""

    text = text.replace(
        "api('/api/discord/guilds')",
        "api('/api/discord/guilds?ops_single=1')",
        1,
    )

    # Dashboard Pro used to wait for channel/role/overview REST calls before it
    # even rendered the first tab. Start those in the background instead.
    text = text.replace(
        "if((g.guilds||[]).length===1){sel.value=g.guilds[0].id;guildId=sel.value;await onGuild()}openTab",
        "if((g.guilds||[]).length===1){sel.value=g.guilds[0].id;guildId=sel.value;onGuild().catch(e=>note(e.message,false))}openTab",
        1,
    )
    text = text.replace(
        "async function onGuild(){if(!guildId)return;await loadDiscordResources();await loadOverview();if(currentTab!=='overview')lazyLoad(currentTab);startLive()}",
        "async function onGuild(){if(!guildId)return;startLive();await Promise.allSettled([loadDiscordResources(),loadOverview()]);if(currentTab!=='overview')lazyLoad(currentTab)}",
        1,
    )
    return text


@web.middleware
async def _security_headers_with_workspace(request: web.Request, handler):
    response = await handler(request)
    allow_inline = request.path in {
        "/",
        "/workspace",
        "/workspace/studio",
        "/workspace/manage",
        "/media",
        "/ops",
        "/now-playing",
        "/status",
    }
    if request.path == "/ops" and response.content_type == "text/html" and response.text:
        response.text = _patch_ops_html(response.text)

    style_src = "style-src 'self' 'unsafe-inline'" if allow_inline else "style-src 'self'"
    script_src = "script-src 'self' 'unsafe-inline'" if allow_inline else "script-src 'self'"
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            "Content-Security-Policy": (
                f"default-src 'self'; {style_src}; {script_src}; img-src 'self' data: http: https:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            ),
            "Cache-Control": "no-store",
        }
    )
    return response


if not getattr(_app_legacy, "_workspace_suite_wrapped", False):
    _original_create_app = _app_legacy.create_app
    _original_index = _app_legacy.index
    _original_auth_middleware = _app_legacy.auth_middleware

    async def _index_with_navigation(request):
        response = await _original_index(request)
        if response.content_type == "text/html" and response.text:
            response.text = response.text.replace("</body>", _HOME_NAV_INJECT + "</body>")
        return response

    @web.middleware
    async def _auth_with_public_status(request: web.Request, handler):
        # Only this deliberately reduced status surface is public. Every other
        # Dashboard Pro API still inherits the dashboard session + CSRF policy.
        if request.path in {"/status", "/api/public/status"}:
            return await handler(request)

        if request.path == "/api/discord/guilds" and request.query.get("ops_single") == "1":
            async def single_guild_handler(inner_request: web.Request):
                return await _ops_single_guild_response(inner_request)

            return await _original_auth_middleware(request, single_guild_handler)

        return await _original_auth_middleware(request, handler)

    _app_legacy.security_headers = _security_headers_with_workspace
    _app_legacy.auth_middleware = _auth_with_public_status
    _app_legacy.index = _index_with_navigation

    def _create_app_with_workspace(config=None):
        app = _original_create_app(config)
        register_workspace_routes(app)
        register_workspace_plus_routes(app)
        register_workspace_editor_routes(app)
        register_media_routes(app)
        register_ops_routes(app)
        return app

    _app_legacy.create_app = _create_app_with_workspace
    _app_legacy._workspace_suite_wrapped = True
