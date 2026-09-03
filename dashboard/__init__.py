"""Dashboard package bootstrap.

The legacy dashboard owns authentication/middleware and the main aiohttp app.
Workspace Suite, Media Hub and Dashboard Pro extend that app here so
``dashboard/app.py`` can stay focused on the existing Control Center while the
project remains modular.
"""

from __future__ import annotations

from aiohttp import web

from . import app_legacy as _app_legacy
from .media_routes import register_media_routes
from .ops_routes import register_ops_routes
from .workspace_editor_routes import register_workspace_editor_routes
from .workspace_plus_routes import register_workspace_plus_routes
from .workspace_routes import register_workspace_routes


OPS_GUILD_ID = "1162733312226361454"


_HOME_NAV_INJECT = r"""
<style>
.homepi-nav-hub{position:relative;display:inline-flex}.homepi-nav-menu{position:absolute;right:0;top:calc(100% + 9px);z-index:1000;width:270px;padding:8px;background:#11151c;border:1px solid #252c38;border-radius:13px;box-shadow:0 18px 45px rgba(0,0,0,.42);display:none}.homepi-nav-hub.open .homepi-nav-menu{display:grid;gap:5px}.homepi-nav-menu a{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:40px;padding:8px 10px;border-radius:9px;color:#f4f6fa;text-decoration:none;font-size:13px}.homepi-nav-menu a:hover{background:#181324;color:#e4d8ff}.homepi-nav-menu small{color:#8e99aa;font-size:10px}.homepi-nav-title{padding:6px 10px 4px;color:#8e99aa;font-size:10px;letter-spacing:.14em;font-weight:800}.homepi-nav-launch{background:#8b5cf6;border-color:#8b5cf6;color:white}.homepi-nav-launch:hover{background:#6d43d5}.homepi-nav-menu a.pro{background:linear-gradient(90deg,rgba(139,92,246,.16),rgba(53,194,255,.08));border:1px solid #40345c}@media(max-width:620px){.homepi-nav-hub{flex:1}.homepi-nav-launch{width:100%}.homepi-nav-menu{position:fixed;left:13px;right:13px;top:auto;bottom:13px;width:auto}}
</style>
<script>
(()=>{
  const top=document.querySelector('.top-actions');
  if(!top||document.getElementById('homepi-nav-hub'))return;
  const hub=document.createElement('div');
  hub.id='homepi-nav-hub';hub.className='homepi-nav-hub';
  hub.innerHTML=`<button type="button" class="homepi-nav-launch" aria-expanded="false">Navigation ▾</button><div class="homepi-nav-menu" role="menu"><div class="homepi-nav-title">HOMEPI PAGES</div><a href="/">Dashboard <small>:8080</small></a><a class="pro" href="/ops">Dashboard Pro <small>Operations</small></a><a href="/now-playing">Now Playing <small>Fullscreen</small></a><a href="/control">Control Center <small>System</small></a><a href="/media">Media Hub <small>Voice · Radio</small></a><a href="/workspace">Workspace <small>Tools</small></a><a href="/workspace/manage">Data Manager <small>CRUD</small></a><a href="/workspace/studio">Workspace Studio <small>Search · Embeds</small></a><a href="/database-admin">Database Admin <small>SQLite</small></a><a href="/status" target="_blank">Public Status <small>Sanitized</small></a></div>`;
  const refresh=document.getElementById('refresh-button');top.insertBefore(hub,refresh||null);
  const button=hub.querySelector('.homepi-nav-launch');
  button.addEventListener('click',event=>{event.stopPropagation();const open=hub.classList.toggle('open');button.setAttribute('aria-expanded',String(open))});
  document.addEventListener('click',event=>{if(!hub.contains(event.target)){hub.classList.remove('open');button.setAttribute('aria-expanded','false')}});
})();
</script>
"""


