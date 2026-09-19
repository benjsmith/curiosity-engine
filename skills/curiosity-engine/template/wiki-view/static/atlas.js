/* atlas.js — size-gated Knowledge Atlas embedding.
 *
 * When enabled, replaces the D3 force graph in #graph with the
 * Knowledge Atlas engine (vendored at static/vendor/knowledge-atlas.js,
 * built from packages/knowledge-atlas in the repo). Everything else —
 * sidebar, modal, subgraph navigator, editing — keeps working: the
 * atlas routes item-open through the same `#page=<id>` hash contract.
 *
 * Wikis with ≤1000 pages may switch Classic ↔ Atlas via the view:
 * control (preference in localStorage). Wikis with >1000 pages are
 * Atlas-only: Classic is never mounted (it hangs), and the chooser is
 * hidden. A leftover classic preference is cleared on large wikis.
 *
 * Explicit per-load override (also useful for development and tests):
 *   http://localhost:8090/?viewer=atlas
 *   http://localhost:8090/?viewer=classic
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'curiosity-engine.viewer';
  var MIN_ATLAS_PAGES = 360;
  var LABEL_TYPES_KEY = 'curiosity-engine.label-types';
  var LABEL_DEFAULTS = ['concept', 'entity', 'note', 'todo'];
  var PHYSICS_DEFAULTS = { charge: -1000, link: 220, collide: 28 };

  function readLabelTypes() {
    try {
      var saved = JSON.parse(localStorage.getItem(LABEL_TYPES_KEY) || 'null');
      if (Array.isArray(saved)) return new Set(saved);
    } catch (e) {}
    return new Set(LABEL_DEFAULTS);
  }

  function initAtlasControls(handle) {
    var mode = 'auto';
    var edgeMode = 'auto';
    var types = readLabelTypes();
    var modeButton = document.getElementById('label-mode');
    var modeState = document.getElementById('label-mode-state');
    var edgeButton = document.getElementById('edge-mode');
    var edgeState = document.getElementById('edge-mode-state');
    var typeButton = document.getElementById('label-types');
    var typeState = document.getElementById('label-types-state');
    var typePanel = document.getElementById('label-types-panel');
    var settingsButton = document.getElementById('settings-trigger');
    var settingsPanel = document.getElementById('settings-panel');
    if (edgeButton) edgeButton.classList.remove('hidden');
    // At large N the force solve is expensive; physics is fixed via
    // mount defaults and the gear UI is hidden so sliders cannot thrash it.
    try {
      var nAttr = document.getElementById('graph') && document.getElementById('graph').dataset.corpusSize;
      var n = nAttr ? parseInt(nAttr, 10) : 0;
      if (n >= 2000 && settingsButton) {
        settingsButton.classList.add('hidden');
        if (settingsPanel) settingsPanel.classList.add('hidden');
      }
    } catch (e) {}

    function paintLabels() {
      if (modeState) modeState.textContent = mode;
      if (typeState) typeState.textContent = types.size + '/12';
      handle.setLabels(mode, Array.from(types));
    }
    function paintEdges() {
      if (edgeState) edgeState.textContent = edgeMode;
      if (handle.setEdges) handle.setEdges(edgeMode);
    }
    // setLabels / setEdges re-render the resident scene (no rebuild, no
    // layout churn) — the cheapest repaint the engine API exposes.
    function repaint() {
      paintLabels();
      paintEdges();
    }
    function setMode(next) {
      mode = next;
      document.documentElement.dataset.labels = mode;
      paintLabels();
    }
    function cycleMode() {
      var order = ['auto', 'on', 'off'];
      setMode(order[(order.indexOf(mode) + 1) % order.length]);
    }
    function setEdgeMode(next) {
      edgeMode = next;
      document.documentElement.dataset.edges = edgeMode;
      paintEdges();
    }
    function cycleEdgeMode() {
      var order = ['auto', 'on', 'off'];
      setEdgeMode(order[(order.indexOf(edgeMode) + 1) % order.length]);
    }
    if (modeButton) modeButton.addEventListener('click', cycleMode);
    if (edgeButton) edgeButton.addEventListener('click', cycleEdgeMode);

    if (typePanel && typeButton) {
      typePanel.querySelectorAll('.label-types-row').forEach(function (row) {
        var key = row.dataset.type;
        var input = row.querySelector('input[type=checkbox]');
        if (!input) return;
        input.checked = types.has(key);
        input.addEventListener('change', function () {
          if (input.checked) types.add(key); else types.delete(key);
          try { localStorage.setItem(LABEL_TYPES_KEY, JSON.stringify(Array.from(types))); } catch (e) {}
          paintLabels();
        });
      });
      typeButton.addEventListener('click', function (ev) {
        ev.stopPropagation();
        typePanel.classList.toggle('hidden');
      });
      var typeReset = document.getElementById('label-types-reset');
      if (typeReset) typeReset.addEventListener('click', function () {
        types = new Set(LABEL_DEFAULTS);
        typePanel.querySelectorAll('.label-types-row').forEach(function (row) {
          var input = row.querySelector('input[type=checkbox]');
          if (input) input.checked = types.has(row.dataset.type);
        });
        try { localStorage.setItem(LABEL_TYPES_KEY, JSON.stringify(Array.from(types))); } catch (e) {}
        paintLabels();
      });
    }

    if (settingsPanel && settingsButton) {
      settingsButton.addEventListener('click', function (ev) {
        ev.stopPropagation();
        settingsPanel.classList.toggle('hidden');
      });
      function bind(inputId, valueId, key) {
        var input = document.getElementById(inputId);
        var output = document.getElementById(valueId);
        if (!input) return;
        input.addEventListener('input', function () {
          var value = parseFloat(input.value);
          if (output) output.textContent = input.value;
          var update = {}; update[key] = value;
          handle.setPhysics(update);
        });
      }
      bind('phys-charge', 'phys-charge-val', 'charge');
      bind('phys-link', 'phys-link-val', 'link');
      bind('phys-collide', 'phys-collide-val', 'collide');
      var physicsReset = document.getElementById('phys-reset');
      if (physicsReset) physicsReset.addEventListener('click', function () {
        Object.keys(PHYSICS_DEFAULTS).forEach(function (key) {
          var stem = key === 'charge' ? 'phys-charge' : key === 'link' ? 'phys-link' : 'phys-collide';
          var input = document.getElementById(stem);
          var output = document.getElementById(stem + '-val');
          if (input) input.value = PHYSICS_DEFAULTS[key];
          if (output) output.textContent = PHYSICS_DEFAULTS[key];
        });
        handle.setPhysics(PHYSICS_DEFAULTS);
      });
    }

    document.addEventListener('click', function (ev) {
      if (typePanel && !typePanel.classList.contains('hidden') &&
          !typePanel.contains(ev.target) && (!typeButton || !typeButton.contains(ev.target))) {
        typePanel.classList.add('hidden');
      }
      if (settingsPanel && !settingsPanel.classList.contains('hidden') &&
          !settingsPanel.contains(ev.target) && (!settingsButton || !settingsButton.contains(ev.target))) {
        settingsPanel.classList.add('hidden');
      }
    });
    paintLabels();
    paintEdges();
    return {
      setMode: setMode,
      cycleMode: cycleMode,
      setEdgeMode: setEdgeMode,
      cycleEdgeMode: cycleEdgeMode,
      repaint: repaint,
    };
  }

  /* Drop the scene's "current focus" decoration.
   *
   * The scene builder always designates one node as the focus — accent
   * ring, its edges lit at priority 1 — and when the host has no focus
   * it picks a deterministic entry node instead. A wiki that stays
   * resident as one full-graph scene never rebuilds on focus changes,
   * so that mark is stuck on a page the user never chose for the whole
   * session, trailing lit edges. Roles are read by the renderer per
   * frame and by the layout only while solving, which has already
   * happened by scene-ready — so demoting them afterwards changes the
   * picture and nothing else. */
  function stripFocusMark(engine) {
    var snap = engine && engine.snapshot ? engine.snapshot() : null;
    var scene = snap && snap.scene;
    if (!scene) return false;
    var changed = false;
    (scene.nodes || []).forEach(function (n) {
      if (n.role === 'focus') { n.role = 'neighbour'; changed = true; }
    });
    (scene.edges || []).forEach(function (e) {
      if (e.priority === 1) { e.priority = 5; changed = true; }
    });
    return changed;
  }

  /* Search hits wear the dashed halo — the renderer's "pinned" mark,
   * read straight off engine state at draw time.
   *
   * What this must NOT do is call pin()/unpin(): those ask for a scene
   * rebuild each, so a 40-hit query fires dozens of async rebuilds per
   * keystroke, and a rebuild landing after the search is cleared
   * repaints the stale halos — hits then stay highlighted for good.
   * Writing the array and asking for one repaint touches no scene at
   * all. Selection is cleared alongside: its solid accent ring is a
   * second, competing highlight on the same nodes. */
  function highlightSearch(handle, repaint, ids) {
    var engine = handle.engine;
    if (engine.trails && Array.isArray(engine.trails.pinned)) {
      engine.trails.pinned = (ids || []).slice();
    }
    if (engine.select) engine.select([], 'replace');
    if (repaint) repaint();
    var host = document.getElementById('graph');
    if (host) host.dataset.searchHits = String((ids || []).length);
  }

  function pageCount(data) {
    if (data && data.pages && typeof data.pages === 'object') {
      return Object.keys(data.pages).length;
    }
    return data && Array.isArray(data.nodes) ? data.nodes.length : 0;
  }

  /* Hard policy: Classic D3 force hangs above ~1k nodes (sync SVG +
   * pre-warm ticks). Wikis with MORE than 1000 pages are Atlas-only —
   * never Graph.init, never offer the view: chooser. */
  var CLASSIC_MAX_PAGES = 1000;

  function classicSafe(data) {
    return pageCount(data) <= CLASSIC_MAX_PAGES;
  }

  function eligible(data) {
    return pageCount(data) > MIN_ATLAS_PAGES;
  }

  function queryChoice() {
    try {
      var choice = new URLSearchParams(window.location.search).get('viewer');
      return choice === 'atlas' || choice === 'classic' ? choice : null;
    } catch (e) {
      return null;
    }
  }

  function clearClassicPreference() {
    try {
      if (localStorage.getItem(STORAGE_KEY) === 'classic') {
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch (e) {}
  }

  function atlasEnabled(data) {
    /* >1000 nodes: Atlas only. Ignore classic localStorage / ?viewer=classic. */
    if (!classicSafe(data)) {
      clearClassicPreference();
      return true;
    }
    var explicit = queryChoice();
    if (explicit) return explicit === 'atlas';
    try {
      return localStorage.getItem(STORAGE_KEY) === 'atlas';
    } catch (e) {
      return false;
    }
  }

  /* view: chooser only when Classic is still a safe option (≤1000).
   * Larger wikis stay on Atlas with no switcher. Changing mode is a
   * reload so Classic and Atlas lifecycles stay independent. */
  function initChoice(data, activeMode) {
    var button = document.getElementById('viewer-mode');
    var state = document.getElementById('viewer-mode-state');
    if (!button || !state || !window.KnowledgeAtlas) return;

    if (!classicSafe(data)) {
      button.classList.add('hidden');
      return;
    }

    state.textContent = activeMode;
    button.title = activeMode === 'atlas'
      ? 'Use the classic force graph'
      : 'Use the Knowledge Atlas';
    button.classList.remove('hidden');
    button.addEventListener('click', function () {
      var next = activeMode === 'atlas' ? 'classic' : 'atlas';
      try { localStorage.setItem(STORAGE_KEY, next); } catch (e) {}

      /* A query override outranks storage. Remove it when the chooser
       * is used so the click always takes effect; preserve every other
       * query parameter and the current #page route. */
      try {
        var url = new URL(window.location.href);
        url.searchParams.delete('viewer');
        window.location.assign(url.toString());
      } catch (e) {
        window.location.reload();
      }
    });
  }

  // Called by main.js instead of Graph.init when the flag is on.
  // Returns a Graph-compatible facade so focus()/clearFocus() callers
  // keep working.
  function init(data) {
    var container = document.getElementById('graph');
    if (!container || !window.KnowledgeAtlas) return null;
    container.innerHTML = '';

    var corpusSize = pageCount(data);
    container.dataset.corpusSize = String(corpusSize);
    /* Edge strokes: controlled by edgeMode (auto/on/off) — drawing only;
     * edges stay in the force graph and link counts. Default auto is a
     * sparse subset on large corpora (full draw when small). */
    var handle = window.KnowledgeAtlas.mount(container, {
      data: data,
      edgeMode: 'auto',
      // Hybrid: Classic field in the core, log-compressed individual
      // nodes on the rim. corpusSize makes the first frame that view
      // (not type-cluster bubbles). Pin capacity to this corpus so
      // first mount, remount, and viewport changes all render the
      // same individual-node scene. The rate HUD is drawn at the TOP
      // of the canvas (`fillText` y = -height/2+22).
      config: {
        layout: 'hybrid',
        corpusSize: corpusSize,
        coreCapacity: Math.max(1, corpusSize),
        maxVisibleNodes: Math.max(1, corpusSize),
        physics: {
          charge: PHYSICS_DEFAULTS.charge,
          link: PHYSICS_DEFAULTS.link,
          collide: PHYSICS_DEFAULTS.collide,
        },
        budget: {
          maxNodes: Math.max(1, corpusSize),
          maxAggregates: 0,
          maxEdges: Math.max(900, (data.edges || []).length),
        },
      },
      onOpenItem: function (id) {
        window.location.hash = '#page=' + encodeURIComponent(id);
      },
      onEvent: function (event) {
        // Every scene arrives carrying a focus mark. Take it off before
        // the user sees it; the engine's own scene-ready paint runs
        // after this callback, so no extra repaint is needed here.
        if (event && event.kind === 'scene-ready' && handle) {
          stripFocusMark(handle.engine);
        }
      },
    });
    var controls = initAtlasControls(handle);
    /* Split targeting (Atlas): Ctrl/⌘-drag rubber-band (Classic parity) +
     * engine multi-select sync. Scene-space boxQuery via hitTester. */
    var splitActive = false;
    var splitPolicies = new Map();
    var splitOnChange = null;
    var splitUnsub = null;
    var rubberCleanup = null;

    function defaultSplitPolicy(type) {
      if (window.KnowledgeAtlas && typeof KnowledgeAtlas.defaultPartitionPolicyForType === 'function') {
        return KnowledgeAtlas.defaultPartitionPolicyForType(type);
      }
      return (type === 'entity' || type === 'concept') ? 'copy' : 'move';
    }

    function pageTypeOf(id) {
      var page = data.pages && data.pages[id];
      var typ = page && (page.type || page.page_type);
      if (!typ && data.nodes) {
        for (var i = 0; i < data.nodes.length; i++) {
          if (data.nodes[i].id === id) { typ = data.nodes[i].type; break; }
        }
      }
      return typ;
    }

    function notifyAtlasSplit() {
      var out = [];
      splitPolicies.forEach(function (policy, id) { out.push({ id: id, policy: policy }); });
      if (splitOnChange) splitOnChange(out);
      try {
        window.dispatchEvent(new CustomEvent('ce:split-selection', { detail: { pages: out } }));
      } catch (e) {}
    }

    function syncEngineSelection() {
      if (handle.engine && handle.engine.select) {
        handle.engine.select(Array.from(splitPolicies.keys()), 'replace');
      }
      if (controls && controls.repaint) controls.repaint();
    }

    function addIdsToSplit(ids) {
      var added = false;
      (ids || []).forEach(function (id) {
        if (!id || splitPolicies.has(id)) return;
        splitPolicies.set(id, defaultSplitPolicy(pageTypeOf(id)));
        added = true;
      });
      if (added) {
        syncEngineSelection();
        notifyAtlasSplit();
      }
    }

    function isRubberClickRect(x0, y0, x1, y1) {
      if (window.KnowledgeAtlas && typeof KnowledgeAtlas.isRubberClick === 'function') {
        return KnowledgeAtlas.isRubberClick({ x0: x0, y0: y0, x1: x1, y1: y1 });
      }
      return Math.abs(x1 - x0) < 4 && Math.abs(y1 - y0) < 4;
    }

    function clientToScene(canvas, clientX, clientY) {
      var rect = canvas.getBoundingClientRect();
      if (window.KnowledgeAtlas && typeof KnowledgeAtlas.clientToScenePoint === 'function') {
        return KnowledgeAtlas.clientToScenePoint(clientX, clientY, rect);
      }
      return {
        x: clientX - rect.left - rect.width / 2,
        y: clientY - rect.top - rect.height / 2,
      };
    }

    function installAtlasRubberBand() {
      if (rubberCleanup) { try { rubberCleanup(); } catch (e) {} rubberCleanup = null; }
      var host = container;
      var rubberEl = null;

      function mainCanvas() {
        return host.querySelector('canvas:not(.atlas-minimap)');
      }

      function onDown(ev) {
        if (!splitActive) return;
        if (!(ev.ctrlKey || ev.metaKey)) return;
        if (ev.button != null && ev.button !== 0) return;
        var t = ev.target;
        if (t && t.closest && t.closest('.atlas-minimap')) return;
        var canvas = mainCanvas();
        if (!canvas) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        var x0 = ev.clientX, y0 = ev.clientY;
        rubberEl = document.createElement('div');
        rubberEl.className = 'split-rubber atlas-split-rubber';
        rubberEl.style.left = x0 + 'px';
        rubberEl.style.top = y0 + 'px';
        rubberEl.style.width = '0px';
        rubberEl.style.height = '0px';
        document.body.appendChild(rubberEl);

        function onMove(e2) {
          var xa = Math.min(x0, e2.clientX), ya = Math.min(y0, e2.clientY);
          rubberEl.style.left = xa + 'px';
          rubberEl.style.top = ya + 'px';
          rubberEl.style.width = Math.abs(e2.clientX - x0) + 'px';
          rubberEl.style.height = Math.abs(e2.clientY - y0) + 'px';
        }
        function onUp(e2) {
          window.removeEventListener('pointermove', onMove, true);
          window.removeEventListener('pointerup', onUp, true);
          window.removeEventListener('pointercancel', onUp, true);
          if (rubberEl && rubberEl.parentNode) rubberEl.parentNode.removeChild(rubberEl);
          rubberEl = null;
          var x1 = e2.clientX, y1 = e2.clientY;
          if (isRubberClickRect(x0, y0, x1, y1)) return;
          var canvas2 = mainCanvas();
          if (!canvas2 || !handle.engine || !handle.engine.hitTester) return;
          var a = clientToScene(canvas2, x0, y0);
          var b = clientToScene(canvas2, x1, y1);
          var hits = handle.engine.hitTester.boxQuery(a.x, a.y, b.x, b.y) || [];
          var ids = hits.map(function (h) { return h && h.id; }).filter(Boolean);
          addIdsToSplit(ids);
        }
        window.addEventListener('pointermove', onMove, true);
        window.addEventListener('pointerup', onUp, true);
        window.addEventListener('pointercancel', onUp, true);
      }

      host.addEventListener('pointerdown', onDown, true);
      rubberCleanup = function () {
        host.removeEventListener('pointerdown', onDown, true);
        if (rubberEl && rubberEl.parentNode) rubberEl.parentNode.removeChild(rubberEl);
        rubberEl = null;
      };
    }

    function atlasSplitEnter(seed, onChange) {
      splitActive = true;
      splitPolicies = new Map();
      (seed || []).forEach(function (s) {
        var id = typeof s === 'string' ? s : s && s.id;
        if (!id) return;
        var policy = (typeof s === 'object' && s.policy === 'copy') ? 'copy' : 'move';
        if (typeof s !== 'object' || !s.policy) {
          policy = defaultSplitPolicy(pageTypeOf(id));
        }
        splitPolicies.set(id, policy);
      });
      splitOnChange = onChange || null;
      if (splitUnsub) { try { splitUnsub(); } catch (e) {} splitUnsub = null; }
      if (handle.engine && handle.engine.on) {
        splitUnsub = handle.engine.on(function (ev) {
          if (!splitActive || !ev || ev.kind !== 'selection-changed') return;
          (ev.ids || []).forEach(function (id) {
            if (!splitPolicies.has(id)) {
              splitPolicies.set(id, defaultSplitPolicy(pageTypeOf(id)));
            }
          });
          notifyAtlasSplit();
        });
      }
      installAtlasRubberBand();
      syncEngineSelection();
      notifyAtlasSplit();
    }

    function atlasSplitExit() {
      splitActive = false;
      splitPolicies = new Map();
      splitOnChange = null;
      if (splitUnsub) { try { splitUnsub(); } catch (e) {} splitUnsub = null; }
      if (rubberCleanup) { try { rubberCleanup(); } catch (e) {} rubberCleanup = null; }
      if (handle.engine && handle.engine.select) handle.engine.select([], 'replace');
      if (controls && controls.repaint) controls.repaint();
    }

    // Covers a scene that landed before onEvent was wired.
    if (stripFocusMark(handle.engine)) controls.repaint();
    // When a static host shards edges to edges.json.gz, assign
    // data.edges after preload and call Sidebar.updateCounts(data)
    // so the footer does not stay at "N pages · 0 links".
    if (window.Sidebar && typeof Sidebar.updateCounts === 'function') {
      Sidebar.updateCounts(data);
    }

    return {
      focus: function (pageId) {
        handle.engine.focus(pageId, 'system');
        if (handle.engine.select) handle.engine.select([pageId], 'replace');
      },
      clearFocus: function () {
        if (handle.engine.select) handle.engine.select([], 'replace');
        if (handle.engine.clearFocus) handle.engine.clearFocus();
        stripFocusMark(handle.engine);
        if (controls && controls.repaint) controls.repaint();
      },
      highlightSearch: function (ids) {
        highlightSearch(handle, controls.repaint, ids);
      },
      splitEnter: atlasSplitEnter,
      splitExit: atlasSplitExit,
      isSplitActive: function () { return splitActive; },
      setLabelMode: controls.setMode,
      cycleLabelMode: controls.cycleMode,
      setEdgeMode: controls.setEdgeMode,
      cycleEdgeMode: controls.cycleEdgeMode,
      /* Curation-replay camera bind (Atlas canvas). */
      getCamera: function () {
        return handle.getCamera ? handle.getCamera() : null;
      },
      setCamera: function (next) {
        if (handle.setCamera) handle.setCamera(next);
      },
      fitToBounds: function (bounds, opts) {
        if (handle.fitToBounds) handle.fitToBounds(bounds, opts);
      },
      fitToContent: function (opts) {
        if (handle.fitToContent) handle.fitToContent(opts);
      },
      destroy: function () {
        atlasSplitExit();
        handle.destroy();
      },
      /* Chrome-free info surface: the engine renders no panels — host
       * chrome (the future telemetry bar, discovery shelf UI, Switch
       * Bay's rail/tab) subscribes here. subscribe(cb) receives every
       * AtlasEvent (scene-ready stats, discovery-engaged, trail-changed,
       * telemetry…); getSnapshot() returns {scene, layout, state, stats}
       * for pull-style rendering. */
      subscribe: function (cb) {
        return handle.engine.on(cb);
      },
      getSnapshot: function () {
        return handle.engine.snapshot();
      },
      controller: handle.engine,
    };
  }

  window.AtlasViewer = {
    minPages: MIN_ATLAS_PAGES,
    classicMaxPages: CLASSIC_MAX_PAGES,
    pageCount: pageCount,
    eligible: eligible,
    classicSafe: classicSafe,
    enabled: atlasEnabled,
    initChoice: initChoice,
    init: init,
  };
})();
