/* atlas.js — flag-gated Knowledge Atlas embedding (experimental).
 *
 * When enabled, replaces the D3 force graph in #graph with the
 * Knowledge Atlas engine (vendored at static/vendor/knowledge-atlas.js,
 * built from packages/knowledge-atlas in the repo). Everything else —
 * sidebar, modal, subgraph navigator, editing — keeps working: the
 * atlas routes item-open through the same `#page=<id>` hash contract.
 *
 * Enable per-browser:  localStorage['curiosity-engine.viewer'] = 'atlas'
 * or per-load:         http://localhost:8090/?viewer=atlas
 * Disable:             remove the key / query param and reload.
 *
 * Off by default; the classic viewer is untouched. See
 * packages/knowledge-atlas/docs/ for the experiment write-up.
 */
(function () {
  'use strict';

  function atlasEnabled() {
    try {
      if (new URLSearchParams(window.location.search).get('viewer') === 'atlas') return true;
      return localStorage.getItem('curiosity-engine.viewer') === 'atlas';
    } catch (e) {
      return false;
    }
  }

  // Called by main.js instead of Graph.init when the flag is on.
  // Returns a Graph-compatible facade so focus()/clearFocus() callers
  // keep working.
  function init(data) {
    var container = document.getElementById('graph');
    if (!container || !window.KnowledgeAtlas) return null;
    container.innerHTML = '';

    var handle = window.KnowledgeAtlas.mount(container, {
      data: data,
      // Hybrid (force core + doc-type hyperbolic rim) per iteration-2
      // feedback; wheel zooms the core like the classic viewer and
      // lens-pulls the rim.
      config: { layout: 'hybrid' },
      onOpenItem: function (id) {
        window.location.hash = '#page=' + encodeURIComponent(id);
      },
    });

    return {
      focus: function (pageId) {
        handle.engine.focus(pageId, 'system');
      },
      clearFocus: function () {},
      setLabelMode: function () {},
      cycleLabelMode: function () {},
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

  window.AtlasViewer = { enabled: atlasEnabled, init: init };
})();
