(() => {
  'use strict';

  const state = {
    csrf: '', tables: [], table: '', metadata: null, rows: [], total: 0,
    limit: 50, offset: 0, query: '', editing: null, mode: 'edit'
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    tableList: $('tableList'), tableFilter: $('tableFilter'), tableTitle: $('tableTitle'),
    tableMeta: $('tableMeta'), rowSearch: $('rowSearch'), newRow: $('newRow'), reloadRows: $('reloadRows'),
    gridWrap: $('gridWrap'), gridHead: $('gridHead'), gridBody: $('gridBody'), emptyState: $('emptyState'),
    pagination: $('pagination'), pageInfo: $('pageInfo'), prevPage: $('prevPage'), nextPage: $('nextPage'),
    notice: $('notice'), editorBackdrop: $('editorBackdrop'), editorForm: $('editorForm'), editorTitle: $('editorTitle'),
    editorMode: $('editorMode'), editorWarning: $('editorWarning'), deleteRow: $('deleteRow'), saveRow: $('saveRow'),
    closeEditor: $('closeEditor'), cancelEditor: $('cancelEditor'), deleteBackdrop: $('deleteBackdrop'),
    deleteConfirm: $('deleteConfirm'), cancelDelete: $('cancelDelete'), confirmDelete: $('confirmDelete'),
    refreshTables: $('refreshTables')
  };

  async function api(url, options = {}) {
    const opts = { credentials: 'same-origin', ...options };
    opts.headers = { ...(opts.headers || {}) };
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    if (opts.method && !['GET', 'HEAD'].includes(opts.method.toUpperCase())) {
      opts.headers['X-CSRF-Token'] = state.csrf;
    }
    const response = await fetch(url, opts);
    let data;
    try { data = await response.json(); }
    catch { data = { ok: false, message: `HTTP ${response.status}` }; }
    if (!response.ok || !data.ok) throw new Error(data.message || `HTTP ${response.status}`);
    return data;
  }

  function showNotice(message, error = false) {
    els.notice.textContent = message;
    els.notice.classList.remove('hidden', 'error');
    if (error) els.notice.classList.add('error');
    clearTimeout(showNotice.timer);
    showNotice.timer = setTimeout(() => els.notice.classList.add('hidden'), 6500);
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  }

  function formatCell(value) {
    if (value === null) return '<span class="muted">NULL</span>';
    if (typeof value === 'object') return esc(JSON.stringify(value));
    const text = String(value);
    return esc(text.length > 180 ? `${text.slice(0, 177)}…` : text);
  }

  async function bootstrap() {
    const data = await api('/api/bootstrap');
    state.csrf = data.csrf;
    await loadTables();
  }

  async function loadTables() {
    try {
      const data = await api('/api/database/tables');
      state.tables = data.tables || [];
      renderTables();
    } catch (err) { showNotice(err.message, true); }
  }

  function renderTables() {
    const needle = els.tableFilter.value.trim().toLowerCase();
    els.tableList.innerHTML = '';
    state.tables.filter(t => !needle || t.name.toLowerCase().includes(needle)).forEach((table) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `table-item${state.table === table.name ? ' active' : ''}`;
      button.innerHTML = `<span>${esc(table.name)}</span><span class="count">${table.rows}</span>`;
      button.addEventListener('click', () => selectTable(table.name));
      els.tableList.appendChild(button);
    });
  }

  async function selectTable(name) {
    state.table = name; state.offset = 0; state.query = ''; els.rowSearch.value = '';
    renderTables();
    els.tableTitle.textContent = name;
    els.tableMeta.textContent = 'Metadaten werden geladen…';
    els.rowSearch.disabled = false; els.reloadRows.disabled = false; els.newRow.disabled = false;
    try {
      state.metadata = await api(`/api/database/admin/${encodeURIComponent(name)}/metadata`);
      if (!state.metadata.editable) {
        els.tableMeta.textContent = `${name} • Nur lesen • ${state.metadata.message}`;
      } else {
        els.tableMeta.textContent = `${name} • Primary Key: ${state.metadata.primary_key.join(', ')} • Safe Edit aktiv`;
      }
      await loadRows();
    } catch (err) { showNotice(err.message, true); }
  }

  async function loadRows() {
    if (!state.table) return;
    try {
      const params = new URLSearchParams({limit: state.limit, offset: state.offset});
      if (state.query) params.set('q', state.query);
      const data = await api(`/api/database/table/${encodeURIComponent(state.table)}?${params}`);
      state.rows = data.rows || []; state.total = data.total || 0;
      renderGrid(data.columns || []);
    } catch (err) { showNotice(err.message, true); }
  }

  function renderGrid(columns) {
    els.emptyState.classList.add('hidden'); els.gridWrap.classList.remove('hidden');
    els.gridHead.innerHTML = `<tr>${columns.map(c => `<th>${esc(c)}</th>`).join('')}<th>Aktion</th></tr>`;
    els.gridBody.innerHTML = '';
    for (const row of state.rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = columns.map(c => `<td title="${esc(row[c])}">${formatCell(row[c])}</td>`).join('') + '<td></td>';
      const actionCell = tr.lastElementChild;
      const edit = document.createElement('button');
      edit.type = 'button'; edit.className = 'row-action'; edit.textContent = state.metadata?.editable ? 'Bearbeiten' : 'Ansehen';
      edit.addEventListener('click', () => openEditor('edit', row));
      actionCell.appendChild(edit); els.gridBody.appendChild(tr);
    }
    if (!state.rows.length) {
      els.gridBody.innerHTML = `<tr><td colspan="${columns.length + 1}" class="muted">Keine Einträge gefunden.</td></tr>`;
    }
    const from = state.total ? state.offset + 1 : 0;
    const to = Math.min(state.offset + state.limit, state.total);
    els.pageInfo.textContent = `${from}–${to} von ${state.total}`;
    els.prevPage.disabled = state.offset <= 0;
    els.nextPage.disabled = state.offset + state.limit >= state.total;
    els.pagination.classList.remove('hidden');
  }

  function columnType(column) { return String(column.type || '').toUpperCase(); }
  function isBlob(column) { return columnType(column).includes('BLOB'); }
  function isNumeric(column) { return /(INT|REAL|FLOA|DOUB|NUM|DEC)/.test(columnType(column)); }

  function openEditor(mode, row = null) {
    state.mode = mode; state.editing = row ? structuredClone(row) : null;
    els.editorMode.textContent = mode === 'new' ? 'NEUER EINTRAG' : 'DATENBANK-EINTRAG';
    els.editorTitle.textContent = mode === 'new' ? `Eintrag in ${state.table} erstellen` : `${state.table} bearbeiten`;
    els.deleteRow.classList.toggle('hidden', mode !== 'edit' || !state.metadata?.editable);
    els.saveRow.classList.toggle('hidden', mode === 'edit' && !state.metadata?.editable);
    els.editorWarning.classList.toggle('hidden', Boolean(state.metadata?.editable));
    els.editorWarning.textContent = state.metadata?.message || '';
    els.editorForm.innerHTML = '';

    for (const column of state.metadata.columns) {
      const isPk = state.metadata.primary_key.includes(column.name);
      const current = row ? row[column.name] : undefined;
      const field = document.createElement('div'); field.className = 'field';
      const label = document.createElement('label');
      label.textContent = `${column.name}${isPk ? ' • PRIMARY KEY' : ''}${column.notnull ? ' • NOT NULL' : ''}`;
      const input = document.createElement('input');
      input.className = 'input'; input.dataset.column = column.name;
      input.dataset.type = column.type || '';
      input.disabled = (mode === 'edit' && isPk) || isBlob(column) || (mode === 'edit' && !state.metadata.editable);
      input.value = current == null ? '' : String(current);
      input.placeholder = column.default != null ? `Default: ${column.default}` : column.type || 'TEXT';
      const nullWrap = document.createElement('label'); nullWrap.className = 'nullable';
      const nullBox = document.createElement('input'); nullBox.type = 'checkbox'; nullBox.dataset.nullFor = column.name;
      nullBox.checked = mode === 'edit' && current === null;
      nullBox.disabled = column.notnull || input.disabled;
      const nullText = document.createElement('span'); nullText.textContent = 'NULL';
      nullWrap.append(nullBox, nullText);
      nullBox.addEventListener('change', () => { input.disabled = nullBox.checked || ((mode === 'edit' && isPk) || isBlob(column)); });
      const small = document.createElement('small');
      small.textContent = `${column.type || 'untyped'}${column.default != null ? ` • default ${column.default}` : ''}${isBlob(column) ? ' • BLOB editing disabled' : ''}`;
      field.append(label, input, nullWrap, small); els.editorForm.appendChild(field);
    }
    els.editorBackdrop.classList.remove('hidden');
  }

  function closeEditor() { els.editorBackdrop.classList.add('hidden'); state.editing = null; }

  function collectValues() {
    const values = {};
    for (const column of state.metadata.columns) {
      const input = els.editorForm.querySelector(`[data-column="${CSS.escape(column.name)}"]`);
      const nullBox = els.editorForm.querySelector(`[data-null-for="${CSS.escape(column.name)}"]`);
      if (!input || isBlob(column)) continue;
      if (state.mode === 'edit' && state.metadata.primary_key.includes(column.name)) continue;
      if (nullBox?.checked) { values[column.name] = null; continue; }
      if (state.mode === 'new' && input.value === '') continue;
      let value = input.value;
      if (isNumeric(column) && value !== '') {
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) throw new Error(`${column.name}: ungültige Zahl.`);
        value = parsed;
      }
      values[column.name] = value;
    }
    return values;
  }

  function currentKey() {
    const key = {};
    for (const name of state.metadata.primary_key) key[name] = state.editing[name];
    return key;
  }

  async function saveRow() {
    try {
      els.saveRow.disabled = true;
      const values = collectValues();
      let result;
      if (state.mode === 'new') {
        result = await api(`/api/database/admin/${encodeURIComponent(state.table)}/insert`, {method: 'POST', body: {values}});
      } else {
        result = await api(`/api/database/admin/${encodeURIComponent(state.table)}/update`, {
          method: 'PATCH', body: {key: currentKey(), values, expected: state.editing}
        });
      }
      closeEditor(); showNotice(`${result.message} Backup erstellt.`); await loadRows(); await loadTables();
    } catch (err) { showNotice(err.message, true); }
    finally { els.saveRow.disabled = false; }
  }

  function openDelete() { els.deleteConfirm.value = ''; els.deleteBackdrop.classList.remove('hidden'); els.deleteConfirm.focus(); }
  function closeDelete() { els.deleteBackdrop.classList.add('hidden'); }

  async function deleteRow() {
    if (els.deleteConfirm.value !== 'DELETE') { showNotice('Bitte DELETE exakt eingeben.', true); return; }
    try {
      els.confirmDelete.disabled = true;
      const result = await api(`/api/database/admin/${encodeURIComponent(state.table)}/delete`, {
        method: 'DELETE', body: {key: currentKey(), expected: state.editing, confirm: 'DELETE'}
      });
      closeDelete(); closeEditor(); showNotice(`${result.message} Backup erstellt.`); await loadRows(); await loadTables();
    } catch (err) { showNotice(err.message, true); }
    finally { els.confirmDelete.disabled = false; }
  }

  let searchTimer;
  els.tableFilter.addEventListener('input', renderTables);
  els.refreshTables.addEventListener('click', loadTables);
  els.reloadRows.addEventListener('click', loadRows);
  els.newRow.addEventListener('click', () => openEditor('new'));
  els.rowSearch.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = els.rowSearch.value.trim(); state.offset = 0; loadRows(); }, 280);
  });
  els.prevPage.addEventListener('click', () => { state.offset = Math.max(0, state.offset - state.limit); loadRows(); });
  els.nextPage.addEventListener('click', () => { state.offset += state.limit; loadRows(); });
  els.closeEditor.addEventListener('click', closeEditor); els.cancelEditor.addEventListener('click', closeEditor);
  els.saveRow.addEventListener('click', saveRow); els.deleteRow.addEventListener('click', openDelete);
  els.cancelDelete.addEventListener('click', closeDelete); els.confirmDelete.addEventListener('click', deleteRow);
  els.editorBackdrop.addEventListener('click', (e) => { if (e.target === els.editorBackdrop) closeEditor(); });
  els.deleteBackdrop.addEventListener('click', (e) => { if (e.target === els.deleteBackdrop) closeDelete(); });

  bootstrap().catch((err) => showNotice(err.message, true));
})();
