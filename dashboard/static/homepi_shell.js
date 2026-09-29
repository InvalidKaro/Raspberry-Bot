(()=>{
  "use strict";

  const pages=[
    {path:"/",label:"Dashboard",description:"System, Git, Bot und Administration"},
    {path:"/ops",label:"Operations",description:"Analytics, Discord, Reliability und Hardware"},
    {path:"/control",label:"Control",description:"Health, Maintenance und Live Cogs"},
    {path:"/database-admin",label:"Database",description:"SQLite-Daten sicher verwalten"},
    {path:"/workspace",label:"Workspace",description:"Composer, Commands, Config und Plugins"},
    {path:"/workspace/studio",label:"Studio",description:"Search, Embeds und Workspace-Katalog"},
    {path:"/workspace/manage",label:"Data Manager",description:"Workspace-Datensätze bearbeiten"},
    {path:"/media",label:"Media",description:"Voice, Radio und Ambient-Library"},
    {path:"/meshtastic",label:"Meshtastic",description:"LoRa-Nodes, RF und Nachrichten"},
    {path:"/now-playing",label:"Now Playing",description:"Fullscreen Media-Status"},
    {path:"/status",label:"Status",description:"Reduzierter Service-Status"},
    {path:"/tools",label:"Tools",description:"Diagnostics, network, backups and maintenance"},
    {path:"/smart-home",label:"Smart Home",description:"Govee control, climate alerts and scheduled scenes"}
  ];

  const body=document.body;
  if(!body||!body.dataset.homepiPage)return;

  const path=location.pathname.replace(/\/$/,"")||"/";
  const ownsCommandShortcut=body.dataset.homepiPage==="ops";
  const isApple=/Mac|iPhone|iPad|iPod/i.test(navigator.platform||navigator.userAgent||"");
  const exact=p=>{
    if(p.path==="/")return path==="/";
    if(p.path==="/workspace")return path==="/workspace"||path.startsWith("/workspace/");
    if(p.path==="/media")return path==="/media"||path==="/now-playing";
    return path===p.path;
  };
  const navPages=pages.filter(p=>["/","/ops","/control","/workspace","/media","/smart-home","/meshtastic","/tools"].includes(p.path));

  const skip=document.createElement("a");
  skip.className="hp-skip";
  skip.href="#hp-main";
  skip.textContent="Zum Hauptinhalt";
  body.prepend(skip);

  let main=document.querySelector("main");
  if(!main){
    main=document.querySelector(".main");
  }
  if(main&&!main.id)main.id="hp-main";

  const bar=document.createElement("header");
  bar.className="hp-globalbar";
  bar.innerHTML=`
    <a class="hp-globalbar__brand" href="/" aria-label="HomePi Dashboard">
      <span class="hp-globalbar__mark" aria-hidden="true">HP</span>
      <span>HomePi</span>
    </a>
    <nav class="hp-globalbar__nav" aria-label="HomePi Hauptbereiche">
      ${navPages.map(p=>`<a href="${p.path}" ${exact(p)?'aria-current="page"':""}>${p.label}</a>`).join("")}
    </nav>
    <div class="hp-globalbar__actions">
      <a class="hp-connection" id="hp-connection" data-state="checking" href="/status" title="Statusseite öffnen">HomePi prüfen</a>
      <button class="hp-command-button" id="hp-command-open" type="button" aria-haspopup="dialog" aria-controls="hp-command">Switcher <kbd>${ownsCommandShortcut?"Click":isApple?"⌘K":"Ctrl K"}</kbd></button>
    </div>
  `;
  body.insertBefore(bar,body.children[1]||null);

  const mobileDock=document.createElement("nav");
  mobileDock.className="hp-mobile-dock";
  mobileDock.setAttribute("aria-label","HomePi mobile navigation");
  const dockPages=[
    {path:"/",label:"Home",icon:"⌂"},
    {path:"/ops",label:"Ops",icon:"◫"},
    {path:"/control",label:"Control",icon:"◉"},
    {path:"/smart-home",label:"Home",icon:"⌁"},
    {path:"/media",label:"Media",icon:"▶"}
  ];
  mobileDock.innerHTML=dockPages.map(page=>`<a href="${page.path}" ${exact(page)?'aria-current="page"':""}><span aria-hidden="true">${page.icon}</span><small>${page.label}</small></a>`).join("");
  body.append(mobileDock);

  const backdrop=document.createElement("div");
  backdrop.className="hp-command-backdrop";
  backdrop.id="hp-command";
  backdrop.dataset.open="false";
  backdrop.setAttribute("role","dialog");
  backdrop.setAttribute("aria-modal","true");
  backdrop.setAttribute("aria-label","HomePi Bereich wechseln");
  backdrop.innerHTML=`
    <section class="hp-command">
      <input class="hp-command__search" id="hp-command-search" type="search" autocomplete="off" spellcheck="false" placeholder="Bereich öffnen …" aria-label="HomePi Bereich suchen">
      <div class="hp-command__results" id="hp-command-results"></div>
      <div class="hp-command__foot">↑ ↓ auswählen · Enter öffnen · Esc schließen</div>
    </section>
  `;
  body.append(backdrop);

  const search=backdrop.querySelector("#hp-command-search");
  const results=backdrop.querySelector("#hp-command-results");
  const openButton=bar.querySelector("#hp-command-open");
  let visible=[...pages];
  let activeIndex=0;
  let lastFocus=null;

  const escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[char]);

  const render=()=>{
    const q=search.value.trim().toLowerCase();
    visible=pages.filter(p=>!q||p.label.toLowerCase().includes(q)||p.description.toLowerCase().includes(q)||p.path.toLowerCase().includes(q));
    activeIndex=Math.min(activeIndex,Math.max(visible.length-1,0));
    if(!visible.length){
      results.innerHTML='<div class="hp-command__empty">Kein Bereich gefunden.</div>';
      return;
    }
    results.innerHTML=visible.map((p,index)=>`
      <button class="hp-command__item" type="button" data-index="${index}" data-active="${index===activeIndex}">
        <span><strong>${escapeHtml(p.label)}</strong><small>${escapeHtml(p.description)}</small></span>
        <span>${escapeHtml(p.path)}</span>
      </button>
    `).join("");
    results.querySelectorAll(".hp-command__item").forEach(button=>{
      button.addEventListener("mouseenter",()=>{activeIndex=Number(button.dataset.index);render();});
      button.addEventListener("click",()=>navigate(Number(button.dataset.index)));
    });
  };

  const setOpen=open=>{
    backdrop.dataset.open=String(open);
    if(open){
      lastFocus=document.activeElement;
      search.value="";
      activeIndex=0;
      render();
      requestAnimationFrame(()=>search.focus());
    }else{
      lastFocus?.focus?.();
    }
  };

  const navigate=index=>{
    const target=visible[index];
    if(target)location.href=target.path;
  };

  openButton.addEventListener("click",()=>setOpen(true));
  backdrop.addEventListener("mousedown",event=>{
    if(event.target===backdrop)setOpen(false);
  });
  search.addEventListener("input",()=>{activeIndex=0;render();});
  search.addEventListener("keydown",event=>{
    if(event.key==="ArrowDown"){
      event.preventDefault();
      activeIndex=Math.min(activeIndex+1,visible.length-1);
      render();
      results.querySelector('[data-active="true"]')?.scrollIntoView({block:"nearest"});
    }else if(event.key==="ArrowUp"){
      event.preventDefault();
      activeIndex=Math.max(activeIndex-1,0);
      render();
      results.querySelector('[data-active="true"]')?.scrollIntoView({block:"nearest"});
    }else if(event.key==="Enter"){
      event.preventDefault();
      navigate(activeIndex);
    }else if(event.key==="Escape"){
      event.preventDefault();
      setOpen(false);
    }
  });

  document.addEventListener("keydown",event=>{
    if(!ownsCommandShortcut&&(event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){
      event.preventDefault();
      setOpen(backdrop.dataset.open!=="true");
    }else if(event.key==="Escape"&&backdrop.dataset.open==="true"){
      setOpen(false);
    }
  });

  const connection=bar.querySelector("#hp-connection");
  const setConnection=(state,text)=>{
    connection.dataset.state=state;
    connection.textContent=text;
  };

  const checkHealth=async()=>{
    if(!navigator.onLine){
      setConnection("offline","Browser offline");
      return;
    }
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),4500);
    try{
      const response=await fetch("/health",{cache:"no-store",signal:controller.signal});
      if(!response.ok)throw new Error(String(response.status));
      const data=await response.json();
      setConnection("online",data.version?`HomePi v${data.version}`:"HomePi online");
    }catch{
      setConnection("offline","HomePi nicht erreichbar");
    }finally{
      clearTimeout(timer);
    }
  };

  window.addEventListener("online",checkHealth);
  window.addEventListener("offline",()=>setConnection("offline","Browser offline"));
  checkHealth();
  setInterval(()=>{if(!document.hidden)checkHealth();},90000);
})();