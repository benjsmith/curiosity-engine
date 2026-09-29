/* embed.js — dual-mount CE embed API for Switchbay (and hosts).
 *
 * Switchbay loads CE static modules without main.js, then calls:
 *   const h = await CEEmbed.create({ embed:true, dataUrl, publicBase, chrome:false })
 *   h.mountSidebar(sidebarEl)
 *   h.mountCanvas(canvasEl)   // first paint or instant reattach from park
 *   h.unmountCanvas()         // soft park (keeps atlas); {destroy:true} hard
 *   h.destroy()
 *
 * One shared data.json session across both mounts. Standalone main.js
 * is untouched (this file only defines window.CEEmbed).
 */
(function () {
  'use strict';

  /** Minimal sidebar fragment if host did not inject #sidebar. */
  var SIDEBAR_HTML =
    '<aside id="sidebar">' +
    '  <header class="sidebar-head">' +
    '    <button id="sidebar-toggle" class="icon-btn" title="Collapse sidebar" aria-label="Collapse sidebar">' +
    '      <svg viewBox="0 0 16 16" width="14" height="14"><path d="M10 4 L6 8 L10 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    '    </button>' +
    '    <span class="workspace-name"></span>' +
    '    <div class="sidebar-mode-seg" role="group" aria-label="Sidebar mode">' +
    '      <button type="button" id="sidebar-mode-pages" class="sidebar-mode-btn" aria-pressed="true">Pages</button>' +
    '      <button type="button" id="sidebar-mode-files" class="sidebar-mode-btn" aria-pressed="false">Files</button>' +
    '    </div>' +
    '  </header>' +
    '  <div class="sidebar-search-wrap">' +
    '    <input id="sidebar-search" type="search" placeholder="Search pages…" autocomplete="off" spellcheck="false">' +
    '    <button id="sidebar-toggle-all" class="icon-btn sidebar-toggle-all" data-action="toggle-all-groups" title="Collapse or expand all" aria-label="Collapse or expand all">' +
    '      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6 L8 2.5 L13 6"/><path d="M3 10 L8 13.5 L13 10"/></svg>' +
    '    </button>' +
    '  </div>' +
    '  <div id="sidebar-list" class="sidebar-list" role="listbox"></div>' +
    '  <div id="filebrowser-pane" class="filebrowser-pane" hidden>' +
    '    <div class="filebrowser-toolbar">' +
    '      <input id="filebrowser-search" type="search" placeholder="Filter files…" autocomplete="off" spellcheck="false">' +
    '      <button type="button" id="filebrowser-refresh" class="icon-btn" title="Refresh">↻</button>' +
    '    </div>' +
    '    <div id="filebrowser-list" class="filebrowser-list" role="tree"></div>' +
    '    <div id="filebrowser-status" class="filebrowser-status" aria-live="polite"></div>' +
    '  </div>' +
    '  <footer class="sidebar-foot"><span class="meta-counts" id="meta-counts"></span></footer>' +
    '</aside>';

  /** Minimal canvas + modal if host did not inject #graph. */
  var CANVAS_HTML =
    '<main id="graph-pane">' +
    '  <div id="graph"></div>' +
    '  <div class="graph-search">' +
    '    <input id="graph-search-input" type="search" placeholder="Search wiki…" autocomplete="off" spellcheck="false" aria-label="Search the graph">' +
    '    <span id="graph-search-count" class="graph-search-count" hidden></span>' +
    '    <button id="graph-search-clear" class="icon-btn graph-search-clear" title="Clear search" aria-label="Clear search" hidden>' +
    '      <svg viewBox="0 0 16 16" width="12" height="12"><line x1="4" y1="4" x2="12" y2="12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><line x1="12" y1="4" x2="4" y2="12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' +
    '    </button>' +
    '  </div>' +
    '  <div class="graph-controls">' +
    '    <button id="viewer-mode" class="ctrl-btn hidden" title="Switch graph viewer"><span class="ctrl-label">view:</span><span id="viewer-mode-state">classic</span></button>' +
    '    <button id="label-mode" class="ctrl-btn" title="Label visibility"><span class="ctrl-label">labels:</span><span id="label-mode-state">auto</span></button>' +
    '    <button id="edge-mode" class="ctrl-btn hidden" title="Edge visibility"><span class="ctrl-label">edges:</span><span id="edge-mode-state">auto</span></button>' +
    '    <button id="label-types" class="ctrl-btn" title="Which page types show labels"><span class="ctrl-label">types:</span><span id="label-types-state">4/11</span></button>' +
    '    <div id="label-types-panel" class="label-types-panel hidden" role="dialog" aria-label="Label types"></div>' +
    '  </div>' +
    '  <button id="settings-trigger" class="icon-btn settings-trigger" title="Physics settings" aria-label="Physics settings" hidden></button>' +
    '  <div id="settings-panel" class="settings-panel hidden" role="dialog" aria-label="Physics settings"></div>' +
    '</main>' +
    '<div id="modal-backdrop" class="hidden" aria-hidden="true"></div>' +
    '<div id="modal" class="hidden" role="dialog" aria-modal="true" aria-hidden="true">' +
    '  <button id="modal-close" class="icon-btn modal-close" title="Close" aria-label="Close">' +
    '    <svg viewBox="0 0 16 16" width="12" height="12"><line x1="4" y1="4" x2="12" y2="12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><line x1="12" y1="4" x2="4" y2="12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' +
    '  </button>' +
    '  <div id="modal-content">' +
    '    <h1 id="modal-title"></h1>' +
    '    <section class="properties"><div class="properties-head">Properties</div><table id="modal-properties"></table></section>' +
    '    <article id="modal-body"></article>' +
    '    <section class="subgraph-pane"><div class="subgraph-head" id="modal-subgraph-head"></div><div id="modal-subgraph"></div></section>' +
    '  </div>' +
    '</div>';

  function resolveDataUrl(opts) {
    if (opts && opts.dataUrl) return opts.dataUrl;
    if (typeof window.ceApi === 'function') return window.ceApi('/data.json');
    var base = (opts && opts.publicBase) || window.CE_PUBLIC_BASE || '';
    base = String(base).replace(/\/$/, '');
    return base ? base + '/data.json' : 'data.json';
  }

  function ensureSidebarDom(el) {
    if (!el) throw new Error('CEEmbed.mountSidebar: missing element');
    if (el.querySelector('#sidebar') || el.id === 'sidebar') return;
    el.innerHTML = SIDEBAR_HTML;
  }

  function ensureCanvasDom(el) {
    if (!el) throw new Error('CEEmbed.mountCanvas: missing element');
    if (el.querySelector('#graph') || el.id === 'graph') return;
    el.innerHTML = CANVAS_HTML;
  }

  function pickViewerMode(data) {
    var atlasViewer = window.AtlasViewer;
    var wantAtlas = !!(
      atlasViewer &&
      window.KnowledgeAtlas &&
      (typeof atlasViewer.enabled === 'function' ? atlasViewer.enabled(data) : true)
    );
    var classicOk =
      !(atlasViewer && typeof atlasViewer.classicSafe === 'function') ||
      !!atlasViewer.classicSafe(data);
    var viewerMode = wantAtlas ? 'atlas' : 'classic';
    if (!classicOk && window.KnowledgeAtlas && atlasViewer) {
      viewerMode = 'atlas';
    }
    return { viewerMode: viewerMode, classicOk: classicOk };
  }

  async function create(opts) {
    opts = opts || {};
    var chrome = opts.chrome === true; // default false for embed
    var dataUrl = resolveDataUrl(opts);
    var destroyed = false;
    var data = null;
    var sidebarEl = null;
    var canvasEl = null;
    var sidebarInited = false;
    var canvasInited = false;
    var canvasParked = false;
    var parkHost = null;
    var graphApi = null;
    var hashHandler = null;
    var onCloseBound = false;

    try {
      document.body.dataset.ceEmbed = '1';
      document.body.classList.add('ce-embed');
      if (!chrome) document.body.classList.add('ce-embed-no-chrome');
    } catch (e) { /* ignore */ }

    var cacheApi = window.CEAtlasCache || null;
    var cacheKey = cacheApi
      ? cacheApi.resolveKey({
          cacheKey: opts.cacheKey,
          workspace: opts.workspace,
        })
      : null;
    var cachedPositions = null;
    var cachedTip = '';
    var usedCache = false;
    var revalidatePromise = null;
    var onDataUpdated = null; // set after mount helpers exist

    async function fetchFreshData() {
      var res = await fetch(dataUrl, { cache: 'no-store' });
      if (!res.ok) {
        throw new Error('CEEmbed.create: data ' + res.status + ' from ' + dataUrl);
      }
      return res.json();
    }

    async function persistCache(nextData, positions) {
      if (!cacheApi || !cacheKey || !nextData) return;
      try {
        await cacheApi.put(cacheKey, {
          generatedAt: cacheApi.tipOf(nextData),
          workspace: (nextData && nextData.workspace) || opts.workspace || '',
          data: nextData,
          positions: positions || cachedPositions || null,
        });
      } catch (e) {
        console.warn('[CEEmbed] cache put failed', e);
      }
    }

    // Cache-first: paint instantly from IndexedDB when present, then
    // background-fetch data.json and soft-refresh if the tip changed.
    if (cacheApi && cacheKey) {
      try {
        var cached = await cacheApi.get(cacheKey);
        if (cached && cached.data) {
          data = cached.data;
          cachedPositions = cached.positions || null;
          cachedTip = cached.generatedAt || cacheApi.tipOf(cached.data);
          usedCache = true;
          try { window.__CE_ATLAS_POSITIONS = cachedPositions; } catch (e) { /* ignore */ }
        }
      } catch (e) {
        console.warn('[CEEmbed] cache get failed', e);
      }
    }

    if (!data) {
      data = await fetchFreshData();
      cachedTip = cacheApi ? cacheApi.tipOf(data) : '';
      void persistCache(data, null);
    } else {
      revalidatePromise = (async function () {
        try {
          var fresh = await fetchFreshData();
          if (destroyed) return;
          var freshTip = cacheApi ? cacheApi.tipOf(fresh) : '';
          if (freshTip && freshTip === cachedTip) {
            // Tip unchanged — still refresh positions sidecar if missing.
            if (!cachedPositions) void persistCache(fresh, null);
            return;
          }
          data = fresh;
          cachedTip = freshTip;
          void persistCache(fresh, cachedPositions);
          if (typeof onDataUpdated === 'function') {
            onDataUpdated(fresh);
          }
        } catch (e) {
          console.warn('[CEEmbed] background revalidate failed', e);
        }
      })();
    }

    function applyHash() {
      if (destroyed || !canvasInited || !graphApi || !window.Modal) return;
      var m = window.location.hash.match(/^#page=([^&]+)$/);
      if (m) {
        var pageId = decodeURIComponent(m[1]);
        var ok = Modal.open(pageId);
        if (ok) {
          if (window.Sidebar && Sidebar.setActive) Sidebar.setActive(pageId);
          if (graphApi.focus) graphApi.focus(pageId);
        }
      } else {
        Modal.close();
        if (graphApi.clearFocus) graphApi.clearFocus();
      }
    }

    function bindHash() {
      if (hashHandler) return;
      hashHandler = applyHash;
      window.addEventListener('hashchange', hashHandler);
      if (window.Modal && Modal.setOnClose && !onCloseBound) {
        Modal.setOnClose(function () {
          if (graphApi && graphApi.clearFocus) graphApi.clearFocus();
        });
        onCloseBound = true;
      }
    }

    function unbindHash() {
      if (hashHandler) {
        window.removeEventListener('hashchange', hashHandler);
        hashHandler = null;
      }
    }

    async function refetchData(currentPageId) {
      if (destroyed) return;
      try {
        var url = dataUrl.indexOf('?') >= 0
          ? dataUrl + '&t=' + Date.now()
          : dataUrl + '?t=' + Date.now();
        var r = await fetch(url, { cache: 'no-store' });
        if (!r.ok) return;
        data = await r.json();
        if (cacheApi && cacheKey) {
          cachedTip = cacheApi.tipOf(data);
          void persistCache(data, cachedPositions);
        }
        if (window.Modal && Modal.refresh) Modal.refresh(data);
        if (window.FileBrowser && FileBrowser.refreshData) FileBrowser.refreshData(data);
        if (window.Subgraph && Subgraph.init) Subgraph.init(data);
        if (window.Sidebar && Sidebar.updateCounts) Sidebar.updateCounts(data);
        if (canvasInited && window.GraphSearch) {
          GraphSearch.init(data, graphApi);
        }
        if (currentPageId && window.Modal && Modal.open) {
          Modal.open(currentPageId);
          if (window.Sidebar && Sidebar.setActive) Sidebar.setActive(currentPageId);
        }
      } catch (e) {
        console.warn('[CEEmbed] refetch failed', e);
      }
    }

    function mountSidebar(el) {
      if (destroyed) return;
      ensureSidebarDom(el);
      // Idempotent: Sidebar.init adds input listeners; skip if already
      // bound to this element with live #sidebar-list.
      var same =
        sidebarInited &&
        sidebarEl === el &&
        !!el.querySelector('#sidebar-list');
      sidebarEl = el;
      if (same) return;

      // Theme toggle only when chrome allowed (shell often hosts theme).
      if (chrome && window.Theme && Theme.init) {
        try { Theme.init(); } catch (e) { /* ignore */ }
      }

      if (window.Sidebar && Sidebar.init) Sidebar.init(data);
      if (window.FileBrowser && FileBrowser.init) {
        try { FileBrowser.init(data); } catch (e) { console.warn('[CEEmbed] FileBrowser.init', e); }
      }
      sidebarInited = true;
    }

    function ensureParkHost() {
      if (parkHost && parkHost.isConnected) return parkHost;
      parkHost = document.createElement('div');
      parkHost.setAttribute('data-ce-canvas-park', '1');
      parkHost.setAttribute('aria-hidden', 'true');
      parkHost.style.cssText =
        'position:fixed;left:-10000px;top:0;width:800px;height:600px;' +
        'overflow:hidden;visibility:hidden;pointer-events:none;z-index:-1;';
      document.body.appendChild(parkHost);
      return parkHost;
    }

    function captureCanvasSize() {
      if (!canvasEl) return;
      var g = canvasEl.querySelector('#graph') || canvasEl;
      var w = g.clientWidth || canvasEl.clientWidth;
      var h = g.clientHeight || canvasEl.clientHeight;
      if (w > 0 && h > 0) {
        var host = ensureParkHost();
        host.style.width = w + 'px';
        host.style.height = h + 'px';
      }
    }

    function destroyCanvasInstance() {
      unbindHash();
      try {
        if (graphApi && typeof graphApi.destroy === 'function') graphApi.destroy();
      } catch (e) { /* ignore */ }
      graphApi = null;
      try { window.CEViewer = null; } catch (e) { /* ignore */ }
      try { delete document.body.dataset.viewer; } catch (e) { /* ignore */ }

      if (canvasEl) {
        var g = canvasEl.querySelector('#graph');
        if (g) g.innerHTML = '';
        try {
          if (window.Modal && Modal.close) Modal.close();
        } catch (e) { /* ignore */ }
      }
      if (parkHost) {
        try { parkHost.innerHTML = ''; } catch (e) { /* ignore */ }
      }
      canvasInited = false;
      canvasParked = false;
      onCloseBound = false;
    }

    /**
     * Soft by default: park canvas DOM off-screen and keep atlas/simulation
     * alive so Graph tab remounts are instant. Pass {destroy:true} for a
     * hard teardown (workspace switch / data remount / session destroy).
     */
    function unmountCanvas(opts) {
      var hard = !!(opts && opts.destroy === true);
      if (hard || !canvasInited) {
        destroyCanvasInstance();
        if (canvasEl) {
          try { canvasEl.innerHTML = ''; } catch (e) { /* ignore */ }
        }
        canvasEl = null;
        return;
      }
      // Soft park — move live DOM into an offscreen host sized to last paint
      // so KnowledgeAtlas ResizeObserver does not collapse to 0×0.
      unbindHash();
      try {
        if (window.Modal && Modal.close) Modal.close();
      } catch (e) { /* ignore */ }
      if (canvasEl) {
        captureCanvasSize();
        var host = ensureParkHost();
        while (canvasEl.firstChild) host.appendChild(canvasEl.firstChild);
      }
      canvasEl = null;
      canvasParked = true;
      // Keep graphApi + canvasInited — remount will reattach, not re-layout.
    }

    function remountParkedCanvas(el) {
      canvasEl = el;
      var host = ensureParkHost();
      while (host.firstChild) el.appendChild(host.firstChild);
      canvasParked = false;
      bindHash();
      applyHash();
      // ResizeObserver fires on reinsert; nudge a frame later for layout.
      try {
        requestAnimationFrame(function () {
          try {
            var g = el.querySelector('#graph');
            if (g && typeof ResizeObserver !== 'undefined') {
              // no-op touch so observers that missed the move still settle
              void g.clientWidth;
            }
          } catch (e) { /* ignore */ }
        });
      } catch (e) { /* ignore */ }
    }

    function mountCanvas(el) {
      if (destroyed) return;

      // Instant path: atlas still alive in park host — reattach only.
      if (canvasParked && canvasInited && graphApi) {
        remountParkedCanvas(el);
        return;
      }

      // Hard remount path: tear previous canvas without touching sidebar.
      if (canvasInited) unmountCanvas({ destroy: true });

      ensureCanvasDom(el);
      canvasEl = el;
      canvasParked = false;

      if (window.Subgraph && Subgraph.init) Subgraph.init(data);
      if (window.Modal && Modal.init) Modal.init(data);

      var pick = pickViewerMode(data);
      var viewerMode = pick.viewerMode;
      var classicOk = pick.classicOk;
      try { document.body.dataset.viewer = viewerMode; } catch (e) { /* ignore */ }

      if (window.AtlasViewer && AtlasViewer.initChoice) {
        try { AtlasViewer.initChoice(data, viewerMode); } catch (e) { /* ignore */ }
      }

      graphApi = window.Graph || null;
      var graphEl = el.querySelector('#graph') || document.getElementById('graph');

      if (viewerMode === 'atlas' && window.AtlasViewer && window.KnowledgeAtlas) {
        var atlas = AtlasViewer.init(data);
        if (atlas) {
          graphApi = atlas;
        } else if (classicOk && window.Graph) {
          Graph.init(data);
          graphApi = Graph;
          viewerMode = 'classic';
          try { document.body.dataset.viewer = viewerMode; } catch (e) { /* ignore */ }
        } else if (graphEl) {
          graphEl.innerHTML =
            '<div style="padding:28px;color:#ccc;font:14px system-ui">' +
            'Atlas failed to start. Classic is disabled for wikis over 1000 pages.</div>';
        }
      } else if (classicOk && window.Graph) {
        Graph.init(data);
        graphApi = Graph;
      } else if (graphEl) {
        graphEl.innerHTML =
          '<div style="padding:28px;color:#ccc;font:14px system-ui">' +
          'Classic is disabled for wikis over 1000 pages. Reload with Atlas.</div>';
      }

      // Re-bind GraphSearch to the (new) canvas API so sidebar hits still work.
      if (window.GraphSearch) GraphSearch.init(data, graphApi);
      window.CEViewer = graphApi;

      if (window.CurationReplay && typeof CurationReplay.bindViewer === 'function') {
        try { CurationReplay.bindViewer(graphApi); } catch (e) { /* ignore */ }
      }
      if (window.Edit && Edit.init) {
        try { Edit.init(data, refetchData); } catch (e) { /* ignore */ }
      }

      canvasInited = true;
      bindHash();
      applyHash();
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      unmountCanvas({ destroy: true });
      if (parkHost && parkHost.parentNode) {
        try { parkHost.parentNode.removeChild(parkHost); } catch (e) { /* ignore */ }
      }
      parkHost = null;
      canvasParked = false;
      if (sidebarEl) {
        try { sidebarEl.innerHTML = ''; } catch (e) { /* ignore */ }
      }
      sidebarEl = null;
      canvasEl = null;
      sidebarInited = false;
      data = null;
      try { window.CEViewer = null; } catch (e) { /* ignore */ }
      try { window.__CE_ATLAS_POSITIONS = null; } catch (e) { /* ignore */ }
      try {
        delete document.body.dataset.ceEmbed;
        delete document.body.dataset.viewer;
        document.body.classList.remove('ce-embed', 'ce-embed-no-chrome');
      } catch (e) { /* ignore */ }
    }

    onDataUpdated = function (fresh) {
      if (destroyed) return;
      data = fresh;
      // Tip changed while atlas live: hard remount canvas, keep sidebar.
      // Seed prior positions so overlapping ids skip force layout.
      if (canvasInited && canvasEl) {
        var el = canvasEl;
        var keepPos = cachedPositions;
        try {
          window.__CE_ATLAS_POSITIONS = keepPos;
        } catch (e) { /* ignore */ }
        try {
          unmountCanvas({ destroy: true });
        } catch (e) { /* ignore */ }
        try {
          mountCanvas(el);
        } catch (e) {
          console.warn('[CEEmbed] remount after tip change failed', e);
        }
      } else {
        // Sidebar-only: refresh list/counts.
        try {
          if (window.Sidebar && Sidebar.init) Sidebar.init(data);
          if (window.FileBrowser && FileBrowser.refreshData) FileBrowser.refreshData(data);
        } catch (e) { /* ignore */ }
      }
    };

    return {
      mountSidebar: mountSidebar,
      mountCanvas: mountCanvas,
      unmountCanvas: unmountCanvas,
      destroy: destroy,
      /** Shared session data (read-only for hosts / tests). */
      getData: function () { return data; },
      /** True when atlas/graph is initialized (mounted or soft-parked). */
      isCanvasLive: function () { return !!(canvasInited && graphApi && !destroyed); },
      /** Soft-refresh data.json without tearing down atlas layout. */
      revalidate: function (currentPageId) { return refetchData(currentPageId); },
      /** Cache key / tip for hosts. */
      getCacheInfo: function () {
        return {
          key: cacheKey,
          tip: cachedTip,
          usedCache: usedCache,
          hasPositions: !!cachedPositions,
        };
      },
    };
  }

  /**
   * One-shot: create + mount sidebar/canvas when mounts provided,
   * or mount canvas into container when it already has #graph.
   */
  async function mount(container, opts) {
    opts = opts || {};
    var handle = await create(opts);
    var mounts = opts.mounts || {};
    if (mounts.sidebar) handle.mountSidebar(mounts.sidebar);
    if (mounts.canvas) handle.mountCanvas(mounts.canvas);
    if (!mounts.sidebar && !mounts.canvas && container) {
      if (container.querySelector('#sidebar')) handle.mountSidebar(container);
      if (container.querySelector('#graph') || container.querySelector('#graph-pane')) {
        handle.mountCanvas(container);
      } else if (!container.querySelector('#sidebar')) {
        // Canvas-only legacy path (chrome:false stub).
        handle.mountCanvas(container);
      }
    }
    return handle;
  }

  window.CEEmbed = { create: create, mount: mount };
})();
