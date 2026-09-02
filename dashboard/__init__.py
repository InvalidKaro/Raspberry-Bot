"""Dashboard package bootstrap.

The legacy dashboard owns authentication/middleware and the main aiohttp app.
Workspace Suite extends that app here so dashboard/app.py can stay focused on
the existing Control Center while the project remains modular.
"""

from aiohttp import web

from . import app_legacy as _app_legacy
from .workspace_plus_routes import register_workspace_plus_routes
from .workspace_routes import register_workspace_routes


_HOME_NAV_INJECT = r"""
<style>
.homepi-nav-hub{position:relative;display:inline-flex}.homepi-nav-menu{position:absolute;right:0;top:calc(100% + 9px);z-index:1000;width:240px;padding:8px;background:#11151c;border:1px solid #252c38;border-radius:13px;box-shadow:0 18px 45px rgba(0,0,0,.42);display:none}.homepi-nav-hub.open .homepi-nav-menu{display:grid;gap:5px}.homepi-nav-menu a{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:40px;padding:8px 10px;border-radius:9px;color:#f4f6fa;text-decoration:none;font-size:13px}.homepi-nav-menu a:hover{background:#181324;color:#e4d8ff}.homepi-nav-menu small{color:#8e99aa;font-size:10px}.homepi-nav-title{padding:6px 10px 4px;color:#8e99aa;font-size:10px;letter-spacing:.14em;font-weight:800}.homepi-nav-launch{background:#8b5cf6;border-color:#8b5cf6;color:white}.homepi-nav-launch:hover{background:#6d43d5}@media(max-width:620px){.homepi-nav-hub{flex:1}.homepi-nav-launch{width:100%}.homepi-nav-menu{position:fixed;left:13px;right:13px;top:auto;bottom:13px;width:auto}}
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
      <a href="/control">Control Center <small>System</small></a>
      <a href="/workspace">Workspace <small>Tools</small></a>
      <a href="/workspace/studio">Workspace Studio <small>Search · Embeds</small></a>
      <a href="/database-admin">Database Admin <small>SQLite</small></a>
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


@web.middleware
async def _security_headers_with_workspace(request: web.Request, handler):
    response = await handler(request)
    allow_inline = request.path in {"/", "/workspace", "/workspace/studio"}
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

    async def _index_with_navigation(request):
        response = await _original_index(request)
        if response.content_type == "text/html" and response.text:
            response.text = response.text.replace("</body>", _HOME_NAV_INJECT + "</body>")
        return response

    # The original dashboard CSP intentionally blocks inline script/style. The
    # Workspace templates currently use inline assets, and the navigation launcher
    # is injected inline on the home page. Scope the relaxed policy to only these
    # authenticated UI pages instead of weakening the whole dashboard.
    _app_legacy.security_headers = _security_headers_with_workspace
    _app_legacy.index = _index_with_navigation

    def _create_app_with_workspace(config=None):
        app = _original_create_app(config)
        register_workspace_routes(app)
        register_workspace_plus_routes(app)
        return app

    _app_legacy.create_app = _create_app_with_workspace
    _app_legacy._workspace_suite_wrapped = True
