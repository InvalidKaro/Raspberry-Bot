(() => {
  'use strict';

  const GUILD_ID = '1162733312226361454';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const WIDGETS = [
    {key:'clock', label:'Uhrzeit', icon:'◷', desc:'Uhrzeit und optional Datum', wide:true},
    {key:'temperature', label:'Temperatur', icon:'♨', desc:'CPU-Temperatur'},
    {key:'ram', label:'RAM', icon:'▥', desc:'Arbeitsspeicher'},
    {key:'cpu', label:'CPU', icon:'⌁', desc:'CPU-Auslastung'},
    {key:'nowplaying', label:'Now Playing', icon:'♫', desc:'Radio / YouTube', wide:true},
    {key:'pihole', label:'Pi-hole', icon:'◉', desc:'DNS-Blocking'},
    {key:'uptime', label:'Uptime', icon:'↟', desc:'Systemlaufzeit'},
    {key:'network', label:'Netzwerk', icon:'⌁', desc:'Netzwerkstatus'}
  ];

  const CONDITIONS = [
    {value:'always', label:'Immer'},
    {value:'media', label:'Nur wenn Medien laufen'},
    {value:'idle', label:'Nur wenn keine Medien laufen'},
    {value:'hot', label:'Wenn Temperatur hoch ist'},
    {value:'busy', label:'Wenn CPU hoch ist'}
  ];

  const DEFAULT_LAYOUT = {
    version: 4,
    profile: 'oled096blue',
    width: 128,
    height: 64,
    controller: 'ssd1306',
    bus: 'i2c',
    i2c_address: '0x3C',
    rotation: 0,
    refresh_seconds: 10,
    brightness: 90,
    pixel_color: 'blue',
    density: 'compact',
    auto_cycle: true,
    default_duration: 5,
    transition: 'cut',
    wake_on_media: true,
    show_footer: true,
    show_labels: true,
    clock_seconds: false,
    pages: [
      {id:'system', name:'System', duration:5, condition:'always', threshold:0, layout:'grid', widgets:['clock','temperature','ram']},
      {id:'performance', name:'Performance', duration:5, condition:'always', threshold:0, layout:'grid', widgets:['cpu','uptime','network']},
      {id:'media', name:'Now Playing', duration:7, condition:'media', threshold:0, layout:'single', widgets:['nowplaying']},
      {id:'pihole', name:'Pi-hole', duration:5, condition:'always', threshold:0, layout:'grid', widgets:['pihole','clock']}
    ]
  };

  let layout = clone(DEFAULT_LAYOUT);
  let liveData = {summary:null, media:null};
  let mounted = false;
  let draggingKey = null;
  let clockTimer = null;
  let cycleTimer = null;
  let loadSerial = 0;
  let activePageId = DEFAULT_LAYOUT.pages[0].id;
  let lastMediaActive = false;

  function ensureStyles(){
    [
      ['ops-display-builder-v4-style','/static/display_builder_v4.css'],
      ['ops-mobile-nav-v2-style','/static/ops_mobile_nav.css']
    ].forEach(([id,href])=>{
      if(document.getElementById(id))return;
      const link=document.createElement('link');
      link.id=id;link.rel='stylesheet';link.href=href;document.head.appendChild(link);
    });
  }

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
  function makeId(prefix='page'){
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,6)}`;
  }
  function fmtUptime(seconds){
    const s=Math.max(0,Number(seconds)||0), d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), m=Math.floor((s%3600)/60);
    if(d)return `${d}d ${h}h`;
    if(h)return `${h}h ${m}m`;
    return `${m}m`;
  }
  function systemData(){return liveData.summary?.system||{};}
  function runtimeData(){return liveData.media?.runtime||{};}
  function mediaActive(){
    const runtime=runtimeData();
    const current=runtime.youtube?.current||{};
    const voice=runtime.voice||{};
    return Boolean(current.title||current.url||voice.title||voice.station||voice.kind==='radio'||voice.playing);
  }

  function normalizePage(raw,index){
    const allowed=new Set(WIDGETS.map(x=>x.key));
    const source=raw&&typeof raw==='object'?raw:{};
    let widgets=Array.isArray(source.widgets)?source.widgets.filter((x,i,a)=>allowed.has(x)&&a.indexOf(x)===i):[];
    if(!widgets.length)widgets=index===0?['clock','temperature','ram']:['cpu'];
    return {
      id:String(source.id||makeId('page')),
      name:String(source.name||`Seite ${index+1}`).slice(0,32),
      duration:clamp(source.duration,2,60,5),
      condition:CONDITIONS.some(x=>x.value===source.condition)?source.condition:'always',
      threshold:clamp(source.threshold,0,100,source.condition==='hot'?70:80),
      layout:['grid','single','stack'].includes(source.layout)?source.layout:'grid',
      widgets:widgets.slice(0,8)
    };
  }

  function normalize(raw){
    const source=raw&&typeof raw==='object'?raw:{};
    const next={...clone(DEFAULT_LAYOUT),...source};
    next.profile='oled096blue';
    next.width=128;
    next.height=64;
    next.controller='ssd1306';
    next.bus='i2c';
    next.i2c_address=['0x3C','0x3D'].includes(String(next.i2c_address))?String(next.i2c_address):'0x3C';
    next.rotation=[0,180].includes(Number(next.rotation))?Number(next.rotation):0;
    next.refresh_seconds=clamp(next.refresh_seconds,5,300,10);
    next.brightness=clamp(next.brightness,10,100,90);
    next.pixel_color='blue';
    next.density='compact';
    next.auto_cycle=next.auto_cycle!==false;
    next.default_duration=clamp(next.default_duration,2,60,5);
    next.transition=['cut','fade'].includes(next.transition)?next.transition:'cut';
    next.wake_on_media=next.wake_on_media!==false;
    next.show_footer=next.show_footer!==false;
    next.show_labels=next.show_labels!==false;
    next.clock_seconds=Boolean(next.clock_seconds);

    if(Array.isArray(source.pages)&&source.pages.length){
      next.pages=source.pages.slice(0,12).map(normalizePage);
    }else if(Array.isArray(source.widgets)&&source.widgets.length){
      next.pages=clone(DEFAULT_LAYOUT.pages);
      next.pages[0].widgets=source.widgets.slice(0,6);
    }else{
      next.pages=clone(DEFAULT_LAYOUT.pages);
    }
    next.version=4;
    return next;
  }

  function activePage(){
    return layout.pages.find(p=>p.id===activePageId)||layout.pages[0];
  }

  function mount(){
    const oldRotation=$('#displayRotation');
    if(!oldRotation)return false;
    const card=oldRotation.closest('.card');
    if(!card)return false;
    card.classList.remove('half');
    card.classList.add('full','ops-display-studio-card');
    const head=$('.head',card);
    if(head)head.innerHTML='<div><h2>0.96″ Blue OLED Builder</h2><div class="tiny">128×64 · SSD1306 · I²C · automatische Seitenrotation</div></div><div class="ops-display-head-actions"><span class="ops-display-save-state" id="displaySaveState">Local preview</span><button type="button" id="displayRefreshLive">↻ Live</button></div>';
    const body=$('.body',card);
    if(!body)return false;
    body.innerHTML=`
      <div class="ops-display-studio">
        <aside class="ops-display-controls">
          <div class="ops-display-builder-tabs" role="tablist">
            <button type="button" class="active" data-builder-tab="device">Display</button>
            <button type="button" data-builder-tab="pages">Seiten</button>
            <button type="button" data-builder-tab="widgets">Widgets</button>
            <button type="button" data-builder-tab="automation">Auto</button>
          </div>

          <div class="ops-display-builder-panel active" data-builder-panel="device">
            <div class="ops-display-control-section">
              <div class="ops-display-control-title"><span>01</span><b>Hardware</b></div>
              <div class="ops-display-hardware-card"><span class="oled-dot"></span><div><b>0.96″ Blue OLED</b><small>128×64 · SSD1306 · I²C</small></div></div>
              <div class="ops-display-two">
                <label>I²C Address<select id="displayAddress"><option value="0x3C">0x3C</option><option value="0x3D">0x3D</option></select></label>
                <label>Rotation<select id="displayRotation"><option value="0">0°</option><option value="180">180°</option></select></label>
              </div>
              <label>Brightness <span id="displayBrightnessValue">90%</span><input id="displayBrightness" type="range" min="10" max="100" value="90"></label>
              <label>Data refresh<input id="displayRefresh" type="number" min="5" max="300" step="1" value="10"></label>
            </div>

            <div class="ops-display-control-section">
              <div class="ops-display-control-title"><span>02</span><b>Pixel UI</b></div>
              <label class="ops-display-switch"><input id="displayLabels" type="checkbox" checked><span>Widget-Bezeichnungen anzeigen</span></label>
              <label class="ops-display-switch"><input id="displayFooter" type="checkbox" checked><span>Statuszeile anzeigen</span></label>
              <label class="ops-display-switch"><input id="displayClockSeconds" type="checkbox"><span>Sekunden in der Uhr anzeigen</span></label>
              <div class="tiny ops-display-hint">Das echte Panel bleibt schwarz/blau. Farben und Desktop-Themes sind absichtlich entfernt.</div>
            </div>
          </div>

          <div class="ops-display-builder-panel" data-builder-panel="pages">
            <div class="ops-display-control-section">
              <div class="ops-display-control-title"><span>03</span><b>Seiten</b></div>
              <div class="ops-display-page-toolbar">
                <button type="button" class="primary" id="displayAddPage">+ Seite</button>
                <button type="button" id="displayDuplicatePage">Duplizieren</button>
              </div>
              <div id="displayPageList" class="ops-display-page-list"></div>
            </div>
            <div class="ops-display-control-section" id="displayPageEditor"></div>
          </div>

          <div class="ops-display-builder-panel" data-builder-panel="widgets">
            <div class="ops-display-control-section">
              <div class="ops-display-control-title"><span>04</span><b>Widgets der aktuellen Seite</b></div>
              <div id="displayWidgets" class="ops-display-widget-library"></div>
              <div class="tiny ops-display-hint">Aktive Widgets werden auf der ausgewählten Seite gezeigt. Reihenfolge per Drag & Drop.</div>
              <div id="displayWidgetOrder" class="ops-display-widget-order"></div>
            </div>
          </div>

          <div class="ops-display-builder-panel" data-builder-panel="automation">
            <div class="ops-display-control-section">
              <div class="ops-display-control-title"><span>05</span><b>Automatischer Wechsel</b></div>
              <label class="ops-display-switch"><input id="displayAutoCycle" type="checkbox" checked><span>Seiten automatisch wechseln</span></label>
              <label class="ops-display-switch"><input id="displayWakeMedia" type="checkbox" checked><span>Bei Medien automatisch auf Now Playing wechseln</span></label>
              <div class="ops-display-two">
                <label>Standarddauer<input id="displayDefaultDuration" type="number" min="2" max="60" value="5"></label>
                <label>Transition<select id="displayTransition"><option value="cut">Direkt</option><option value="fade">Soft Fade</option></select></label>
              </div>
              <div class="ops-display-auto-info"><span>AUTO</span><div><b>Keine Hardware-Tasten nötig</b><small>Seiten wechseln nach Dauer und Bedingungen selbstständig.</small></div></div>
            </div>
          </div>

          <div class="ops-display-actions">
            <button type="button" class="primary" id="displaySave">Konfiguration speichern</button>
            <button type="button" id="displayReset">Reset</button>
            <button type="button" id="displayCopy">JSON kopieren</button>
          </div>
        </aside>

        <section class="ops-display-workbench">
          <div class="ops-display-workbench-bar">
            <div><b>Live OLED Preview</b><span id="displayResolution">128 × 64 px · Blue OLED</span></div>
            <div class="ops-display-workbench-actions"><span class="ops-display-live-dot"></span><span id="displayCycleState">AUTO</span><button type="button" id="displayFullscreen">⛶</button></div>
          </div>
          <div class="ops-display-stage" id="displayStage">
            <div class="ops-display-device" id="displayDevice" data-profile="oled096blue" data-micro="true">
              <div class="ops-display-bezel">
                <div id="displayPreview" class="ops-display-screen" data-theme="blue-oled" data-micro="true">
                  <div class="ops-display-screen-grid" id="displayScreenGrid"></div>
                  <div class="ops-display-screen-footer" id="displayScreenFooter"><span id="displayPageName">SYSTEM</span><span id="displayScreenMeta">LIVE · 10s</span></div>
                </div>
              </div>
            </div>
          </div>
          <div class="ops-display-page-dots" id="displayPageDots"></div>
          <div class="ops-display-inspector">
            <div><span>Panel</span><b>0.96″ Blue</b></div>
            <div><span>Page</span><b id="displayInspectorPage">System</b></div>
            <div><span>Pages</span><b id="displayInspectorPages">4</b></div>
            <div><span>Mode</span><b id="displayInspectorMode">AUTO</b></div>
            <div><span>Brightness</span><b id="displayInspectorBrightness">90%</b></div>
          </div>
        </section>
      </div>`;
    bind();
    mounted=true;
    return true;
  }

  function bind(){
    $$('.ops-display-builder-tabs button').forEach(btn=>btn.addEventListener('click',()=>switchBuilderTab(btn.dataset.builderTab)));
    ['displayAddress','displayRotation','displayRefresh','displayTransition'].forEach(id=>{
      const el=$('#'+id);if(el)el.addEventListener('change',readControlsAndRender);
    });
    ['displayBrightness','displayDefaultDuration'].forEach(id=>{
      const el=$('#'+id);if(el)el.addEventListener('input',readControlsAndRender);
    });
    ['displayLabels','displayFooter','displayClockSeconds','displayAutoCycle','displayWakeMedia'].forEach(id=>{
      const el=$('#'+id);if(el)el.addEventListener('change',readControlsAndRender);
    });
    $('#displayAddPage').onclick=addPage;
    $('#displayDuplicatePage').onclick=duplicatePage;
    $('#displaySave').onclick=saveLayout;
    $('#displayReset').onclick=resetLayout;
    $('#displayCopy').onclick=copyLayout;
    $('#displayRefreshLive').onclick=refreshLiveData;
    $('#displayFullscreen').onclick=toggleFullscreen;
  }

  function switchBuilderTab(name){
    $$('.ops-display-builder-tabs button').forEach(btn=>btn.classList.toggle('active',btn.dataset.builderTab===name));
    $$('.ops-display-builder-panel').forEach(panel=>panel.classList.toggle('active',panel.dataset.builderPanel===name));
  }

  function readControlsAndRender(){
    layout.i2c_address=$('#displayAddress')?.value||'0x3C';
    layout.rotation=Number($('#displayRotation')?.value||0);
    layout.refresh_seconds=clamp($('#displayRefresh')?.value,5,300,10);
    layout.brightness=clamp($('#displayBrightness')?.value,10,100,90);
    layout.default_duration=clamp($('#displayDefaultDuration')?.value,2,60,5);
    layout.transition=$('#displayTransition')?.value||'cut';
    layout.show_labels=Boolean($('#displayLabels')?.checked);
    layout.show_footer=Boolean($('#displayFooter')?.checked);
    layout.clock_seconds=Boolean($('#displayClockSeconds')?.checked);
    layout.auto_cycle=Boolean($('#displayAutoCycle')?.checked);
    layout.wake_on_media=Boolean($('#displayWakeMedia')?.checked);
    render();
    scheduleCycle();
  }

  function syncControls(){
    if($('#displayAddress'))$('#displayAddress').value=layout.i2c_address;
    if($('#displayRotation'))$('#displayRotation').value=String(layout.rotation);
    if($('#displayRefresh'))$('#displayRefresh').value=String(layout.refresh_seconds);
    if($('#displayBrightness'))$('#displayBrightness').value=String(layout.brightness);
    if($('#displayBrightnessValue'))$('#displayBrightnessValue').textContent=`${layout.brightness}%`;
    if($('#displayDefaultDuration'))$('#displayDefaultDuration').value=String(layout.default_duration);
    if($('#displayTransition'))$('#displayTransition').value=layout.transition;
    if($('#displayLabels'))$('#displayLabels').checked=layout.show_labels;
    if($('#displayFooter'))$('#displayFooter').checked=layout.show_footer;
    if($('#displayClockSeconds'))$('#displayClockSeconds').checked=layout.clock_seconds;
    if($('#displayAutoCycle'))$('#displayAutoCycle').checked=layout.auto_cycle;
    if($('#displayWakeMedia'))$('#displayWakeMedia').checked=layout.wake_on_media;
  }

  function addPage(){
    const page={id:makeId(),name:`Seite ${layout.pages.length+1}`,duration:layout.default_duration,condition:'always',threshold:0,layout:'grid',widgets:['clock']};
    layout.pages.push(page);
    activePageId=page.id;
    render();
    switchBuilderTab('pages');
    scheduleCycle();
  }

  function duplicatePage(){
    const source=activePage();if(!source)return;
    const page=clone(source);
    page.id=makeId();
    page.name=`${source.name} Copy`.slice(0,32);
    layout.pages.push(page);
    activePageId=page.id;
    render();
    scheduleCycle();
  }

  function deletePage(id){
    if(layout.pages.length<=1){notify('Mindestens eine Seite muss bleiben.',false);return;}
    const index=layout.pages.findIndex(p=>p.id===id);
    if(index<0)return;
    layout.pages.splice(index,1);
    if(activePageId===id)activePageId=layout.pages[Math.max(0,index-1)].id;
    render();
    scheduleCycle();
  }

  function movePage(id,delta){
    const index=layout.pages.findIndex(p=>p.id===id);
    const next=index+delta;
    if(index<0||next<0||next>=layout.pages.length)return;
    const [page]=layout.pages.splice(index,1);
    layout.pages.splice(next,0,page);
    render();
  }

  function renderPageList(){
    const root=$('#displayPageList');if(!root)return;
    root.innerHTML=layout.pages.map((page,index)=>`<button type="button" class="ops-display-page-item ${page.id===activePageId?'active':''}" data-page="${esc(page.id)}"><span class="page-no">${String(index+1).padStart(2,'0')}</span><div><b>${esc(page.name)}</b><small>${esc(conditionLabel(page.condition))} · ${page.duration}s</small></div><i>${page.id===activePageId?'●':'○'}</i></button>`).join('');
    $$('.ops-display-page-item',root).forEach(btn=>btn.onclick=()=>{
      activePageId=btn.dataset.page;
      render();
      scheduleCycle();
    });
  }

  function conditionLabel(value){return CONDITIONS.find(x=>x.value===value)?.label||'Immer';}

  function renderPageEditor(){
    const root=$('#displayPageEditor');const page=activePage();if(!root||!page)return;
    const thresholdVisible=page.condition==='hot'||page.condition==='busy';
    root.innerHTML=`
      <div class="ops-display-control-title"><span>•</span><b>Aktuelle Seite konfigurieren</b></div>
      <label>Name<input id="displayPageNameInput" maxlength="32" value="${esc(page.name)}"></label>
      <div class="ops-display-two">
        <label>Dauer<input id="displayPageDuration" type="number" min="2" max="60" value="${page.duration}"></label>
        <label>Layout<select id="displayPageLayout"><option value="grid">Grid</option><option value="single">Single</option><option value="stack">Stack</option></select></label>
      </div>
      <label>Bedingung<select id="displayPageCondition">${CONDITIONS.map(x=>`<option value="${x.value}">${esc(x.label)}</option>`).join('')}</select></label>
      <label id="displayThresholdLabel" style="${thresholdVisible?'':'display:none'}">Schwellwert<input id="displayPageThreshold" type="number" min="0" max="100" value="${page.threshold||(page.condition==='hot'?70:80)}"></label>
      <div class="ops-display-page-actions">
        <button type="button" id="displayPageUp">↑</button>
        <button type="button" id="displayPageDown">↓</button>
        <button type="button" class="danger" id="displayDeletePage">Löschen</button>
      </div>`;
    $('#displayPageLayout').value=page.layout;
    $('#displayPageCondition').value=page.condition;
    $('#displayPageNameInput').oninput=e=>{page.name=e.target.value.slice(0,32)||'Seite';renderPageList();renderScreen();renderInspector();};
    $('#displayPageDuration').oninput=e=>{page.duration=clamp(e.target.value,2,60,layout.default_duration);renderPageList();scheduleCycle();};
    $('#displayPageLayout').onchange=e=>{page.layout=e.target.value;renderScreen();};
    $('#displayPageCondition').onchange=e=>{
      page.condition=e.target.value;
      if(page.condition==='hot'&&!page.threshold)page.threshold=70;
      if(page.condition==='busy'&&!page.threshold)page.threshold=80;
      renderPageEditor();renderPageList();scheduleCycle();
    };
    const threshold=$('#displayPageThreshold');if(threshold)threshold.oninput=e=>{page.threshold=clamp(e.target.value,0,100,page.condition==='hot'?70:80);};
    $('#displayPageUp').onclick=()=>movePage(page.id,-1);
    $('#displayPageDown').onclick=()=>movePage(page.id,1);
    $('#displayDeletePage').onclick=()=>deletePage(page.id);
  }

  function toggleWidget(key){
    const page=activePage();if(!page)return;
    const pos=page.widgets.indexOf(key);
    if(pos>=0){
      if(page.widgets.length===1){notify('Mindestens ein Widget muss auf der Seite bleiben.',false);return;}
      page.widgets.splice(pos,1);
    }else{
      page.widgets.push(key);
    }
    render();
  }

  function renderWidgetControls(){
    const page=activePage();
    const library=$('#displayWidgets');const order=$('#displayWidgetOrder');
    if(!page||!library||!order)return;
    const active=new Set(page.widgets);
    library.innerHTML=WIDGETS.map(w=>`<button type="button" data-widget="${w.key}" class="${active.has(w.key)?'active':''}"><span>${w.icon}</span><div><b>${esc(w.label)}</b><small>${esc(w.desc)}</small></div><i>${active.has(w.key)?'✓':'+'}</i></button>`).join('');
    $$('button[data-widget]',library).forEach(btn=>btn.onclick=()=>toggleWidget(btn.dataset.widget));
    order.innerHTML=page.widgets.map((key,index)=>{
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
        const from=page.widgets.indexOf(draggingKey),to=page.widgets.indexOf(target);
        if(from<0||to<0)return;
        page.widgets.splice(from,1);page.widgets.splice(to,0,draggingKey);render();
      };
    });
  }

  function widgetData(key){
    const system=systemData();
    const runtime=runtimeData();
    const voice=runtime.voice||{};
    const current=runtime.youtube?.current||{};
    const now=new Date();
    const timeOptions={hour:'2-digit',minute:'2-digit'};
    if(layout.clock_seconds)timeOptions.second='2-digit';
    const map={
      clock:{value:now.toLocaleTimeString('de-DE',timeOptions),sub:now.toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit'})},
      temperature:{value:system.temperature_c==null?'—':`${Math.round(Number(system.temperature_c))}°`,sub:'TEMP'},
      ram:{value:system.memory_percent==null?'—':`${Math.round(Number(system.memory_percent))}%`,sub:'RAM'},
      cpu:{value:system.cpu_percent==null?'—':`${Math.round(Number(system.cpu_percent))}%`,sub:'CPU'},
      nowplaying:{value:current.title||voice.title||voice.station||'Nichts läuft',sub:voice.channel_name||'MEDIA'},
      pihole:{value:system.pihole?.active?'ON':'—',sub:'PI-HOLE'},
      uptime:{value:fmtUptime(system.uptime_seconds),sub:'UPTIME'},
      network:{value:'ON',sub:'NET'}
    };
    return map[key]||{value:'—',sub:key.toUpperCase()};
  }

  function isPageEligible(page){
    const system=systemData();
    switch(page.condition){
      case 'media': return mediaActive();
      case 'idle': return !mediaActive();
      case 'hot': return Number(system.temperature_c||0)>=Number(page.threshold||70);
      case 'busy': return Number(system.cpu_percent||0)>=Number(page.threshold||80);
      default: return true;
    }
  }

  function eligiblePages(){
    const pages=layout.pages.filter(isPageEligible);
    return pages.length?pages:[layout.pages[0]];
  }

  function renderScreen(){
    const grid=$('#displayScreenGrid');const screen=$('#displayPreview');const page=activePage();
    if(!grid||!screen||!page)return;
    screen.dataset.layout=page.layout;
    screen.dataset.labels=layout.show_labels?'true':'false';
    screen.classList.toggle('fade-transition',layout.transition==='fade');
    const widgets=page.layout==='single'?page.widgets.slice(0,1):page.widgets.slice(0,3);
    grid.innerHTML=widgets.map(key=>{
      const def=WIDGETS.find(x=>x.key===key)||{label:key,icon:'◇'};
      const data=widgetData(key);
      const wide=def.wide||page.layout==='single'?' wide':'';
      return `<article class="ops-screen-widget${wide}" data-widget="${key}"><div class="ops-screen-widget-top"><span>${def.icon}</span><small>${esc(def.label)}</small></div><strong>${esc(data.value)}</strong><em>${esc(data.sub)}</em>${key==='cpu'||key==='ram'?`<div class="ops-screen-meter"><i style="width:${parseFloat(data.value)||0}%"></i></div>`:''}</article>`;
    }).join('');
    const footer=$('#displayScreenFooter');if(footer)footer.style.display=layout.show_footer?'flex':'none';
    const pageName=$('#displayPageName');if(pageName)pageName.textContent=page.name.toUpperCase().slice(0,12);
    const meta=$('#displayScreenMeta');if(meta)meta.textContent=`${layout.auto_cycle?'AUTO':'HOLD'} · ${page.duration}s`;
  }

  function renderDevice(){
    const stage=$('#displayStage'),device=$('#displayDevice');if(!stage||!device)return;
    const width=128,height=64;
    const maxW=Math.min(760,Math.max(280,stage.clientWidth-48));
    const maxH=500;
    const scale=Math.min(maxW/width,maxH/height,4.6);
    device.style.width=`${Math.max(340,width*scale)}px`;
    device.style.aspectRatio=`${width}/${height}`;
    device.dataset.rotation=String(layout.rotation);
    const resolution=$('#displayResolution');if(resolution)resolution.textContent=`${width} × ${height} px · 0.96″ Blue OLED · magnified`;
  }

  function renderPageDots(){
    const root=$('#displayPageDots');if(!root)return;
    root.innerHTML=layout.pages.map(page=>`<button type="button" class="${page.id===activePageId?'active':''} ${isPageEligible(page)?'':'disabled'}" data-page-dot="${esc(page.id)}" aria-label="${esc(page.name)}"><span></span></button>`).join('');
    $$('[data-page-dot]',root).forEach(btn=>btn.onclick=()=>{activePageId=btn.dataset.pageDot;render();scheduleCycle();});
  }

  function renderInspector(){
    const page=activePage();
    if($('#displayInspectorPage'))$('#displayInspectorPage').textContent=page?.name||'—';
    if($('#displayInspectorPages'))$('#displayInspectorPages').textContent=String(layout.pages.length);
    if($('#displayInspectorMode'))$('#displayInspectorMode').textContent=layout.auto_cycle?'AUTO':'HOLD';
    if($('#displayInspectorBrightness'))$('#displayInspectorBrightness').textContent=`${layout.brightness}%`;
    if($('#displayCycleState'))$('#displayCycleState').textContent=layout.auto_cycle?'AUTO':'HOLD';
  }

  function render(){
    if(!mounted)return;
    if(!layout.pages.some(p=>p.id===activePageId))activePageId=layout.pages[0].id;
    syncControls();renderPageList();renderPageEditor();renderWidgetControls();renderDevice();renderScreen();renderPageDots();renderInspector();
  }

  function scheduleCycle(){
    if(cycleTimer){clearTimeout(cycleTimer);cycleTimer=null;}
    if(!mounted||!layout.auto_cycle)return;
    const page=activePage();
    const duration=clamp(page?.duration,2,60,layout.default_duration)*1000;
    cycleTimer=setTimeout(()=>advancePage('timer'),duration);
  }

  function advancePage(){
    const pages=eligiblePages();if(!pages.length)return;
    const currentIndex=pages.findIndex(p=>p.id===activePageId);
    const next=pages[(currentIndex+1+pages.length)%pages.length]||pages[0];
    if(next.id!==activePageId){activePageId=next.id;render();}
    scheduleCycle();
  }

  function reactToLiveState(){
    const active=mediaActive();
    if(layout.wake_on_media&&active&&!lastMediaActive){
      const mediaPage=layout.pages.find(p=>p.condition==='media');
      if(mediaPage){activePageId=mediaPage.id;render();scheduleCycle();}
    }else if(!isPageEligible(activePage())){
      const next=eligiblePages()[0];if(next){activePageId=next.id;render();scheduleCycle();}
    }
    lastMediaActive=active;
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
      reactToLiveState();renderScreen();renderPageDots();
    }catch(error){debug('Pi Display Live Data',error);}
    finally{if(btn){btn.disabled=false;btn.textContent='↻ Live';}}
  }

  async function loadLayout(){
    if(!mounted&&!mount())return;
    const serial=++loadSerial;
    const state=$('#displaySaveState');if(state)state.textContent='Loading…';
    try{
      const data=await api(`/api/ops/display?guild_id=${GUILD_ID}`);
      if(serial!==loadSerial)return;
      layout=normalize(data.layout);
      activePageId=layout.pages[0].id;
      if(state)state.textContent=data.updated_at?`Saved · ${data.updated_at}`:'Unsaved default';
      render();scheduleCycle();refreshLiveData();
    }catch(error){
      layout=clone(DEFAULT_LAYOUT);activePageId=layout.pages[0].id;
      if(state)state.textContent='Local fallback';
      render();scheduleCycle();debug('Pi Display Layout',error);
    }
  }

  async function saveLayout(){
    readControlsAndRender();
    const btn=$('#displaySave'),state=$('#displaySaveState');
    if(btn){btn.disabled=true;btn.textContent='Saving…';}
    try{
      await post('/api/ops/display',{guild_id:GUILD_ID,layout:clone(layout)});
      if(state)state.textContent=`Saved · ${new Date().toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'})}`;
      notify('OLED-Konfiguration gespeichert.');
    }catch(error){debug('Pi Display Save',error);notify(error.message||String(error),false);}
    finally{if(btn){btn.disabled=false;btn.textContent='Konfiguration speichern';}}
  }

  function resetLayout(){
    if(!confirm('OLED Builder auf den 0.96″ Auto-Standard zurücksetzen?'))return;
    layout=clone(DEFAULT_LAYOUT);activePageId=layout.pages[0].id;render();scheduleCycle();notify('0.96″ Auto-Standard geladen. Noch nicht gespeichert.');
  }

  async function copyLayout(){
    const text=JSON.stringify(layout,null,2);
    try{await navigator.clipboard.writeText(text);notify('Display JSON kopiert.');}
    catch(_){window.prompt('Display JSON kopieren:',text);}
  }

  function toggleFullscreen(){
    const workbench=$('.ops-display-workbench');if(!workbench)return;
    workbench.classList.toggle('fullscreen');
    document.body.classList.toggle('ops-display-fullscreen',workbench.classList.contains('fullscreen'));
    setTimeout(renderDevice,40);
  }

  function notify(message,ok=true){if(typeof note==='function')note(message,ok);}
  function debug(title,error){if(typeof window.showOpsDebug==='function')window.showOpsDebug(title,error,'Display Builder');}

  function install(){
    ensureStyles();
    if(!mount())return;
    window.loadDisplay=loadLayout;
    window.saveDisplay=saveLayout;
    window.renderDisplayPreview=render;
    window.renderDisplayWidgets=renderWidgetControls;
    window.toggleDisplayWidget=toggleWidget;
    const navButton=$('#nav [data-tab="hardware"]');
    if(navButton)navButton.addEventListener('click',()=>setTimeout(loadLayout,40));
    window.addEventListener('resize',()=>{if(mounted)renderDevice();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('.ops-display-workbench.fullscreen'))toggleFullscreen();});
    if(clockTimer)clearInterval(clockTimer);
    clockTimer=setInterval(()=>{
      if(!mounted)return;
      const section=$('#hardware');
      if(section&&section.classList.contains('active'))renderScreen();
    },1000);
    loadLayout();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});
  else install();
})();