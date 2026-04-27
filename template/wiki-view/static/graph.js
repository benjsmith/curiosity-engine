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
  let _isDragging = false;            // suppress hover-focus changes mid-drag

  // Type canonicalization — frontmatter uses singular forms, subdir
  // names plural; collapse to a single key per type so the user-facing
  // filter operates on one axis.
  const TYPE_CANONICAL = {
    analysis:     'analysis',     analyses: 'analysis',
    concept:      'concept',      concepts: 'concept',
    entity:       'entity',       entities: 'entity',
    evidence:     'evidence',
    fact:         'fact',         facts:    'fact',
    figure:       'figure',       figures:  'figure',
    table:        'table',        tables:   'table',
    source:       'source',       sources:  'source',
    note:         'note',         notes:    'note',
    todo:         'todo',         'todo-list': 'todo',
    unclassified: 'unclassified',
  };
  function canonicalType(t) { return TYPE_CANONICAL[t] || t || 'default'; }

  // Types whose labels are eligible for display under the global label
  // mode (auto/on/off). Types absent from the set behave like the old
  // quiet list — node visible, label only on direct hover. Defaults
  // chosen so the static-graph view stays readable: concept + entity
  // are the structural hubs, note + todo are user input. Source / fact
  // / analysis / evidence / figure / table carry too many or too long
  // titles to show by default. Persisted in localStorage; user toggles
  // via the label-types popover.
  const LABEL_TYPE_DEFAULTS = ['concept', 'entity', 'note', 'todo'];
  const ALL_LABEL_TYPES = [
    'concept', 'entity', 'evidence', 'fact', 'analysis',
    'figure',  'table',  'source',   'note', 'todo',
    'unclassified',
  ];
  let _labelTypeFilter = (() => {
    try {
      const raw = localStorage.getItem('curiosity-engine.label-types');
      if (raw) return new Set(JSON.parse(raw));
    } catch (e) {}
    return new Set(LABEL_TYPE_DEFAULTS);
  })();
  function isLabelTypeAllowed(t) {
    return _labelTypeFilter.has(canonicalType(t));
  }
  function persistLabelTypes() {
    try {
      localStorage.setItem(
        'curiosity-engine.label-types',
        JSON.stringify([..._labelTypeFilter]),
      );
    } catch (e) {}
  }

  // Title parsing + wrapping. Frontmatter titles often start with a
  // bracketed type abbreviation (e.g. `[ana] Pretraining Data Curation`).
  // We pull the prefix out so it can be rendered in a muted colour on
  // its own line above the title body, and we wrap the body to at most
  // 2 words per line per the user's spec.
  const TITLE_PREFIX_RE = /^(\[[^\]]+\])\s+(.+)$/;
  function parseTitle(title) {
    const m = TITLE_PREFIX_RE.exec(title);
    if (m) return { prefix: m[1], rest: m[2] };
    return { prefix: '', rest: title };
  }
  function wrapTitleBody(text, wordsPerLine) {
    const wpl = wordsPerLine || 2;
    const words = text.split(/\s+/).filter(Boolean);
    const lines = [];
    for (let i = 0; i < words.length; i += wpl) {
      lines.push(words.slice(i, i + wpl).join(' '));
    }
    return lines.length ? lines : [''];
  }

  // Line height for multi-line label rendering (matches CSS font-size 11px).
  const LABEL_LINE_HEIGHT_SVG = 12;

  // Physics defaults — exposed via the settings panel as live sliders.
  // Values bumped from previous round for more spread between clusters.
  const PHYSICS_DEFAULTS = { charge: -420, link: 110, collide: 10 };
  const PHYSICS = Object.assign({}, PHYSICS_DEFAULTS);

  // Minimum on-screen sizes for a label to ever be considered.
  const MIN_NODE_RADIUS_FOR_LABEL_PX = 6;
  const MIN_LABEL_TEXT_HEIGHT_PX = 10;   // text smaller than this isn't legible
  // Padding around each label's bounding box (px) when checking collisions.
  const LABEL_PADDING_PX = 4;
  // SVG text font-size in user units (must match CSS .node text font-size).
  const LABEL_FONT_SVG = 11;

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

    // ── Layer order ──
    //   edges (bottom) → circles → labels (top)
    // Labels live in their own g so they paint after every circle —
    // a sibling node's circle can no longer cover an earlier node's
    // label text.
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
        .on('mouseenter', (ev, d) => {
          if (_isDragging) return;
          setFocus(d.id, 'hover');
        })
        .on('mouseleave', () => {
          if (_isDragging) return;
          if (focusOrigin === 'hover') setFocus(null);
        })
        .on('click', (ev, d) => {
          ev.stopPropagation();
          window.location.hash = '#page=' + encodeURIComponent(d.id);
        });

    nodeSel.append('circle')
      .attr('r', d => d.r = nodeRadius(d))
      .attr('fill', d => colourFor(d.type, palette));

    textSel = g.append('g').attr('class', 'node-labels')
      .selectAll('text')
      .data(nodes, d => d.id)
      .enter().append('text')
        .attr('class', 'node-label')
        .attr('data-id', d => d.id);
    textSel.each(function(d) {
      const t = d3.select(this);
      const { prefix, rest } = parseTitle(d.title || d.id);
      const lines = wrapTitleBody(rest, 2);
      const numLines = lines.length + (prefix ? 1 : 0);
      d._labelLineCount = numLines;
      // y so the bottom-line baseline sits at -r - 3 (just above the
      // circle) once the text is translated to the node's position.
      const r = nodeRadius(d);
      t.attr('y', -r - 3 - (numLines - 1) * LABEL_LINE_HEIGHT_SVG);
      if (prefix) {
        t.append('tspan')
          .attr('class', 'label-prefix')
          .attr('x', 0)
          .text(prefix);
      }
      lines.forEach((line, i) => {
        const ts = t.append('tspan').attr('x', 0).text(line);
        if (prefix || i > 0) ts.attr('dy', LABEL_LINE_HEIGHT_SVG);
      });
    });

    // Measure each label's natural bbox once so the auto-mode collision
    // check uses real (multi-line) dimensions. getBBox returns SVG user
    // units; multiplied by zoom k for screen px during collision testing.
    textSel.each(function(d) {
      try {
        const b = this.getBBox();
        d._labelW = b.width;
        d._labelH = b.height;
      } catch (e) {
        const longest = (d.title || d.id).split(/\s+/).slice(0, 2).join(' ');
        d._labelW = longest.length * 6.5;
        d._labelH = (d._labelLineCount || 1) * LABEL_LINE_HEIGHT_SVG;
      }
    });

    // Force simulation. Tuned for cluster separation; pre-warmed below
    // so the user doesn't watch the layout flop into place on first paint.
    simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(edges).id(d => d.id).distance(PHYSICS.link).strength(0.55))
      .force('charge', d3.forceManyBody().strength(PHYSICS.charge).distanceMax(500))
      .force('center', d3.forceCenter().strength(0.04))
      .force('collide', d3.forceCollide(d => nodeRadius(d) + PHYSICS.collide))
      .stop();   // halt the auto-loop while we hand-tick

    // Pre-warm: 350 manual ticks land the layout almost fully settled.
    // No DOM updates happen during these ticks (we haven't bound 'tick'
    // yet) so this is effectively free vs. animating each frame.
    simulation.alpha(1).alphaDecay(0.05);
    for (let i = 0; i < 350; i++) simulation.tick();

    // Bind the per-tick render callback and gently restart with low
    // alpha so the layout breathes without animating from scratch.
    // 0.15 gets us ~50 visible ticks before alpha decays below 0.001 —
    // enough to see things gently relax, short enough that any per-tick
    // cost (314 transform writes + 1675 edge attr writes on a typical
    // wiki) doesn't manifest as a stutter.
    simulation.on('tick', tick);
    simulation.alpha(0.15).alphaDecay(0.05).restart();
    simulation.on('end', () => scheduleAutoRecompute());

    // Drag — Obsidian-style click-and-hold to move. Release lets physics
    // take over again (no pinning). clickDistance(5) means small pointer
    // movement during a click still registers as a click — without it
    // even a 1-px wobble suppresses the native click event and the user
    // has to double-click to open the modal.
    nodeSel.call(
      d3.drag()
        .clickDistance(5)
        .on('start', (ev, d) => {
          _isDragging = true;
          if (!ev.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on('drag', (ev, d) => {
          d.fx = ev.x; d.fy = ev.y;
        })
        .on('end', (ev, d) => {
          _isDragging = false;
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
    initSettingsPanel();
    initLabelTypesPanel();

    applyVisibility();

    return { focus: focusOnPage };
  }

  /* Settings panel — top-right gear icon opens a popover with physics
   * sliders. Live updates simulation forces; values persist for the
   * session via memory only (refresh resets to defaults). */
  function initSettingsPanel() {
    const trigger = document.querySelector('#settings-trigger');
    const panel = document.querySelector('#settings-panel');
    if (!trigger || !panel) return;

    trigger.addEventListener('click', (ev) => {
      ev.stopPropagation();
      panel.classList.toggle('hidden');
    });
    document.addEventListener('click', (ev) => {
      if (panel.classList.contains('hidden')) return;
      if (panel.contains(ev.target)) return;
      if (trigger.contains(ev.target)) return;
      panel.classList.add('hidden');
    });

    function bindSlider(inputId, valueId, apply) {
      const input = document.querySelector('#' + inputId);
      const out = document.querySelector('#' + valueId);
      if (!input || !out) return;
      input.addEventListener('input', () => {
        const v = parseFloat(input.value);
        out.textContent = input.value;
        apply(v);
        simulation.alpha(0.35).restart();
      });
    }

    bindSlider('phys-charge', 'phys-charge-val', v => {
      PHYSICS.charge = v;
      simulation.force('charge').strength(v);
    });
    bindSlider('phys-link', 'phys-link-val', v => {
      PHYSICS.link = v;
      simulation.force('link').distance(v);
    });
    bindSlider('phys-collide', 'phys-collide-val', v => {
      PHYSICS.collide = v;
      simulation.force('collide', d3.forceCollide(d => nodeRadius(d) + v));
    });

    const resetBtn = document.querySelector('#phys-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        Object.assign(PHYSICS, PHYSICS_DEFAULTS);
        const set = (id, valId, v) => {
          const i = document.querySelector('#' + id);
          const o = document.querySelector('#' + valId);
          if (i) i.value = v;
          if (o) o.textContent = v;
        };
        set('phys-charge',  'phys-charge-val',  PHYSICS.charge);
        set('phys-link',    'phys-link-val',    PHYSICS.link);
        set('phys-collide', 'phys-collide-val', PHYSICS.collide);
        simulation.force('charge').strength(PHYSICS.charge);
        simulation.force('link').distance(PHYSICS.link);
        simulation.force('collide', d3.forceCollide(d => nodeRadius(d) + PHYSICS.collide));
        simulation.alpha(0.6).restart();
      });
    }

    // Initial paint of the slider readouts so they match the active values.
    const setOut = (valId, v) => {
      const o = document.querySelector('#' + valId);
      if (o) o.textContent = v;
    };
    setOut('phys-charge-val',  PHYSICS.charge);
    setOut('phys-link-val',    PHYSICS.link);
    setOut('phys-collide-val', PHYSICS.collide);
  }

  /* Label-types popover. Lets the user pick which page types' labels
   * are eligible for display under the global label mode. Toggling a
   * type off makes its labels hover-only (the previous "quiet" rule).
   * State is persisted in localStorage and applied on next render. */
  function initLabelTypesPanel() {
    const trigger = document.querySelector('#label-types');
    const panel = document.querySelector('#label-types-panel');
    const stateEl = document.querySelector('#label-types-state');
    if (!trigger || !panel) return;

    function paintCount() {
      if (stateEl) stateEl.textContent =
        `${_labelTypeFilter.size}/${ALL_LABEL_TYPES.length}`;
    }

    function applyChange() {
      persistLabelTypes();
      paintCount();
      scheduleAutoRecompute();
      applyLabelOpacity();
    }

    panel.querySelectorAll('.label-types-row').forEach(row => {
      const t = row.dataset.type;
      const cb = row.querySelector('input[type=checkbox]');
      if (!cb) return;
      cb.checked = _labelTypeFilter.has(t);
      cb.addEventListener('change', () => {
        if (cb.checked) _labelTypeFilter.add(t);
        else _labelTypeFilter.delete(t);
        applyChange();
      });
    });

    trigger.addEventListener('click', (ev) => {
      ev.stopPropagation();
      panel.classList.toggle('hidden');
    });
    document.addEventListener('click', (ev) => {
      if (panel.classList.contains('hidden')) return;
      if (panel.contains(ev.target)) return;
      if (trigger.contains(ev.target)) return;
      panel.classList.add('hidden');
    });

    const resetBtn = document.querySelector('#label-types-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        _labelTypeFilter = new Set(LABEL_TYPE_DEFAULTS);
        panel.querySelectorAll('.label-types-row').forEach(row => {
          const t = row.dataset.type;
          const cb = row.querySelector('input[type=checkbox]');
          if (cb) cb.checked = _labelTypeFilter.has(t);
        });
        applyChange();
      });
    }

    paintCount();
  }

  /* tick — hot path during simulation settle and drag.
   *
   * Deliberately NOT calling applyLabelOpacity here. Opacity only
   * changes on focus / zoom / type-filter / auto-recompute events,
   * none of which fire during a layout settle, so re-running ~314
   * inline-style writes every frame is pure waste. The opacity is
   * kept correct via:
   *   - applyVisibility() on focus changes
   *   - the zoom handler
   *   - scheduleAutoRecompute() on resize and on simulation 'end'
   */
  function tick() {
    edgeSel
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
    textSel.attr('transform', d => `translate(${d.x},${d.y})`);
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

    // Source NODES stay visible — only their labels are gated to hover.
    // Same applies to edges that touch sources.
    nodeSel.attr('data-vis', d => {
      if (hasFocus) {
        if (d.id === focusId) return 'focus';
        if (focusSet.has(d.id)) return 'neighbour';
        return 'dim';
      }
      return null;
    });

    edgeSel.attr('data-vis', e => {
      const sId = (typeof e.source === 'object') ? e.source.id : e.source;
      const tId = (typeof e.target === 'object') ? e.target.id : e.target;
      if (hasFocus) {
        const touchesFocus = sId === focusId || tId === focusId;
        if (touchesFocus) return 'focus';
        return 'dim';
      }
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
      // Types the user has filtered out → label only on direct hover.
      // Defaults to concept/entity/note/todo; user toggles others via
      // the label-types popover.
      if (!isLabelTypeAllowed(d.type)) {
        return (hasFocus && d.id === focusId) ? 1 : 0;
      }

      // Focus + 1-hop neighbours always show full opacity.
      if (hasFocus && focusSet.has(d.id)) return 1;

      // Decide raw visibility (would-be-shown, ignoring dim).
      let visible;
      if (labelMode === 'on')       visible = true;
      else if (labelMode === 'off') visible = false;
      else                          visible = _autoVisibleIds.has(d.id);
      if (!visible) return 0;

      // Visible non-neighbour during a focus state → dim the label so
      // attention follows the highlighted neighbourhood. Labels live in
      // a separate layer now so they don't inherit the node-group dim
      // CSS — apply equivalent opacity here.
      if (hasFocus) return 0.18;
      return 1;
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

  /* Greedy bbox-collision label placement (Obsidian-style).
   *
   * Approach:
   *   - Zoom gate: at low zoom the rendered text is too small to read
   *     anyway, so emit no auto labels (matches Obsidian where labels
   *     only appear once you zoom in).
   *   - Candidate filter: node is non-source, on-screen radius and label
   *     height clear minimum thresholds.
   *   - Candidates sorted by degree (largest = most connections first),
   *     so the most-connected nodes win when they collide with neighbours.
   *   - Label widths come from getComputedTextLength() measured at
   *     element-creation time, scaled by current zoom k to land in
   *     screen pixels.
   */
  function recomputeAutoVisible() {
    if (!nodes) { _autoVisibleIds = new Set(); return; }
    const r = svg.node().getBoundingClientRect();
    const k = zoomTransform.k;

    // Soft zoom gate. SVG text inside the zoomed group renders at
    // LABEL_FONT_SVG * k screen pixels — gate when that's below
    // legibility threshold.
    const minLineHScreen = LABEL_FONT_SVG * k;
    if (minLineHScreen < MIN_LABEL_TEXT_HEIGHT_PX) {
      _autoVisibleIds = new Set();
      return;
    }

    const candidates = [];
    for (const d of nodes) {
      if (!isLabelTypeAllowed(d.type)) continue;  // user-filtered → never auto-label
      if (d.x == null) continue;
      const screenR = nodeRadius(d) * k;
      if (screenR < MIN_NODE_RADIUS_FOR_LABEL_PX) continue;
      const sx = d.x * k + zoomTransform.x + r.width / 2;
      const sy = d.y * k + zoomTransform.y + r.height / 2;
      if (sx < -100 || sy < -100 || sx > r.width + 100 || sy > r.height + 100) continue;
      const labelWScreen = (d._labelW || (d.title || d.id).length * 6.5) * k;
      const labelHScreen = (d._labelH || LABEL_LINE_HEIGHT_SVG) * k;
      const w = labelWScreen + LABEL_PADDING_PX * 2;
      const h = labelHScreen + LABEL_PADDING_PX * 2;
      candidates.push({
        id: d.id,
        degree: d.degree || 0,
        x: sx - w / 2,
        y: sy - screenR - 3 * k - h,
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
