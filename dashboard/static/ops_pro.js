(() => {
  'use strict';

  const FIXED_GUILD = '1162733312226361454';
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function addProfessionalShell() {
    document.body.classList.add('ops-pro-v4');

    const brand = qs('.brand');
    if (brand && !qs('.ops-brand-mark', brand)) {
      brand.innerHTML = `
        <div class="ops-brand-mark">
          <div class="ops-brand-logo">HP</div>
          <div class="ops-brand-meta">
            <strong>HomePi Operations</strong>
            <span>Dashboard Pro · Control Plane</span>
          </div>
        </div>
        <small>Discord, Media, Reliability & Infrastructure</small>`;
    }

    const nav = qs('#nav');
    if (nav && !qs('.ops-nav-label', nav)) {
      const labels = {
        overview: 'Operations',
        discord: 'Discord',
        media: 'Runtime',
        tickets: 'Automation',
        hardware: 'Infrastructure'
      };
      Object.entries(labels).forEach(([tab, label]) => {
        const button = qs(`[data-tab="${tab}"]`, nav);
        if (!button) return;
        const marker = document.createElement('div');
        marker.className = 'ops-nav-label';
        marker.textContent = label;
        nav.insertBefore(marker, button);
      });

      const footer = document.createElement('div');
      footer.className = 'ops-side-footer';
      footer.innerHTML = `
        <div class="status-line"><span class="status-dot-live"></span><span>Dashboard service online</span></div>
        <code>Guild ${FIXED_GUILD}</code>`;
      nav.appendChild(footer);
    }

    const topbar = qs('.topbar');
    if (topbar && !qs('.ops-context-chip', topbar)) {
      const chip = document.createElement('div');
      chip.className = 'ops-context-chip';
      chip.innerHTML = `<span>Scope</span><strong>${FIXED_GUILD}</strong>`;
      const toolbar = qs('.toolbar', topbar);
      if (toolbar) toolbar.insertBefore(chip, toolbar.firstChild);
    }

    qsa('.section-title').forEach((title) => {
      const block = title.firstElementChild;
      if (!block || qs('.ops-section-kicker', block)) return;
      const kicker = document.createElement('div');
      kicker.className = 'ops-section-kicker';
      kicker.textContent = 'HomePi / Dashboard Pro';
      block.insertBefore(kicker, block.firstChild);
    });
  }

  function updateDocumentTitle(tab) {
    const section = qs(`#${tab}`);
    const title = section ? qs('.section-title h1', section) : null;
    document.title = title ? `${title.textContent} · HomePi Pro` : 'HomePi Dashboard Pro';
  }

  function wrapOpenTab() {
    if (typeof window.openTab !== 'function' || window.openTab.__opsProWrapped) return;
    const original = window.openTab;
    const wrapped = function(tab) {
      const result = original.apply(this, arguments);
      updateDocumentTitle(tab);
      if (tab === 'messages') setTimeout(loadMessageStatus, 80);
      return result;
    };
    wrapped.__opsProWrapped = true;
    window.openTab = wrapped;
  }

  function messageStatusContainer() {
    const sendButton = qs('#messages button[onclick="scheduleMessage()"]');
    if (!sendButton) return null;
    let root = qs('#opsMessageStatus');
    if (!root) {
      root = document.createElement('div');
      root.id = 'opsMessageStatus';
      root.className = 'ops-message-status';
      root.innerHTML = '<div class="ops-message-status-head"><span>Delivery status</span><button type="button" id="refreshMessageStatus">Refresh</button></div><div class="tiny">Noch keine Statusdaten geladen.</div>';
      sendButton.insertAdjacentElement('afterend', root);
      qs('#refreshMessageStatus', root).onclick = loadMessageStatus;
    }
    return root;
  }

  async function loadMessageStatus() {
    const root = messageStatusContainer();
    if (!root || typeof api !== 'function') return;
    try {
      const data = await api(`/api/ops/messages/status?guild_id=${encodeURIComponent(FIXED_GUILD)}`);
      const rows = Array.isArray(data.messages) ? data.messages : [];
      const head = '<div class="ops-message-status-head"><span>Delivery status</span><button type="button" id="refreshMessageStatus">Refresh</button></div>';
      root.innerHTML = head + (rows.length ? rows.map((row) => {
        const state = String(row.status || 'pending');
        const when = row.processed_at || row.send_at || row.created_at || '';
        const result = row.result || (state === 'pending' ? 'wartet auf Versand' : '—');
        return `<div class="ops-message-status-row ${state}"><span class="state"></span><div><b>${state.toUpperCase()}</b><div class="ops-message-result">${esc(result)}</div></div><span>${esc(when)}</span></div>`;
      }).join('') : '<div class="tiny">Noch keine Nachrichten aus dem Message Studio.</div>');
      qs('#refreshMessageStatus', root).onclick = loadMessageStatus;
    } catch (error) {
      root.innerHTML = `<div class="ops-message-status-head"><span>Delivery status</span></div><div class="tiny">${esc(error.message || String(error))}</div>`;
    }
  }

  function validButtons() {
    const raw = qs('#msgButtons')?.value.trim() || '';
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error('Link-Buttons müssen ein JSON-Array sein.');
    return parsed.slice(0, 5).map((button) => {
      const label = String(button?.label || 'Link').trim().slice(0, 80);
      const url = String(button?.url || '').trim();
      if (!url.startsWith('https://')) throw new Error(`Button „${label}“ braucht eine HTTPS-URL.`);
      return {label, url};
    });
  }

  function composerPayload() {
    const title = qs('#msgTitle')?.value.trim() || '';
    const description = qs('#msgBody')?.value.trim() || '';
    const footer = qs('#msgFooter')?.value.trim() || '';
    const image = qs('#msgImage')?.value.trim() || '';
    const color = qs('#msgColor')?.value.trim() || '#8b5cf6';
    const channelId = qs('#msgChannel')?.value || '';
    if (!channelId) throw new Error('Bitte zuerst einen Text-Channel auswählen.');
    if (!title && !description && !footer && !image) throw new Error('Die Nachricht ist leer. Titel oder Beschreibung eintragen.');
    if (image && !image.startsWith('https://')) throw new Error('Die Bild-URL muss mit https:// beginnen.');
    return {
      guild_id: FIXED_GUILD,
      channel_id: channelId,
      content: '',
      embed: {title, description, color, footer, image},
      buttons: validButtons()
    };
  }

  async function professionalScheduleMessage() {
    const button = qs('#messages button[onclick="scheduleMessage()"]');
    try {
      const payload = composerPayload();
      const rawSendAt = qs('#msgSendAt')?.value || '';
      const sendAt = rawSendAt ? new Date(rawSendAt) : null;
      if (sendAt && Number.isNaN(sendAt.getTime())) throw new Error('Der gewählte Versandzeitpunkt ist ungültig.');

      if (button) {
        button.disabled = true;
        button.textContent = sendAt ? 'Wird geplant …' : 'Wird gesendet …';
      }

      let result;
      if (!sendAt || sendAt.getTime() <= Date.now() + 15000) {
        result = await post('/api/ops/messages/send', payload);
        note(`Nachricht gesendet${result.message_id ? ` · ID ${result.message_id}` : ''}.`);
      } else {
        result = await post('/api/ops/messages', {...payload, send_at: sendAt.toISOString()});
        note(`Nachricht geplant · Job ${result.id || 'erstellt'}.`);
      }
      await loadMessageStatus();
      return result;
    } catch (error) {
      note(error.message || String(error), false);
      if (typeof window.showOpsDebug === 'function') window.showOpsDebug('Message Studio', error, 'Send');
      return null;
    } finally {
      if (button) {
        button.disabled = false;
        updateSendButtonLabel();
      }
    }
  }

  function updateSendButtonLabel() {
    const button = qs('#messages button[onclick="scheduleMessage()"]');
    if (!button) return;
    const hasFutureTime = Boolean(qs('#msgSendAt')?.value);
    button.textContent = hasFutureTime ? 'Nachricht planen' : 'Jetzt senden';
  }

  function setupMessageStudio() {
    if (!qs('#messages')) return;
    window.scheduleMessage = professionalScheduleMessage;
    const sendAt = qs('#msgSendAt');
    if (sendAt) sendAt.addEventListener('input', updateSendButtonLabel);
    updateSendButtonLabel();
    messageStatusContainer();

    const channel = qs('#msgChannel');
    if (channel) {
      channel.addEventListener('change', () => {
        const option = channel.selectedOptions?.[0];
        channel.title = option ? `Ziel: ${option.textContent}` : '';
      });
    }
  }

  function loadDisplayDesigner() {
    if (document.getElementById('ops-display-designer-script')) return;
    if (!document.getElementById('ops-display-designer-style')) {
      const link = document.createElement('link');
      link.id = 'ops-display-designer-style';
      link.rel = 'stylesheet';
      link.href = '/static/display_designer.css';
      document.head.appendChild(link);
    }
    const script = document.createElement('script');
    script.id = 'ops-display-designer-script';
    script.src = '/static/display_designer.js';
    script.defer = true;
    script.onerror = () => {
      if (typeof window.showOpsDebug === 'function') window.showOpsDebug('Pi Display Studio', 'display_designer.js konnte nicht geladen werden.', 'Asset');
    };
    document.body.appendChild(script);
  }

  function installFetchDiagnostics() {
    if (window.__opsProFetchDiagnostics) return;
    window.__opsProFetchDiagnostics = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async function(input, init) {
      const started = performance.now();
      try {
        const response = await nativeFetch(input, init);
        if (!response.ok && typeof window.showOpsDebug === 'function') {
          const url = typeof input === 'string' ? input : input?.url || String(input);
          let text = '';
          try { text = await response.clone().text(); } catch (_) {}
          window.showOpsDebug(`HTTP ${response.status}`, `${url}\n${text.slice(0, 3000)}`, `${Math.round(performance.now() - started)}ms`);
        }
        return response;
      } catch (error) {
        if (typeof window.showOpsDebug === 'function') {
          const url = typeof input === 'string' ? input : input?.url || String(input);
          window.showOpsDebug('Network Error', `${url}\n${error?.stack || error}`, `${Math.round(performance.now() - started)}ms`);
        }
        throw error;
      }
    };
  }

  function boot() {
    addProfessionalShell();
    wrapOpenTab();
    setupMessageStudio();
    installFetchDiagnostics();
    loadDisplayDesigner();
    updateDocumentTitle(location.hash.slice(1) || 'overview');
    if ((location.hash.slice(1) || 'overview') === 'messages') setTimeout(loadMessageStatus, 200);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
