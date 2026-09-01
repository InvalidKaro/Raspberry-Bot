(() => {
  let csrf = "";
  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";

  async function request(url, options = {}) {
    const headers = {"Content-Type": "application/json", ...(options.headers || {})};
    if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
    const response = await fetch(url, {...options, headers, cache:"no-store"});
    const data = await response.json().catch(() => ({ok:false,message:"Invalid response"}));
    if (response.status === 401) location.href = "/login";
    return {response, data};
  }

  async function bootstrap() {
    const {response, data} = await request("/api/bootstrap");
    if (!response.ok || !data.ok) throw new Error(data.message || "Bootstrap failed");
    csrf = data.csrf;
  }

  function fmtMb(value) {
    return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} MB` : "—";
  }

  function fmtPercent(value) {
    return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)}%` : "—";
  }

  function renderRecommendations(system = {}) {
    const target = $("health-recommendations");
    const score = $("health-score");
    if (!target || !score) return;

    const recommendations = [];
    let severity = 0;
    const ram = Number(system.memory_percent);
    const swap = Number(system.swap_percent);
    const cpu = Number(system.cpu_average_30s ?? system.cpu_percent);
    const temp = Number(system.temperature_c);
    const disk = Number(system.disk_percent);
    const botRam = Number(system.bot_memory_mb);
    const dashRam = Number(system.dashboard_memory_mb);

    if (!system.bot_active) {
      recommendations.push(["Critical", "Bot service is offline. Check the service logs before restarting it."]);
      severity = Math.max(severity, 3);
    }
    if (Number.isFinite(ram) && ram >= 90) {
      recommendations.push(["Critical", "RAM usage is above 90%. Clear bot caches first; use Python GC only if memory remains high."]);
      severity = Math.max(severity, 3);
    } else if (Number.isFinite(ram) && ram >= 80) {
      recommendations.push(["Warning", "RAM usage is above 80%. Check Bot RAM and Dashboard RAM to find the main consumer."]);
      severity = Math.max(severity, 2);
    }
    if (Number.isFinite(swap) && swap >= 50) {
      recommendations.push(["Warning", "Swap usage is high. Avoid repeated heavy exports and check whether a process keeps growing."]);
      severity = Math.max(severity, 2);
    }
    if (Number.isFinite(cpu) && cpu >= 80) {
      recommendations.push(["Warning", "CPU has been high for the recent sample window. Inspect the process list on the main dashboard."]);
      severity = Math.max(severity, 2);
    }
    if (Number.isFinite(temp) && temp >= 75) {
      recommendations.push(["Critical", "Pi temperature is high. Check airflow, enclosure and sustained CPU load."]);
      severity = Math.max(severity, 3);
    } else if (Number.isFinite(temp) && temp >= 65) {
      recommendations.push(["Notice", "Pi is warm. No action is required yet, but keep an eye on sustained temperature."]);
      severity = Math.max(severity, 1);
    }
    if (Number.isFinite(disk) && disk >= 90) {
      recommendations.push(["Critical", "Disk usage is above 90%. Check logs, backups and database growth."]);
      severity = Math.max(severity, 3);
    } else if (Number.isFinite(disk) && disk >= 80) {
      recommendations.push(["Warning", "Disk usage is above 80%. Review old logs and backups before storage becomes tight."]);
      severity = Math.max(severity, 2);
    }
    if (Number.isFinite(botRam) && botRam >= 350) {
      recommendations.push(["Warning", `Bot RSS is ${botRam.toFixed(1)} MB. Use Clear Bot Caches, then watch whether it grows again.`]);
      severity = Math.max(severity, 2);
    }
    if (Number.isFinite(dashRam) && dashRam >= 220) {
      recommendations.push(["Warning", `Dashboard RSS is ${dashRam.toFixed(1)} MB. A dashboard restart can reclaim memory if it keeps growing.`]);
      severity = Math.max(severity, 2);
    }

    if (!recommendations.length) {
      recommendations.push(["Good", "No immediate optimization is needed. Current RAM, CPU, temperature and disk values look healthy."]);
    }

    score.textContent = severity >= 3 ? "ACTION NEEDED" : severity === 2 ? "CHECK SOON" : severity === 1 ? "WATCH" : "HEALTHY";
    target.replaceChildren();
    for (const [label, text] of recommendations.slice(0, 6)) {
      const row = document.createElement("div");
      row.className = "stack-row";
      const left = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = label;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = text;
      left.append(strong, meta);
      row.appendChild(left);
      target.appendChild(row);
    }
  }

  function renderSystem(system = {}) {
    $("ram-percent").textContent = fmtPercent(system.memory_percent);
    $("ram-detail").textContent = `${system.memory_used_mb ?? "—"} / ${system.memory_total_mb ?? "—"} MB`;
    $("bot-ram").textContent = fmtMb(system.bot_memory_mb);
    $("bot-cpu").textContent = `${fmtPercent(system.bot_cpu_percent)} CPU`;
    $("dashboard-ram").textContent = fmtMb(system.dashboard_memory_mb);
    $("dashboard-cpu").textContent = `${fmtPercent(system.dashboard_cpu_percent)} CPU`;
    $("cpu-percent").textContent = fmtPercent(system.cpu_percent);
    $("cpu-average").textContent = `${fmtPercent(system.cpu_average_30s)} / 30s`;
    $("temperature").textContent = system.temperature_c == null ? "—" : `${Number(system.temperature_c).toFixed(1)} °C`;
    const temp = Number(system.temperature_c);
    $("temp-state").textContent = !Number.isFinite(temp) ? "No sensor data" : temp >= 75 ? "High" : temp >= 65 ? "Warm" : "Normal";
    $("swap-percent").textContent = fmtPercent(system.swap_percent);
    $("swap-detail").textContent = `${system.swap_used_mb ?? "—"} / ${system.swap_total_mb ?? "—"} MB`;
    $("disk-percent").textContent = fmtPercent(system.disk_percent);
    $("disk-detail").textContent = `${system.disk_used_gb ?? "—"} / ${system.disk_total_gb ?? "—"} GB`;
    $("bot-state").textContent = system.bot_active ? "ONLINE" : "OFFLINE";
    $("sample-age").textContent = `sample ${system.sample_age_seconds ?? "—"}s old`;

    const ram = Number(system.memory_percent);
    const swap = Number(system.swap_percent);
    const state = $("low-ram-state");
    if (Number.isFinite(ram) && ram >= 90) state.textContent = "RAM CRITICAL";
    else if (Number.isFinite(ram) && ram >= 80) state.textContent = "RAM HIGH";
    else if (Number.isFinite(swap) && swap >= 40) state.textContent = "SWAP ACTIVE";
    else state.textContent = "LOW-RAM TUNED";

    renderRecommendations(system);
  }

  function svgNode(name, attrs = {}) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }

  function renderTrend(svg, rows, key, options = {}) {
    if (!svg) return;
    svg.replaceChildren();
    const width = 900;
    const height = 180;
    const padX = 38;
    const padY = 20;
    const values = rows.map((row) => Number(row[key])).filter((value) => Number.isFinite(value));

    const styles = getComputedStyle(document.documentElement);
    const border = styles.getPropertyValue("--border").trim() || "#252c38";
    const muted = styles.getPropertyValue("--muted").trim() || "#8e99aa";
    const accent = options.color || styles.getPropertyValue("--accent").trim() || "#8b5cf6";

    for (const ratio of [0, 0.5, 1]) {
      const y = padY + (height - padY * 2) * ratio;
      svg.appendChild(svgNode("line", {x1:padX, y1:y, x2:width-padX, y2:y, stroke:border, "stroke-width":1}));
    }

    if (values.length < 2) {
      const text = svgNode("text", {x:width/2, y:height/2, fill:muted, "text-anchor":"middle", "font-size":18});
      text.textContent = "Not enough history yet";
      svg.appendChild(text);
      return;
    }

    let min = options.min == null ? Math.min(...values) : Number(options.min);
    let max = options.max == null ? Math.max(...values) : Number(options.max);
    if (!Number.isFinite(min)) min = 0;
    if (!Number.isFinite(max)) max = 100;
    if (max - min < 1) max = min + 1;
    const margin = options.padding == null ? (max - min) * 0.08 : Number(options.padding);
    min = Math.max(options.floor == null ? -Infinity : Number(options.floor), min - margin);
    max += margin;

    const points = values.map((value, index) => {
      const x = padX + (index / (values.length - 1)) * (width - padX * 2);
      const y = height - padY - ((value - min) / (max - min)) * (height - padY * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");

    svg.appendChild(svgNode("polyline", {
      points,
      fill:"none",
      stroke:accent,
      "stroke-width":4,
      "stroke-linecap":"round",
      "stroke-linejoin":"round"
    }));

    const last = values[values.length - 1];
    const label = svgNode("text", {x:width-padX, y:18, fill:muted, "text-anchor":"end", "font-size":16});
    label.textContent = `${last.toFixed(1)}${options.suffix || ""}`;
    svg.appendChild(label);
  }

  function renderHistory(data = {}) {
    const history = Array.isArray(data.history) ? data.history : [];
    $("history-state").textContent = `${data.interval_seconds || 90}s · ${history.length} points`;
    const styles = getComputedStyle(document.documentElement);
    renderTrend($("ram-chart"), history, "ram_percent", {min:0, max:100, suffix:"%", color:styles.getPropertyValue("--accent").trim()});
    renderTrend($("cpu-chart"), history, "cpu_percent", {min:0, max:100, suffix:"%", color:styles.getPropertyValue("--good").trim()});
    renderTrend($("temp-chart"), history, "temperature", {floor:20, suffix:" °C", color:styles.getPropertyValue("--warning").trim()});
  }

  function renderPersonnel(data = {}) {
    const rows = Array.isArray(data.rows) ? data.rows : [];
    $("perso-total").textContent = `E ${data.total_e ?? 0} · BWG ${data.total_b ?? 0}`;
    const target = $("perso-leaderboard");
    target.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No personnel data yet.";
      target.appendChild(empty);
      return;
    }
    rows.slice(0, 10).forEach((row, index) => {
      const item = document.createElement("div");
      item.className = "stack-row";
      const label = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = `${index + 1}. ${row.display_name}`;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `E ${row.inductions} · BWG ${row.bwg}`;
      label.append(strong, meta);
      const total = document.createElement("strong");
      total.textContent = String(row.activity ?? 0);
      item.append(label, total);
      target.appendChild(item);
    });
  }

  async function load() {
    const results = await Promise.all([
      request("/api/control-center"),
      request("/api/cogs"),
      request("/api/status"),
      request("/api/control/history?limit=80"),
      request("/api/control/personnel")
    ]);
    const [{response:r1,data:c},{response:r2,data:g},{response:r3,data:s},{response:r4,data:h},{response:r5,data:p}] = results;
    if (!r1.ok || !c.ok) throw new Error(c.message || "Control center failed");
    const o = c.overview || {};
    $("tickets").textContent = o.tickets ?? "—";
    $("open-tickets").textContent = `${o.open_tickets ?? 0} open`;
    $("personnel").textContent = o.personnel ?? "—";
    $("perso-detail").textContent = `E ${o.inductions ?? 0} · BWG ${o.bwg ?? 0}`;
    $("mod-cases").textContent = o.mod_cases ?? "—";
    $("errors").textContent = o.errors_24h ?? "—";
    $("backups").textContent = c.backups ?? "—";

    if (r3.ok && s.ok && s.system) renderSystem(s.system);
    if (r4.ok && h.ok) renderHistory(h);
    if (r5.ok && p.ok) renderPersonnel(p);

    const exts = (g && g.extensions) || [];
    $("extension-count").textContent = `${exts.length} configured`;
    $("extension-list").innerHTML = "";
    for (const ext of exts) {
      const row = document.createElement("div");
      row.className = "stack-row";
      const label = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = ext;
      label.appendChild(strong);
      const buttons = document.createElement("div");
      buttons.className = "button-row";
      for (const action of ["reload", "load", "unload"]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = action[0].toUpperCase() + action.slice(1);
        button.addEventListener("click", () => queue(action, ext));
        buttons.appendChild(button);
      }
      row.append(label, buttons);
      $("extension-list").appendChild(row);
    }
  }

  async function queue(action, extension = "") {
    if (action === "unload" && !confirm(`Unload ${extension}?`)) return;
    const body = action === "sync" ? {} : {extension};
    const {response, data} = await request(`/api/cogs/${action}`, {method:"POST", body:JSON.stringify(body)});
    if (!response.ok || !data.ok) {
      $("command-result").textContent = data.message || "Action failed";
      return;
    }
    $("command-result").textContent = `Queued #${data.command_id}: ${action}${extension ? ` ${extension}` : ""}`;
    poll(data.command_id, "command-result");
  }

  async function maintenance(action) {
    const output = $("maintenance-result");
    output.textContent = `Queuing ${action}…`;
    const {response, data} = await request(`/api/control/maintenance/${action}`, {
      method:"POST",
      body:JSON.stringify({})
    });
    if (!response.ok || !data.ok) {
      output.textContent = data.message || "Maintenance action failed";
      return;
    }
    output.textContent = `Queued #${data.command_id}: ${action}`;
    poll(data.command_id, "maintenance-result");
  }

  async function poll(id, outputId) {
    const output = $(outputId);
    for (let n = 0; n < 25; n++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const {response, data} = await request(`/api/dashboard-command/${id}`);
      if (response.ok && data.ok && data.command) {
        const command = data.command;
        output.textContent = `#${command.id} · ${command.status}\n${command.result || "Waiting…"}`;
        if (command.status !== "pending") {
          await load();
          return;
        }
      }
    }
    output.textContent += "\nTimed out waiting for the bot queue.";
  }

  function setQuickBusy(busy) {
    for (const id of ["git-pull", "restart-bot", "restart-dashboard", "update-all"]) {
      const el = $(id);
      if (el) el.disabled = busy;
    }
  }

  async function quick(action, confirmText = "") {
    if (confirmText && !confirm(confirmText)) return;
    setQuickBusy(true);
    $("quick-result").textContent = `Running ${action}…`;
    try {
      const {response, data} = await request(`/api/control/system/${action}`, {
        method:"POST",
        body:JSON.stringify({})
      });
      if (!response.ok || !data.ok) {
        $("quick-result").textContent = data.message || `${action} failed`;
        return;
      }
      $("quick-result").textContent = data.message || `${action} completed.`;
      if (data.dashboard_restarting) {
        $("quick-result").textContent += "\nDashboard is restarting. Reconnecting…";
        await reconnectDashboard();
      } else {
        await load();
      }
    } catch (error) {
      $("quick-result").textContent = String(error);
    } finally {
      setQuickBusy(false);
    }
  }

  async function reconnectDashboard() {
    for (let n = 0; n < 30; n++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        const response = await fetch("/health", {cache:"no-store"});
        if (response.ok) {
          location.reload();
          return;
        }
      } catch (_) {}
    }
    $("quick-result").textContent += "\nDashboard did not come back within 30 seconds.";
  }

  async function init() {
    try {
      await bootstrap();
      await load();
      $("refresh").addEventListener("click", load);
      $("sync").addEventListener("click", () => queue("sync"));
      $("cache-clear").addEventListener("click", () => maintenance("cache-clear"));
      $("gc-run").addEventListener("click", () => maintenance("gc"));
      $("db-optimize").addEventListener("click", () => maintenance("database-optimize"));
      $("git-pull").addEventListener("click", () => quick("pull"));
      $("restart-bot").addEventListener("click", () => quick("restart-bot", "Restart Raspberry-Bot now?"));
      $("restart-dashboard").addEventListener("click", () => quick("restart-dashboard", "Restart the dashboard now?"));
      $("update-all").addEventListener("click", () => quick("update-all", "Run git pull and restart both Bot + Dashboard?"));
    } catch (error) {
      $("command-result").textContent = String(error);
    }
  }

  init();
})();