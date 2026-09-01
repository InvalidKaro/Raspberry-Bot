(() => {
  let csrf = "";
  const $ = (id) => document.getElementById(id);

  async function request(url, options = {}) {
    const headers = {"Content-Type": "application/json", ...(options.headers || {})};
    if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
    const response = await fetch(url, {...options, headers});
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
  }

  async function load() {
    const [{response:r1,data:c},{response:r2,data:g},{response:r3,data:s}] = await Promise.all([
      request("/api/control-center"), request("/api/cogs"), request("/api/status")
    ]);
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
    poll(data.command_id);
  }

  async function poll(id) {
    for (let n = 0; n < 20; n++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const {response, data} = await request(`/api/dashboard-command/${id}`);
      if (response.ok && data.ok && data.command) {
        const command = data.command;
        $("command-result").textContent = `#${command.id} · ${command.status}\n${command.result || "Waiting…"}`;
        if (command.status !== "pending") {
          await load();
          return;
        }
      }
    }
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
