const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

function ago(timestamp) {
  const value = Number(timestamp || 0);
  if (!value) return "–";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - value));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function num(value, digits = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "–";
}

function renderNodes(rows) {
  const peers = (Array.isArray(rows) ? rows : []).filter((row) => !row.is_local);
  $("node-count-pill").textContent = `${peers.length} Peers`;
  if (!peers.length) {
    $("node-rows").innerHTML = '<tr><td colspan="7" class="empty">Noch keine Nodes empfangen.</td></tr>';
    return;
  }
  $("node-rows").innerHTML = peers.map((row) => `<tr><td>${esc(row.name || row.short_name || "–")}</td><td>${esc(row.id || "–")}</td><td>${ago(row.last_heard)}</td><td>${num(row.snr, 1)}</td><td>${esc(row.hops_away ?? "–")}</td><td>${row.battery_level == null ? "–" : `${esc(row.battery_level)} %`}</td><td>${esc(row.hardware || "–")}</td></tr>`).join("");
}

function renderMessages(rows) {
  const items = Array.isArray(rows) ? [...rows].reverse() : [];
  $("message-count").textContent = String(items.length);
  if (!items.length) {
    $("messages").innerHTML = '<div class="empty">Noch keine Meshtastic-Nachrichten gespeichert.</div>';
    return;
  }
  $("messages").innerHTML = items.map((item) => `<div class="message"><div class="message-head"><span>${esc(item.from || "Unbekannt")}</span><span>${ago(item.received_at)}</span></div><div class="message-text">${esc(item.text || "")}</div></div>`).join("");
}

async function load() {
  try {
    const response = await fetch("/api/meshtastic", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const state = payload.state || {};
    const local = state.local || {};
    const radio = state.radio || {};
    const message = state.last_message || {};
    const display = payload.display2 || {};
    const connected = Boolean(state.connected);

    $("dot").classList.toggle("online", connected);
    $("connection").textContent = connected ? "Online" : "Offline / Retry";
    $("device").textContent = state.device || "auto";
    $("nodes").textContent = String(state.nodes_total || 0);
    $("active").textContent = `${state.nodes_active_10m || 0} aktiv · 10 min · ${state.nodes_active_60m || 0} in 1 h`;
    $("rx").textContent = String(state.rx_packets || 0);
    $("last-packet").textContent = `Letztes Paket: ${ago(state.last_packet_at)}`;
    $("region").textContent = radio.region || "–";
    $("radio").textContent = [radio.modem_preset, radio.primary_channel, radio.tx_enabled == null ? "" : (radio.tx_enabled ? "TX an" : "TX aus")].filter(Boolean).join(" · ") || "–";
    $("local-name").textContent = local.name || local.short_name || local.id || "–";
    $("local-meta").textContent = [local.id, local.hardware].filter(Boolean).join(" · ") || "–";
    $("firmware").textContent = `Firmware ${local.firmware || "–"}`;
    $("rf").textContent = `RSSI ${num(state.last_rssi)} dBm · SNR ${num(state.last_snr, 1)} dB`;
    $("rf-from").textContent = `Von ${state.last_from || "–"}`;
    $("rf-age").textContent = ago(state.last_packet_at);
    $("message-from").textContent = `Von ${message.from || "–"}`;
    $("message-text").textContent = message.text || "Noch keine Nachricht";
    $("message-age").textContent = ago(message.received_at);
    $("display-mode").textContent = display.mode || "–";
    $("display-page").textContent = display.current_page || "–";
    $("display-meta").textContent = payload.display2_file_exists ? `OLED ${display.hardware_connected ? "verbunden" : "Standby"} · Bus ${display.i2c_bus ?? "–"} · ${display.i2c_address || "–"}` : "Display-2-Statusdatei noch nicht vorhanden";
    $("collector-meta").textContent = payload.state_file_exists ? `State ${payload.state_age_seconds == null ? "–" : `${Math.round(payload.state_age_seconds)}s`} alt · aktualisiert ${state.updated_at || "–"}` : "Collector-State noch nicht vorhanden";
    $("collector-error").textContent = state.last_error || "";
    renderNodes(state.nodes);
    renderMessages(state.messages);
  } catch (error) {
    $("collector-error").textContent = `Dashboard-Fehler: ${error.message}`;
  }
}

$("refresh").addEventListener("click", load);
load();
setInterval(load, 3000);
