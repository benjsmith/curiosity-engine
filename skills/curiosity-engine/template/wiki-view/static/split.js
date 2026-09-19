/* CE workspace partition UI (Phase 2b+ / 2b++++++ rubber-band).
 *
 * Collects page refs with move|copy policy and POSTs /api/split.
 * Classic graph: Ctrl/Cmd-drag rubber-band, Shift-click multi-select,
 * click toggle, Alt/right-click flip policy. Deep-link: ?split=1.
 * Listens for ce:split-proposal / sy:split-proposal (embed shells).
 * Honors window.ceApi / CE_PUBLIC_BASE (embed v2 under /embed/ce).
 */
window.SplitPanel = (function () {
  function apiUrl(path) {
    if (typeof window.ceApi === "function") return window.ceApi(path);
    var base = (window.CE_PUBLIC_BASE || "").replace(/\/$/, "");
    if (!path) path = "/";
    if (path.charAt(0) !== "/") path = "/" + path;
    return base + path;
  }

  function viewerApi() {
    return window.CEViewer || window.Graph || null;
  }

  /** @type {Array<{id:string, policy:"move"|"copy"}>} */
  var selection = [];
  var busy = false;
  var els = {};
  var syncingFromGraph = false;

  function currentPageId() {
    var m = (window.location.hash || "").match(/^#page=([^&]+)$/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function render() {
    if (!els.list) return;
    if (!selection.length) {
      els.list.innerHTML =
        '<div class="split-empty">Select on the graph: Ctrl/⌘-drag rubber-band, ' +
        "Shift-click to add, click to toggle. Or Add current.</div>";
    } else {
      els.list.innerHTML = selection.map(function (e, i) {
        return (
          '<div class="split-row" data-i="' + i + '">' +
            '<span class="split-id" title="' + e.id + '">' + e.id + "</span>" +
            '<select class="split-policy" aria-label="Policy for ' + e.id + '">' +
              '<option value="move"' + (e.policy === "move" ? " selected" : "") + ">move</option>" +
              '<option value="copy"' + (e.policy === "copy" ? " selected" : "") + ">copy</option>" +
            "</select>" +
            '<button type="button" class="icon-btn split-remove" title="Remove" aria-label="Remove">×</button>' +
          "</div>"
        );
      }).join("");
    }
    if (els.count) els.count.textContent = String(selection.length);
    if (els.submit) els.submit.disabled = busy || !selection.length || !(els.name && els.name.value.trim());
  }

  function addId(id, policy) {
    id = (id || "").trim();
    if (!id) return;
    for (var i = 0; i < selection.length; i++) {
      if (selection[i].id === id) return;
    }
    selection.push({ id: id, policy: policy === "copy" ? "copy" : "move" });
    render();
    pushToGraph();
  }

  function setSelection(pages) {
    syncingFromGraph = true;
    selection = [];
    (pages || []).forEach(function (p) {
      var id = typeof p === "string" ? p : p && p.id;
      if (!id) return;
      var policy = typeof p === "object" && p.policy === "copy" ? "copy" : "move";
      selection.push({ id: String(id), policy: policy });
    });
    render();
    syncingFromGraph = false;
  }

  function pushToGraph() {
    if (syncingFromGraph) return;
    var api = viewerApi();
    if (!api || typeof api.splitEnter !== "function") return;
    if (!document.body || document.body.dataset.split !== "1") return;
    try {
      api.splitEnter(selection.slice(), onGraphSelection);
    } catch (e) { /* ignore */ }
  }

  function onGraphSelection(sel) {
    setSelection(sel || []);
  }

  function enterGraphMode() {
    var api = viewerApi();
    if (api && typeof api.splitEnter === "function") {
      try {
        api.splitEnter(selection.slice(), onGraphSelection);
      } catch (e) { /* ignore */ }
    }
  }

  function exitGraphMode() {
    var api = viewerApi();
    if (api && typeof api.splitExit === "function") {
      try { api.splitExit(); } catch (e) { /* ignore */ }
    }
  }

  function setOpen(open) {
    if (!els.panel) return;
    els.panel.hidden = !open;
    if (els.toggle) els.toggle.setAttribute("aria-pressed", open ? "true" : "false");
    document.body.dataset.split = open ? "1" : "0";
    if (open) enterGraphMode();
    else exitGraphMode();
  }

  async function submit() {
    if (busy || !selection.length) return;
    var name = (els.name && els.name.value || "").trim();
    var target = (els.target && els.target.value || "").trim();
    if (!name && !target) {
      if (els.status) els.status.textContent = "Name or absolute target path required.";
      return;
    }
    busy = true;
    render();
    if (els.status) els.status.textContent = "Partitioning…";
    var move = [];
    var copy = [];
    selection.forEach(function (e) {
      if (e.policy === "copy") copy.push(e.id);
      else move.push(e.id);
    });
    var body = { move: move, copy: copy };
    if (name) body.name = name;
    if (target) body.target = target;
    try {
      var r = await fetch(apiUrl("/api/split"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      var b = await r.json().catch(function () { return {}; });
      if (!r.ok) {
        if (els.status) els.status.textContent = b.error || ("HTTP " + r.status);
        return;
      }
      if (els.status) {
        els.status.textContent =
          "Done → " + (b.target || name) +
          " (" + (b.moved || 0) + " moved, " + (b.copied || 0) + " copied).";
      }
      selection = [];
      render();
      exitGraphMode();
    } catch (e) {
      if (els.status) els.status.textContent = (e && e.message) || String(e);
    } finally {
      busy = false;
      render();
    }
  }

  function applyProposal(pages) {
    if (!pages || !pages.length) return;
    setSelection(pages.map(function (p) {
      return typeof p === "string" ? { id: p, policy: "move" } : p;
    }));
    setOpen(true);
    if (els.status) {
      els.status.textContent =
        "Proposal: " + pages.length + " page(s) highlighted — review & confirm.";
    }
  }

  function init() {
    els.toggle = document.getElementById("split-toggle");
    els.panel = document.getElementById("split-panel");
    els.list = document.getElementById("split-list");
    els.name = document.getElementById("split-name");
    els.target = document.getElementById("split-target");
    els.add = document.getElementById("split-add");
    els.submit = document.getElementById("split-submit");
    els.cancel = document.getElementById("split-cancel");
    els.status = document.getElementById("split-status");
    els.count = document.getElementById("split-count");
    if (!els.panel) return;

    if (els.toggle) {
      els.toggle.addEventListener("click", function () {
        setOpen(els.panel.hidden);
      });
    }
    if (els.cancel) {
      els.cancel.addEventListener("click", function () {
        selection = [];
        setOpen(false);
        render();
      });
    }
    if (els.add) {
      els.add.addEventListener("click", function () {
        addId(currentPageId(), "move");
        if (!currentPageId() && els.status) {
          els.status.textContent = "Open a page on the graph first (#page=…), or rubber-band select.";
        }
      });
    }
    if (els.submit) els.submit.addEventListener("click", submit);
    if (els.name) els.name.addEventListener("input", render);
    if (els.list) {
      els.list.addEventListener("change", function (ev) {
        var row = ev.target.closest && ev.target.closest(".split-row");
        if (!row || !ev.target.classList.contains("split-policy")) return;
        var i = Number(row.getAttribute("data-i"));
        if (selection[i]) selection[i].policy = ev.target.value === "copy" ? "copy" : "move";
        pushToGraph();
      });
      els.list.addEventListener("click", function (ev) {
        var btn = ev.target.closest && ev.target.closest(".split-remove");
        if (!btn) return;
        var row = btn.closest(".split-row");
        if (!row) return;
        var i = Number(row.getAttribute("data-i"));
        if (!isNaN(i)) selection.splice(i, 1);
        render();
        pushToGraph();
      });
    }

    window.addEventListener("ce:split-selection", function (ev) {
      var pages = ev && ev.detail && ev.detail.pages;
      if (!pages) return;
      setSelection(pages);
      if (document.body.dataset.split !== "1") setOpen(true);
    });

    function onPropose(ev) {
      var detail = ev && ev.detail;
      var pages = detail && (detail.pages || detail.ids);
      applyProposal(pages || []);
    }
    window.addEventListener("ce:split-proposal", onPropose);
    window.addEventListener("sy:split-proposal", onPropose);

    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get("split") === "1") setOpen(true);
    } catch (e) { /* ignore */ }
    render();
  }

  return {
    init: init,
    add: addId,
    setSelection: setSelection,
    open: function () { setOpen(true); },
    close: function () { setOpen(false); },
  };
})();
