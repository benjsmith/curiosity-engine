/* Force-directed graph view.
 *
 * Responsibilities:
 *   - Render nodes (sized by degree, coloured by type) and edges
 *     (solid for wikilink, dashed for depicts).
 *   - Force simulation with collision avoidance + center gravity.
 *   - Hover: highlight one-hop neighbourhood, dim the rest.
 *   - Click: open the modal for that page.
 *   - Pan/zoom + label-mode toggle (auto/on/off).
 *
 * Public API: window.Graph = { init(data), focus(pageId), labelMode: { set(mode), cycle() } }.
 */

window.Graph = (function () {
  let g = null;             // svg group containing nodes + edges
  let svg = null;
  let nodes = null;
  let edges = null;
  let simulation = null;
  let zoomBehavior = null;
  let zoomTransform = d3.zoomIdentity;
  let neighbours = new Map();   // id -> Set<id>
  let nodeById = new Map();
  let labelMode = 'auto';
  let modeStateEl = null;
  let viewportRefreshHandle = null;

  // Auto-mode label thresholds.
  const MIN_NODE_RADIUS_FOR_LABEL_PX = 7;   // node must be at least this big on screen
  const RADIUS_FRAC = 0.68;                 // 68% of the smaller viewport dimension

  function nodeRadius(d) {
    return 4 + Math.sqrt((d.degree || 0) + 1) * 1.6;
  }

  function colourFor(type, palette) {
    return palette[type] || palette.default || '#7a7a7a';
  }

  function init(data) {
    nodes = data.nodes.map(n => Object.assign({}, n));   // copy: D3 mutates
    edges = data.edges.map(e => Object.assign({}, e));
    nodes.forEach(n => nodeById.set(n.id, n));

    // Build adjacency for hover-highlight.
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

    // Edge layer first so nodes paint on top.
    const edgeSel = g.append('g').attr('class', 'edges')
      .selectAll('line')
      .data(edges)
      .enter().append('line')
        .attr('class', 'edge')
        .attr('data-kind', d => d.type);

    const nodeSel = g.append('g').attr('class', 'nodes')
      .selectAll('g')
      .data(nodes, d => d.id)
      .enter().append('g')
        .attr('class', 'node')
        .attr('data-id', d => d.id)
        .attr('data-type', d => d.type)
        .on('mouseenter', (ev, d) => highlightNeighbourhood(d.id))
        .on('mouseleave', clearHighlight)
        .on('click', (ev, d) => {
          ev.stopPropagation();
          window.location.hash = '#page=' + encodeURIComponent(d.id);
        });

    nodeSel.append('circle')
      .attr('r', d => d.r = nodeRadius(d))
      .attr('fill', d => colourFor(d.type, palette));

    nodeSel.append('text')
      .attr('dy', d => -nodeRadius(d) - 3)
      .text(d => d.title || d.id);

    simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges).id(d => d.id).distance(70).strength(0.7))
      .force('charge', d3.forceManyBody().strength(-220))
      .force('center', d3.forceCenter())
      .force('collide', d3.forceCollide(d => nodeRadius(d) + 3))
      .alpha(1)
      .on('tick', () => {
        edgeSel
          .attr('x1', d => d.source.x)
          .attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x)
          .attr('y2', d => d.target.y);
        nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
        applyLabelVisibility();
      });

    zoomBehavior = d3.zoom()
      .scaleExtent([0.15, 4])
      .on('zoom', (ev) => {
        zoomTransform = ev.transform;
        g.attr('transform', ev.transform);
        applyLabelVisibility();
      });
    svg.call(zoomBehavior);

    // Background click clears highlight.
    svg.on('click', () => clearHighlight());

    // Resize handling.
    window.addEventListener('resize', resize);
    resize();

    // Label-mode controls.
    modeStateEl = document.querySelector('#label-mode-state');
    document.querySelector('#label-mode').addEventListener('click', cycleLabelMode);

    return { focus };
  }

  function resize() {
    if (!svg) return;
    const r = svg.node().getBoundingClientRect();
    svg.attr('viewBox', [-r.width / 2, -r.height / 2, r.width, r.height]);
    if (simulation) simulation.alpha(0.3).restart();
    applyLabelVisibility();
  }

  function highlightNeighbourhood(id) {
    if (document.body.dataset.modal === 'open') return;
    const hood = neighbours.get(id) || new Set();
    g.selectAll('.node')
      .attr('data-dim', n => (n.id !== id && !hood.has(n.id)) ? 'true' : null)
      .attr('data-focus', n => n.id === id ? 'true' : null);
    g.selectAll('.edge')
      .attr('data-dim', e => (e.source.id !== id && e.target.id !== id) ? 'true' : null)
      .attr('data-focus', e => (e.source.id === id || e.target.id === id) ? 'true' : null);
  }

  function clearHighlight() {
    if (!g) return;
    g.selectAll('.node').attr('data-dim', null).attr('data-focus', null);
    g.selectAll('.edge').attr('data-dim', null).attr('data-focus', null);
  }

  function focus(pageId) {
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

  function setLabelMode(mode) {
    labelMode = mode;
    document.documentElement.dataset.labels = mode;
    if (modeStateEl) modeStateEl.textContent = mode;
    applyLabelVisibility();
  }

  function cycleLabelMode() {
    const order = ['auto', 'on', 'off'];
    const next = order[(order.indexOf(labelMode) + 1) % order.length];
    setLabelMode(next);
  }

  /* applyLabelVisibility — runs every tick + zoom + resize. Hot path,
   * keep it cheap: no DOM measurement inside the loop. */
  function applyLabelVisibility() {
    if (!g) return;
    const sel = g.selectAll('.node text');
    if (labelMode === 'on') {
      sel.style('opacity', 1);
      return;
    }
    if (labelMode === 'off') {
      sel.style('opacity', 0);
      return;
    }
    // auto: visible when (a) the node's on-screen radius >= threshold,
    // and (b) the node's on-screen position is within the 68% radius
    // of the smaller viewport dimension. Edge fade applied via smooth
    // step so the boundary is soft, not snapping.
    const r = svg.node().getBoundingClientRect();
    const minDim = Math.min(r.width, r.height);
    const radiusBudget = (minDim * RADIUS_FRAC) / 2;
    const k = zoomTransform.k;
    sel.style('opacity', function(d) {
      const screenR = nodeRadius(d) * k;
      if (screenR < MIN_NODE_RADIUS_FOR_LABEL_PX) return 0;
      const sx = d.x * k + zoomTransform.x;
      const sy = d.y * k + zoomTransform.y;
      const dist = Math.hypot(sx, sy);
      // smoothstep between 0.6 and 1.0 of the radius budget — full
      // opacity at <0.6, fade to 0 at >1.0.
      const t = (dist - radiusBudget * 0.6) / (radiusBudget * 0.4);
      return Math.max(0, Math.min(1, 1 - t));
    });
  }

  return { init, focus, setLabelMode, cycleLabelMode };
})();
