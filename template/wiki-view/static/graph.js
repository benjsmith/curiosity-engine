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

  // Auto-mode label thresholds. Tighter than v1 (was 0.68 radius).
  const MIN_NODE_RADIUS_FOR_LABEL_PX = 9;
  const CENTRAL_FRAC = 0.35;   // labels in auto only show within 35% of min dim

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

    // Force simulation. Tuned for cluster separation:
    //   - charge stronger and limited in range so distant clusters don't tug
    //   - link distance longer so rooted hubs splay out
    //   - collide expanded so dense clusters loosen
    simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges).id(d => d.id).distance(85).strength(0.55))
      .force('charge', d3.forceManyBody().strength(-310).distanceMax(420))
      .force('center', d3.forceCenter().strength(0.04))
      .force('collide', d3.forceCollide(d => nodeRadius(d) + 7))
      .alpha(1.4)
      .alphaDecay(0.018)
      .on('tick', tick);

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
        applyVisibility();
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
        if (d.type === 'sources') return 'hidden';
        return 'dim';
      }
      // Idle: sources hidden, everything else visible.
      if (d.type === 'sources') return 'hidden';
      return null;
    });

    edgeSel.attr('data-vis', e => {
      const sId = (typeof e.source === 'object') ? e.source.id : e.source;
      const tId = (typeof e.target === 'object') ? e.target.id : e.target;
      if (hasFocus) {
        const touchesFocus = sId === focusId || tId === focusId;
        const sourceVis = focusSet.has(sId) || (sId !== focusId && nodeById.get(sId)?.type !== 'sources');
        const targetVis = focusSet.has(tId) || (tId !== focusId && nodeById.get(tId)?.type !== 'sources');
        if (touchesFocus) return 'focus';
        // Dim non-focus edges; hide them entirely if a sources endpoint
        // would be hidden in this state (avoids edges leading to nothing).
        if (!sourceVis || !targetVis) return 'hidden';
        return 'dim';
      }
      // Idle: hide edges that touch a sources node (since sources are hidden).
      const sIsSource = nodeById.get(sId)?.type === 'sources';
      const tIsSource = nodeById.get(tId)?.type === 'sources';
      if (sIsSource || tIsSource) return 'hidden';
      return null;
    });

    applyLabelOpacity();
  }

  /* applyLabelOpacity — runs every tick + zoom + resize. Hot path; avoid
   * DOM reads inside the loop. */
  function applyLabelOpacity() {
    if (!textSel) return;
    const hasFocus = focusId != null;
    const focusSet = hasFocus
      ? (() => { const s = new Set(neighbours.get(focusId) || []); s.add(focusId); return s; })()
      : null;
    const r = svg.node().getBoundingClientRect();
    const minDim = Math.min(r.width, r.height);
    const radiusBudget = (minDim * CENTRAL_FRAC) / 2;
    const k = zoomTransform.k;

    textSel.style('opacity', function(d) {
      // Hidden node → no label.
      if (d.type === 'sources' && (!hasFocus || !focusSet.has(d.id))) return 0;

      // Always show labels for focus + neighbours, regardless of mode.
      if (hasFocus && focusSet.has(d.id)) return 1;

      if (labelMode === 'on')  return 1;
      if (labelMode === 'off') return 0;

      // auto: must clear screen-radius bar AND sit in central area.
      const screenR = nodeRadius(d) * k;
      if (screenR < MIN_NODE_RADIUS_FOR_LABEL_PX) return 0;
      const sx = d.x * k + zoomTransform.x;
      const sy = d.y * k + zoomTransform.y;
      const dist = Math.hypot(sx, sy);
      const t = (dist - radiusBudget * 0.6) / (radiusBudget * 0.4);
      return Math.max(0, Math.min(1, 1 - t));
    });
  }

  function setLabelMode(mode) {
    labelMode = mode;
    document.documentElement.dataset.labels = mode;
    if (modeStateEl) modeStateEl.textContent = mode;
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
