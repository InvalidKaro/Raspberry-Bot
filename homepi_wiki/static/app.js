const prefix = window.HOMEPI_WIKI_PREFIX || '/wiki';
const input = document.getElementById('search');
const results = document.getElementById('search-results');
const menu = document.getElementById('menu');
const sidebar = document.getElementById('sidebar');
const storage = document.getElementById('storage');
let timer;

if (menu && sidebar) {
  menu.addEventListener('click', () => sidebar.classList.toggle('open'));
  document.addEventListener('click', (event) => {
    if (window.innerWidth <= 900 && sidebar.classList.contains('open') && !sidebar.contains(event.target) && event.target !== menu) {
      sidebar.classList.remove('open');
    }
  });
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

async function runSearch(query) {
  if (!results) return;
  if (!query.trim()) {
    results.hidden = true;
    results.innerHTML = '';
    return;
  }
  try {
    const response = await fetch(`${prefix}/api/search?q=${encodeURIComponent(query)}`, {cache: 'no-store'});
    const data = await response.json();
    const rows = data.results || [];
    if (!rows.length) {
      results.innerHTML = '<div style="padding:10px;color:#8ea0ba">Keine Treffer</div>';
    } else {
      results.innerHTML = rows.map(row => `
        <a href="${prefix}/page/${encodeURIComponent(row.slug)}">
          <b>${esc(row.title)}</b>
          <small>${esc(row.category)} · ${row.snippet || esc(row.description || '')}</small>
        </a>`).join('');
    }
    results.hidden = false;
  } catch (_) {
    results.innerHTML = '<div style="padding:10px;color:#8ea0ba">Suche derzeit nicht verfügbar</div>';
    results.hidden = false;
  }
}

if (input) {
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(input.value), 180);
  });
}

async function refreshStatus() {
  try {
    const response = await fetch(`${prefix}/api/status`, {cache: 'no-store'});
    const data = await response.json();
    if (storage && data.storage) {
      storage.textContent = `${data.storage.free_gb} GB frei · ${data.zim_count} ZIM-Datei${data.zim_count === 1 ? '' : 'en'}`;
    } else if (storage) {
      storage.textContent = data.kiwix_available ? `${data.zim_count} ZIM-Dateien erkannt` : 'USB/Kiwix noch nicht eingerichtet';
    }
  } catch (_) {
    const status = document.getElementById('server-status');
    if (status) status.textContent = 'Status nicht verfügbar';
  }
}
refreshStatus();
