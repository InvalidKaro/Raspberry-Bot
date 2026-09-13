(() => {
  const $ = (selector) => document.querySelector(selector);

  function duration(seconds) {
    const total = Number(seconds || 0);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return `${days ? `${days} Tage ` : ""}${hours ? `${hours} Std. ` : ""}${minutes} Min.`;
  }

  function serviceRow(service) {
    const row = document.createElement("div");
    row.className = "service";

    const name = document.createElement("b");
    name.textContent = String(service?.name || "Unbekannter Service");

    const pill = document.createElement("span");
    pill.className = `pill${service?.online ? "" : " bad"}`;
    pill.textContent = service?.online ? "Online" : "Offline";

    row.append(name, pill);
    return row;
  }

  async function refresh() {
    try {
      const response = await fetch("/api/public/status", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error("Status nicht verfügbar");

      const operational = data.status === "operational";
      $("#dot").className = `dot ${operational ? "" : "warn"}`.trim();
      $("#status").textContent = operational
        ? "Alle Kernsysteme betriebsbereit"
        : "Eingeschränkter Betrieb";
      $("#uptime").textContent = `Host-Uptime: ${duration(data.uptime_seconds)}`;

      const services = $("#services");
      services.replaceChildren(...(Array.isArray(data.services) ? data.services.map(serviceRow) : []));
      $("#updated").textContent = `Zuletzt geprüft: ${new Date(data.updated_at).toLocaleString("de-DE")}`;
    } catch (error) {
      $("#dot").className = "dot bad";
      $("#status").textContent = "Status derzeit nicht abrufbar";
      $("#uptime").textContent = error instanceof Error ? error.message : "Unbekannter Fehler";
    }
  }

  refresh();
  window.setInterval(refresh, 60000);
})();
