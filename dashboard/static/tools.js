(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let csrf = "";
  let toastTimer = null;

  function toast(message, ok = true) {
    const node = $("tools-toast");
    node.textContent = String(message || "Done.");
    node.className = `tools-toast show ${ok ? "" : "bad"}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.className = "tools-toast";
    }, 3500);
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const method = String(options.method || "GET").toUpperCase();
    if (method !== "GET") {
      headers.set("X-CSRF-Token", csrf);
    }
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    let data = {};
    if ((response.headers.get("content-type") || "").includes("json")) {
      try {
        data = await response.json();
      } catch {
        data = {};
      }
    }
    if (response.status === 401) {
      location.href = "/login";
      throw new Error("Authentication expired.");
    }
    return { response, data };
  }

  async function bootstrap() {
    const { response, data } = await request("/api/bootstrap");
    if (!response.ok || !data.ok) {
      throw new Error(data.message || "Could not bootstrap dashboard session.");
    }
    csrf = data.csrf;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("de-DE", {
      dateStyle: "short",
      timeStyle: "short",
    });
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  }

  function setBusy(button, busy, label = null) {
    button.disabled = busy;
    if (label !== null) {
      if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
      button.textContent = busy ? label : button.dataset.originalLabel;
    }
  }

  function renderDiagnostics(data) {
    const diagnostics = data.diagnostics || {};
    const checks = Array.isArray(diagnostics.checks) ? diagnostics.checks : [];
    const list = $("diagnostic-list");
    const badge = $("diagnostic-badge");
    const overall = checks.length > 0 && checks.every((row) => row.ok);

    list.replaceChildren();
    if (!checks.length) {
      const empty = document.createElement("div");
      empty.className = "tools-empty";
      empty.textContent = "No diagnostic results were returned.";
      list.append(empty);
    } else {
      for (const check of checks) {
        const row = document.createElement("div");
        row.className = `diagnostic-row ${check.ok ? "good" : "bad"}`;

        const dot = document.createElement("span");
        dot.className = "diagnostic-dot";

        const detail = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = String(check.label || check.key || "Check");
        const small = document.createElement("small");
        small.textContent = String(check.detail || "No details");
        detail.append(title, small);

        const state = document.createElement("b");
        state.textContent = check.ok ? "PASS" : "FAIL";

        row.append(dot, detail, state);
        list.append(row);
      }
    }

    badge.textContent = overall ? "All passed" : "Needs attention";
    badge.dataset.state = overall ? "good" : "bad";
    $("summary-diagnostics").textContent = overall ? "Healthy" : "Check failed";
    $("summary-diagnostics-detail").textContent = `${checks.filter((row) => row.ok).length}/${checks.length} checks passed`;

    const database = diagnostics.database || {};
    $("summary-database").textContent = database.ok ? "Integrity OK" : "Needs check";
    $("summary-database-detail").textContent = database.size_bytes
      ? `${formatBytes(database.size_bytes)} · quick_check`
      : "SQLite quick_check";

    renderServiceStates(data.system || {});
  }

  async function runDiagnostics() {
    const button = $("run-diagnostics");
    setBusy(button, true, "Running…");
    $("diagnostic-badge").textContent = "Running";
    $("diagnostic-badge").dataset.state = "warn";
    try {
      const { response, data } = await request("/api/tools/diagnostics");
      renderDiagnostics(data);
      toast(
        data.diagnostics?.ok ? "All diagnostics passed." : "Diagnostics found something to review.",
        Boolean(data.diagnostics?.ok),
      );
      if (!response.ok) {
        throw new Error(data.message || "Diagnostics failed.");
      }
    } catch (error) {
      $("diagnostic-badge").textContent = "Failed";
      $("diagnostic-badge").dataset.state = "bad";
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  async function runNetworkTest(kind) {
    const target = $("network-target").value.trim();
    const button = kind === "ping" ? $("network-ping") : $("network-dns");
    if (!target) {
      toast("Enter a hostname or IP first.", false);
      return;
    }
    setBusy(button, true, kind === "ping" ? "Pinging…" : "Resolving…");
    $("network-output").textContent = "Running test…";
    try {
      const { response, data } = await request("/api/tools/network", {
        method: "POST",
        body: JSON.stringify({ kind, target }),
      });

      if (kind === "dns") {
        $("network-output").textContent = data.ok
          ? [
              `Target: ${data.target}`,
              "",
              ...(data.addresses || []).map((address) => `→ ${address}`),
            ].join("\n")
          : String(data.message || "DNS lookup failed.");
      } else {
        $("network-output").textContent = String(data.output || data.message || "No ping output.");
      }

      if (!response.ok || !data.ok) {
        toast(data.message || `${kind} failed.`, false);
      } else {
        toast(`${kind === "ping" ? "Ping" : "DNS lookup"} completed.`);
      }
    } catch (error) {
      $("network-output").textContent = error.message;
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  function updateWeekdayVisibility() {
    $("weekday-field").hidden = $("backup-frequency").value !== "weekly";
  }

  function renderSchedule(schedule) {
    $("backup-enabled").checked = Boolean(schedule.enabled);
    $("backup-frequency").value = schedule.frequency || "daily";
    $("backup-time").value = schedule.time || "03:30";
    $("backup-weekday").value = String(schedule.weekday ?? 6);
    $("backup-retention").value = String(schedule.retention ?? 7);
    $("backup-next").textContent = formatDate(schedule.next_run_at);
    $("backup-last").textContent = formatDate(schedule.last_run_at);
    $("backup-count").textContent = String((schedule.automatic_backups || []).length);
    $("backup-message").textContent = schedule.last_result?.message || "Schedule loaded.";
    $("summary-backup").textContent = schedule.enabled ? "Enabled" : "Disabled";
    $("summary-backup-detail").textContent = schedule.enabled
      ? `${schedule.frequency || "daily"} · ${schedule.time || "03:30"}`
      : "Automatic backups off";
    updateWeekdayVisibility();
  }

  async function loadSchedule() {
    try {
      const { response, data } = await request("/api/tools/schedule");
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not load backup schedule.");
      renderSchedule(data.schedule || {});
    } catch (error) {
      $("summary-backup").textContent = "Unavailable";
      $("backup-message").textContent = error.message;
    }
  }

  async function saveSchedule() {
    const button = $("save-backup-schedule");
    setBusy(button, true, "Saving…");
    try {
      const payload = {
        enabled: $("backup-enabled").checked,
        frequency: $("backup-frequency").value,
        time: $("backup-time").value,
        weekday: Number($("backup-weekday").value),
        retention: Number($("backup-retention").value),
      };
      const { response, data } = await request("/api/tools/schedule", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not save schedule.");
      renderSchedule(data.schedule || {});
      toast("Automatic backup schedule saved.");
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  async function backupNow() {
    const button = $("backup-now");
    setBusy(button, true, "Backing up…");
    try {
      const { response, data } = await request("/api/tools/backup-now", {
        method: "POST",
        body: "{}",
      });
      if (!response.ok || !data.ok) throw new Error(data.message || "Backup failed.");
      if (data.schedule) renderSchedule(data.schedule);
      toast(data.message || "Database backup created.");
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  function renderUpdates(data) {
    const list = $("update-list");
    const updates = Array.isArray(data.updates) ? data.updates : [];
    list.replaceChildren();

    $("summary-updates").textContent = data.available
      ? `${Number(data.count || 0)} available`
      : "Unavailable";

    if (!data.available) {
      const empty = document.createElement("div");
      empty.className = "tools-empty";
      empty.textContent = data.message || "APT is unavailable.";
      list.append(empty);
      return;
    }

    if (!updates.length) {
      const empty = document.createElement("div");
      empty.className = "tools-empty";
      empty.textContent = "No package updates are present in the cached APT index.";
      list.append(empty);
      return;
    }

    for (const update of updates) {
      const row = document.createElement("div");
      row.className = "update-row";
      const title = document.createElement("strong");
      title.textContent = String(update.package || "package");
      const detail = document.createElement("small");
      detail.textContent = String(update.detail || "");
      row.append(title, detail);
      list.append(row);
    }
  }

  async function checkUpdates() {
    const button = $("check-updates");
    setBusy(button, true, "Checking…");
    try {
      const { data } = await request("/api/tools/updates");
      renderUpdates(data);
      toast(
        data.available
          ? `${Number(data.count || 0)} cached package update(s) found.`
          : data.message || "APT unavailable.",
        Boolean(data.available),
      );
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  function renderServiceStates(system) {
    const services = Array.isArray(system.services) ? system.services : [];
    const names = {
      "raspberry-bot": "bot",
      "raspberry-dashboard": "dashboard",
      "pihole-FTL": "pihole",
      tailscaled: "tailscale",
    };
    for (const row of services) {
      const key = names[row.name];
      if (!key) continue;
      const node = document.querySelector(`[data-service-row="${key}"]`);
      if (!node) continue;
      node.dataset.state = row.load === "not-found"
        ? "missing"
        : row.active === "active"
          ? "active"
          : "inactive";
    }
  }

  async function loadServiceStatus() {
    try {
      const { response, data } = await request("/api/status");
      if (response.ok && data.ok) renderServiceStates(data.system || {});
    } catch {
      // The toolbox remains usable even when the status sampler is unavailable.
    }
  }

  async function waitForDashboard() {
    $("service-message").textContent = "Dashboard restarting — waiting for :8080…";
    await new Promise((resolve) => setTimeout(resolve, 1200));
    for (let attempt = 0; attempt < 25; attempt += 1) {
      try {
        const response = await fetch("/health", { cache: "no-store" });
        if (response.ok) {
          location.reload();
          return;
        }
      } catch {
        // Expected while the aiohttp process is restarting.
      }
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
    $("service-message").textContent = "Dashboard did not return within the expected window.";
  }

  async function serviceAction(button) {
    const service = button.dataset.service;
    const action = button.dataset.action;
    if (!service || !action) return;

    if (action === "stop") {
      const label = button.closest(".service-control")?.querySelector("strong")?.textContent || service;
      if (!confirm(`Stop ${label}? This can interrupt HomePi functionality.`)) return;
    }
    if (service === "dashboard" && !confirm("Restart the HomePi dashboard service? The page will reconnect automatically.")) {
      return;
    }

    setBusy(button, true, "Working…");
    try {
      const { response, data } = await request(
        `/api/tools/service/${encodeURIComponent(service)}/${encodeURIComponent(action)}`,
        { method: "POST", body: "{}" },
      );
      $("service-message").textContent = data.message || "Service action finished.";
      if (!response.ok || !data.ok) throw new Error(data.message || "Service action failed.");
      toast(data.message || "Service action completed.");
      if (data.dashboard_restarting) {
        waitForDashboard();
        return;
      }
      setTimeout(loadServiceStatus, 800);
    } catch (error) {
      $("service-message").textContent = error.message;
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  async function maintenanceAction(button) {
    const action = button.dataset.maintenance;
    if (!action) return;
    setBusy(button, true, "Queueing…");
    try {
      const { response, data } = await request(
        `/api/control/maintenance/${encodeURIComponent(action)}`,
        { method: "POST", body: "{}" },
      );
      $("maintenance-message").textContent = data.message || "Maintenance job queued.";
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not queue maintenance job.");
      toast(`${data.message || "Maintenance queued."}${data.command_id ? ` (#${data.command_id})` : ""}`);
    } catch (error) {
      $("maintenance-message").textContent = error.message;
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  async function init() {
    try {
      await bootstrap();
    } catch (error) {
      toast(error.message, false);
      return;
    }

    $("run-diagnostics").addEventListener("click", runDiagnostics);
    $("network-ping").addEventListener("click", () => runNetworkTest("ping"));
    $("network-dns").addEventListener("click", () => runNetworkTest("dns"));
    $("network-target").addEventListener("keydown", (event) => {
      if (event.key === "Enter") runNetworkTest("ping");
    });
    $("backup-frequency").addEventListener("change", updateWeekdayVisibility);
    $("save-backup-schedule").addEventListener("click", saveSchedule);
    $("backup-now").addEventListener("click", backupNow);
    $("check-updates").addEventListener("click", checkUpdates);

    document.querySelectorAll("[data-service][data-action]").forEach((button) => {
      button.addEventListener("click", () => serviceAction(button));
    });
    document.querySelectorAll("[data-maintenance]").forEach((button) => {
      button.addEventListener("click", () => maintenanceAction(button));
    });

    await Promise.all([loadSchedule(), loadServiceStatus()]);
  }

  init();
})();
