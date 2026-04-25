/* Subgraph view inside the doc viewer modal.
 *
 * Static radial layout: the focused page is the centre node, all
 * 1-hop neighbours arrange evenly around it. Clicking a neighbour
 * pushes a hash change which the main-router picks up and swaps the
 * modal content to that page. The wheel pattern is deliberate — a
 * deterministic, fast layout that's readable at a glance and never
 * has to relax like the main graph.
 *
 * Public API: Subgraph.init(data), Subgraph.render(centerId), Subgraph.clear()
 */
window.Subgraph = (function () {
  let nodes = null, palette = null;
  let nodeById = new Map();
  let neighbours = new Map();
  let containerEl = null;
  let headEl = null;

  function init(data) {
    nodes = data.nodes;
    palette = data.palette || {};
    nodeById = new Map();
    neighbours = new Map();
    nodes.forEach(n => nodeById.set(n.id, n));
    (data.edges || []).forEach(e => {
      const s = (typeof e.source === 'object') ? e.source.id : e.source;
      const t = (typeof e.target === 'object') ? e.target.id : e.target;
      if (!neighbours.has(s)) neighbours.set(s, new Set());
      if (!neighbours.has(t)) neighbours.set(t, new Set());
      neighbours.get(s).add(t);
      neighbours.get(t).add(s);
    });
    containerEl = document.querySelector('#modal-subgraph');
    headEl = document.querySelector('#modal-subgraph-head');

    // Click delegation — neighbour click navigates, centre is inert.
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
    const nIds = neighbours.get(centerId) || new Set();
    const ns = [...nIds].map(id => nodeById.get(id)).filter(Boolean);

    if (headEl) {
      headEl.textContent = ns.length === 0
        ? 'No connections'
        : `Connections (${ns.length})`;
    }
    if (ns.length === 0) {
      containerEl.innerHTML = '';
      return;
    }

    // Sort neighbours by degree desc so larger nodes occupy the more
    // visually prominent angular slots (the wheel is symmetric, but
    // dense neighbourhoods read better with the top edges populated by
    // hubs).
    ns.sort((a, b) => (b.degree || 0) - (a.degree || 0));

    // Geometry. Container width is fluid; we pick a height that scales
    // gently with neighbour count so dense wheels don't compress.
    const W = containerEl.clientWidth || 600;
    const H = Math.max(280, Math.min(420, 240 + ns.length * 4));
    const cx = W / 2, cy = H / 2;
    const ringR = Math.min(W, H) * 0.36;

    const colourFor = t => palette[t] || palette.default || '#7a7a7a';
    const radius = d => 4 + Math.sqrt((d.degree || 0) + 1) * 1.4;
    const centerR = Math.max(8, radius(center) + 2);

    // Hard truncation for label legibility in dense wheels.
    const labelChars = ns.length > 16 ? 16 : (ns.length > 8 ? 22 : 28);
    const trunc = (s, n) => s.length > n ? s.substring(0, n - 1) + '…' : s;

    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;

    // Edges first so nodes paint on top.
    const positioned = ns.map((n, i) => {
      const angle = (2 * Math.PI * i / ns.length) - Math.PI / 2;  // start at 12 o'clock
      return {
        n,
        x: cx + ringR * Math.cos(angle),
        y: cy + ringR * Math.sin(angle),
        angle,
      };
    });
    for (const p of positioned) {
      svg += `<line class="sub-edge" x1="${cx}" y1="${cy}" x2="${p.x.toFixed(1)}" y2="${p.y.toFixed(1)}"/>`;
    }

    // Centre node (no click — already showing).
    svg += `<g class="sub-node sub-center" data-id="${escapeAttr(center.id)}" data-type="${escapeAttr(center.type)}">
      <circle cx="${cx}" cy="${cy}" r="${centerR}" fill="${colourFor(center.type)}"/>
      <text x="${cx}" y="${cy + centerR + 14}" text-anchor="middle">${escapeHtml(trunc(center.title || center.id, 32))}</text>
      <title>${escapeHtml(center.title || center.id)}</title>
    </g>`;

    // Neighbour nodes — labels positioned outside the ring so they
    // don't tangle with the centre. Anchor flips left/right of centre.
    for (const p of positioned) {
      const r = radius(p.n);
      const dx = Math.cos(p.angle);
      const dy = Math.sin(p.angle);
      const labelDist = r + 8;
      const lx = (p.x + dx * labelDist).toFixed(1);
      const ly = (p.y + dy * labelDist + 4).toFixed(1);    // +4 baseline shim
      const anchor = dx > 0.15 ? 'start' : dx < -0.15 ? 'end' : 'middle';
      svg += `<g class="sub-node" data-id="${escapeAttr(p.n.id)}" data-type="${escapeAttr(p.n.type)}">
        <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r}" fill="${colourFor(p.n.type)}"/>
        <text x="${lx}" y="${ly}" text-anchor="${anchor}">${escapeHtml(trunc(p.n.title || p.n.id, labelChars))}</text>
        <title>${escapeHtml(p.n.title || p.n.id)}</title>
      </g>`;
    }

    svg += '</svg>';
    containerEl.innerHTML = svg;
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
