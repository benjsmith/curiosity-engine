/* Sidebar: blazingly responsive search + alphabetical page list.
 *
 * Search ranks via Fuse.js (prefix → substring → fuzzy). Title +
 * frontmatter properties are indexed; body text isn't (would balloon
 * the bundle). Empty query falls back to a static alphabetical list.
 */
window.Sidebar = (function () {
  let listEl, searchEl, fuse, allNodes, allPages;

  function init(data) {
    listEl = document.querySelector('#sidebar-list');
    searchEl = document.querySelector('#sidebar-search');
    allNodes = data.nodes;
    allPages = data.pages || {};

    // Build index records — node fields + property values flattened
    // into a single string so Fuse can match across both axes.
    const records = allNodes.map(n => {
      const page = allPages[n.id] || {};
      const props = Object.values(page.properties || {})
        .map(v => Array.isArray(v) ? v.join(' ') : String(v))
        .join(' ');
      return { id: n.id, title: n.title || n.id, type: n.type, props };
    });
    fuse = new Fuse(records, {
      keys: [
        { name: 'title', weight: 0.7 },
        { name: 'type',  weight: 0.1 },
        { name: 'props', weight: 0.2 },
      ],
      threshold: 0.35,
      ignoreLocation: true,
      minMatchCharLength: 1,
    });

    // Sidebar collapse / restore.
    const collapseBtn = document.querySelector('#sidebar-toggle');
    if (collapseBtn) {
      collapseBtn.addEventListener('click', () => {
        document.body.dataset.sidebar = 'collapsed';
      });
    }
    // A pinned restore button — added once, hidden by default; CSS
    // shows it when sidebar is collapsed.
    const restore = document.createElement('button');
    restore.className = 'icon-btn sidebar-restore';
    restore.setAttribute('title', 'Show sidebar');
    restore.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M6 4 L10 8 L6 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    restore.addEventListener('click', () => {
      document.body.dataset.sidebar = '';
    });
    document.querySelector('#graph-pane').appendChild(restore);

    // Default sorted alphabetical view.
    renderList(records.slice().sort((a, b) =>
      a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })));

    searchEl.addEventListener('input', (ev) => {
      const q = ev.target.value.trim();
      if (!q) {
        renderList(records.slice().sort((a, b) =>
          a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })));
        return;
      }
      const results = fuse.search(q, { limit: 200 });
      renderList(results.map(r => r.item));
    });

    // Footer counts.
    const counts = document.querySelector('#meta-counts');
    if (counts) {
      counts.textContent = `${allNodes.length} pages · ${(data.edges || []).length} links`;
    }
  }

  function renderList(items) {
    const html = items.map(rec =>
      `<button class="sidebar-row" data-id="${escapeAttr(rec.id)}" role="option">
         <span class="dot dot-${escapeAttr(rec.type)}"></span>
         <span class="row-title">${escapeHtml(rec.title)}</span>
       </button>`
    ).join('');
    listEl.innerHTML = html;
  }

  function setActive(pageId) {
    listEl.querySelectorAll('.sidebar-row').forEach(row => {
      row.dataset.active = (row.dataset.id === pageId) ? 'true' : '';
    });
    const active = listEl.querySelector(`.sidebar-row[data-id="${cssEscape(pageId)}"][data-active="true"]`);
    if (active && active.scrollIntoView) {
      active.scrollIntoView({ block: 'nearest' });
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s); }
  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, c => '\\' + c);
  }

  // Delegate clicks to the list element so search-result swap doesn't
  // need rebinding.
  document.addEventListener('click', (ev) => {
    const row = ev.target.closest && ev.target.closest('.sidebar-row');
    if (!row) return;
    window.location.hash = '#page=' + encodeURIComponent(row.dataset.id);
  });

  return { init, setActive };
})();
