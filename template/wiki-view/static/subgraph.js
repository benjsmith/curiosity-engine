/* Subgraph view inside the doc viewer modal.
 *
 * 2-hop force-directed mini-graph:
 *   - Centre node = current modal target. Pinned at the viewport middle
 *     so the rest of the layout fans around it. Label always visible.
 *   - 1-hop neighbours = direct connections. Label on hover.
 *   - 2-hop neighbours = connections of connections. Smaller, slightly
 *     faded; label on hover. Skipped entirely when the 1-hop set is
 *     already dense (>30 nodes).
 *   - All edges between any pair of nodes in the set are drawn.
 *
 * Layout runs as a normal d3-force simulation but is pre-warmed (250
 * manual ticks) before any DOM update, so the user sees the settled
 * layout at first paint without watching it relax.
 *
 * Click any non-centre node → push hash, main-router swaps the modal
 * content to that page; the subgraph re-renders for the new centre.
 */
window.Subgraph = (function () {
  let allNodes = null, allEdges = null, palette = null;
  let nodeById = new Map();
  let neighbours = new Map();
  let containerEl = null, headEl = null;

  // Node-set caps. 1-hop dense neighbourhoods (e.g. Scaling Laws with
  // 50+ direct edges) skip the 2-hop expansion to keep the wheel
  // readable. The hard cap stops 2-hop runaway on small but well-
  // connected hops.
  const HOP1_HOP2_THRESHOLD = 30;
  const HARD_NODE_CAP = 80;

  function init(data) {
    allNodes = data.nodes;
    allEdges = data.edges || [];
    palette = data.palette || {};
    nodeById = new Map(allNodes.map(n => [n.id, n]));
    neighbours = new Map();
    allEdges.forEach(e => {
      const s = (typeof e.source === 'object') ? e.source.id : e.source;
      const t = (typeof e.target === 'object') ? e.target.id : e.target;
      if (!neighbours.has(s)) neighbours.set(s, new Set());
      if (!neighbours.has(t)) neighbours.set(t, new Set());
      neighbours.get(s).add(t);
      neighbours.get(t).add(s);
    });
    containerEl = document.querySelector('#modal-subgraph');
    headEl = document.querySelector('#modal-subgraph-head');

    if (containerEl) {
      containerEl.addEventListener('click', (ev) => {
        const g = ev.target.closest && ev.target.closest('.sub-node');
        if (!g) return;
        if (g.classList.contains('sub-center')) return;
        const id = g.dataset.id;
        if (id) window.location.hash = '#page=' + encodeURIComponent(id);
      });
    }
  }

  function render(centerId) {
    if (!containerEl) return;
    const center = nodeById.get(centerId);
    if (!center) { clear(); return; }

    // Build the 2-hop node set.
    const hop1 = neighbours.get(centerId) || new Set();
    const ids = new Set([centerId]);
    hop1.forEach(id => ids.add(id));

    let includedHop2 = false;
    if (hop1.size < HOP1_HOP2_THRESHOLD) {
      for (const n1 of hop1) {
        if (ids.size >= HARD_NODE_CAP) break;
        const h2 = neighbours.get(n1) || new Set();
        for (const n2 of h2) {
          if (ids.size >= HARD_NODE_CAP) break;
          if (!ids.has(n2)) {
            ids.add(n2);
            includedHop2 = true;
          }
        }
      }
    }

    if (headEl) {
      const cnt = ids.size - 1;
      const desc = includedHop2 ? '2-hop' : '1-hop';
      headEl.textContent = cnt > 0 ? `Connections (${cnt}, ${desc})` : 'No connections';
    }
    if (ids.size <= 1) { containerEl.innerHTML = ''; return; }

    const subNodes = [...ids].map(id => Object.assign({
      hop: id === centerId ? 0 : (hop1.has(id) ? 1 : 2),
    }, nodeById.get(id)));
    const subById = new Map(subNodes.map(n => [n.id, n]));
    const subEdges = [];
    for (const e of allEdges) {
      const sId = (typeof e.source === 'object') ? e.source.id : e.source;
      const tId = (typeof e.target === 'object') ? e.target.id : e.target;
      if (subById.has(sId) && subById.has(tId) && sId !== tId) {
        subEdges.push({ source: sId, target: tId, type: e.type });
      }
    }

    const W = containerEl.clientWidth || 600;
    const H = Math.max(360, Math.min(560, 300 + subNodes.length * 3));

    // Pin the centre to the viewport middle and run a settled simulation.
    const cn = subById.get(centerId);
    cn.fx = W / 2;
    cn.fy = H / 2;

    const sim = d3.forceSimulation(subNodes)
      .force('link', d3.forceLink(subEdges).id(d => d.id)
        .distance(d => 38 + (d.source.hop + d.target.hop) * 6)
        .strength(0.55))
      .force('charge', d3.forceManyBody()
        .strength(d => d.hop === 0 ? -380 : -160)
        .distanceMax(260))
      .force('collide', d3.forceCollide(d => subRadius(d) + 5))
      .alpha(1)
      .alphaDecay(0.06)
      .stop();

    for (let i = 0; i < 250; i++) sim.tick();

    // Clamp positions inside the SVG so nothing escapes off-screen
    // during the static render.
    const margin = 28;
    for (const n of subNodes) {
      n.x = Math.max(margin, Math.min(W - margin, n.x));
      n.y = Math.max(margin, Math.min(H - margin, n.y));
    }

    const colourFor = t => palette[t] || palette.default || '#7a7a7a';
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;

    // Edges first.
    for (const e of subEdges) {
      const s = subById.get(typeof e.source === 'object' ? e.source.id : e.source);
      const t = subById.get(typeof e.target === 'object' ? e.target.id : e.target);
      svg += `<line class="sub-edge" data-kind="${escapeAttr(e.type)}" x1="${s.x.toFixed(1)}" y1="${s.y.toFixed(1)}" x2="${t.x.toFixed(1)}" y2="${t.y.toFixed(1)}"/>`;
    }

    // Nodes.
    for (const n of subNodes) {
      const r = subRadius(n);
      const isCenter = n.id === centerId;
      const cls = isCenter ? 'sub-node sub-center' : 'sub-node';
      svg += `<g class="${cls}" data-id="${escapeAttr(n.id)}" data-type="${escapeAttr(n.type)}" data-hop="${n.hop}">
        <circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${r}" fill="${colourFor(n.type)}"/>
        <text x="${n.x.toFixed(1)}" y="${(n.y - r - 4).toFixed(1)}" text-anchor="middle">${escapeHtml(n.title || n.id)}</text>
        <title>${escapeHtml(n.title || n.id)}</title>
      </g>`;
    }
    svg += '</svg>';
    containerEl.innerHTML = svg;
  }

  function subRadius(d) {
    const baseR = 4 + Math.sqrt((d.degree || 0) + 1) * 1.4;
    if (d.hop === 0) return baseR + 2;
    if (d.hop === 2) return Math.max(3, baseR - 1);
    return baseR;
  }

  function clear() {
    if (containerEl) containerEl.innerHTML = '';
    if (headEl) headEl.textContent = '';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s); }

  return { init, render, clear };
})();
