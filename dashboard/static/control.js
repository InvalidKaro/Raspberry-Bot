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

  async function load() {
    const [{response:r1,data:c},{response:r2,data:g}] = await Promise.all([
      request("/api/control-center"), request("/api/cogs")
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
