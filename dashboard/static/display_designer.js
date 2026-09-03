(() => {
  'use strict';

  const GUILD_ID = '1162733312226361454';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const WIDGETS = [
    {key:'clock', label:'Clock', icon:'◷', desc:'Uhrzeit & Datum', wide:true},
    {key:'temperature', label:'Temperature', icon:'♨', desc:'CPU Temperatur'},
    {key:'ram', label:'Memory', icon:'▥', desc:'RAM Auslastung'},
    {key:'cpu', label:'CPU', icon:'⌁', desc:'CPU Last'},
    {key:'nowplaying', label:'Now Playing', icon:'♫', desc:'Radio / YouTube', wide:true},
    {key:'pihole', label:'Pi-hole', icon:'◉', desc:'DNS Blocking'},
    {key:'uptime', label:'Uptime', icon:'↟', desc:'Systemlaufzeit'},
    {key:'network', label:'Network', icon:'⌁', desc:'Netzwerkstatus'}
  ];

  const PROFILES = {
    oled096: {label:'0.96″ OLED', width:128, height:64, micro:true},
    tft097: {label:'0.96–0.97″ TFT', width:160, height:80, micro:true},
    oled13: {label:'1.3″ OLED', width:128, height:64, micro:true},
    tft114: {label:'1.14″ TFT', width:240, height:135, micro:true},
    tft28: {label:'2.8″ TFT', width:320, height:240},
    tft35: {label:'3.5″ TFT', width:480, height:320}
  };

  const DEFAULT_LAYOUT = {
    version: 3,
    profile: 'oled096',
    rotation: 0,
    refresh_seconds: 10,
    theme: 'obsidian',
    accent: '#7c5cff',
    density: 'compact',
    brightness: 85,
    widgets: ['clock','temperature','ram']
  };

  let layout = clone(DEFAULT_LAYOUT);
  let liveData = {summary:null, media:null};
  let mounted = false;
  let draggingKey = null;
  let clockTimer = null;
  let loadSerial = 0;

  function clone(value){return JSON.parse(JSON.stringify(value));}
  function esc(value){
    return String(value == null ? '' : value)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
  }
  function clamp(value,min,max,fallback){
    const n=Number(value);
    return Number.isFinite(n)?Math.max(min,Math.min(max,n)):fallback;
  }
  function fmtUptime(seconds){
    const s=Math.max(0,Number(seconds)||0), d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), m=Math.floor((s%3600)/60);
    if(d)return `${d}d ${h}h`;
    if(h)return `${h}h ${m}m`;
    return `${m}m`;
  }
  function safeColor(value){return /^#[0-9a-f]{6}$/i.test(String(value||''))?String(value):DEFAULT_LAYOUT.accent;}

  function normalize(raw){
    const next={...clone(DEFAULT_LAYOUT), ...(raw && typeof raw==='object'?raw:{})};
    if(!PROFILES[next.profile])next.profile=DEFAULT_LAYOUT.profile;
    next.rotation=[0,90,180,270].includes(Number(next.rotation))?Number(next.rotation):0;
    next.refresh_seconds=clamp(next.refresh_seconds,5,300,10);
    next.brightness=clamp(next.brightness,10,100,85);
    next.theme=['aurora','obsidian','cyber','minimal'].includes(next.theme)?next.theme:'obsidian';
    next.density=['compact','comfortable','spacious'].includes(next.density)?next.density:'compact';
    next.accent=safeColor(next.accent);
    const allowed=new Set(WIDGETS.map(x=>x.key));
    next.widgets=Array.isArray(next.widgets)?next.widgets.filter((x,i,a)=>allowed.has(x)&&a.indexOf(x)===i):clone(DEFAULT_LAYOUT.widgets);
    if(!next.widgets.length)next.widgets=['clock'];
    next.version=3;
    return next;
  }

  function mount(){
    const oldRotation=$('#displayRotation');
    if(!oldRotation)return false;
    const card=oldRotation.closest('.card');
    if(!card)return false;
    card.classList.remove('half');
    card.classList.add('full','ops-display-studio-card');
    const head=$('.head',card);
    if(head)head.innerHTML='<div><h2>Pi Micro Display Studio</h2><div class="tiny">0.96–0.97″ Layout Composer · OLED / TFT</div></div><div class="ops-display-head-actions"><span class="ops-display-save-state" id="displaySaveState">Local preview</span><button type="button" id="displayRefreshLive">↻ Live</button></div>';
    const body=$('.body',card);
    if(!body)return false;
    body.innerHTML=`
      <div class="ops-display-studio">
        <aside class="ops-display-controls">
          <div class="ops-display-control-section">
            <div class="ops-display-control-title"><span>01</span><b>Micro canvas</b></div>
            <label>Device profile<select id="displayProfile">
              <option value="oled096">0.96″ OLED · 128×64</option>
              <option value="tft097">0.96–0.97″ TFT · 160×80</option>
              <option value="oled13">1.3″ OLED · 128×64</option>
              <option value="tft114">1.14″ TFT · 240×135</option>
              <option value="tft28">2.8″ TFT · 320×240</option>
              <option value="tft35">3.5″ TFT · 480×320</option>
            </select></label>
            <div class="ops-display-two">
              <label>Rotation<select id="displayRotation"><option value="0">0°</option><option value="90">90°</option><option value="180">180°</option><option value="270">270°</option></select></label>
              <label>Refresh<input id="displayRefresh" type="number" min="5" max="300" step="1" value="10"></label>
            </div>
            <label>Brightness <span id="displayBrightnessValue">85%</span><input id="displayBrightness" type="range" min="10" max="100" value="85"></label>
            <div class="tiny ops-display-hint">Die Vorschau wird vergrößert dargestellt. Auf 0.96″ zählt jeder Pixel – deshalb sind kompakte Layouts Standard.</div>
          </div>

          <div class="ops-display-control-section">
            <div class="ops-display-control-title"><span>02</span><b>Visual system</b></div>
            <div class="ops-display-theme-grid" id="displayThemes">
              <button type="button" data-theme="aurora"><i class="aurora"></i><span>Aurora</span></button>
              <button type="button" data-theme="obsidian"><i class="obsidian"></i><span>Obsidian</span></button>
              <button type="button" data-theme="cyber"><i class="cyber"></i><span>Cyber</span></button>
              <button type="button" data-theme="minimal"><i class="minimal"></i><span>Minimal</span></button>
            </div>
            <div class="ops-display-two">
              <label>Accent<input id="displayAccent" type="color" value="#7c5cff"></label>
              <label>Density<select id="displayDensity"><option value="compact">Compact</option><option value="comfortable">Comfortable</option><option value="spacious">Spacious</option></select></label>
            </div>
          </div>

          <div class="ops-display-control-section">
            <div class="ops-display-control-title"><span>03</span><b>Widget library</b></div>
            <div id="displayWidgets" class="ops-display-widget-library"></div>
            <div class="tiny ops-display-hint">Für 128×64 sind 1–3 Widgets pro Ansicht sinnvoll. Aktive Widgets per Drag & Drop sortieren.</div>
            <div id="displayWidgetOrder" class="ops-display-widget-order"></div>
          </div>

          <div class="ops-display-actions">
            <button type="button" class="primary" id="displaySave">Layout speichern</button>
            <button type="button" id="displayReset">Reset</button>
            <button type="button" id="displayCopy">JSON kopieren</button>
          </div>
        </aside>

        <section class="ops-display-workbench">
          <div class="ops-display-workbench-bar">
            <div><b>Magnified device preview</b><span id="displayResolution">128 × 64 px · 0.96″ OLED</span></div>
            <div class="ops-display-workbench-actions"><span class="ops-display-live-dot"></span><span>Preview</span><button type="button" id="displayFullscreen">⛶</button></div>
          </div>
          <div class="ops-display-stage" id="displayStage">
            <div class="ops-display-device" id="displayDevice">
              <div class="ops-display-bezel">
                <div id="displayPreview" class="ops-display-screen" data-theme="obsidian">
                  <div class="ops-display-screen-grid" id="displayScreenGrid"></div>
                  <div class="ops-display-screen-footer"><span>HOMEPI</span><span id="displayScreenMeta">LIVE · 10s</span></div>
                </div>
              </div>
            </div>
          </div>
          <div class="ops-display-inspector">
            <div><span>Panel</span><b id="displayInspectorProfile">0.96″</b></div>
            <div><span>Theme</span><b id="displayInspectorTheme">Obsidian</b></div>
            <div><span>Widgets</span><b id="displayInspectorWidgets">3</b></div>
            <div><span>Refresh</span><b id="displayInspectorRefresh">10s</b></div>
            <div><span>Brightness</span><b id="displayInspectorBrightness">85%</b></div>
          </div>
        </section>
      </div>`;
    bind();
    mounted=true;
    return true;
  }

  function bind(){
    ['displayProfile','displayRotation','displayRefresh','displayDensity'].forEach(id=>{
      const el=$('#'+id); if(el)el.addEventListener('change',readControlsAndRender);
    });
    const accent=$('#displayAccent'); if(accent)accent.addEventListener('input',readControlsAndRender);
    const bright=$('#displayBrightness'); if(bright)bright.addEventListener('input',readControlsAndRender);
    $$('#displayThemes button').forEach(btn=>btn.addEventListener('click',()=>{layout.theme=btn.dataset.theme;syncControls();render();}));
    const save=$('#displaySave'); if(save)save.onclick=saveLayout;
    const reset=$('#displayReset'); if(reset)reset.onclick=resetLayout;
    const copy=$('#displayCopy'); if(copy)copy.onclick=copyLayout;
    const live=$('#displayRefreshLive'); if(live)live.onclick=refreshLiveData;
    const full=$('#displayFullscreen'); if(full)full.onclick=toggleFullscreen;
  }

  function readControlsAndRender(){
    layout.profile=$('#displayProfile')?.value||layout.profile;
    layout.rotation=Number($('#displayRotation')?.value||0);
    layout.refresh_seconds=clamp($('#displayRefresh')?.value,5,300,10);
    layout.brightness=clamp($('#displayBrightness')?.value,10,100,85);
    layout.theme=$('#displayThemes button.active')?.dataset.theme||layout.theme;
    layout.accent=safeColor($('#displayAccent')?.value);
    layout.density=$('#displayDensity')?.value||layout.density;
    render();
  }

  function syncControls(){
    if($('#displayProfile'))$('#displayProfile').value=layout.profile;
    if($('#displayRotation'))$('#displayRotation').value=String(layout.rotation);
    if($('#displayRefresh'))$('#displayRefresh').value=String(layout.refresh_seconds);
    if($('#displayBrightness'))$('#displayBrightness').value=String(layout.brightness);
    if($('#displayBrightnessValue'))$('#displayBrightnessValue').textContent=`${layout.brightness}%`;
    if($('#displayAccent'))$('#displayAccent').value=layout.accent;
    if($('#displayDensity'))$('#displayDensity').value=layout.density;
    $$('#displayThemes button').forEach(btn=>btn.classList.toggle('active',btn.dataset.theme===layout.theme));
  }

  function toggleWidget(key){
    const pos=layout.widgets.indexOf(key);
    if(pos>=0){
      if(layout.widgets.length===1){notify('Mindestens ein Widget muss aktiv bleiben.',false);return;}
      layout.widgets.splice(pos,1);
    }else layout.widgets.push(key);
    render();
  }

  function renderWidgetControls(){
    const library=$('#displayWidgets');
    const order=$('#displayWidgetOrder');
    if(!library||!order)return;
    const active=new Set(layout.widgets);
    library.innerHTML=WIDGETS.map(w=>`<button type="button" data-widget="${w.key}" class="${active.has(w.key)?'active':''}"><span>${w.icon}</span><div><b>${esc(w.label)}</b><small>${esc(w.desc)}</small></div><i>${active.has(w.key)?'✓':'+'}</i></button>`).join('');
    $$('button[data-widget]',library).forEach(btn=>btn.onclick=()=>toggleWidget(btn.dataset.widget));

    order.innerHTML=layout.widgets.map((key,index)=>{
      const w=WIDGETS.find(x=>x.key===key)||{label:key,icon:'◇'};
      return `<div class="ops-display-order-item" draggable="true" data-key="${key}"><span class="handle">⋮⋮</span><span class="icon">${w.icon}</span><b>${esc(w.label)}</b><span class="index">${String(index+1).padStart(2,'0')}</span></div>`;
    }).join('');
    $$('.ops-display-order-item',order).forEach(item=>{
      item.ondragstart=()=>{draggingKey=item.dataset.key;item.classList.add('dragging');};
      item.ondragend=()=>{draggingKey=null;item.classList.remove('dragging');};
      item.ondragover=e=>e.preventDefault();
      item.ondrop=e=>{
        e.preventDefault();
        const target=item.dataset.key;
        if(!draggingKey||draggingKey===target)return;
        const from=layout.widgets.indexOf(draggingKey),to=layout.widgets.indexOf(target);
        if(from<0||to<0)return;
        layout.widgets.splice(from,1);
        layout.widgets.splice(to,0,draggingKey);
        render();
      };
    });
  }

  function widgetData(key){
    const system=liveData.summary?.system||{};
    const runtime=liveData.media?.runtime||{};
    const voice=runtime.voice||{};
    const yt=runtime.youtube||{};
    const current=yt.current||{};
    const now=new Date();
    const map={
      clock:{value:now.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}),sub:now.toLocaleDateString('de-DE',{weekday:'short',day:'2-digit',month:'2-digit'})},
      temperature:{value:system.temperature_c==null?'—':`${system.temperature_c}°`,sub:'CPU TEMP'},
      ram:{value:system.memory_percent==null?'—':`${system.memory_percent}%`,sub:'MEMORY'},
      cpu:{value:system.cpu_percent==null?'—':`${system.cpu_percent}%`,sub:'CPU LOAD'},
      nowplaying:{value:current.title||voice.title||'Nothing playing',sub:voice.channel_name||'MEDIA IDLE'},
      pihole:{value:system.pihole?.active?'ON':'—',sub:'PI-HOLE'},
      uptime:{value:fmtUptime(system.uptime_seconds),sub:'UPTIME'},
      network:{value:'ONLINE',sub:'NETWORK'}
    };
    return map[key]||{value:'—',sub:key.toUpperCase()};
  }

  function renderScreen(){
    const grid=$('#displayScreenGrid');
    const screen=$('#displayPreview');
    if(!grid||!screen)return;
    const profile=PROFILES[layout.profile]||PROFILES.oled096;
    screen.dataset.theme=layout.theme;
    screen.dataset.density=layout.density;
    screen.dataset.micro=profile.micro?'true':'false';
    screen.style.setProperty('--display-accent',layout.accent);
    screen.style.setProperty('--display-brightness',String(layout.brightness/100));
    const visibleWidgets=profile.micro?layout.widgets.slice(0,3):layout.widgets;
    grid.innerHTML=visibleWidgets.map(key=>{
      const def=WIDGETS.find(x=>x.key===key)||{label:key,icon:'◇'};
      const data=widgetData(key);
      const wide=def.wide?' wide':'';
      const playing=key==='nowplaying'&&String(data.value)!=='Nothing playing';
      return `<article class="ops-screen-widget${wide}${playing?' playing':''}" data-widget="${key}"><div class="ops-screen-widget-top"><span>${def.icon}</span><small>${esc(def.label)}</small></div><strong>${esc(data.value)}</strong><em>${esc(data.sub)}</em>${key==='cpu'||key==='ram'?`<div class="ops-screen-meter"><i style="width:${parseFloat(data.value)||0}%"></i></div>`:''}</article>`;
    }).join('');
    if(profile.micro&&layout.widgets.length>3){
      grid.insertAdjacentHTML('beforeend',`<div class="ops-screen-overflow">+${layout.widgets.length-3} weitere · nächste Seite</div>`);
    }
    const meta=$('#displayScreenMeta');if(meta)meta.textContent=`LIVE · ${layout.refresh_seconds}s`;
  }

  function renderDevice(){
    const profile=PROFILES[layout.profile]||PROFILES.oled096;
    const rotated=layout.rotation===90||layout.rotation===270;
    const width=rotated?profile.height:profile.width;
    const height=rotated?profile.width:profile.height;
    const stage=$('#displayStage'),device=$('#displayDevice');
    if(!stage||!device)return;
    const maxW=Math.min(760,Math.max(260,stage.clientWidth-56));
    const maxH=520;
    const scale=profile.micro?Math.min(maxW/width,maxH/height,4.2):Math.min(maxW/width,maxH/height,1.45);
    device.style.width=`${Math.max(profile.micro?320:240,width*scale)}px`;
    device.style.aspectRatio=`${width}/${height}`;
    device.dataset.profile=layout.profile;
    device.dataset.rotation=String(layout.rotation);
    device.dataset.micro=profile.micro?'true':'false';
    const resolution=$('#displayResolution');if(resolution)resolution.textContent=`${width} × ${height} px · ${profile.label}${profile.micro?' · magnified':''}`;
  }

  function renderInspector(){
    const profile=PROFILES[layout.profile]||PROFILES.oled096;
    const panel=$('#displayInspectorProfile');if(panel)panel.textContent=profile.label;
    const theme=$('#displayInspectorTheme');if(theme)theme.textContent=layout.theme.charAt(0).toUpperCase()+layout.theme.slice(1);
    const widgets=$('#displayInspectorWidgets');if(widgets)widgets.textContent=String(layout.widgets.length);
    const refresh=$('#displayInspectorRefresh');if(refresh)refresh.textContent=`${layout.refresh_seconds}s`;
    const brightness=$('#displayInspectorBrightness');if(brightness)brightness.textContent=`${layout.brightness}%`;
  }

  function render(){
    if(!mounted)return;
    syncControls();
    renderWidgetControls();
    renderDevice();
    renderScreen();
    renderInspector();
  }

  async function refreshLiveData(){
    if(typeof api!=='function')return;
    const btn=$('#displayRefreshLive');if(btn){btn.disabled=true;btn.textContent='…';}
    try{
      const results=await Promise.allSettled([
        api(`/api/ops/summary?guild_id=${GUILD_ID}`),
        api(`/api/ops/media?guild_id=${GUILD_ID}`)
      ]);
      if(results[0].status==='fulfilled')liveData.summary=results[0].value;
      if(results[1].status==='fulfilled')liveData.media=results[1].value;
      renderScreen();
      notify('Display Preview aktualisiert.');
    }catch(error){
      debug('Pi Display Live Data',error);
    }finally{
      if(btn){btn.disabled=false;btn.textContent='↻ Live';}
    }
  }

  async function loadLayout(){
    if(!mounted&& !mount())return;
    const serial=++loadSerial;
    const state=$('#displaySaveState');if(state)state.textContent='Loading layout…';
    try{
      const data=await api(`/api/ops/display?guild_id=${GUILD_ID}`);
      if(serial!==loadSerial)return;
      layout=normalize(data.layout);
      if(state)state.textContent=data.updated_at?`Saved · ${data.updated_at}`:'Unsaved default';
      render();
      refreshLiveData();
    }catch(error){
      layout=clone(DEFAULT_LAYOUT);
      if(state)state.textContent='Local fallback';
      render();
      debug('Pi Display Layout',error);
    }
  }

  async function saveLayout(){
    readControlsAndRender();
    const btn=$('#displaySave');const state=$('#displaySaveState');
    if(btn){btn.disabled=true;btn.textContent='Saving…';}
    try{
      await post('/api/ops/display',{guild_id:GUILD_ID,layout:clone(layout)});
      if(state)state.textContent=`Saved · ${new Date().toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'})}`;
      notify('Pi Display Layout gespeichert.');
    }catch(error){
      debug('Pi Display Save',error);
      notify(error.message||String(error),false);
    }finally{
      if(btn){btn.disabled=false;btn.textContent='Layout speichern';}
    }
  }

  function resetLayout(){
    if(!confirm('Display Layout auf den 0.96″ Standard zurücksetzen?'))return;
    layout=clone(DEFAULT_LAYOUT);
    render();
    notify('0.96″ Standardlayout geladen. Noch nicht gespeichert.');
  }

  async function copyLayout(){
    const text=JSON.stringify(layout,null,2);
    try{
      await navigator.clipboard.writeText(text);
      notify('Display JSON kopiert.');
    }catch(_){
      window.prompt('Display JSON kopieren:',text);
    }
  }

  function toggleFullscreen(){
    const workbench=$('.ops-display-workbench');
    if(!workbench)return;
    workbench.classList.toggle('fullscreen');
    document.body.classList.toggle('ops-display-fullscreen',workbench.classList.contains('fullscreen'));
    setTimeout(renderDevice,40);
  }

  function notify(message,ok=true){
    if(typeof note==='function')note(message,ok);
  }
  function debug(title,error){
    if(typeof window.showOpsDebug==='function')window.showOpsDebug(title,error,'Display Studio');
  }

  function install(){
    if(!mount())return;
    window.loadDisplay=loadLayout;
    window.saveDisplay=saveLayout;
    window.renderDisplayPreview=render;
    window.renderDisplayWidgets=renderWidgetControls;
    window.toggleDisplayWidget=toggleWidget;

    const navButton=$('#nav [data-tab="hardware"]');
    if(navButton)navButton.addEventListener('click',()=>setTimeout(loadLayout,40));
    window.addEventListener('resize',()=>{if(mounted)renderDevice();});
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape'&&$('.ops-display-workbench.fullscreen'))toggleFullscreen();
    });
    if(clockTimer)clearInterval(clockTimer);
    clockTimer=setInterval(()=>{
      if(!mounted||!layout.widgets.includes('clock'))return;
      const section=$('#hardware');
      if(section&&section.classList.contains('active'))renderScreen();
    },1000);

    loadLayout();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});
  else install();
})();