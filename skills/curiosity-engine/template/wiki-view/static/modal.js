/* Doc viewer modal.
 *
 * open(pageId) populates the title + properties + body, sets
 * body[data-modal=open] to fade graph + sidebar, and shows the modal.
 * close() hides + clears state. Clicking the backdrop or the X button
 * closes; ESC also closes.
 */
window.Modal = (function () {
  let pages = {};
  let modal, backdrop, closeBtn, titleEl, propsEl, bodyEl;
  let onClose = null;

  function init(data) {
    pages = data.pages || {};
    modal = document.querySelector('#modal');
    backdrop = document.querySelector('#modal-backdrop');
    closeBtn = document.querySelector('#modal-close');
    titleEl = document.querySelector('#modal-title');
    propsEl = document.querySelector('#modal-properties');
    bodyEl = document.querySelector('#modal-body');

    backdrop.addEventListener('click', close);
    closeBtn.addEventListener('click', close);
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && document.body.dataset.modal === 'open') {
        close();
      }
    });

    // Body wikilink delegation — clicking a wikilink swaps the modal
    // to that page without closing.
    bodyEl.addEventListener('click', (ev) => {
      const a = ev.target.closest && ev.target.closest('a.wikilink');
      if (!a) return;
      ev.preventDefault();
      if (a.classList.contains('unresolved')) return;
      const target = a.dataset.page;
      if (target) {
        window.location.hash = '#page=' + encodeURIComponent(target);
      }
    });
    if (window.VaultSources) {
      VaultSources.bind(bodyEl);
      VaultSources.bind(propsEl);
    }
  }

  function open(pageId) {
    const page = pages[pageId];
    if (!page) {
      console.warn('Modal: unknown page', pageId);
      return false;
    }
    titleEl.textContent = page.title || pageId;
    renderProperties(page);
    bodyEl.innerHTML = page.body_html || '';
    finishBody(page);
    modal.classList.remove('hidden');
    backdrop.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    backdrop.setAttribute('aria-hidden', 'false');
    document.body.dataset.modal = 'open';
    bodyEl.parentElement.scrollTop = 0;
    if (window.Subgraph) Subgraph.render(pageId);
    if (window.Edit) Edit.updateForPage(page);
    return true;
  }

  /* Refresh the cached `pages` dict (called after a successful edit
   * so the modal shows the rebuilt body_html on next open). */
  function refresh(data) { pages = data.pages || {}; }

  function close() {
    modal.classList.add('hidden');
    backdrop.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    backdrop.setAttribute('aria-hidden', 'true');
    document.body.dataset.modal = '';
    if (window.Subgraph) Subgraph.clear();
    if (typeof onClose === 'function') onClose();
    // Strip the page=… part of the hash if present, so closing leaves
    // a clean URL the user can bookmark for the graph view.
    if (window.location.hash.startsWith('#page=')) {
      history.replaceState(null, '', window.location.pathname);
    }
  }

  /* setOnClose registers a *persistent* close listener (not one-shot).
   * main.js uses this to clear the graph focus on every close. */
  function setOnClose(cb) { onClose = cb; }

  function renderProperties(page) {
    const rows = [];
    rows.push(propRow('title', page.title, 'list'));
    rows.push(propRow('type',  page.type,  'list'));
    const props = page.properties || {};
    const order = ['created', 'updated', 'sources'];
    const seen = new Set(['title', 'type']);
    for (const key of order) {
      if (key in props) {
        rows.push(propRow(key, props[key], iconForKey(key)));
        seen.add(key);
      }
    }
    for (const [k, v] of Object.entries(props)) {
      if (seen.has(k)) continue;
      rows.push(propRow(k, v, 'list'));
    }
    propsEl.innerHTML = rows.join('');
  }

  function propRow(key, value, iconKind) {
    const v = formatValue(value);
    const icon = renderIcon(iconKind);
    return `<tr>
      <td class="prop-key">${icon}<span>${escapeHtml(key)}</span></td>
      <td class="prop-val">${v}</td>
    </tr>`;
  }

  function iconForKey(k) {
    if (k === 'created' || k === 'updated') return 'calendar';
    return 'list';
  }

  function renderIcon(kind) {
    if (kind === 'calendar') {
      return `<span class="prop-icon"><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><rect x="2.5" y="3.5" width="11" height="10" rx="1.5"/><line x1="2.5" y1="6.5" x2="13.5" y2="6.5"/><line x1="5.5" y1="2.5" x2="5.5" y2="4.5"/><line x1="10.5" y1="2.5" x2="10.5" y2="4.5"/></svg></span>`;
    }
    return `<span class="prop-icon"><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><line x1="3" y1="5" x2="13" y2="5"/><line x1="3" y1="8" x2="13" y2="8"/><line x1="3" y1="11" x2="13" y2="11"/></svg></span>`;
  }

  function finishBody(page) {
    if (window.VaultSources) VaultSources.enhance(bodyEl);
    // Source stubs: offer an explicit full-doc control above the preview.
    if (page && page.type === 'source') {
      const existing = bodyEl.querySelector('.vault-open-banner');
      if (!existing) {
        let name = null;
        const cite = bodyEl.querySelector('.cite-vault, .cite');
        if (cite) name = (cite.dataset && cite.dataset.vault) || cite.textContent;
        const srcs = page.properties && page.properties.sources;
        if (!name && Array.isArray(srcs) && srcs[0]) name = srcs[0];
        if (window.VaultSources) name = VaultSources.normalizeName(name);
        if (name) {
          const bar = document.createElement('p');
          bar.className = 'vault-open-banner';
          bar.innerHTML =
            '<button type="button" class="vault-open-btn cite-vault" data-vault="' +
            escapeHtml(name) +
            '">Open full vault source</button>' +
            '<span class="vault-open-hint">Source page is a summary — full extracted text opens in a new tab.</span>';
          bodyEl.insertBefore(bar, bodyEl.firstChild);
        }
      }
    }
  }

  function formatValue(v) {
    if (v == null) return '<span style="color:var(--text-faint)">—</span>';
    if (Array.isArray(v)) {
      return v.map(item => {
        const s = String(item);
        const vaultName = window.VaultSources && VaultSources.normalizeName(s);
        if (vaultName) {
          return `<div><button type="button" class="cite cite-vault prop-vault" data-vault="${escapeHtml(vaultName)}">${escapeHtml(vaultName)}</button></div>`;
        }
        return `<div>${escapeHtml(s)}</div>`;
      }).join('');
    }
    return escapeHtml(String(v));
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  return { init, open, close, setOnClose, refresh };
})();
