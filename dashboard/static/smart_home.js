(() => {
  "use strict";

  const GUILD_ID = "1162733312226361454";
  const $ = (id) => document.getElementById(id);
  let csrf = "";
  let toastTimer = null;
  let devices = [];
  let sensors = [];
  let schedules = [];
  let channels = [];

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  function toast(message, ok = true) {
    const node = $("sh-toast");
    node.textContent = String(message || "Done.");
    node.className = `sh-toast show ${ok ? "" : "bad"}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.className = "sh-toast";
    }, 3500);
  }

  function setBridge(state, text) {
    const node = $("bridge-state");
    node.dataset.state = state;
    node.querySelector("span").textContent = text;
  }

  function setBusy(button, busy, label = "Working…") {
    if (!button) return;
    if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
    button.disabled = busy;
    button.textContent = busy ? label : button.dataset.originalLabel;
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
      throw new Error(data.message || "Could not bootstrap dashboard.");
    }
    csrf = data.csrf;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function queueCommand(action, payload = {}, { timeoutMs = 24000 } = {}) {
    const { response, data } = await request("/api/smart-home/command", {
      method: "POST",
      body: JSON.stringify({ action, payload }),
    });
    if (!response.ok || !data.ok) {
      throw new Error(data.message || "Could not queue Smart Home command.");
    }

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await sleep(500);
      const status = await request(`/api/smart-home/command/${data.command_id}`);
      if (!status.response.ok || !status.data.ok) {
        throw new Error(status.data.message || "Could not read bot command status.");
      }
      const row = status.data.command || {};
      if (row.status === "done") {
        setBridge("good", "Bot bridge online");
        return row.parsed_result ?? { message: row.result || "Completed." };
      }
      if (row.status === "failed") {
        setBridge("bad", "Bot command failed");
        throw new Error(row.result || "Bot command failed.");
      }
    }

    setBridge("bad", "Bot bridge timeout");
    throw new Error("Raspberry-Bot did not process the Smart Home command in time.");
  }

  function renderSummary() {
    $("summary-devices").textContent = String(devices.length);
    const sensor = sensors[0];
    if (sensor && sensor.temperature_c != null) {
      $("summary-climate").textContent = `${Number(sensor.temperature_c).toFixed(1)} °C`;
      const humidity = sensor.humidity_percent != null
        ? `${Number(sensor.humidity_percent).toFixed(1)} % RH`
        : "Humidity unavailable";
      $("summary-climate-detail").textContent = humidity;
    } else {
      $("summary-climate").textContent = sensors.length ? "Detected" : "No reading";
      $("summary-climate-detail").textContent = sensors.length ? "Refresh climate" : "No sensor cached";
    }
    $("summary-schedules").textContent = String(schedules.filter((row) => Number(row.enabled)).length);
  }

  function renderTargetOptions() {
    const select = $("schedule-target");
    const current = select.value;
    select.innerHTML = '<option value="all">All devices</option>' + devices.map((device) => (
      `<option value="${escapeHtml(device.selector)}">${escapeHtml(device.model)} · ${escapeHtml(device.transport)}</option>`
    )).join("");
    if ([...select.options].some((option) => option.value === current)) {
      select.value = current;
    }
  }

  function deviceCard(device) {
    const canBrightness = (device.capabilities || []).includes("brightness");
    const canRgb = (device.capabilities || []).includes("rgb");
    return `
      <article class="device-card" data-selector="${escapeHtml(device.selector)}">
        <div class="device-card-head">
          <div>
            <strong>${escapeHtml(device.model || device.display_name || "Govee")}</strong>
            <small>${escapeHtml(device.detail || device.display_name || "")}</small>
          </div>
          <span class="device-transport">${escapeHtml(device.transport || "Govee")}</span>
        </div>
        <div class="device-actions">
          <div class="power-row">
            <button type="button" data-device-action="power-on">On</button>
            <button type="button" class="off" data-device-action="power-off">Off</button>
            <button type="button" data-device-action="night">Night</button>
            <button type="button" data-device-action="gaming">Gaming</button>
          </div>
          ${canBrightness ? `
            <div class="device-inline">
              <label>
                <span>BRIGHTNESS</span>
                <input type="range" min="1" max="100" value="50" data-device-brightness aria-label="Brightness">
              </label>
              <button type="button" data-device-action="brightness">Apply</button>
            </div>
          ` : ""}
          ${canRgb ? `
            <div class="device-inline">
              <label>
                <span>COLOR</span>
                <input type="color" value="#69A7FF" data-device-color aria-label="RGB color">
              </label>
              <button type="button" data-device-action="color">Apply</button>
            </div>
          ` : ""}
        </div>
      </article>
    `;
  }

  function renderDevices() {
    const grid = $("device-grid");
    if (!devices.length) {
      grid.innerHTML = '<div class="sh-empty">No controllable Govee device is cached. Run a LAN/BLE scan.</div>';
      renderTargetOptions();
      renderSummary();
      return;
    }
    grid.innerHTML = devices.map(deviceCard).join("");
    grid.querySelectorAll("[data-device-action]").forEach((button) => {
      button.addEventListener("click", () => runDeviceAction(button));
    });
    renderTargetOptions();
    renderSummary();
  }

  function hexToRgb(hex) {
    const value = String(hex || "").replace("#", "");
    if (!/^[0-9a-fA-F]{6}$/.test(value)) return null;
    return {
      r: parseInt(value.slice(0, 2), 16),
      g: parseInt(value.slice(2, 4), 16),
      b: parseInt(value.slice(4, 6), 16),
    };
  }

  async function runDeviceAction(button) {
    const card = button.closest("[data-selector]");
    const selector = card?.dataset.selector;
    const action = button.dataset.deviceAction;
    if (!selector || !action) return;
    setBusy(button, true);
    try {
      let result;
      if (action === "power-on" || action === "power-off") {
        result = await queueCommand("smart-home-power", {
          selector,
          on: action === "power-on",
        });
      } else if (action === "night" || action === "gaming") {
        result = await queueCommand("smart-home-preset", {
          selector,
          preset: action,
        });
      } else if (action === "brightness") {
        const value = Number(card.querySelector("[data-device-brightness]")?.value || 50);
        result = await queueCommand("smart-home-brightness", { selector, value });
      } else if (action === "color") {
        const rgb = hexToRgb(card.querySelector("[data-device-color]")?.value);
        if (!rgb) throw new Error("Invalid RGB color.");
        result = await queueCommand("smart-home-color", { selector, ...rgb });
      }
      toast(result?.display_name
        ? `${result.display_name} updated via ${result.transport || "Smart Home"}.`
        : "Smart Home action completed.");
      loadRecent();
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  async function runScene(button) {
    const preset = button.dataset.scene;
    if (!preset) return;
    if (preset === "off" && !confirm("Switch off all controllable Smart Home lights?")) {
      return;
    }
    setBusy(button, true);
    try {
      const result = await queueCommand("smart-home-preset", {
        selector: "all",
        preset,
      }, { timeoutMs: 45000 });
      const failed = Number(result.failed || 0);
      const message = failed
        ? `${result.applied || 0} device(s) updated, ${failed} failed.`
        : `${result.applied || 0} device(s) updated.`;
      toast(message, failed === 0);
      loadRecent();
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  function renderSnapshot(result) {
    devices = Array.isArray(result.devices) ? result.devices : [];
    sensors = Array.isArray(result.sensors) ? result.sensors : [];
    renderDevices();
    renderClimateLive();
    const discovery = result.discovery;
    if (discovery) {
      const errors = [discovery.lan_error, discovery.ble_error].filter(Boolean);
      $("discovery-detail").textContent = errors.length
        ? `${discovery.lan_count || 0} LAN · ${discovery.ble_count || 0} BLE · partial`
        : `${discovery.lan_count || 0} LAN · ${discovery.ble_count || 0} BLE`;
    } else {
      $("discovery-detail").textContent = "Bot cache";
    }
  }

  async function refreshSnapshot(scan = false) {
    const button = scan ? $("scan-devices") : $("refresh-snapshot");
    setBusy(button, true, scan ? "Scanning…" : "Loading…");
    setBridge("checking", scan ? "Scanning through bot…" : "Reading bot cache…");
    try {
      const result = await queueCommand(
        scan ? "smart-home-scan" : "smart-home-snapshot",
        scan ? {} : { refresh: false },
        { timeoutMs: scan ? 45000 : 18000 },
      );
      renderSnapshot(result);
      setBridge("good", "Bot bridge online");
      if (scan) toast("LAN and BLE discovery completed.");
      loadRecent();
    } catch (error) {
      setBridge("bad", "Bot bridge unavailable");
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  function renderClimateLive() {
    const sensor = sensors[0];
    $("climate-temp").textContent = sensor?.temperature_c != null
      ? `${Number(sensor.temperature_c).toFixed(1)} °C`
      : "—";
    $("climate-humidity").textContent = sensor?.humidity_percent != null
      ? `${Number(sensor.humidity_percent).toFixed(1)} %`
      : "—";
    $("climate-battery").textContent = sensor?.battery_percent != null
      ? `${Number(sensor.battery_percent).toFixed(0)} %`
      : "—";
    $("climate-sensor-name").textContent = sensor
      ? `${sensor.model || "Govee"} · ${sensor.address || "BLE"}`
      : "Sensor";
    renderSummary();
  }

  function points(values, width = 800, height = 230) {
    if (values.length < 2) return "";
    const finite = values.filter((value) => Number.isFinite(value));
    if (finite.length < 2) return "";
    let min = Math.min(...finite);
    let max = Math.max(...finite);
    if (Math.abs(max - min) < 0.1) {
      min -= 1;
      max += 1;
    } else {
      const margin = (max - min) * 0.12;
      min -= margin;
      max += margin;
    }
    const pad = 10;
    return values.map((value, index) => {
      if (!Number.isFinite(value)) return null;
      const x = pad + (index / Math.max(1, values.length - 1)) * (width - pad * 2);
      const y = height - pad - ((value - min) / (max - min)) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).filter(Boolean).join(" ");
  }

  async function loadHistory() {
    const hours = Number($("climate-period").value || 24);
    try {
      const { response, data } = await request(`/api/smart-home/history?hours=${hours}`);
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not load climate history.");
      const samples = Array.isArray(data.samples) ? data.samples : [];
      $("temperature-line").setAttribute(
        "points",
        points(samples.map((row) => Number(row.temperature_c))),
      );
      $("humidity-line").setAttribute(
        "points",
        points(samples.map((row) => Number(row.humidity_percent))),
      );
      $("climate-samples").textContent = `${samples.length} sample${samples.length === 1 ? "" : "s"}`;

      const temp = data.stats?.temperature;
      const humidity = data.stats?.humidity;
      $("climate-temp-range").textContent = temp
        ? `min ${temp.min} · avg ${temp.avg} · max ${temp.max} °C`
        : "No history";
      $("climate-humidity-range").textContent = humidity
        ? `min ${humidity.min} · avg ${humidity.avg} · max ${humidity.max} %`
        : "No history";
    } catch (error) {
      toast(error.message, false);
    }
  }

  async function refreshClimate() {
    const button = $("climate-refresh");
    setBusy(button, true, "Scanning…");
    try {
      const result = await queueCommand("smart-home-climate-refresh", {}, { timeoutMs: 30000 });
      sensors = Array.isArray(result.sensors) ? result.sensors : [];
      renderClimateLive();
      await loadHistory();
      toast(sensors.length ? "Live climate reading stored." : "No supported climate sensor received.", Boolean(sensors.length));
      loadRecent();
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  function channelOptions(selected = "") {
    const rows = channels.filter((channel) => [0, 5, 15].includes(Number(channel.type)));
    return '<option value="">None</option>' + rows.map((channel) => (
      `<option value="${escapeHtml(channel.id)}" ${String(channel.id) === String(selected) ? "selected" : ""}>#${escapeHtml(channel.name)}</option>`
    )).join("");
  }

  function populateChannels() {
    const alertCurrent = $("alert-channel").value;
    const scheduleCurrent = $("schedule-channel").value;
    $("alert-channel").innerHTML = channelOptions(alertCurrent).replace("None", "Select channel…");
    $("schedule-channel").innerHTML = channelOptions(scheduleCurrent);
  }

  async function loadChannels() {
    try {
      const { response, data } = await request(`/api/discord/guilds/${GUILD_ID}`);
      if (!response.ok || !data.ok) throw new Error(data.message || "Discord channels unavailable.");
      channels = Array.isArray(data.channels) ? data.channels : [];
      populateChannels();
    } catch (error) {
      channels = [];
      populateChannels();
      toast(`Discord channel list: ${error.message}`, false);
    }
  }

  async function loadAlerts() {
    try {
      const { response, data } = await request("/api/smart-home/alerts");
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not load alert config.");
      const config = data.config || {};
      $("alert-enabled").checked = Boolean(Number(config.enabled));
      $("alert-channel").value = config.channel_id ? String(config.channel_id) : "";
      $("alert-temp-min").value = config.temp_min ?? "";
      $("alert-temp-max").value = config.temp_max ?? "";
      $("alert-humidity-min").value = config.humidity_min ?? "";
      $("alert-humidity-max").value = config.humidity_max ?? "";
      $("alert-cooldown").value = config.cooldown_minutes ?? 60;
      $("alert-last-state").textContent = config.last_reason
        ? `Last alert: ${config.last_reason}`
        : config.last_fired_at
          ? `Last fired: ${config.last_fired_at}`
          : "No climate alert has fired yet.";
      $("summary-alerts").textContent = Number(config.enabled) ? "Enabled" : "Disabled";
      $("summary-alert-detail").textContent = Number(config.enabled)
        ? `Cooldown ${config.cooldown_minutes || 60} min`
        : "Climate thresholds";
    } catch (error) {
      $("summary-alerts").textContent = "Unavailable";
      toast(error.message, false);
    }
  }

  function nullableNumber(id) {
    const value = $(id).value.trim();
    return value === "" ? null : Number(value);
  }

  async function saveAlerts() {
    const button = $("save-alerts");
    setBusy(button, true, "Saving…");
    try {
      const payload = {
        enabled: $("alert-enabled").checked,
        channel_id: $("alert-channel").value || null,
        temp_min: nullableNumber("alert-temp-min"),
        temp_max: nullableNumber("alert-temp-max"),
        humidity_min: nullableNumber("alert-humidity-min"),
        humidity_max: nullableNumber("alert-humidity-max"),
        cooldown_minutes: Number($("alert-cooldown").value || 60),
      };
      if (payload.enabled && !payload.channel_id) {
        throw new Error("Select a Discord channel before enabling alerts.");
      }
      const { response, data } = await request("/api/smart-home/alerts", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not save alerts.");
      await loadAlerts();
      toast("Discord climate alerts saved.");
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(button, false);
    }
  }

  function weekdayText(raw) {
    const names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const values = String(raw || "").split(",")
      .map((value) => Number(value))
      .filter((value) => value >= 0 && value <= 6);
    if (values.length === 7) return "Daily";
    return values.map((value) => names[value]).join(", ") || "No days";
  }

  function renderSchedules() {
    const list = $("schedule-list");
    $("summary-schedules").textContent = String(schedules.filter((row) => Number(row.enabled)).length);
    if (!schedules.length) {
      list.innerHTML = '<div class="sh-empty">No Smart Home scenes are scheduled.</div>';
      return;
    }

    list.innerHTML = schedules.map((row) => `
      <article class="schedule-row ${Number(row.enabled) ? "enabled" : ""}" data-schedule-id="${row.id}">
        <span></span>
        <div>
          <strong>${escapeHtml(row.name)}</strong>
          <small>${escapeHtml(row.run_time)} · ${escapeHtml(weekdayText(row.weekdays))} · ${escapeHtml(row.preset)} · ${escapeHtml(row.device_selector === "all" ? "all devices" : row.device_selector)}${row.last_result ? ` · last: ${escapeHtml(row.last_result)}` : ""}</small>
        </div>
        <div class="schedule-row-actions">
          <button type="button" data-schedule-action="edit">Edit</button>
          <button type="button" data-schedule-action="toggle">${Number(row.enabled) ? "Disable" : "Enable"}</button>
          <button class="delete" type="button" data-schedule-action="delete">Delete</button>
        </div>
      </article>
    `).join("");

    list.querySelectorAll("[data-schedule-action]").forEach((button) => {
      button.addEventListener("click", () => scheduleRowAction(button));
    });
  }

  async function loadSchedules() {
    try {
      const { response, data } = await request("/api/smart-home/schedules");
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not load schedules.");
      schedules = Array.isArray(data.schedules) ? data.schedules : [];
      renderSchedules();
    } catch (error) {
      toast(error.message, false);
    }
  }

  function resetScheduleForm() {
    $("schedule-id").value = "";
    $("schedule-name").value = "Night routine";
    $("schedule-target").value = "all";
    $("schedule-preset").value = "night";
    $("schedule-time").value = "23:30";
    $("schedule-channel").value = "";
    $("schedule-enabled").checked = true;
    document.querySelectorAll('input[name="weekday"]').forEach((input) => {
      input.checked = true;
    });
  }

  function editSchedule(row) {
    $("schedule-id").value = row.id;
    $("schedule-name").value = row.name || "";
    $("schedule-target").value = [...$("schedule-target").options].some((option) => option.value === row.device_selector)
      ? row.device_selector
      : "all";
    $("schedule-preset").value = row.preset || "off";
    $("schedule-time").value = row.run_time || "23:30";
    $("schedule-channel").value = row.notify_channel_id ? String(row.notify_channel_id) : "";
    $("schedule-enabled").checked = Boolean(Number(row.enabled));
    const weekdays = new Set(String(row.weekdays || "").split(","));
    document.querySelectorAll('input[name="weekday"]').forEach((input) => {
      input.checked = weekdays.has(input.value);
    });
    $("schedule-form").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function saveSchedule(event) {
    event.preventDefault();
    const submit = $("schedule-form").querySelector('button[type="submit"]');
    setBusy(submit, true, "Saving…");
    try {
      const weekdays = [...document.querySelectorAll('input[name="weekday"]:checked')]
        .map((input) => Number(input.value));
      const payload = {
        action: "save",
        id: Number($("schedule-id").value || 0),
        name: $("schedule-name").value.trim(),
        device_selector: $("schedule-target").value,
        preset: $("schedule-preset").value,
        run_time: $("schedule-time").value,
        weekdays,
        notify_channel_id: $("schedule-channel").value || null,
        enabled: $("schedule-enabled").checked,
      };
      const { response, data } = await request("/api/smart-home/schedules", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not save schedule.");
      resetScheduleForm();
      await loadSchedules();
      toast("Smart Home schedule saved.");
    } catch (error) {
      toast(error.message, false);
    } finally {
      setBusy(submit, false);
    }
  }

  async function scheduleRowAction(button) {
    const rowNode = button.closest("[data-schedule-id]");
    const row = schedules.find((item) => String(item.id) === String(rowNode?.dataset.scheduleId));
    if (!row) return;
    const action = button.dataset.scheduleAction;

    if (action === "edit") {
      editSchedule(row);
      return;
    }

    if (action === "delete") {
      if (!confirm(`Delete schedule "${row.name}"?`)) return;
      setBusy(button, true, "Deleting…");
      try {
        const { response, data } = await request("/api/smart-home/schedules", {
          method: "POST",
          body: JSON.stringify({ action: "delete", id: row.id }),
        });
        if (!response.ok || !data.ok) throw new Error(data.message || "Could not delete schedule.");
        await loadSchedules();
        toast("Schedule deleted.");
      } catch (error) {
        toast(error.message, false);
      } finally {
        setBusy(button, false);
      }
      return;
    }

    if (action === "toggle") {
      setBusy(button, true, "Saving…");
      try {
        const payload = {
          action: "save",
          id: row.id,
          name: row.name,
          device_selector: row.device_selector,
          preset: row.preset,
          run_time: row.run_time,
          weekdays: String(row.weekdays).split(",").map(Number),
          notify_channel_id: row.notify_channel_id,
          enabled: !Number(row.enabled),
        };
        const { response, data } = await request("/api/smart-home/schedules", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        if (!response.ok || !data.ok) throw new Error(data.message || "Could not update schedule.");
        await loadSchedules();
        toast(payload.enabled ? "Schedule enabled." : "Schedule disabled.");
      } catch (error) {
        toast(error.message, false);
      } finally {
        setBusy(button, false);
      }
    }
  }

  function actionLabel(action) {
    return String(action || "")
      .replace(/^smart-home-/, "")
      .replaceAll("-", " ");
  }

  async function loadRecent() {
    try {
      const { response, data } = await request("/api/smart-home/recent");
      if (!response.ok || !data.ok) throw new Error(data.message || "Could not load activity.");
      const rows = Array.isArray(data.commands) ? data.commands : [];
      const box = $("smart-activity");
      if (!rows.length) {
        box.innerHTML = '<div class="sh-empty">No Smart Home dashboard actions yet.</div>';
        return;
      }
      box.innerHTML = rows.map((row) => `
        <div class="activity-row ${escapeHtml(row.status)}">
          <span class="activity-dot"></span>
          <div>
            <strong>${escapeHtml(actionLabel(row.action))}</strong>
            <small>${escapeHtml(row.result || (row.status === "pending" ? "Waiting for Raspberry-Bot…" : ""))}</small>
          </div>
          <time>${escapeHtml(row.processed_at || row.created_at || "")}</time>
        </div>
      `).join("");
    } catch (error) {
      toast(error.message, false);
    }
  }

  async function init() {
    try {
      await bootstrap();
    } catch (error) {
      toast(error.message, false);
      return;
    }

    $("scan-devices").addEventListener("click", () => refreshSnapshot(true));
    $("refresh-snapshot").addEventListener("click", () => refreshSnapshot(false));
    $("climate-refresh").addEventListener("click", refreshClimate);
    $("climate-period").addEventListener("change", loadHistory);
    $("save-alerts").addEventListener("click", saveAlerts);
    $("schedule-form").addEventListener("submit", saveSchedule);
    $("new-schedule").addEventListener("click", resetScheduleForm);
    $("cancel-schedule").addEventListener("click", resetScheduleForm);
    $("refresh-activity").addEventListener("click", loadRecent);
    document.querySelectorAll("[data-scene]").forEach((button) => {
      button.addEventListener("click", () => runScene(button));
    });

    await loadChannels();
    await Promise.all([
      loadAlerts(),
      loadSchedules(),
      loadHistory(),
      loadRecent(),
    ]);
    await refreshSnapshot(false);
  }

  init();
})();
