(() => {
  "use strict";
  const csrf = String(document.querySelector('meta[name="dashboard-csrf"]')?.content || "");
  const botService = String(document.querySelector('meta[name="bot-service"]')?.content || "raspberry-bot");
  const $ = (id) => document.getElementById(id);
  let toastTimer = null;

  function toast(message, ok = true) {
    const node = $("toast");
    node.textContent = String(message || (ok ? "Done." : "Action failed."));
    node.className = ok ? "show good" : "show bad";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.className = ""; }, 3200);
  }

  function percentBar(id, value) {
    const n = Number(value);
    $(id).style.width = `${Math.max(0, Math.min(100, Number.isFinite(n) ? n : 0))}%`;
  }

  function uptime(seconds) {
    let s = Math.max(0, Number(seconds) || 0);
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600;
    const m = Math.floor(s / 60);
    return `${d}d ${h}h ${m}m`;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if ((options.method || "GET").toUpperCase() !== "GET") headers.set("X-CSRF-Token", csrf);
    const response = await fetch(path, { ...options, headers });
    let data;
    try { data = await response.json(); }
    catch { data = { ok: false, message: `HTTP ${response.status}` }; }
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Authentication expired.");
    }
    return { response, data };
  }

  function renderStatus(data) {
    const system = data.system || {};
    const git = data.git || {};
    const botOnline = Boolean(system.bot_active);
    $("bot-status").textContent = botOnline ? "Online" : "Offline";
    $("bot-service-name").textContent = botService;
    $("bot-pill").className = `status-pill ${botOnline ? "online" : "offline"}`;
    $("bot-pill").querySelector("span:last-child").textContent = botOnline ? "Bot online" : "Bot offline";

    const pihole = system.pihole || {};
    $("pihole-status").textContent = !pihole.installed ? "Not detected" : pihole.active ? "Online" : "Offline";
    $("pihole-detail").textContent = pihole.blocking === true ? "Blocking enabled" : pihole.blocking === false ? "Blocking disabled" : "Blocking state unknown";

    const cpu = Number(system.cpu_percent) || 0;
    $("cpu").textContent = `${cpu.toFixed(1)}%`; percentBar("cpu-bar", cpu);
    const temp = Number(system.temperature_c);
    $("temp").textContent = Number.isFinite(temp) ? `${temp.toFixed(1)}°C` : "N/A";
    percentBar("temp-bar", Number.isFinite(temp) ? (temp / 85) * 100 : 0);
    const ram = Number(system.memory_percent) || 0;
    $("ram").textContent = `${ram.toFixed(1)}%`; $("ram-detail").textContent = `${system.memory_used_mb ?? "—"} / ${system.memory_total_mb ?? "—"} MB`; percentBar("ram-bar", ram);
    const disk = Number(system.disk_percent) || 0;
    $("disk").textContent = `${disk.toFixed(1)}%`; $("disk-detail").textContent = `${system.disk_used_gb ?? "—"} / ${system.disk_total_gb ?? "—"} GB`; percentBar("disk-bar", disk);
    $("uptime").textContent = uptime(system.uptime_seconds);
    $("load").textContent = Array.isArray(system.load_average) ? system.load_average.join(" / ") : "—";
    $("net-rx").textContent = `${system.network_rx_mb ?? "—"} MB`;
    $("net-tx").textContent = `${system.network_tx_mb ?? "—"} MB`;

    $("branch").textContent = git.ok ? git.branch : "Unavailable";
    $("last-commit").textContent = git.ok ? (git.last_commit || "No commit information.") : (git.message || "Git unavailable.");
    $("git-summary").textContent = git.ok ? (git.dirty ? `${git.changes.length} local change(s)` : "Working tree clean") : "Repository status unavailable";
    $("git-branch-title").textContent = `Branch ${git.ok ? git.branch : "—"}`;
    $("git-commit-detail").textContent = git.ok ? (git.last_commit || "—") : (git.message || "—");
    $("change-count").textContent = String(git.ok ? git.changes.length : 0);
    $("git-changes").textContent = git.ok && git.changes.length ? git.changes.join("\n") : git.ok ? "Clean working tree." : (git.message || "Git unavailable.");
  }

  async function refreshStatus(showToast = false) {
    const button = $("refresh-button"); button.disabled = true;
    try {
      const { response, data } = await api("/api/status");
      if (!response.ok || !data.ok) throw new Error(data.message || "Status request failed.");
      renderStatus(data); if (showToast) toast("Status refreshed.");
    } catch (error) { toast(error.message, false); }
    finally { button.disabled = false; }
  }

  async function runBotAction(action, button) {
    if (action === "stop" && !window.confirm("Stop the Discord bot service?")) return;
    button.disabled = true;
    try {
      const { response, data } = await api(`/api/bot/${encodeURIComponent(action)}`, { method: "POST" });
      toast(data.message || `Bot ${action} completed.`, response.ok && data.ok);
      await new Promise((resolve) => setTimeout(resolve, 900));
      await refreshStatus(false);
    } catch (error) { toast(error.message, false); }
    finally { button.disabled = false; }
  }

  async function loadLogs() {
    const button = $("logs-refresh"); button.disabled = true; $("log-output").textContent = "Loading logs…";
    try {
      const { response, data } = await api("/api/logs");
      if (!response.ok || !data.ok) throw new Error(data.logs || data.message || "Could not read logs.");
      $("log-output").textContent = data.logs || "No log output.";
    } catch (error) { $("log-output").textContent = error.message; toast(error.message, false); }
    finally { button.disabled = false; }
  }

  async function gitAction(action, button) {
    if (!window.confirm(`Run git ${action} on the Raspberry Pi repository?`)) return;
    button.disabled = true; $("git-output").textContent = `Running git ${action}…`;
    try {
      const { response, data } = await api(`/api/git/${encodeURIComponent(action)}`, { method: "POST" });
      $("git-output").textContent = data.message || `git ${action} finished.`;
      toast(data.message || `git ${action} finished.`, response.ok && data.ok);
      await refreshStatus(false);
    } catch (error) { $("git-output").textContent = error.message; toast(error.message, false); }
    finally { button.disabled = false; }
  }

  function switchSection(sectionId) {
    document.querySelectorAll(".section").forEach((section) => section.classList.toggle("active", section.id === sectionId));
    document.querySelectorAll(".nav-item[data-section]").forEach((button) => button.classList.toggle("active", button.dataset.section === sectionId));
    const titles = { overview: "System overview", logs: "Bot logs", git: "Git control" };
    $("page-title").textContent = titles[sectionId] || "HomePi Control";
    if (sectionId === "logs") loadLogs();
  }

  document.querySelectorAll(".nav-item[data-section]").forEach((button) => button.addEventListener("click", () => switchSection(button.dataset.section)));
  document.querySelectorAll("[data-bot-action]").forEach((button) => button.addEventListener("click", () => runBotAction(button.dataset.botAction, button)));
  document.querySelectorAll("[data-git-action]").forEach((button) => button.addEventListener("click", () => gitAction(button.dataset.gitAction, button)));
  $("refresh-button").addEventListener("click", () => refreshStatus(true));
  $("logs-refresh").addEventListener("click", loadLogs);
  $("logout-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await api("/logout", { method: "POST" }); } finally { window.location.href = "/login"; } });
  refreshStatus(false);
  window.setInterval(() => refreshStatus(false), 30000);
})();
