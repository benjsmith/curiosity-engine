/* CE filebrowser (Phase 2b spike) — browse / search / highlight / context menu.
 *
 * Consumes GET /api/tree (vault/ + wiki/ paths). Pages|Files toggle sits
 * above the existing page sidebar. Pack file-routes, FS mutate ops, and
 * Switchbay workspace-relative pack paths are deferred.
 */
window.FileBrowser = (function () {
  function apiUrl(path) {
    if (typeof window.ceApi === "function") return window.ceApi(path);
    var base = (window.CE_PUBLIC_BASE || "").replace(/\/$/, "");
    if (!path) path = "/";
    if (path.charAt(0) !== "/") path = "/" + path;
    return base + path;
  }

  /** @typedef {{ name: string, path: string, isDir: boolean, children: any[] }} FileNode */

  var mode = "pages"; // pages | files
  var files = /** @type {string[]|null} */ (null);
  var error = null;
  var query = "";
  var sort = "asc"; // asc | desc
  var expanded = new Set([""]);
  var selected = null;
  var searchHits = new Set(); // relative paths highlighted by graph search
  var pathToPageId = Object.create(null);
  var menu = null; // { x, y, path, isDir }
  var els = {};

  function fileExt(path) {
    var slash = path.lastIndexOf("/");
    var dot = path.lastIndexOf(".");
    if (dot <= slash) return "";
    return path.slice(dot + 1).toLowerCase();
  }

  /** Build a query matcher. Returns null on bad regex. Empty → match all. */
  function buildMatcher(q) {
    q = (q || "").trim();
    if (!q) return function () { return true; };
    var re = q.match(/^\/(.+)\/([gimsuy]*)$/);
    if (re) {
      try {
        var r = new RegExp(re[1], re[2] || "i");
        return function (p) { return r.test(p); };
      } catch (e) {
        return null;
      }
    }
    var glob = q.match(/^\*\.([A-Za-z0-9_]+)$/);
    if (glob) {
      var ext = glob[1].toLowerCase();
      return function (p) { return fileExt(p) === ext; };
    }
    var lower = q.toLowerCase();
    return function (p) { return p.toLowerCase().indexOf(lower) !== -1; };
  }

  function buildTree(paths, sortMode) {
    var root = { name: "", path: "", isDir: true, children: [] };
    for (var i = 0; i < paths.length; i++) {
      var p = paths[i];
      var parts = p.split("/");
      var node = root;
      for (var j = 0; j < parts.length; j++) {
        var isLeaf = j === parts.length - 1;
        var childName = parts[j];
        var childPath = parts.slice(0, j + 1).join("/");
        var child = null;
        for (var k = 0; k < node.children.length; k++) {
          if (node.children[k].name === childName) { child = node.children[k]; break; }
        }
        if (!child) {
          child = { name: childName, path: childPath, isDir: !isLeaf, children: [] };
          node.children.push(child);
        }
        node = child;
      }
    }
    function sortRec(n) {
      n.children.sort(function (a, b) {
        if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
        var cmp = a.name.localeCompare(b.name);
        return sortMode === "asc" ? cmp : -cmp;
      });
      n.children.forEach(sortRec);
    }
    sortRec(root);
    return root.children;
  }

  function ancestorDirs(path) {
    var parts = path.split("/");
    var out = [""];
    var acc = "";
    for (var i = 0; i < parts.length - 1; i++) {
      acc = acc ? acc + "/" + parts[i] : parts[i];
      out.push(acc);
    }
    return out;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c];
    });
  }

  function cssEscapeAttr(value) {
    return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function indexPages(data) {
    pathToPageId = Object.create(null);
    var pages = (data && data.pages) || {};
    Object.keys(pages).forEach(function (id) {
      var page = pages[id];
      if (!page || !page.path) return;
      // data.json paths are wiki-relative (entities/foo.md).
      pathToPageId["wiki/" + page.path] = id;
      pathToPageId[page.path] = id;
    });
  }

  function filteredPaths() {
    if (!files) return [];
    var matcher = buildMatcher(query);
    if (!matcher) return null; // bad regex
    return files.filter(matcher);
  }

  function setMode(next) {
    mode = next === "files" ? "files" : "pages";
    document.body.dataset.sidebarMode = mode;
    if (els.segPages) els.segPages.setAttribute("aria-pressed", mode === "pages" ? "true" : "false");
    if (els.segFiles) els.segFiles.setAttribute("aria-pressed", mode === "files" ? "true" : "false");
    if (els.pageSearch) els.pageSearch.hidden = mode !== "pages";
    if (els.pageList) els.pageList.hidden = mode !== "pages";
    if (els.fbPane) els.fbPane.hidden = mode !== "files";
    if (mode === "files") {
      if (files === null && !error) refresh();
      else render();
    }
  }

  function refresh() {
    error = null;
    if (els.fbStatus) els.fbStatus.textContent = "Loading…";
    fetch(apiUrl("/api/tree"))
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (body) {
        files = Array.isArray(body.files) ? body.files : [];
        // Expand top-level roots by default.
        ["vault", "wiki"].forEach(function (r) { expanded.add(r); });
        render();
      })
      .catch(function (e) {
        error = e.message || String(e);
        files = [];
        render();
      });
  }

  function renderRow(node, depth) {
    var isHit = searchHits.has(node.path);
    var isSel = selected === node.path;
    var chevron = node.isDir
      ? (expanded.has(node.path)
          ? '<span class="fb-chevron" aria-hidden="true">▾</span>'
          : '<span class="fb-chevron" aria-hidden="true">▸</span>')
      : '<span class="fb-chevron fb-leaf" aria-hidden="true"></span>';
    var cls = "fb-row"
      + (node.isDir ? " fb-dir" : " fb-file")
      + (isHit ? " fb-hit" : "")
      + (isSel ? " fb-selected" : "");
    return (
      '<div class="' + cls + '" role="treeitem" data-fb-path="' + escapeHtml(node.path) + '"'
      + ' data-fb-dir="' + (node.isDir ? "1" : "0") + '"'
      + ' aria-expanded="' + (node.isDir ? (expanded.has(node.path) ? "true" : "false") : "false") + '"'
      + ' style="--fb-depth:' + depth + '">'
      + chevron
      + '<span class="fb-name">' + escapeHtml(node.name) + "</span>"
      + "</div>"
    );
  }

  function renderTree(nodes, depth, into) {
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      into.push(renderRow(n, depth));
      if (n.isDir && expanded.has(n.path) && n.children.length) {
        renderTree(n.children, depth + 1, into);
      }
    }
  }

  function render() {
    if (!els.fbList) return;
    closeMenu();
    if (error) {
      els.fbList.innerHTML = '<div class="fb-empty">Tree error: ' + escapeHtml(error) + "</div>";
      if (els.fbStatus) els.fbStatus.textContent = "";
      return;
    }
    if (files === null) {
      els.fbList.innerHTML = '<div class="fb-empty">Loading…</div>';
      return;
    }
    var matched = filteredPaths();
    if (matched === null) {
      els.fbList.innerHTML = '<div class="fb-empty fb-bad-re">Invalid regex</div>';
      if (els.fbStatus) els.fbStatus.textContent = "";
      return;
    }
    // When searching, auto-expand ancestors of hits.
    if (query.trim()) {
      matched.forEach(function (p) {
        ancestorDirs(p).forEach(function (d) { expanded.add(d); });
      });
    }
    var tree = buildTree(matched, sort);
    var html = [];
    renderTree(tree, 0, html);
    els.fbList.innerHTML = html.length
      ? html.join("")
      : '<div class="fb-empty">No files' + (query.trim() ? " match" : "") + "</div>";
    if (els.fbStatus) {
      els.fbStatus.textContent = matched.length + " file" + (matched.length === 1 ? "" : "s");
    }
  }

  function openPath(path) {
    selected = path;
    render();
    if (path.indexOf("wiki/") === 0 && path.slice(-3) === ".md") {
      var id = pathToPageId[path];
      if (!id) {
        // Fallback: wiki/entities/foo.md → entities/foo
        id = path.slice("wiki/".length, -3);
      }
      if (window.Modal && Modal.open) {
        var ok = Modal.open(id);
        if (ok) {
          history.replaceState(null, "", "#page=" + encodeURIComponent(id));
          if (window.Sidebar && Sidebar.setActive) Sidebar.setActive(id);
        }
      }
      return;
    }
    if (path.indexOf("vault/") === 0 && /\.extracted\.md$/i.test(path)) {
      var base = path.split("/").pop();
      if (window.VaultSources && typeof VaultSources.openExtracted === "function") {
        VaultSources.openExtracted(base);
      } else if (window.VaultSources && typeof VaultSources.open === "function") {
        VaultSources.open(base);
      } else {
        // Soft open via API in a new tab.
        window.open(apiUrl("/api/vault/" + encodeURIComponent(base)), "_blank");
      }
    }
  }

  function onListClick(ev) {
    var row = ev.target.closest && ev.target.closest(".fb-row");
    if (!row) return;
    var path = row.getAttribute("data-fb-path");
    var isDir = row.getAttribute("data-fb-dir") === "1";
    if (isDir) {
      if (expanded.has(path)) expanded.delete(path);
      else expanded.add(path);
      selected = path;
      render();
      return;
    }
    openPath(path);
  }

  function onListContext(ev) {
    var row = ev.target.closest && ev.target.closest(".fb-row");
    if (!row) return;
    ev.preventDefault();
    var path = row.getAttribute("data-fb-path");
    var isDir = row.getAttribute("data-fb-dir") === "1";
    selected = path;
    menu = { x: ev.clientX, y: ev.clientY, path: path, isDir: isDir };
    render();
    showMenu();
  }

  function showMenu() {
    if (!els.fbMenu || !menu) return;
    var items = [];
    if (!menu.isDir) {
      items.push({ action: "open", label: "Open" });
    } else {
      items.push({
        action: "toggle",
        label: expanded.has(menu.path) ? "Collapse" : "Expand",
      });
    }
    items.push({ action: "copy", label: "Copy path" });
    items.push({ action: "reveal", label: "Reveal" });
    els.fbMenu.innerHTML = items.map(function (it) {
      return '<button type="button" class="fb-menu-item" data-fb-action="'
        + it.action + '">' + escapeHtml(it.label) + "</button>";
    }).join("");
    els.fbMenu.hidden = false;
    els.fbMenu.style.left = menu.x + "px";
    els.fbMenu.style.top = menu.y + "px";
    // Clamp into viewport after measure.
    requestAnimationFrame(function () {
      var rect = els.fbMenu.getBoundingClientRect();
      var x = Math.min(menu.x, window.innerWidth - rect.width - 8);
      var y = Math.min(menu.y, window.innerHeight - rect.height - 8);
      els.fbMenu.style.left = Math.max(8, x) + "px";
      els.fbMenu.style.top = Math.max(8, y) + "px";
    });
  }

  function closeMenu() {
    menu = null;
    if (els.fbMenu) {
      els.fbMenu.hidden = true;
      els.fbMenu.innerHTML = "";
    }
  }

  function onMenuClick(ev) {
    var btn = ev.target.closest && ev.target.closest("[data-fb-action]");
    if (!btn || !menu) return;
    var action = btn.getAttribute("data-fb-action");
    var path = menu.path;
    closeMenu();
    if (action === "open") openPath(path);
    else if (action === "toggle") {
      if (expanded.has(path)) expanded.delete(path);
      else expanded.add(path);
      render();
    } else if (action === "copy") {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(path).catch(function () {});
      }
    } else if (action === "reveal") {
      ancestorDirs(path).forEach(function (d) { expanded.add(d); });
      selected = path;
      render();
      var el = els.fbList && els.fbList.querySelector(
        '[data-fb-path="' + cssEscapeAttr(path) + '"]'
      );
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    }
  }

  /** Highlight tree rows from graph-search page ids (wiki paths). */
  function setSearchHits(ids) {
    searchHits = new Set();
    (ids || []).forEach(function (id) {
      var wikiRel = null;
      // Prefer reverse lookup from pathToPageId.
      Object.keys(pathToPageId).forEach(function (p) {
        if (pathToPageId[p] === id && p.indexOf("wiki/") === 0) wikiRel = p;
      });
      if (!wikiRel) wikiRel = "wiki/" + id + ".md";
      searchHits.add(wikiRel);
      ancestorDirs(wikiRel).forEach(function (d) { expanded.add(d); });
    });
    if (mode === "files") render();
  }

  function init(data) {
    indexPages(data);
    els.segPages = document.getElementById("sidebar-mode-pages");
    els.segFiles = document.getElementById("sidebar-mode-files");
    els.pageSearch = document.querySelector(".sidebar-search-wrap");
    els.pageList = document.getElementById("sidebar-list");
    els.fbPane = document.getElementById("filebrowser-pane");
    els.fbList = document.getElementById("filebrowser-list");
    els.fbSearch = document.getElementById("filebrowser-search");
    els.fbStatus = document.getElementById("filebrowser-status");
    els.fbSort = document.getElementById("filebrowser-sort");
    els.fbRefresh = document.getElementById("filebrowser-refresh");
    els.fbMenu = document.getElementById("filebrowser-menu");

    if (!els.fbPane || !els.fbList) return;

    if (els.segPages) {
      els.segPages.addEventListener("click", function () { setMode("pages"); });
    }
    if (els.segFiles) {
      els.segFiles.addEventListener("click", function () { setMode("files"); });
    }
    if (els.fbSearch) {
      els.fbSearch.addEventListener("input", function (ev) {
        query = ev.target.value;
        render();
      });
    }
    if (els.fbSort) {
      els.fbSort.addEventListener("click", function () {
        sort = sort === "asc" ? "desc" : "asc";
        els.fbSort.title = sort === "asc" ? "Sort A→Z" : "Sort Z→A";
        els.fbSort.setAttribute("aria-label", els.fbSort.title);
        render();
      });
    }
    if (els.fbRefresh) {
      els.fbRefresh.addEventListener("click", function () { refresh(); });
    }
    els.fbList.addEventListener("click", onListClick);
    els.fbList.addEventListener("contextmenu", onListContext);
    if (els.fbMenu) els.fbMenu.addEventListener("click", onMenuClick);
    document.addEventListener("click", function (ev) {
      if (!menu) return;
      if (els.fbMenu && els.fbMenu.contains(ev.target)) return;
      closeMenu();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closeMenu();
    });

    // Optional deep-link: ?filebrowser=1
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get("filebrowser") === "1") setMode("files");
      else setMode("pages");
    } catch (e) {
      setMode("pages");
    }
  }

  function refreshData(data) {
    indexPages(data);
  }

  return {
    init: init,
    refresh: refresh,
    refreshData: refreshData,
    setSearchHits: setSearchHits,
    setMode: setMode,
    // Test seam
    _buildMatcher: buildMatcher,
    _buildTree: buildTree,
    _fileExt: fileExt,
  };
})();
