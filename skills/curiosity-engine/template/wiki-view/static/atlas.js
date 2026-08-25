/* atlas.js — size-gated Knowledge Atlas embedding.
 *
 * When enabled, replaces the D3 force graph in #graph with the
 * Knowledge Atlas engine (vendored at static/vendor/knowledge-atlas.js,
 * built from packages/knowledge-atlas in the repo). Everything else —
 * sidebar, modal, subgraph navigator, editing — keeps working: the
 * atlas routes item-open through the same `#page=<id>` hash contract.
 *
 * Wikis above 360 pages get a Classic / Atlas chooser in the graph
 * controls. Classic remains the default until the user opts in. The
 * preference is stored in localStorage, but is ignored for small wikis.
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
  var PHYSICS_DEFAULTS = { charge: -420, link: 110, collide: 10 };

  function readLabelTypes() {
    try {
      var saved = JSON.parse(localStorage.getItem(LABEL_TYPES_KEY) || 'null');
      if (Array.isArray(saved)) return new Set(saved);
    } catch (e) {}
    return new Set(LABEL_DEFAULTS);
  }

  function initAtlasControls(handle) {
    var mode = 'auto';
    var types = readLabelTypes();
    var modeButton = document.getElementById('label-mode');
    var modeState = document.getElementById('label-mode-state');
    var typeButton = document.getElementById('label-types');
    var typeState = document.getElementById('label-types-state');
    var typePanel = document.getElementById('label-types-panel');
    var settingsButton = document.getElementById('settings-trigger');
    var settingsPanel = document.getElementById('settings-panel');

    function paintLabels() {
      if (modeState) modeState.textContent = mode;
      if (typeState) typeState.textContent = types.size + '/12';
      handle.setLabels(mode, Array.from(types));
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
    if (modeButton) modeButton.addEventListener('click', cycleMode);

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
    return { setMode: setMode, cycleMode: cycleMode };
  }

  function pageCount(data) {
    if (data && data.pages && typeof data.pages === 'object') {
      return Object.keys(data.pages).length;
    }
    return data && Array.isArray(data.nodes) ? data.nodes.length : 0;
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

  function atlasEnabled(data) {
    var explicit = queryChoice();
    if (explicit) return explicit === 'atlas';
    if (!eligible(data)) return false;
    try {
      return localStorage.getItem(STORAGE_KEY) === 'atlas';
    } catch (e) {
      return false;
    }
  }

  /* The selector is host chrome rather than engine chrome. It appears
   * only when Atlas's bounded-scene model adds value. Changing mode is
   * deliberately a reload: it leaves the classic graph lifecycle and
   * Atlas canvas teardown independent and keeps hash routing intact. */
  function initChoice(data, activeMode) {
    var button = document.getElementById('viewer-mode');
    var state = document.getElementById('viewer-mode-state');
    if (!button || !state || !eligible(data)) return;

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
    var handle = window.KnowledgeAtlas.mount(container, {
      data: data,
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
        budget: {
          maxNodes: Math.max(1, corpusSize),
          maxAggregates: 0,
          maxEdges: Math.max(900, (data.edges || []).length),
        },
      },
      onOpenItem: function (id) {
        window.location.hash = '#page=' + encodeURIComponent(id);
      },
    });
    var controls = initAtlasControls(handle);

    return {
      focus: function (pageId) {
        handle.engine.focus(pageId, 'system');
      },
      clearFocus: function () {},
      setLabelMode: controls.setMode,
      cycleLabelMode: controls.cycleMode,
      destroy: function () {
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
    pageCount: pageCount,
    eligible: eligible,
    enabled: atlasEnabled,
    initChoice: initChoice,
    init: init,
  };
})();
