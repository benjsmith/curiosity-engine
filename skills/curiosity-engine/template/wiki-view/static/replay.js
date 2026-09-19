/* Curation replay overlay (Phase 2b+++).
 *
 * Vanilla port of Switchbay CurationReplay UX — play / pause / scrub / step
 * over a HistoryDoc. Timing contract matches knowledge-atlas
 * playReplayTimeline; SVG force layout is a slim d3 host (no React).
 * Honors window.ceApi / CE_PUBLIC_BASE. Deep-link: ?replay=1
 */
window.CurationReplay = (function () {
  var W = 1000;
  var H = 600;
  var PHYSICS = { charge: -420, link: 110, collide: 10 };
  var TYPE_FALLBACK = {
    project: "#4d1ae8",
    analysis: "#1d6996",
    concept: "#38a6a5",
    entity: "#0f8554",
    evidence: "#73af48",
    fact: "#edad08",
    figure: "#e17c05",
    table: "#cc503e",
    source: "#94346e",
    note: "#6f4070",
    todo: "#9656a2",
    "todo-list": "#9656a2",
    unclassified: "#ffffff",
  };

  function apiUrl(path) {
    if (typeof window.ceApi === "function") return window.ceApi(path);
    var base = (window.CE_PUBLIC_BASE || "").replace(/\/$/, "");
    if (!path) path = "/";
    if (path.charAt(0) !== "/") path = "/" + path;
    return base + path;
  }

  function colourFor(type, palette) {
    if (palette && palette[type]) return palette[type];
    return TYPE_FALLBACK[type] || TYPE_FALLBACK.unclassified || "#ffffff";
  }

  function radiusFor(deg) {
    return 4 + Math.sqrt((deg || 0) + 1) * 1.6;
  }

  function sortEvents(events) {
    return (events || []).slice().sort(function (a, b) {
      if (a.t !== b.t) return a.t - b.t;
      var ao = a.op === "node" ? 0 : 1;
      var bo = b.op === "node" ? 0 : 1;
      return ao - bo;
    });
  }

  /** Client-side fallback when /api/curation/history is unavailable. */
  function buildFromData(data) {
    var nodesIn = (data && data.nodes) || [];
    var edgesIn = (data && data.edges) || [];
    var duration = 12;
    var nodes = nodesIn
      .map(function (n) {
        return {
          id: String(n.id || "").trim(),
          title: String(n.title || n.id || ""),
          type: String(n.type || "unclassified"),
          degree: typeof n.degree === "number" ? n.degree : 0,
        };
      })
      .filter(function (n) {
        return n.id;
      });
    if (!nodes.length) {
      return { duration: duration, events: [], source: "empty", degree: {}, node_count: 0 };
    }
    nodes.sort(function (a, b) {
      var as = a.type === "source" || a.type === "sources" ? 0 : 1;
      var bs = b.type === "source" || b.type === "sources" ? 0 : 1;
      if (as !== bs) return as - bs;
      if (b.degree !== a.degree) return b.degree - a.degree;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
    var byId = {};
    var indexOf = {};
    nodes.forEach(function (n, i) {
      byId[n.id] = n;
      indexOf[n.id] = i;
    });
    var nCount = nodes.length;
    function tOf(i) {
      return nCount <= 1 ? 0 : Math.round((i / (nCount - 1)) * duration * 1000) / 1000;
    }
    function edgeId(ref) {
      if (!ref) return "";
      if (typeof ref === "string") return ref;
      return typeof ref.id === "string" ? ref.id : "";
    }
    var events = nodes.map(function (n, i) {
      return { t: tOf(i), op: "node", id: n.id, title: n.title, type: n.type };
    });
    var seen = {};
    edgesIn.forEach(function (e) {
      var s = edgeId(e.source);
      var t = edgeId(e.target);
      if (!s || !t || s === t || !byId[s] || !byId[t]) return;
      var pair = s < t ? s + "|" + t : t + "|" + s;
      if (seen[pair]) return;
      seen[pair] = true;
      var later = Math.max(indexOf[s], indexOf[t]);
      events.push({ t: tOf(later), op: "edge", source: s, target: t });
    });
    events = sortEvents(events);
    var degree = {};
    nodes.forEach(function (n) {
      degree[n.id] = 0;
    });
    events.forEach(function (ev) {
      if (ev.op !== "edge") return;
      degree[ev.source] = (degree[ev.source] || 0) + 1;
      degree[ev.target] = (degree[ev.target] || 0) + 1;
    });
    return {
      duration: duration,
      events: events,
      source: "ce-data-json-client",
      node_count: nodes.length,
      degree: degree,
    };
  }

  /**
   * Port of playReplayTimeline — advances from `startIndex`, remapping
   * event times relative to the first remaining event.
   */
  function playFrom(history, startIndex, handlers) {
    var all = sortEvents(history && history.events);
    var events = all.slice(Math.max(0, startIndex));
    if (!events.length) {
      var doneId = setTimeout(function () {
        handlers.onDone && handlers.onDone();
      }, 0);
      return function () {
        clearTimeout(doneId);
      };
    }
    var tBase = events[0].t;
    var remapped = events.map(function (ev, i) {
      var copy = Object.assign({}, ev, { t: ev.t - tBase });
      copy._absIndex = startIndex + i;
      return copy;
    });
    var rate = handlers.rate && handlers.rate > 0 ? handlers.rate : 1;
    var now =
      handlers.now ||
      function () {
        return typeof performance !== "undefined" ? performance.now() : Date.now();
      };
    var schedule =
      handlers.schedule ||
      function (fn, ms) {
        return setTimeout(fn, ms);
      };
    var cancel =
      handlers.cancel ||
      function (id) {
        clearTimeout(id);
      };
    var cancelled = false;
    var timer = null;
    var index = 0;
    var t0 = now();

    function tick() {
      if (cancelled) return;
      if (index >= remapped.length) {
        handlers.onDone && handlers.onDone();
        return;
      }
      var wall = (now() - t0) * rate;
      while (index < remapped.length && remapped[index].t <= wall) {
        handlers.onEvent(remapped[index], remapped[index]._absIndex);
        index += 1;
      }
      if (index >= remapped.length) {
        handlers.onDone && handlers.onDone();
        return;
      }
      var nextT = remapped[index].t;
      var delay = Math.max(0, (nextT - wall) / rate);
      timer = schedule(tick, delay);
    }
    timer = schedule(tick, 0);
    return function () {
      cancelled = true;
      if (timer != null) cancel(timer);
    };
  }

  var state = {
    data: null,
    history: null,
    palette: null,
    overlay: null,
    svg: null,
    cursor: 0,
    playing: false,
    stopPlay: null,
    sim: null,
    nodes: [],
    links: [],
    byId: null,
    degree: null,
    els: {},
  };

  function setCounts(n, e) {
    if (state.els.nCount) state.els.nCount.textContent = String(n);
    if (state.els.eCount) state.els.eCount.textContent = String(e);
  }

  function setStatus(msg) {
    if (state.els.status) state.els.status.textContent = msg || "";
  }

  function updateChrome() {
    var total = (state.history && state.history.events && state.history.events.length) || 0;
    if (state.els.scrub) {
      state.els.scrub.max = String(Math.max(0, total));
      state.els.scrub.value = String(state.cursor);
    }
    if (state.els.pos) {
      state.els.pos.textContent = state.cursor + " / " + total;
    }
    if (state.els.play) {
      state.els.play.textContent = state.playing ? "Pause" : "Play";
      state.els.play.setAttribute("aria-pressed", state.playing ? "true" : "false");
    }
  }

  function tearSim() {
    if (state.stopPlay) {
      state.stopPlay();
      state.stopPlay = null;
    }
    state.playing = false;
    if (state.sim) {
      state.sim.stop();
      state.sim = null;
    }
    state.nodes = [];
    state.links = [];
    state.byId = new Map();
  }

  function ensureLayers() {
    var sel = d3.select(state.svg);
    sel.selectAll("*").remove();
    state.svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    state.svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    var root = sel.append("g").attr("class", "ce-replay-root");
    state.linkG = root.append("g").attr("class", "ce-replay-links");
    state.nodeG = root.append("g").attr("class", "ce-replay-nodes");
    state.labelG = root.append("g").attr("class", "ce-replay-labels");
    state.rootG = root;

    var zoom = d3
      .zoom()
      .scaleExtent([0.15, 6])
      .on("zoom", function (event) {
        root.attr("transform", event.transform.toString());
      });
    sel.call(zoom);
    sel.call(zoom.transform, d3.zoomIdentity);

    state.sim = d3
      .forceSimulation(state.nodes)
      .force("charge", d3.forceManyBody().strength(PHYSICS.charge).distanceMax(500))
      .force("center", d3.forceCenter(W / 2, H / 2).strength(0.05))
      .force(
        "collide",
        d3.forceCollide(function (d) {
          return d.r + PHYSICS.collide;
        }),
      )
      .force(
        "link",
        d3
          .forceLink(state.links)
          .id(function (d) {
            return d.id;
          })
          .distance(PHYSICS.link)
          .strength(0.55),
      )
      .alphaDecay(0.02)
      .velocityDecay(0.35);

    state.sim.on("tick", function () {
      state.linkG
        .selectAll("line")
        .attr("x1", function (d) {
          return d.source.x || 0;
        })
        .attr("y1", function (d) {
          return d.source.y || 0;
        })
        .attr("x2", function (d) {
          return d.target.x || 0;
        })
        .attr("y2", function (d) {
          return d.target.y || 0;
        });
      state.nodeG
        .selectAll("circle")
        .attr("cx", function (d) {
          return d.x || 0;
        })
        .attr("cy", function (d) {
          return d.y || 0;
        })
        .attr("r", function (d) {
          return d.r;
        });
      state.labelG
        .selectAll("text")
        .attr("x", function (d) {
          return d.x || 0;
        })
        .attr("y", function (d) {
          return (d.y || 0) - d.r - 4;
        });
    });
  }

  function paint() {
    state.nodeG
      .selectAll("circle")
      .data(state.nodes, function (d) {
        return d.id;
      })
      .join(function (enter) {
        return enter
          .append("circle")
          .attr("fill", function (d) {
            return colourFor(d.type, state.palette);
          })
          .attr("stroke", "rgba(0,0,0,0.35)")
          .attr("stroke-width", 0.6)
          .attr("opacity", 0.95);
      });

    var labelled = state.nodes.slice().sort(function (a, b) {
      return (b.finalDeg || 0) - (a.finalDeg || 0);
    }).slice(0, 24);

    state.labelG
      .selectAll("text")
      .data(labelled, function (d) {
        return d.id;
      })
      .join(function (enter) {
        return enter
          .append("text")
          .attr("text-anchor", "middle")
          .attr("fill", "var(--text-muted, #aaa)")
          .attr("font-size", 10)
          .attr("opacity", 0.85)
          .text(function (d) {
            return d.title;
          });
      });

    state.linkG
      .selectAll("line")
      .data(state.links)
      .join(function (enter) {
        return enter
          .append("line")
          .attr("stroke", "#888")
          .attr("stroke-width", 0.5)
          .attr("stroke-opacity", 0.35);
      });

    state.sim.nodes(state.nodes);
    state.sim.force("link").links(state.links);
    state.sim.alpha(0.35).restart();
    setCounts(state.nodes.length, state.links.length);
  }

  function applyEvent(ev) {
    if (ev.op === "node") {
      if (state.byId.has(ev.id)) return;
      var angle = state.nodes.length * 0.61803 * Math.PI * 2;
      var r = 160 + Math.random() * 60;
      var cx = state.nodes.length
        ? state.nodes.reduce(function (s, n) {
            return s + (n.x || W / 2);
          }, 0) / state.nodes.length
        : W / 2;
      var cy = state.nodes.length
        ? state.nodes.reduce(function (s, n) {
            return s + (n.y || H / 2);
          }, 0) / state.nodes.length
        : H / 2;
      var node = {
        id: ev.id,
        title: ev.title,
        type: ev.type,
        finalDeg: (state.degree && state.degree[ev.id]) || 0,
        r: radiusFor(0),
        x: cx + Math.cos(angle) * r,
        y: cy + Math.sin(angle) * r,
      };
      state.nodes.push(node);
      state.byId.set(ev.id, node);
    } else if (ev.op === "edge") {
      var s = state.byId.get(ev.source);
      var t = state.byId.get(ev.target);
      if (s && t) {
        state.links.push({ source: s, target: t });
        s.r = radiusFor((s.finalDeg || 0) * 0.5 + 1);
        t.r = radiusFor((t.finalDeg || 0) * 0.5 + 1);
      }
    }
  }

  function rebuildTo(index) {
    tearSim();
    ensureLayers();
    state.cursor = Math.max(0, Math.min(index, (state.history.events || []).length));
    var events = sortEvents(state.history.events);
    for (var i = 0; i < state.cursor; i++) applyEvent(events[i]);
    paint();
    updateChrome();
  }

  function pause() {
    if (state.stopPlay) {
      state.stopPlay();
      state.stopPlay = null;
    }
    state.playing = false;
    updateChrome();
  }

  function play() {
    if (!state.history) return;
    var total = (state.history.events || []).length;
    if (state.cursor >= total) {
      rebuildTo(0);
    }
    pause();
    state.playing = true;
    updateChrome();
    setStatus("Playing…");
    var start = state.cursor;
    state.stopPlay = playFrom(state.history, start, {
      rate: Math.max(0.25, (state.history.duration || 12) / 8),
      onEvent: function (ev, absIndex) {
        applyEvent(ev);
        state.cursor = absIndex + 1;
        paint();
        updateChrome();
      },
      onDone: function () {
        state.playing = false;
        state.stopPlay = null;
        state.cursor = total;
        updateChrome();
        setStatus("Done — scrub or Replay");
      },
    });
  }

  function step(delta) {
    pause();
    var total = (state.history.events || []).length;
    rebuildTo(Math.max(0, Math.min(total, state.cursor + delta)));
    setStatus(delta > 0 ? "Step →" : "Step ←");
  }

  function closeOverlay() {
    pause();
    tearSim();
    if (state.overlay) {
      state.overlay.remove();
      state.overlay = null;
    }
    document.body.dataset.replay = "0";
    if (state.els.toggle) state.els.toggle.setAttribute("aria-pressed", "false");
  }

  async function loadHistory() {
    try {
      var r = await fetch(apiUrl("/api/curation/history"));
      if (r.ok) {
        var doc = await r.json();
        if (doc && Array.isArray(doc.events)) return doc;
      }
    } catch (e) {
      /* fall through */
    }
    return buildFromData(state.data);
  }

  async function openOverlay() {
    if (state.overlay) {
      closeOverlay();
      return;
    }
    var pane = document.getElementById("graph-pane");
    if (!pane) return;

    var overlay = document.createElement("div");
    overlay.className = "ce-curation-replay";
    overlay.innerHTML =
      '<svg class="ce-curation-replay-svg" aria-label="Curation replay graph"></svg>' +
      '<div class="ce-curation-replay-chrome">' +
      '<button type="button" class="ctrl-btn" data-act="play" aria-pressed="false">Play</button>' +
      '<button type="button" class="ctrl-btn" data-act="step-back" title="Step back">‹</button>' +
      '<button type="button" class="ctrl-btn" data-act="step-fwd" title="Step forward">›</button>' +
      '<input type="range" class="ce-replay-scrub" data-act="scrub" min="0" max="0" value="0" aria-label="Replay scrubber">' +
      '<span class="ce-replay-pos" data-pos>0 / 0</span>' +
      '<span class="ce-replay-counts"><span data-n>0</span> nodes · <span data-e>0</span> edges</span>' +
      '<button type="button" class="ctrl-btn" data-act="close" title="Close replay">✕</button>' +
      "</div>" +
      '<div class="ce-curation-replay-label" data-status>Loading curation history…</div>';
    pane.appendChild(overlay);
    state.overlay = overlay;
    state.svg = overlay.querySelector("svg");
    state.els.play = overlay.querySelector('[data-act="play"]');
    state.els.scrub = overlay.querySelector('[data-act="scrub"]');
    state.els.pos = overlay.querySelector("[data-pos]");
    state.els.nCount = overlay.querySelector("[data-n]");
    state.els.eCount = overlay.querySelector("[data-e]");
    state.els.status = overlay.querySelector("[data-status]");
    document.body.dataset.replay = "1";
    if (state.els.toggle) state.els.toggle.setAttribute("aria-pressed", "true");

    overlay.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-act]");
      if (!btn) return;
      var act = btn.getAttribute("data-act");
      if (act === "play") {
        if (state.playing) pause();
        else play();
      } else if (act === "step-back") step(-1);
      else if (act === "step-fwd") step(1);
      else if (act === "close") closeOverlay();
    });
    state.els.scrub.addEventListener("input", function () {
      pause();
      rebuildTo(parseInt(state.els.scrub.value, 10) || 0);
      setStatus("Scrub");
    });

    state.history = await loadHistory();
    state.degree = state.history.degree || {};
    var nEv = (state.history.events || []).length;
    setStatus(
      nEv
        ? "Replay — play / scrub / step (source: " + (state.history.source || "?") + ")"
        : "No curation history yet",
    );
    rebuildTo(0);
  }

  function init(data) {
    state.data = data;
    state.palette = (data && data.palette) || null;
    state.els.toggle = document.getElementById("replay-toggle");
    if (state.els.toggle) {
      state.els.toggle.addEventListener("click", function () {
        openOverlay();
      });
    }
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get("replay") === "1") {
        openOverlay();
      }
    } catch (e) {}
  }

  return { init: init, open: openOverlay, close: closeOverlay };
})();
