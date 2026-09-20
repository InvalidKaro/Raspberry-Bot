"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let csrf = "";
  let busy = false;
  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials:"same-origin", cache:"no-store", ...options,
      headers:{"Content-Type":"application/json", ...(options.method === "POST" ? {"X-CSRF-Token":csrf} : {})}
    });
    if (response.status === 401) { window.location.assign("/login"); throw new Error("Login erforderlich."); }
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.message || "Aktion fehlgeschlagen.");
    return data;
  }
  function render(data) {
    const currentSelection = $("profile").value;
    $("active").textContent = data.active_profile ? "Aktives Profil: " + data.active_profile : "Keine VPN-Proxy-Verbindung";
    $("ready").textContent = data.proxy_ready ? "Lokaler Proxy bereit" : "Proxy inaktiv";
    $("proxy").textContent = data.proxy || "Keine Anwendung wird über den VPN-Proxy geleitet.";
    $("mode").textContent = data.message;
    $("profile").replaceChildren();
    for (const profile of data.profiles || []) {
      const option = document.createElement("option");
      option.value = profile;
      option.textContent = profile + (profile === data.active_profile ? " · aktiv" : "");
      $("profile").append(option);
    }
    if ([...$("profile").options].some(option => option.value === currentSelection)) $("profile").value = currentSelection;
    $("connect").disabled = busy || !data.profiles.length;
    $("disconnect").disabled = busy || !data.active_profile;
  }
  async function refresh() {
    try { render(await api("/api/vpn/status")); }
    catch (error) { $("result").textContent = error.message; }
  }
  async function action(kind) {
    if (busy) return;
    const profile = $("profile").value;
    if (kind === "connect" && !profile) return;
    busy = true;
    $("connect").disabled = $("disconnect").disabled = true;
    $("result").textContent = kind === "connect" ? "Starte lokalen Proxy …" : "Trenne lokalen Proxy …";
    try {
      const data = await api("/api/vpn/" + kind, {method:"POST",body:JSON.stringify(kind === "connect" ? {profile} : {})});
      $("result").textContent = kind === "connect" ? "Lokaler Proxy gestartet. Externe IP bitte separat prüfen." : "Proxy getrennt.";
      render(data);
    } catch (error) {
      $("result").textContent = error.message;
    } finally { busy = false; await refresh(); }
  }
  $("connect").addEventListener("click", () => action("connect"));
  $("disconnect").addEventListener("click", () => action("disconnect"));
  $("refresh").addEventListener("click", refresh);
  (async () => {
    try { csrf = (await api("/api/bootstrap")).csrf || ""; await refresh(); }
    catch (error) { $("result").textContent = error.message; }
  })();
})();
