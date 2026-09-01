/* search.js — graph search overlay.
 *
 * The sidebar's Fuse box filters the page LIST. This one marks the
 * GRAPH: type a query and every matching node wears a dashed halo while
 * the rest of the field recedes, in both viewers. The page list marks
 * the same hits, so the two surfaces always agree about what matched.
 *
 * Deliberately not done here:
 *   - No auto-zoom to the hits. The camera stays where the user put it;
 *     a search that flies the graph somewhere else loses their place.
 *   - No forced labels. Labels stay on whatever the `labels` control
 *     and the type filter say; hovering a hit names it. Labelling 40
 *     hits at once buries the canvas in overlapping text.
 *   - No edge recolouring. Accent-striping every edge that touches a
 *     hit turns a broad query into a wall of accent lines.
 *
 * Substring match over id, title, path, type and page properties —
 * predictable, and it finds the source filename a page came from.
 */
window.GraphSearch = (function () {
  'use strict';

  var DEBOUNCE_MS = 160;

  function haystack(data, node) {
    var page = (data.pages || {})[node.id] || {};
    var bits = [
      node.id, node.title, node.path, node.type,
      page.id, page.title, page.path, page.type,
    ];
    var props = page.properties || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined) return;
      bits.push(Array.isArray(v) ? v.join(' ') : String(v));
    });
    return bits.filter(Boolean).join(' ').toLowerCase();
  }

  /** Ids of every node matching `query`. Empty query → no hits. */
  function match(data, query) {
    var q = String(query || '').trim().toLowerCase();
    if (!q) return [];
    return (data.nodes || [])
      .filter(function (n) { return haystack(data, n).indexOf(q) !== -1; })
      .map(function (n) { return n.id; });
  }

  function init(data, graphApi) {
    var input = document.getElementById('graph-search-input');
    var clearBtn = document.getElementById('graph-search-clear');
    var countEl = document.getElementById('graph-search-count');
    if (!input || !clearBtn) return;

    var timer = 0;

    function paint(query) {
      var q = String(query || '').trim();
      var ids = match(data, q);
      if (graphApi && graphApi.highlightSearch) graphApi.highlightSearch(ids);
      // Always call, including with an empty list — that is how a
      // cancelled search clears the page list.
      if (window.Sidebar && Sidebar.setSearchHits) Sidebar.setSearchHits(ids);
      clearBtn.hidden = !q;
      if (countEl) {
        countEl.hidden = !q;
        countEl.textContent = q ? String(ids.length) : '';
      }
    }

    function applyNow(q) {
      window.clearTimeout(timer);
      paint(q);
    }

    input.addEventListener('input', function () {
      var q = input.value;
      if (!q.trim()) { applyNow(''); return; }
      window.clearTimeout(timer);
      timer = window.setTimeout(function () { paint(q); }, DEBOUNCE_MS);
    });
    clearBtn.addEventListener('click', function () {
      input.value = '';
      applyNow('');
      input.focus();
    });
    input.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      ev.stopPropagation();
      if (input.value) { input.value = ''; applyNow(''); } else input.blur();
    });
    /* No ⌘F / Ctrl-F binding. The box is on screen already, and the
     * listener only fired when focus happened to be inside the graph
     * pane — everywhere else the browser's own find bar opened, so the
     * shortcut gave you two search boxes instead of one. Claiming it
     * reliably means intercepting at the document, which takes
     * find-in-page away from the sidebar list and the open page. */
    paint('');
  }

  return { init: init, match: match };
})();
