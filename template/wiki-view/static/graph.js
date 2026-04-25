/* Force-directed graph view.
 *
 * Visibility model
 * ────────────────
 * Each node carries a `data-vis` attribute reflecting one of:
 *   "focus"   — current hover/modal target. Highlight ring + label always shown.
 *   "neighbour"— 1-hop of focus. Visible at full opacity, label always shown.
 *   "dim"     — visible but reduced opacity (we have a focus, but this node
 *               isn't part of its 1-hop).
 *   "hidden"  — not painted at all. Default for `sources`-type nodes when
 *               not in the focus set; lets the rest of the graph breathe.
 *   (none)    — idle state; default visible (non-source types).
 *
 * Edges follow the visibility of their endpoints — an edge is hidden if
 * either endpoint is hidden, dimmed if neither endpoint is the focus,
 * focused if either endpoint is the focus.
 *
 * Labels: hover ALWAYS shows labels for the focus + 1-hop neighbours,
 * regardless of `labelMode`. For non-focus nodes, the mode applies:
 *   on   — always shown
 *   off  — never shown
 *   auto — shown when (a) screen-radius >= MIN_PX and (b) the node sits
 *          inside the central viewport rect (35% of min dim).
 */

window.Graph = (function () {
  let g = null;
  let svg = null;
  let nodes = null;
  let edges = null;
  let simulation = null;
  let zoomBehavior = null;
  let zoomTransform = d3.zoomIdentity;
  let neighbours = new Map();
  let nodeById = new Map();
  let labelMode = 'auto';
  let modeStateEl = null;
  let nodeSel = null;
  let edgeSel = null;
  let textSel = null;
  let focusId = null;       // hover OR modal target; null = idle
  let focusOrigin = null;   // 'hover' | 'modal' — for ordering rules
  let _autoVisibleIds = new Set();    // cache: ids whose labels show in auto mode
  let _autoRecomputeScheduled = false;

  // Source-type matcher: frontmatter says 'source', subdir name 'sources'.
  function isSourceType(t) { return t === 'source' || t === 'sources'; }

  // Minimum on-screen pixel size for a label to ever be considered.
  const MIN_NODE_RADIUS_FOR_LABEL_PX = 7;
  // Padding around each label's bounding box (px) when checking collisions.
  const LABEL_PADDING_PX = 4;

  function nodeRadius(d) {
    return 4 + Math.sqrt((d.degree || 0) + 1) * 1.6;
  }

  function colourFor(type, palette) {
    return palette[type] || palette.default || '#7a7a7a';
  }

  function init(data) {
    nodes = data.nodes.map(n => Object.assign({}, n));
    edges = data.edges.map(e => Object.assign({}, e));
    nodes.forEach(n => nodeById.set(n.id, n));

    edges.forEach(e => {
      if (!neighbours.has(e.source)) neighbours.set(e.source, new Set());
      if (!neighbours.has(e.target)) neighbours.set(e.target, new Set());
      neighbours.get(e.source).add(e.target);
      neighbours.get(e.target).add(e.source);
    });

    const palette = data.palette || {};

    const container = document.querySelector('#graph');
    svg = d3.select(container).append('svg');
    g = svg.append('g').attr('class', 'viewport');

    edgeSel = g.append('g').attr('class', 'edges')
      .selectAll('line')
      .data(edges)
      .enter().append('line')
        .attr('class', 'edge')
        .attr('data-kind', d => d.type);

    nodeSel = g.append('g').attr('class', 'nodes')
      .selectAll('g')
      .data(nodes, d => d.id)
      .enter().append('g')
        .attr('class', 'node')
        .attr('data-id', d => d.id)
        .attr('data-type', d => d.type)
        .on('mouseenter', (ev, d) => setFocus(d.id, 'hover'))
        .on('mouseleave', () => {
          if (focusOrigin === 'hover') setFocus(null);
        })
        .on('click', (ev, d) => {
          ev.stopPropagation();
          window.location.hash = '#page=' + encodeURIComponent(d.id);
        });

    nodeSel.append('circle')
      .attr('r', d => d.r = nodeRadius(d))
      .attr('fill', d => colourFor(d.type, palette));

    textSel = nodeSel.append('text')
      .attr('dy', d => -nodeRadius(d) - 3)
      .text(d => d.title || d.id);

    // Force simulation. Tuned for cluster separation; pre-warmed below
    // so the user doesn't watch the layout flop into place on first paint.
    simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges).id(d => d.id).distance(85).strength(0.55))
      .force('charge', d3.forceManyBody().strength(-310).distanceMax(420))
      .force('center', d3.forceCenter().strength(0.04))
      .force('collide', d3.forceCollide(d => nodeRadius(d) + 7))
      .stop();   // halt the auto-loop while we hand-tick

    // Pre-warm: 250 manual ticks land the layout very close to settled.
    // No DOM updates happen during these ticks (we haven't bound 'tick'
    // yet) so this is effectively free vs. animating each frame.
    simulation.alpha(1).alphaDecay(0.05);
    for (let i = 0; i < 250; i++) simulation.tick();

    // Bind the per-tick render callback and gently restart with low
    // alpha so the layout breathes without animating from scratch.
    simulation.on('tick', tick);
    simulation.alpha(0.25).alphaDecay(0.05).restart();
    simulation.on('end', () => scheduleAutoRecompute());

    // Drag — Obsidian-style click-and-hold to move. Release lets physics
    // take over again (no pinning).
    nodeSel.call(
      d3.drag()
        .on('start', (ev, d) => {
          if (!ev.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on('drag', (ev, d) => {
          d.fx = ev.x; d.fy = ev.y;
        })
        .on('end', (ev, d) => {
          if (!ev.active) simulation.alphaTarget(0);
          d.fx = null; d.fy = null;
        })
    );

    zoomBehavior = d3.zoom()
      .scaleExtent([0.15, 4])
      .filter((event) => {
        // Allow zoom on the SVG background but not when starting on a
        // node (drag on node should preempt pan).
        if (event.type === 'wheel') return true;
        return !event.target.closest || !event.target.closest('.node');
      })
      .on('zoom', (ev) => {
        zoomTransform = ev.transform;
        g.attr('transform', ev.transform);
        scheduleAutoRecompute();
        applyLabelOpacity();
      });
    svg.call(zoomBehavior);

    svg.on('click', () => {
      if (focusOrigin === 'hover') setFocus(null);
    });

    window.addEventListener('resize', resize);
    resize();

    modeStateEl = document.querySelector('#label-mode-state');
    document.querySelector('#label-mode').addEventListener('click', cycleLabelMode);

    // Initial paint puts sources into hidden state.
    applyVisibility();

    return { focus: focusOnPage };
  }

  function tick() {
    edgeSel
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
    applyLabelOpacity();
  }

  function resize() {
    if (!svg) return;
    const r = svg.node().getBoundingClientRect();
    svg.attr('viewBox', [-r.width / 2, -r.height / 2, r.width, r.height]);
    if (simulation) simulation.alpha(0.3).restart();
    scheduleAutoRecompute();
    applyLabelOpacity();
  }

  /* setFocus / clear: idempotent. Pass null to clear. */
  function setFocus(id, origin) {
    if (id === focusId) {
      if (origin) focusOrigin = origin;   // upgrade hover→modal etc.
      return;
    }
    focusId = id;
    focusOrigin = id ? origin : null;
    applyVisibility();
  }

  function focusOnPage(pageId) {
    setFocus(pageId, 'modal');
    const d = nodeById.get(pageId);
    if (!d || d.x == null) return;
    const r = svg.node().getBoundingClientRect();
    const k = Math.max(0.9, zoomTransform.k);
    const tx = -d.x * k;
    const ty = -d.y * k;
    svg.transition().duration(450)
      .call(zoomBehavior.transform,
            d3.zoomIdentity.translate(tx, ty).scale(k));
  }

  /* Compute per-node + per-edge visibility. Called on every focus
   * change and zoom end (label opacity is split out into its own
   * loop that runs every tick). */
  function applyVisibility() {
    if (!g) return;
    const hasFocus = focusId != null;
    const focusSet = hasFocus
      ? (() => { const s = new Set(neighbours.get(focusId) || []); s.add(focusId); return s; })()
      : null;

    nodeSel.attr('data-vis', d => {
      if (hasFocus) {
        if (d.id === focusId) return 'focus';
        if (focusSet.has(d.id)) return 'neighbour';
        if (isSourceType(d.type)) return 'hidden';
        return 'dim';
      }
      if (isSourceType(d.type)) return 'hidden';
      return null;
    });

    edgeSel.attr('data-vis', e => {
      const sId = (typeof e.source === 'object') ? e.source.id : e.source;
      const tId = (typeof e.target === 'object') ? e.target.id : e.target;
      const sIsSource = isSourceType(nodeById.get(sId)?.type);
      const tIsSource = isSourceType(nodeById.get(tId)?.type);
      if (hasFocus) {
        const touchesFocus = sId === focusId || tId === focusId;
        if (touchesFocus) return 'focus';
        // Hide edges whose source endpoint would itself be hidden in
        // this focus state (avoids edges leading to nothing).
        const sourceHidden = sIsSource && !focusSet.has(sId);
        const targetHidden = tIsSource && !focusSet.has(tId);
        if (sourceHidden || targetHidden) return 'hidden';
        return 'dim';
      }
      if (sIsSource || tIsSource) return 'hidden';
      return null;
    });

    applyLabelOpacity();
  }

  /* applyLabelOpacity — runs every tick + zoom + resize. Hot path; cheap
   * lookups only. Auto-mode visibility is precomputed in
   * `_autoVisibleIds` and reused until zoom/layout settles. */
  function applyLabelOpacity() {
    if (!textSel) return;
    const hasFocus = focusId != null;
    const focusSet = hasFocus
      ? (() => { const s = new Set(neighbours.get(focusId) || []); s.add(focusId); return s; })()
      : null;

    textSel.style('opacity', function(d) {
      const isSource = isSourceType(d.type);

      // Source labels only show when the source itself is the focus —
      // not when a source is just a 1-hop neighbour. Keeps the visible
      // graph clear of source-title clutter when reading a non-source
      // page.
      if (isSource) {
        return (hasFocus && d.id === focusId) ? 1 : 0;
      }

      // Non-sources: focus + neighbours always show, regardless of mode.
      if (hasFocus && focusSet.has(d.id)) return 1;

      if (labelMode === 'on')  return 1;
      if (labelMode === 'off') return 0;

      // auto: collision-free greedy set computed in recomputeAutoVisible.
      return _autoVisibleIds.has(d.id) ? 1 : 0;
    });
  }

  /* scheduleAutoRecompute coalesces zoom/resize/tick/end events into a
   * single rAF-deferred recompute. The collision check is O(n²) in
   * the candidate count which is fine at <300 candidates, but we don't
   * want to run it on every tick. */
  function scheduleAutoRecompute() {
    if (_autoRecomputeScheduled) return;
    _autoRecomputeScheduled = true;
    requestAnimationFrame(() => {
      _autoRecomputeScheduled = false;
      recomputeAutoVisible();
      applyLabelOpacity();
    });
  }

  /* Greedy bbox-collision label placement.
   *
   * Candidate filter: node is non-source, on-screen radius >= MIN_PX.
   * Candidates sorted by degree (largest = most connections first), so
   * the most-connected nodes win label slots when they collide with
   * smaller neighbours.
   *
   * Bounding box is approximated from character count (Inter at 11px is
   * ~6.2 px/char). A fully accurate measure would call getBBox per
   * label, which works but costs reflows; the heuristic is good enough.
   */
  function recomputeAutoVisible() {
    if (!nodes) { _autoVisibleIds = new Set(); return; }
    const r = svg.node().getBoundingClientRect();
    const k = zoomTransform.k;

    const candidates = [];
    for (const d of nodes) {
      if (isSourceType(d.type)) continue;     // sources never auto-label
      if (d.x == null) continue;              // pre-tick guard
      const screenR = nodeRadius(d) * k;
      if (screenR < MIN_NODE_RADIUS_FOR_LABEL_PX) continue;
      // Node centre in screen space (svg viewBox has origin at centre).
      const sx = d.x * k + zoomTransform.x + r.width / 2;
      const sy = d.y * k + zoomTransform.y + r.height / 2;
      // Cull candidates outside the visible viewport (with a margin).
      if (sx < -100 || sy < -100 || sx > r.width + 100 || sy > r.height + 100) continue;
      const title = d.title || d.id;
      const w = title.length * 6.2 + LABEL_PADDING_PX * 2;
      const h = 14 + LABEL_PADDING_PX * 2;
      candidates.push({
        id: d.id,
        degree: d.degree || 0,
        // bbox above the node circle (text-anchor middle, dy = -r - 3)
        x: sx - w / 2,
        y: sy - screenR - 3 - h,
        w, h,
      });
    }

    candidates.sort((a, b) => b.degree - a.degree);

    const placed = [];
    const visibleIds = new Set();
    for (const c of candidates) {
      let collide = false;
      for (let i = 0; i < placed.length; i++) {
        const p = placed[i];
        if (c.x < p.x + p.w && c.x + c.w > p.x &&
            c.y < p.y + p.h && c.y + c.h > p.y) {
          collide = true; break;
        }
      }
      if (!collide) {
        placed.push(c);
        visibleIds.add(c.id);
      }
    }
    _autoVisibleIds = visibleIds;
  }

  function setLabelMode(mode) {
    labelMode = mode;
    document.documentElement.dataset.labels = mode;
    if (modeStateEl) modeStateEl.textContent = mode;
    if (mode === 'auto') recomputeAutoVisible();
    applyLabelOpacity();
  }

  function cycleLabelMode() {
    const order = ['auto', 'on', 'off'];
    setLabelMode(order[(order.indexOf(labelMode) + 1) % order.length]);
  }

  return {
    init,
    focus: focusOnPage,
    setLabelMode,
    cycleLabelMode,
    clearFocus: () => setFocus(null),
  };
})();
