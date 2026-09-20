"use strict";
async function refreshVpn() {
  const active = document.getElementById("active");
  const mode = document.getElementById("mode");
  const list = document.getElementById("profiles");
  try {
    const response = await fetch("/api/vpn/status", {credentials: "same-origin", cache: "no-store"});
    if (!response.ok) throw new Error("Status konnte nicht geladen werden.");
    const data = await response.json();
    active.textContent = data.active_interfaces.length
      ? "Aktive WireGuard-Interfaces: " + data.active_interfaces.join(", ")
      : "Kein aktives WireGuard-Interface erkannt.";
    mode.textContent = data.message;
    list.replaceChildren();
    if (!data.profiles.length) {
      const li = document.createElement("li");
      li.textContent = "Keine lesbaren Profile gefunden.";
      list.append(li);
    }
    for (const profile of data.profiles) {
      const li = document.createElement("li");
      li.textContent = profile + (data.active_profiles.includes(profile) ? " (aktiv)" : "");
      list.append(li);
    }
  } catch (error) {
    active.textContent = error.message;
    mode.textContent = "";
    list.replaceChildren();
  }
}
document.getElementById("refresh").addEventListener("click", refreshVpn);
refreshVpn();
