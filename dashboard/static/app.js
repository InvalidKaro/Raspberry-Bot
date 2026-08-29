(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let csrf = "";
  let botService = "raspberry-bot";
  let currentFile = "";
  let allFiles = [];
  let lastStatus = null;
  let toastTimer = null;

  function toast(message, ok = true) {
    const node = $("toast");
    node.textContent = String(message || (ok ? "Done." : "Action failed."));
    node.className = ok ? "show good" : "show bad";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.className = ""; }, 3200);
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if ((options.method || "GET").toUpperCase() !== "GET" && csrf) headers.set("X-CSRF-Token", csrf);
    const response = await fetch(path, { ...options, headers });
    let data;
    try { data = await response.json(); } catch { data = { ok: false, message: `HTTP ${response.status}` }; }
    if (response.status === 401) { window.location.href = "/login"; throw new Error("Session expired."); }
    return { response, data };
  }

  async function bootstrap() {
    const { response, data } = await request("/api/bootstrap");
    if (!response.ok || !data.ok) throw new Error(data.message || "Dashboard bootstrap failed.");
    csrf = String(data.csrf || "");
    botService = String(data.bot_service || "raspberry-bot");
    $("bot-service-name").textContent = botService;
  }

  function percentBar(id, value) { const v = Math.max(0, Math.min(100, Number(value) || 0)); $(id).style.width = `${v}%`; }
  function uptime(seconds) { let s=Math.max(0,Number(seconds)||0);const d=Math.floor(s/86400);s%=86400;const h=Math.floor(s/3600);s%=3600;return `${d}d ${h}h ${Math.floor(s/60)}m`; }
  function rawPathFromStatus(line) { const text=String(line||""); return text.length >= 4 ? text.slice(3).trim().split(" -> ").pop() : text.trim(); }

  function renderStatus(data) {
    lastStatus = data;
    const system=data.system||{}, git=data.git||{}, online=Boolean(system.bot_active);
    $("bot-status").textContent=online?"Online":"Offline";
    $("bot-pill").className=`status-pill ${online?"online":"offline"}`;
    $("bot-pill").querySelector("span:last-child").textContent=online?"Bot online":"Bot offline";
    const p=system.pihole||{};
    $("pihole-status").textContent=!p.installed?"Not detected":p.active?"Online":"Offline";
    $("pihole-detail").textContent=p.blocking===true?"Blocking enabled":p.blocking===false?"Blocking disabled":"Blocking state unknown";
    const cpu=Number(system.cpu_percent)||0; $("cpu").textContent=`${cpu.toFixed(1)}%`; percentBar("cpu-bar",cpu);
    const temp=Number(system.temperature_c); $("temp").textContent=Number.isFinite(temp)?`${temp.toFixed(1)}°C`:"N/A"; percentBar("temp-bar",Number.isFinite(temp)?temp/85*100:0);
    const ram=Number(system.memory_percent)||0; $("ram").textContent=`${ram.toFixed(1)}%`; $("ram-detail").textContent=`${system.memory_used_mb??"—"} / ${system.memory_total_mb??"—"} MB`; percentBar("ram-bar",ram);
    const disk=Number(system.disk_percent)||0; $("disk").textContent=`${disk.toFixed(1)}%`; $("disk-detail").textContent=`${system.disk_used_gb??"—"} / ${system.disk_total_gb??"—"} GB`; percentBar("disk-bar",disk);
    $("uptime").textContent=uptime(system.uptime_seconds); $("load").textContent=Array.isArray(system.load_average)?system.load_average.join(" / "):"—"; $("net-rx").textContent=`${system.network_rx_mb??"—"} MB`; $("net-tx").textContent=`${system.network_tx_mb??"—"} MB`;
    $("overview-branch").textContent=`Git · ${git.ok?git.branch:"Unavailable"}`; $("overview-change-count").textContent=git.ok?`${git.changes.length} changes`:"—"; $("overview-commit").textContent=git.last_commit||git.message||"—"; $("overview-git-summary").textContent=git.ok?(git.dirty?"Working tree has local changes":"Working tree clean"):"Repository unavailable";
    renderGit(git);
  }

  function renderGit(git) {
    $("git-branch-title").textContent=`Branch ${git.ok?git.branch:"—"}`;
    $("git-commit-detail").textContent=git.last_commit||git.message||"—";
    $("change-count").textContent=`${git.ok?git.changes.length:0} changes`;
    const box=$("commit-files"); box.innerHTML="";
    const paths=[];
    if (git.ok) for (const line of git.changes) { const path=rawPathFromStatus(line); if(!path)continue; paths.push(path); const label=document.createElement("label");label.className="check-row";const input=document.createElement("input");input.type="checkbox";input.value=path;input.checked=true;const span=document.createElement("span");span.textContent=line;label.append(input,span);box.append(label); }
    if (!paths.length) box.innerHTML='<p class="muted">No changes to commit.</p>';
  }

  async function refreshStatus(show=false) { const b=$("refresh-button");b.disabled=true;try{const {response,data}=await request("/api/status");if(!response.ok||!data.ok)throw new Error(data.message||"Status failed.");renderStatus(data);if(show)toast("Status refreshed.");}catch(e){toast(e.message,false);}finally{b.disabled=false;} }
  async function botAction(action,button){if(action==="stop"&&!confirm("Stop Raspberry-Bot?"))return;button.disabled=true;try{const {response,data}=await request(`/api/bot/${action}`,{method:"POST"});toast(data.message,response.ok&&data.ok);setTimeout(()=>refreshStatus(false),800);}catch(e){toast(e.message,false);}finally{button.disabled=false;} }
  async function loadLogs(){const b=$("logs-refresh");b.disabled=true;$("log-output").textContent="Loading…";try{const {response,data}=await request("/api/logs");if(!response.ok||!data.ok)throw new Error(data.logs||data.message||"Log read failed.");$("log-output").textContent=data.logs||"No logs.";}catch(e){$("log-output").textContent=e.message;toast(e.message,false);}finally{b.disabled=false;} }

  async function loadFiles(){try{const {response,data}=await request("/api/editor/files");if(!response.ok||!data.ok)throw new Error(data.message||"File list failed.");allFiles=data.files||[];renderFiles();}catch(e){$("file-list").innerHTML=`<p class="muted pad">${e.message}</p>`;} }
  function renderFiles(){const query=$("file-filter").value.trim().toLowerCase(),box=$("file-list");box.innerHTML="";for(const item of allFiles){if(query&&!item.path.toLowerCase().includes(query))continue;const b=document.createElement("button");b.type="button";b.className=`file-button ${item.path===currentFile?"active":""}`;b.textContent=item.path;b.title=item.path;b.addEventListener("click",()=>openFile(item.path));box.append(b);}if(!box.children.length)box.innerHTML='<p class="muted pad">No matching files.</p>'; }
  async function openFile(path){try{const {response,data}=await request(`/api/editor/read?path=${encodeURIComponent(path)}`);if(!response.ok||!data.ok)throw new Error(data.message||"Could not open file.");currentFile=data.path;$("editor-path").textContent=currentFile;$("code-editor").value=data.content;$("code-editor").disabled=false;$("save-code").disabled=false;$("validate-code").disabled=false;$("editor-message").textContent=`${data.size} bytes`;updateEditorPosition();renderFiles();}catch(e){toast(e.message,false);} }
  function updateEditorPosition(){const area=$("code-editor"),pos=area.selectionStart||0,before=area.value.slice(0,pos),lines=before.split("\n");$("editor-position").textContent=`Ln ${lines.length}, Col ${lines[lines.length-1].length+1} · ${area.value.length} chars`; }
  async function validateCode(){if(!currentFile)return;const {response,data}=await request("/api/editor/validate",{method:"POST",body:JSON.stringify({path:currentFile,content:$("code-editor").value})});$("editor-message").textContent=data.message||"Validation finished.";toast(data.message,response.ok&&data.ok);}
  async function saveCode(){if(!currentFile)return;const b=$("save-code");b.disabled=true;try{const {response,data}=await request("/api/editor/save",{method:"POST",body:JSON.stringify({path:currentFile,content:$("code-editor").value})});$("editor-message").textContent=data.message||"Save finished.";toast(data.message,response.ok&&data.ok);if(response.ok&&data.ok){await refreshStatus(false);await refreshDiff();}}finally{b.disabled=false;} }

  async function refreshDiff(){try{const {response,data}=await request("/api/git/diff");$("git-diff").textContent=data.diff||data.message||"No diff.";if(!response.ok)toast(data.message||"Diff failed.",false);}catch(e){$("git-diff").textContent=e.message;} }
  async function gitAction(action,button){if(!confirm(`Run git ${action}?`))return;button.disabled=true;try{const {response,data}=await request(`/api/git/${action}`,{method:"POST"});toast(data.message,response.ok&&data.ok);await refreshStatus(false);await refreshDiff();}finally{button.disabled=false;} }
  async function commitSelected(){const paths=[...document.querySelectorAll("#commit-files input:checked")].map(i=>i.value),message=$("commit-message").value.trim();const b=$("commit-button");b.disabled=true;try{const {response,data}=await request("/api/git/commit",{method:"POST",body:JSON.stringify({message,paths})});toast(data.message,response.ok&&data.ok);if(response.ok){$("commit-message").value="";await refreshStatus(false);await refreshDiff();}}finally{b.disabled=false;} }

  async function loadGuilds(){try{const {response,data}=await request("/api/discord/guilds");if(!response.ok||!data.ok)throw new Error(data.message||"Discord guild lookup failed.");const select=$("guild-select");select.innerHTML='<option value="">Choose a server…</option>';for(const g of data.guilds){const o=document.createElement("option");o.value=g.id;o.textContent=g.name;select.append(o);}}catch(e){$("guild-select").innerHTML='<option value="">Could not load servers</option>';$("config-status").textContent=e.message;}}
  function fillSelect(id,rows,filterType,current,emptyLabel="Disabled / not set"){const s=$(id);s.innerHTML="";const e=document.createElement("option");e.value="";e.textContent=emptyLabel;s.append(e);for(const row of rows){if(filterType!==null&&row.type!==filterType)continue;const o=document.createElement("option");o.value=row.id;o.textContent=`# ${row.name}`;if(String(current||"")===String(row.id))o.selected=true;s.append(o);}}
  async function loadGuildConfig(){const guild=$("guild-select").value;if(!guild){$("save-config").disabled=true;return;}$("config-status").textContent="Loading configuration…";try{const [resA,resB]=await Promise.all([request(`/api/discord/guilds/${guild}`),request(`/api/config/${guild}`)]);if(!resA.response.ok||!resA.data.ok)throw new Error(resA.data.message||"Discord resources failed.");if(!resB.response.ok||!resB.data.ok)throw new Error(resB.data.message||"Configuration failed.");const r=resA.data,c=resB.data.config||{};fillSelect("ticket-category",r.channels,4,c.ticket_category_id);fillSelect("ticket-log",r.channels,0,c.ticket_log_channel_id);fillSelect("welcome-channel",r.channels,0,c.welcome_channel_id,"Disabled");fillSelect("suggestion-channel",r.channels,0,c.suggestion_channel_id,"Disabled");fillSelect("audit-channel",r.channels,0,c.general_log_channel_id,"Disabled");const roles=$("staff-roles");roles.innerHTML="";const selected=new Set((c.staff_roles||[]).map(x=>String(x.role_id)));for(const role of r.roles){const o=document.createElement("option");o.value=role.id;o.textContent=role.name;o.selected=selected.has(String(role.id));roles.append(o);}const color=Number(c.embed_color);$("embed-color").value=Number.isFinite(color)&&color>=0?`#${color.toString(16).padStart(6,"0").toUpperCase()}`:"";$("save-config").disabled=false;$("config-status").textContent="Configuration loaded.";}catch(e){$("save-config").disabled=true;$("config-status").textContent=e.message;toast(e.message,false);}}
  async function saveGuildConfig(){const guild=$("guild-select").value;if(!guild)return;const selected=[...$("staff-roles").selectedOptions].map(o=>({role_id:o.value,permission_level:10}));const payload={embed_color:$("embed-color").value,ticket_category_id:$("ticket-category").value,ticket_log_channel_id:$("ticket-log").value,welcome_channel_id:$("welcome-channel").value,suggestion_channel_id:$("suggestion-channel").value,general_log_channel_id:$("audit-channel").value,staff_roles:selected};const b=$("save-config");b.disabled=true;$("config-status").textContent="Saving and restarting bot…";try{const {response,data}=await request(`/api/config/${guild}`,{method:"POST",body:JSON.stringify(payload)});$("config-status").textContent=data.message||"Finished.";toast(data.message,response.ok&&data.ok);setTimeout(()=>refreshStatus(false),1000);}finally{b.disabled=false;} }

  async function deploy(){if(!confirm("Validate the Python project and restart Raspberry-Bot?"))return;const b=$("deploy-button");b.disabled=true;$("deploy-output").textContent="Running preflight and deploy…";try{const {response,data}=await request("/api/deploy",{method:"POST"});$("deploy-output").textContent=data.message||"Deploy finished.";toast(data.message,response.ok&&data.ok);setTimeout(()=>refreshStatus(false),800);}finally{b.disabled=false;} }
  async function rollback(){if(!confirm("Rollback code to the commit stored before the last dashboard deploy? This uses git reset --hard and requires a clean working tree."))return;const b=$("rollback-button");b.disabled=true;try{const {response,data}=await request("/api/rollback",{method:"POST"});$("deploy-output").textContent=data.message||"Rollback finished.";toast(data.message,response.ok&&data.ok);await refreshStatus(false);await refreshDiff();}finally{b.disabled=false;} }

  function switchSection(id){document.querySelectorAll(".section").forEach(s=>s.classList.toggle("active",s.id===id));document.querySelectorAll(".nav-item[data-section]").forEach(b=>b.classList.toggle("active",b.dataset.section===id));const titles={overview:"System overview",botconfig:"Bot configuration",code:"Code editor",git:"Git & deploy",logs:"Bot logs"};$("page-title").textContent=titles[id]||"HomePi Control";if(id==="logs")loadLogs();if(id==="git")refreshDiff();if(id==="code"&&!allFiles.length)loadFiles();if(id==="botconfig"&&$("guild-select").options.length<=1)loadGuilds();}

  async function init(){try{await bootstrap();}catch(e){toast(e.message,false);return;}document.querySelectorAll(".nav-item[data-section]").forEach(b=>b.addEventListener("click",()=>switchSection(b.dataset.section)));document.querySelectorAll("[data-bot-action]").forEach(b=>b.addEventListener("click",()=>botAction(b.dataset.botAction,b)));document.querySelectorAll("[data-git-action]").forEach(b=>b.addEventListener("click",()=>gitAction(b.dataset.gitAction,b)));$("refresh-button").addEventListener("click",()=>refreshStatus(true));$("logs-refresh").addEventListener("click",loadLogs);$("reload-files").addEventListener("click",loadFiles);$("file-filter").addEventListener("input",renderFiles);$("code-editor").addEventListener("keyup",updateEditorPosition);$("code-editor").addEventListener("click",updateEditorPosition);$("validate-code").addEventListener("click",validateCode);$("save-code").addEventListener("click",saveCode);$("refresh-diff").addEventListener("click",refreshDiff);$("commit-button").addEventListener("click",commitSelected);$("guild-select").addEventListener("change",loadGuildConfig);$("save-config").addEventListener("click",saveGuildConfig);$("deploy-button").addEventListener("click",deploy);$("rollback-button").addEventListener("click",rollback);$("logout-button").addEventListener("click",async()=>{await request("/logout",{method:"POST"});window.location.href="/login";});await refreshStatus(false);setInterval(()=>refreshStatus(false),30000);}
  init();
})();
