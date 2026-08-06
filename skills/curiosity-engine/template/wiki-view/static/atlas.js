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
    };
  }

  window.AtlasViewer = { enabled: atlasEnabled, init: init };
})();
