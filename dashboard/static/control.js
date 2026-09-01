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

  async function init() {
    try {
      await bootstrap();
      await load();
      $("refresh").addEventListener("click", load);
      $("sync").addEventListener("click", () => queue("sync"));
    } catch (error) {
      $("command-result").textContent = String(error);
    }
  }

  init();
})();
