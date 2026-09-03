(() => {
  'use strict';

  const GUILD_ID = '1162733312226361454';
  const $ = (selector, root = document) => root.querySelector(selector);

  const PAGES = [
    {id:'clock', name:'Uhrzeit', mode:'focus', widgets:['clock']},
    {id:'system', name:'System', mode:'split', widgets:['temperature','ram']},
    {id:'performance', name:'Leistung', mode:'split', widgets:['cpu','uptime']},
    {id:'network', name:'Netzwerk', mode:'split', widgets:['network','pihole']},
    {id:'media', name:'Media', mode:'focus', widgets:['nowplaying']}
  ];

  const WIDGETS = {
    clock:{label:'Uhrzeit', icon:'◷'},
    temperature:{label:'Temperatur', icon:'♨'},
    ram:{label:'RAM', icon:'▥'},
    cpu:{label:'CPU', icon:'⌁'},
    uptime:{label:'Uptime', icon:'↟'},
    network:{label:'Netzwerk', icon:'⌁'},
    pihole:{label:'Pi-hole', icon:'◉'},
    nowplaying:{label:'Now Playing', icon:'♫'}
  };

  const DEFAULT_LAYOUT = {
    version: 5,
    profile: 'oled096blue',
    width: 128,
    height: 64,
    controller: 'ssd1306',
    bus: 'i2c',
    i2c_address: '0x3C',
    pixel_color: 'blue',
    rotation: 0,
    brightness: 90,
    refresh_seconds: 10,
    page_seconds: 5,
    show_labels: true,
    show_footer: true,
    media_priority: true,
    alert_priority: true,
    auto_cycle: true,
    pages: []
  };

  let layout = normalize(DEFAULT_LAYOUT);
  let liveData = {summary:null, media:null};
  let mounted = false;
  let activePageIndex = 0;
  let cycleTimer = null;
  let liveTimer = null;
  let clockTimer = null;
  let lastMediaActive = false;
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
    const total=Math.max(0,Number(seconds)||0);
    const days=Math.floor(total/86400);
    const hours=Math.floor((total%86400)/3600);
    const minutes=Math.floor((total%3600)/60);
    if(days)return `${days}d ${hours}h`;
    if(hours)return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }
  function generatedPages(pageSeconds){
    return PAGES.map(page=>({...clone(page),duration:pageSeconds,condition:'always'}));
  }
  function normalize(raw){
    const source=raw&&typeof raw==='object'?raw:{};
    const pageSeconds=clamp(source.page_seconds ?? source.default_duration,2,30,5);
    return {
      ...clone(DEFAULT_LAYOUT),
      i2c_address:['0x3C','0x3D'].includes(String(source.i2c_address))?String(source.i2c_address):'0x3C',
      rotation:[0,180].includes(Number(source.rotation))?Number(source.rotation):0,
      brightness:clamp(source.brightness,10,100,90),
      refresh_seconds:clamp(source.refresh_seconds,5,120,10),
      page_seconds:pageSeconds,
      show_labels:source.show_labels!==false,
      show_footer:source.show_footer!==false,
      media_priority:source.media_priority ?? source.wake_on_media ?? true,
      alert_priority:source.alert_priority!==false,
      auto_cycle:true,
      pages:generatedPages(pageSeconds),
      version:5
    };
  }
  function currentPage(){return layout.pages[activePageIndex]||layout.pages[0];}
  function systemData(){return liveData.summary?.system||{};}
  function runtimeData(){return liveData.media?.runtime||{};}
  function mediaActive(){
    const runtime=runtimeData();
    const current=runtime.youtube?.current||{};
    const voice=runtime.voice||{};
    return Boolean(current.title||current.url||voice.title||voice.station||voice.kind==='radio'||voice.playing);
  }

  function ensureStyles(){
    if(!document.getElementById('ops-display-builder-v4-style')){
      const link=document.createElement('link');
      link.id='ops-display-builder-v4-style';
      link.rel='stylesheet';
      link.href='/static/display_builder_v4.css';
      document.head.appendChild(link);
    }
    if(!document.getElementById('ops-mobile-nav-v2-style')){
      const link=document.createElement('link');
      link.id='ops-mobile-nav-v2-style';
      link.rel='stylesheet';
      link.href='/static/ops_mobile_nav.css';
      document.head.appendChild(link);
    }
  }

  function mount(){
    const anchor=$('#displayRotation');
    if(!anchor)return false;
    const card=anchor.closest('.card');
    if(!card)return false;
    card.classList.remove('half');
    card.classList.add('full','ops-display-studio-card');
    const head=$('.head',card);
    if(head)head.innerHTML='<div><h2>0.96″ Blue OLED</h2><div class="tiny">128×64 · SSD1306 · automatisch · ohne Tasten</div></div><div class="ops-display-head-actions"><span class="ops-display-save-state" id="displaySaveState">Preview</span><button type="button" id="displayRefreshLive">↻ Live</button></div>';
    const body=$('.body',card);
    if(!body)return false;

    body.innerHTML=`
      <div class="ops-display-simple">
        <aside class="ops-display-simple-controls">
          <section class="ops-display-simple-section">
            <div class="ops-display-simple-title"><span>OLED</span><small>0.96″ · 128×64 · blau</small></div>
            <div class="ops-display-simple-hardware"><i></i><div><b>SSD1306 I²C</b><span>schwarzer Hintergrund · blaue Pixel</span></div></div>
            <div class="ops-display-two">
              <label>I²C<select id="displayAddress"><option value="0x3C">0x3C</option><option value="0x3D">0x3D</option></select></label>
              <label>Drehung<select id="displayRotation"><option value="0">0°</option><option value="180">180°</option></select></label>
            </div>
            <label>Helligkeit <b id="displayBrightnessValue">90%</b><input id="displayBrightness" type="range" min="10" max="100" value="90"></label>
          </section>

          <section class="ops-display-simple-section">
            <div class="ops-display-simple-title"><span>Automatik</span><small>alle Infos werden gezeigt</small></div>
            <div class="ops-display-two">
              <label>Seitenwechsel<input id="displayPageSeconds" type="number" min="2" max="30" value="5"><em>Sekunden</em></label>
              <label>Daten aktualisieren<input id="displayRefresh" type="number" min="5" max="120" value="10"><em>Sekunden</em></label>
            </div>
            <label class="ops-display-switch"><input id="displayMediaPriority" type="checkbox" checked><span>Bei Radio / YouTube sofort Media anzeigen</span></label>
            <label class="ops-display-switch"><input id="displayAlertPriority" type="checkbox" checked><span>Bei hoher CPU / Temperatur passende Seite priorisieren</span></label>
            <label class="ops-display-switch"><input id="displayLabels" type="checkbox" checked><span>Kleine Bezeichnungen anzeigen</span></label>
            <label class="ops-display-switch"><input id="displayFooter" type="checkbox" checked><span>Seitenname unten anzeigen</span></label>
          </section>

          <section class="ops-display-simple-section">
            <div class="ops-display-simple-title"><span>Ablauf</span><small>fest und vollständig</small></div>
            <div id="displaySequence" class="ops-display-sequence"></div>
            <div class="ops-display-simple-note">Jeder Kernwert kommt mindestens einmal pro Durchlauf vor. Es gibt keine manuellen Display-Tasten und keine versteckten Seiten.</div>
          </section>

          <div class="ops-display-simple-actions">
            <button type="button" class="primary" id="displaySave">Speichern</button>
            <button type="button" id="displayReset">Standard</button>
          </div>
        </aside>

        <section class="ops-display-simple-preview">
          <div class="ops-display-simple-previewbar">
            <div><b>Echte Proportion</b><span>128×64 px · 2:1 · vergrößerte Vorschau</span></div>
            <div><span class="ops-display-live-dot"></span><strong id="displayCycleState">AUTO</strong><button type="button" id="displayFullscreen">⛶</button></div>
          </div>
          <div class="ops-display-stage" id="displayStage">
            <div class="ops-display-device" id="displayDevice" data-profile="oled096blue" data-micro="true">
              <div class="ops-display-bezel">
                <div id="displayPreview" class="ops-display-screen" data-theme="blue-oled" data-micro="true">
                  <div class="ops-display-screen-grid" id="displayScreenGrid"></div>
                  <div class="ops-display-screen-footer" id="displayScreenFooter"><span id="displayPageName">UHRZEIT</span><span id="displayScreenMeta">1 / 5</span></div>
                </div>
              </div>
            </div>
          </div>
          <div class="ops-display-passive-dots" id="displayPageDots"></div>
          <div class="ops-display-simple-inspector">
            <div><span>Panel</span><b>0.96″</b></div>
            <div><span>Auflösung</span><b>128×64</b></div>
            <div><span>Seite</span><b id="displayInspectorPage">1/5</b></div>
            <div><span>Wechsel</span><b id="displayInspectorSpeed">5s</b></div>
          </div>
        </section>
      </div>`;

    bind();
    mounted=true;
    return true;
  }

  function bind(){
    ['displayAddress','displayRotation','displayPageSeconds','displayRefresh'].forEach(id=>{
      const el=$('#'+id);if(el)el.addEventListener('change',readControlsAndRender);
    });
    const brightness=$('#displayBrightness');if(brightness)brightness.addEventListener('input',readControlsAndRender);
    ['displayMediaPriority','displayAlertPriority','displayLabels','displayFooter'].forEach(id=>{
      const el=$('#'+id);if(el)el.addEventListener('change',readControlsAndRender);
    });
    $('#displaySave').onclick=saveLayout;
    $('#displayReset').onclick=resetLayout;
    $('#displayRefreshLive').onclick=refreshLiveData;
    $('#displayFullscreen').onclick=toggleFullscreen;
  }

  function readControlsAndRender(){
    layout.i2c_address=$('#displayAddress')?.value||'0x3C';
    layout.rotation=Number($('#displayRotation')?.value||0);
    layout.brightness=clamp($('#displayBrightness')?.value,10,100,90);
    layout.page_seconds=clamp($('#displayPageSeconds')?.value,2,30,5);
    layout.refresh_seconds=clamp($('#displayRefresh')?.value,5,120,10);
    layout.media_priority=Boolean($('#displayMediaPriority')?.checked);
    layout.alert_priority=Boolean($('#displayAlertPriority')?.checked);
    layout.show_labels=Boolean($('#displayLabels')?.checked);
    layout.show_footer=Boolean($('#displayFooter')?.checked);
    layout.pages=generatedPages(layout.page_seconds);
    render();
    scheduleCycle();
    scheduleLiveRefresh();
  }

  function syncControls(){
    if($('#displayAddress'))$('#displayAddress').value=layout.i2c_address;
    if($('#displayRotation'))$('#displayRotation').value=String(layout.rotation);
    if($('#displayBrightness'))$('#displayBrightness').value=String(layout.brightness);
    if($('#displayBrightnessValue'))$('#displayBrightnessValue').textContent=`${layout.brightness}%`;
    if($('#displayPageSeconds'))$('#displayPageSeconds').value=String(layout.page_seconds);
    if($('#displayRefresh'))$('#displayRefresh').value=String(layout.refresh_seconds);
    if($('#displayMediaPriority'))$('#displayMediaPriority').checked=layout.media_priority;
    if($('#displayAlertPriority'))$('#displayAlertPriority').checked=layout.alert_priority;
    if($('#displayLabels'))$('#displayLabels').checked=layout.show_labels;
    if($('#displayFooter'))$('#displayFooter').checked=layout.show_footer;
  }

  function widgetData(key){
    const system=systemData();
    const runtime=runtimeData();
    const voice=runtime.voice||{};
    const current=runtime.youtube?.current||{};
    const now=new Date();
    const map={
      clock:{value:now.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}),sub:now.toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit'})},
      temperature:{value:system.temperature_c==null?'—':`${Math.round(Number(system.temperature_c))}°`,sub:'TEMP'},
      ram:{value:system.memory_percent==null?'—':`${Math.round(Number(system.memory_percent))}%`,sub:'RAM'},
      cpu:{value:system.cpu_percent==null?'—':`${Math.round(Number(system.cpu_percent))}%`,sub:'CPU'},
      uptime:{value:fmtUptime(system.uptime_seconds),sub:'UPTIME'},
      network:{value:'ON',sub:'NET'},
      pihole:{value:system.pihole?.active?'ON':'—',sub:'PI-HOLE'},
      nowplaying:{value:current.title||voice.title||voice.station||'Nichts läuft',sub:voice.channel_name||'MEDIA'}
    };
    return map[key]||{value:'—',sub:String(key).toUpperCase()};
  }

  function renderSequence(){
    const root=$('#displaySequence');if(!root)return;
    root.innerHTML=layout.pages.map((page,index)=>`<div class="ops-display-sequence-item ${index===activePageIndex?'active':''}"><span>${index+1}</span><div><b>${esc(page.name)}</b><small>${page.widgets.map(key=>WIDGETS[key]?.label||key).join(' · ')}</small></div><em>${layout.page_seconds}s</em></div>`).join('');
  }

  function renderScreen(){
    const page=currentPage();
    const grid=$('#displayScreenGrid');const screen=$('#displayPreview');
    if(!page||!grid||!screen)return;
    screen.dataset.layout=page.mode;
    screen.dataset.labels=layout.show_labels?'true':'false';
    screen.style.setProperty('--display-brightness',String(layout.brightness/100));

    grid.innerHTML=page.widgets.map(key=>{
      const def=WIDGETS[key]||{label:key,icon:'◇'};
      const data=widgetData(key);
      return `<article class="ops-screen-widget${page.mode==='focus'?' wide':''}" data-widget="${key}"><div class="ops-screen-widget-top"><span>${def.icon}</span><small>${esc(def.label)}</small></div><strong>${esc(data.value)}</strong><em>${esc(data.sub)}</em>${key==='cpu'||key==='ram'?`<div class="ops-screen-meter"><i style="width:${parseFloat(data.value)||0}%"></i></div>`:''}</article>`;
    }).join('');

    const footer=$('#displayScreenFooter');if(footer)footer.style.display=layout.show_footer?'flex':'none';
    if($('#displayPageName'))$('#displayPageName').textContent=page.name.toUpperCase().slice(0,12);
    if($('#displayScreenMeta'))$('#displayScreenMeta').textContent=`${activePageIndex+1} / ${layout.pages.length}`;
  }

  function renderDevice(){
    const stage=$('#displayStage'),device=$('#displayDevice');if(!stage||!device)return;
    const maxWidth=Math.min(560,Math.max(280,stage.clientWidth-36));
    const width=Math.min(512,maxWidth);
    device.style.width=`${width}px`;
    device.style.aspectRatio='2 / 1';
    device.dataset.rotation=String(layout.rotation);
  }

  function renderDots(){
    const root=$('#displayPageDots');if(!root)return;
    root.innerHTML=layout.pages.map((_,index)=>`<span class="${index===activePageIndex?'active':''}"></span>`).join('');
  }

  function renderInspector(){
    if($('#displayInspectorPage'))$('#displayInspectorPage').textContent=`${activePageIndex+1}/${layout.pages.length}`;
    if($('#displayInspectorSpeed'))$('#displayInspectorSpeed').textContent=`${layout.page_seconds}s`;
    if($('#displayCycleState'))$('#displayCycleState').textContent='AUTO';
  }

  function render(){
    if(!mounted)return;
    if(activePageIndex<0||activePageIndex>=layout.pages.length)activePageIndex=0;
    syncControls();
    renderSequence();
    renderDevice();
    renderScreen();
    renderDots();
    renderInspector();
  }

  function scheduleCycle(){
    if(cycleTimer)clearTimeout(cycleTimer);
    cycleTimer=setTimeout(()=>{
      activePageIndex=(activePageIndex+1)%layout.pages.length;
      render();
      scheduleCycle();
    },layout.page_seconds*1000);
  }

  function jumpToPage(id){
    const index=layout.pages.findIndex(page=>page.id===id);
    if(index<0||index===activePageIndex)return;
    activePageIndex=index;
    render();
    scheduleCycle();
  }

  function reactToLiveState(){
    const system=systemData();
    const media=mediaActive();
    if(layout.media_priority&&media&&!lastMediaActive){
      jumpToPage('media');
    }else if(layout.alert_priority&&Number(system.temperature_c||0)>=75){
      jumpToPage('system');
    }else if(layout.alert_priority&&Number(system.cpu_percent||0)>=85){
      jumpToPage('performance');
    }
    lastMediaActive=media;
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
      reactToLiveState();
      renderScreen();
    }catch(error){debug('Pi Display Live Data',error);}
    finally{if(btn){btn.disabled=false;btn.textContent='↻ Live';}}
  }

  function scheduleLiveRefresh(){
    if(liveTimer)clearInterval(liveTimer);
    liveTimer=setInterval(refreshLiveData,layout.refresh_seconds*1000);
  }

  async function loadLayout(){
    if(!mounted&&!mount())return;
    const serial=++loadSerial;
    const state=$('#displaySaveState');if(state)state.textContent='Lädt…';
    try{
      const data=await api(`/api/ops/display?guild_id=${GUILD_ID}`);
      if(serial!==loadSerial)return;
      layout=normalize(data.layout);
      activePageIndex=0;
      if(state)state.textContent=data.updated_at?'Gespeichert':'Standard';
      render();
      scheduleCycle();
      scheduleLiveRefresh();
      refreshLiveData();
    }catch(error){
      layout=normalize(DEFAULT_LAYOUT);
      activePageIndex=0;
      if(state)state.textContent='Lokaler Standard';
      render();
      scheduleCycle();
      scheduleLiveRefresh();
      debug('Pi Display Layout',error);
    }
  }

  async function saveLayout(){
    readControlsAndRender();
    const btn=$('#displaySave'),state=$('#displaySaveState');
    if(btn){btn.disabled=true;btn.textContent='Speichert…';}
    try{
      await post('/api/ops/display',{guild_id:GUILD_ID,layout:clone(layout)});
      if(state)state.textContent='Gespeichert';
      notify('OLED-Konfiguration gespeichert.');
    }catch(error){debug('Pi Display Save',error);notify(error.message||String(error),false);}
    finally{if(btn){btn.disabled=false;btn.textContent='Speichern';}}
  }

  function resetLayout(){
    if(!confirm('OLED-Einstellungen auf Standard zurücksetzen?'))return;
    layout=normalize(DEFAULT_LAYOUT);
    activePageIndex=0;
    render();
    scheduleCycle();
    scheduleLiveRefresh();
    notify('Standard geladen. Noch nicht gespeichert.');
  }

  function toggleFullscreen(){
    const preview=$('.ops-display-simple-preview');if(!preview)return;
    preview.classList.toggle('fullscreen');
    document.body.classList.toggle('ops-display-fullscreen',preview.classList.contains('fullscreen'));
    setTimeout(renderDevice,40);
  }

  function notify(message,ok=true){if(typeof note==='function')note(message,ok);}
  function debug(title,error){if(typeof window.showOpsDebug==='function')window.showOpsDebug(title,error,'Display');}

  function install(){
    ensureStyles();
    if(!mount())return;
    window.loadDisplay=loadLayout;
    window.saveDisplay=saveLayout;
    window.renderDisplayPreview=render;
    window.renderDisplayWidgets=renderSequence;
    window.toggleDisplayWidget=()=>{};

    const navButton=$('#nav [data-tab="hardware"]');
    if(navButton)navButton.addEventListener('click',()=>setTimeout(loadLayout,40));
    window.addEventListener('resize',()=>{if(mounted)renderDevice();});
    document.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&$('.ops-display-simple-preview.fullscreen'))toggleFullscreen();
    });

    if(clockTimer)clearInterval(clockTimer);
    clockTimer=setInterval(()=>{
      if(!mounted)return;
      const page=currentPage();
      if(page&&page.widgets.includes('clock'))renderScreen();
    },1000);

    loadLayout();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});
  else install();
})();