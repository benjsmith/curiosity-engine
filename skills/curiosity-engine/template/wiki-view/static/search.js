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
 *
 * Large-wiki responsiveness: debounce input, require MIN_QUERY_CHARS
 * before running full match / canvas rings / list highlights (1-char
 * prefixes match nearly everything on ~27k corpora). Deep-link
 * `#page=` routing is unaffected — it never goes through this box.
 */
window.GraphSearch = (function () {
  'use strict';

  var DEBOUNCE_MS = 200;
  var MIN_QUERY_CHARS = 2;

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

  /** Precompute haystacks once — matching on 27k nodes must not rebuild
   * property strings on every keystroke. */
  function buildIndex(data) {
    var nodes = data.nodes || [];
    var out = new Array(nodes.length);
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      out[i] = { id: n.id, hay: haystack(data, n) };
    }
    return out;
  }

  /** Ids of every node matching `query`. Empty / too-short → no hits. */
  function match(data, query, index) {
    var q = String(query || '').trim().toLowerCase();
    if (!q || q.length < MIN_QUERY_CHARS) return [];
    var rows = index || buildIndex(data);
    var ids = [];
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].hay.indexOf(q) !== -1) ids.push(rows[i].id);
    }
    return ids;
  }

  function init(data, graphApi) {
    var input = document.getElementById('graph-search-input');
    var clearBtn = document.getElementById('graph-search-clear');
    var countEl = document.getElementById('graph-search-count');
    if (!input || !clearBtn) return;

    var timer = 0;
    var index = buildIndex(data);

    function paint(query) {
      var q = String(query || '').trim();
      var ready = q.length >= MIN_QUERY_CHARS;
      var ids = ready ? match(data, q, index) : [];
      if (graphApi && graphApi.highlightSearch) graphApi.highlightSearch(ids);
      // Always call, including with an empty list — that is how a
      // cancelled / too-short search clears the page list.
      if (window.Sidebar && Sidebar.setSearchHits) Sidebar.setSearchHits(ids);
      if (window.FileBrowser && FileBrowser.setSearchHits) FileBrowser.setSearchHits(ids);
      clearBtn.hidden = !q;
      if (countEl) {
        if (!q) {
          countEl.hidden = true;
          countEl.textContent = '';
        } else if (!ready) {
          // Typed but below min length — no expensive match yet.
          countEl.hidden = false;
          countEl.textContent = '…';
        } else {
          countEl.hidden = false;
          countEl.textContent = String(ids.length);
        }
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

  return { init: init, match: match, MIN_QUERY_CHARS: MIN_QUERY_CHARS, DEBOUNCE_MS: DEBOUNCE_MS };
})();