_OPS_DEBUG_INJECT = r"""
<style>
#ops-debug-stack{position:fixed;right:16px;top:76px;z-index:10000;width:min(520px,calc(100vw - 28px));display:grid;gap:10px;pointer-events:none}.ops-debug-window{pointer-events:auto;background:rgba(37,15,20,.98);border:1px solid #8d3542;border-radius:14px;box-shadow:0 22px 60px rgba(0,0,0,.5);overflow:hidden}.ops-debug-window .ops-debug-head{display:flex;gap:10px;align-items:center;padding:10px 12px;background:#35171d;border-bottom:1px solid #6f2e38}.ops-debug-window .ops-debug-head strong{flex:1;font-size:12px;color:#ffd6da}.ops-debug-window .ops-debug-head button{background:transparent;border:0;color:#ffb8bf;padding:2px 6px;font-size:17px}.ops-debug-window .ops-debug-body{padding:10px 12px}.ops-debug-window .ops-debug-body pre{margin:0;max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:#ffdfe2}.ops-debug-window .ops-debug-meta{margin-top:8px;color:#d9979e;font-size:10px}@media(max-width:720px){#ops-debug-stack{top:12px;right:12px;width:calc(100vw - 24px)}}
</style>
<div id="ops-debug-stack" aria-live="assertive"></div>
<script>
(function(){
  var fixedGuild='1162733312226361454';
  var seq=0;
  var stack=document.getElementById('ops-debug-stack');
  function safe(value){
    if(value&&value.stack)return String(value.name||'Error')+': '+String(value.message||value)+'\n'+String(value.stack);
    if(typeof value==='string')return value;
    try{return JSON.stringify(value,null,2)}catch(ignore){return String(value)}
  }
  window.showOpsDebug=function(title,details,meta){
    if(!stack)return;
    var box=document.createElement('div');
    box.className='ops-debug-window';
    seq+=1;
    box.innerHTML='<div class="ops-debug-head"><strong></strong><button type="button" aria-label="Schließen">×</button></div><div class="ops-debug-body"><pre></pre><div class="ops-debug-meta"></div></div>';
    box.querySelector('strong').textContent='Dashboard Pro Error #'+seq+' · '+String(title||'Fehler');
    box.querySelector('pre').textContent=safe(details);
    box.querySelector('.ops-debug-meta').textContent='Guild '+fixedGuild+(meta?' · '+meta:'')+' · '+new Date().toLocaleTimeString();
    box.querySelector('button').onclick=function(){box.remove()};
    stack.insertBefore(box,stack.firstChild);
    while(stack.children.length>5)stack.removeChild(stack.lastElementChild);
  };
  window.addEventListener('error',function(event){
    var where=(event.filename||'')+':'+(event.lineno||0)+':'+(event.colno||0);
    window.showOpsDebug('JavaScript Error',(event.error||event.message||'Unbekannter Fehler')+'\n'+where,'JS');
  });
  window.addEventListener('unhandledrejection',function(event){window.showOpsDebug('Unhandled Promise',event.reason||'Unbekannter Promise-Fehler','Promise')});
})();
</script>
"""


async def _ops_fixed_guild(_: web.Request) -> web.Response:
    """Return exactly one synthetic guild for Dashboard Pro bootstrap.

    No Discord request happens here. Real resource calls only start after the
    Dashboard shell is visible, using the fixed guild ID.
    """

    return web.json_response(
        {
            "ok": True,
            "guilds": [
                {
                    "id": OPS_GUILD_ID,
                    "name": f"Server {OPS_GUILD_ID}",
                    "icon": None,
                    "owner_id": None,
                    "member_count": 0,
                    "presence_count": 0,
                    "description": None,
                    "features": [],
                }
            ],
            "fixed_guild": True,
        }
    )


def _patch_ops_html(text: str) -> str:
    """Use one fixed guild without rewriting Dashboard Pro functions."""

    # Only change the guild-list endpoint. This keeps Media/Now Playing on the
    # normal multi-guild endpoint and avoids the expensive full guild scan here.
    text = text.replace(
        "api('/api/discord/guilds')",
        "api('/api/ops/fixed-guild')",
        1,
    )

    # The original bootstrap waited for channels + overview before rendering the
    # selected tab. Start that load in the background so the UI appears at once.
    text = text.replace("await onGuild()", "onGuild()", 1)

    # Debug listener is synchronous ES5-style code and therefore cannot create
    # another Safari async-parser failure itself.
    text = text.replace("<body>", "<body>" + _OPS_DEBUG_INJECT, 1)
    return text


@web.middleware
async def _security_headers_with_workspace(request: web.Request, handler):
    response = await handler(request)
    allow_inline = request.path in {"/","/workspace","/workspace/studio","/workspace/manage","/media","/ops","/now-playing","/status"}
    if request.path == "/ops" and response.content_type == "text/html" and response.text:
        response.text = _patch_ops_html(response.text)
    style_src = "style-src 'self' 'unsafe-inline'" if allow_inline else "style-src 'self'"
    script_src = "script-src 'self' 'unsafe-inline'" if allow_inline else "script-src 'self'"
    response.headers.update({"X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY","Referrer-Policy":"no-referrer","Permissions-Policy":"camera=(), microphone=(), geolocation=(), payment=(), usb=()","Content-Security-Policy":f"default-src 'self'; {style_src}; {script_src}; img-src 'self' data: http: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'","Cache-Control":"no-store"})
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
        if request.path in {"/status", "/api/public/status"}:
            return await handler(request)
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
        app.router.add_get("/api/ops/fixed-guild", _ops_fixed_guild)
        return app

    _app_legacy.create_app = _create_app_with_workspace
    _app_legacy._workspace_suite_wrapped = True
