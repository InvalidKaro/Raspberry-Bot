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


_OPS_DEBUG_INJECT = r"""
<style>
#ops-debug-stack{position:fixed;right:16px;top:76px;z-index:10000;width:min(520px,calc(100vw - 28px));display:grid;gap:10px;pointer-events:none}
.ops-debug-window{pointer-events:auto;background:rgba(37,15,20,.98);border:1px solid #8d3542;border-radius:14px;box-shadow:0 22px 60px rgba(0,0,0,.5);overflow:hidden;animation:opsDebugIn .16s ease-out}
.ops-debug-window .ops-debug-head{display:flex;gap:10px;align-items:center;padding:10px 12px;background:#35171d;border-bottom:1px solid #6f2e38}
.ops-debug-window .ops-debug-head strong{flex:1;font-size:12px;color:#ffd6da}
.ops-debug-window .ops-debug-head button{background:transparent;border:0;color:#ffb8bf;padding:2px 6px;font-size:17px}
.ops-debug-window .ops-debug-body{padding:10px 12px}
.ops-debug-window .ops-debug-body pre{margin:0;max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:#ffdfe2}
.ops-debug-window .ops-debug-meta{margin-top:8px;color:#d9979e;font-size:10px}
@keyframes opsDebugIn{from{opacity:0;transform:translateY(-8px) scale(.98)}to{opacity:1;transform:none}}
@media(max-width:720px){#ops-debug-stack{top:12px;right:12px;width:calc(100vw - 24px)}}
</style>
<div id="ops-debug-stack" aria-live="assertive"></div>
<script>
(()=>{
  const FIXED_GUILD='1162733312226361454';
  let seq=0;
  const stack=document.getElementById('ops-debug-stack');
  function safeText(value){
    if(value instanceof Error)return `${value.name}: ${value.message}\n${value.stack||''}`;
    if(typeof value==='string')return value;
    try{return JSON.stringify(value,null,2)}catch{return String(value)}
  }
  window.showOpsDebug=(title,details,meta='')=>{
    if(!stack)return;
    const box=document.createElement('div');
    box.className='ops-debug-window';
    const id=++seq;
    box.innerHTML=`<div class="ops-debug-head"><strong>Dashboard Pro Error #${id} · ${String(title||'Fehler')}</strong><button type="button" aria-label="Schließen">×</button></div><div class="ops-debug-body"><pre></pre><div class="ops-debug-meta"></div></div>`;
    box.querySelector('pre').textContent=safeText(details);
    box.querySelector('.ops-debug-meta').textContent=`Guild ${FIXED_GUILD}${meta?' · '+meta:''} · ${new Date().toLocaleTimeString()}`;
    box.querySelector('button').onclick=()=>box.remove();
    stack.prepend(box);
    while(stack.children.length>5)stack.lastElementChild.remove();
  };
  const rawFetch=window.fetch.bind(window);
  window.fetch=async function(input,init={}){
    const url=typeof input==='string'?input:(input&&input.url)||String(input);
    const method=(init&&init.method)||'GET';
    try{
      const response=await rawFetch(input,init);
      if(!response.ok){
        let body='';
        try{body=await response.clone().text()}catch{}
        showOpsDebug(`HTTP ${response.status}`,`${method} ${url}\n\n${body.slice(0,4000)||response.statusText||'Keine Response-Daten'}`,'HTTP');
      }
      return response;
    }catch(error){
      showOpsDebug('Fetch fehlgeschlagen',`${method} ${url}\n\n${safeText(error)}`,'Network');
      throw error;
    }
  };
  window.addEventListener('error',event=>showOpsDebug('JavaScript Error',event.error||`${event.message}\n${event.filename||''}:${event.lineno||0}:${event.colno||0}`,'JS'));
  window.addEventListener('unhandledrejection',event=>showOpsDebug('Unhandled Promise',event.reason||'Unbekannter Promise-Fehler','Promise'));
})();
</script>
"""


def _patch_ops_html(text: str) -> str:
    """Run Dashboard Pro exclusively against one guild and surface all failures."""

    fixed_select = (
        '<select id="guild">'
        '<option value="1162733312226361454">Server 1162733312226361454</option>'
        '</select>'
    )
    text = text.replace(
        '<select id="guild"><option value="">Server wählen …</option></select>',
        fixed_select,
        1,
    )

    original_bootstrap = (
        "async function bootstrap(){const b=await api('/api/bootstrap');csrf=b.csrf;"
        "const g=await api('/api/discord/guilds');const sel=$('#guild');"
        "for(const item of g.guilds||[]){const o=document.createElement('option');"
        "o.value=item.id;o.textContent=item.name;sel.appendChild(o)}"
        "if((g.guilds||[]).length===1){sel.value=g.guilds[0].id;guildId=sel.value;await onGuild()}"
        "openTab(location.hash.slice(1)||'overview');setupQuick();setupPreview()}"
    )
    fixed_bootstrap = (
        "async function bootstrap(){const b=await api('/api/bootstrap');csrf=b.csrf;"
        "const sel=$('#guild');guildId='1162733312226361454';sel.value=guildId;"
        "openTab(location.hash.slice(1)||'overview');setupQuick();setupPreview();"
        "onGuild().catch(e=>{if(window.showOpsDebug)showOpsDebug('Guild Bootstrap',e,'Bootstrap');note(e.message,false)})}"
    )
    text = text.replace(original_bootstrap, fixed_bootstrap, 1)

    text = text.replace(
        "$('#guild').onchange=async()=>{guildId=$('#guild').value;await onGuild()};",
        "$('#guild').onchange=async()=>{guildId='1162733312226361454';$('#guild').value=guildId;await onGuild()};",
        1,
    )
    text = text.replace(
        "async function onGuild(){if(!guildId)return;await loadDiscordResources();await loadOverview();if(currentTab!=='overview')lazyLoad(currentTab);startLive()}",
        "async function onGuild(){guildId='1162733312226361454';const sel=$('#guild');if(sel)sel.value=guildId;startLive();const jobs=[['Discord Ressourcen',loadDiscordResources()],['Overview',loadOverview()]];const results=await Promise.allSettled(jobs.map(x=>x[1]));results.forEach((r,i)=>{if(r.status==='rejected'&&window.showOpsDebug)showOpsDebug(jobs[i][0],r.reason,'Initial Load')});if(currentTab!=='overview')lazyLoad(currentTab)}",
        1,
    )
    text = text.replace(
        "function note(text,ok=true){const n=$('#notice');n.textContent=text;n.className='notice '+(ok?'':'bad')+' show';clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),4500)}",
        "function note(text,ok=true){const n=$('#notice');n.textContent=text;n.className='notice '+(ok?'':'bad')+' show';clearTimeout(n._t);n._t=setTimeout(()=>n.classList.remove('show'),4500);if(!ok&&window.showOpsDebug)window.showOpsDebug('Dashboard Meldung',text,'UI')}",
        1,
    )
    text = text.replace(
        "bootstrap().catch(e=>note(e.message,false));",
        "bootstrap().catch(e=>{if(window.showOpsDebug)window.showOpsDebug('Bootstrap fehlgeschlagen',e,'Bootstrap');note(e.message,false)});",
        1,
    )

    # Install the fetch/error instrumentation before Dashboard Pro's own script
    # executes, otherwise a bootstrap failure can happen before the popup exists.
    text = text.replace("<body>", "<body>" + _OPS_DEBUG_INJECT, 1)
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
        return app

    _app_legacy.create_app = _create_app_with_workspace
    _app_legacy._workspace_suite_wrapped = True
