# Prototype results — mode comparison and recommendation

Session: 2026-08-06, automated evaluation (unit + Playwright + visual
inspection of captured screenshots). No human-subject data yet — the
repeatable task harness exists, but the "useful discoveries per unit
time" numbers below are structural proxies measured by the machine,
not user studies. Read them as "the mechanism demonstrably works",
not "users prefer X".

## What was built and compared

All prototype stages P0–P5 are implemented behind configuration
(`AtlasConfig.layout` + lens), on identical scene data:

| stage | state |
|---|---|
| P0 force baseline | ✅ d3-force, CE-viewer constants, parity look ([img/04-layout-force.png](img/04-layout-force.png)) |
| P1 focus atlas | ✅ default mode ([img/01-workspace-focus.png](img/01-workspace-focus.png)) |
| P2 explainable library effect | ✅ six horizon classes with reasons + omitted counts + lens mix |
| P3 hyperbolic | ✅ same scenes through Poincaré-style compression ([img/04-layout-hyperbolic.png](img/04-layout-hyperbolic.png)) |
| P4 semantic zoom + scale | ✅ band hysteresis ([img/03-zoomed-out.png](img/03-zoomed-out.png)); 1M-node procedural corpus ([img/05-scaled-1m.png](img/05-scaled-1m.png)) |
| P5 adaptive layout | ✅ topology router (tree→hyperbolic, chain→layered, bipartite→two-sided, mesh→force) |

## Measurements (workspace-small ≈ 400 nodes; viewport 1280×800; seed 42)

| metric | value | source |
|---|---|---|
| scene nodes / aggregates / edges | 53 / 11 / 67 | bounded by budget 60/12/120 |
| scene build | 84–99 ms first build, 22 ms warm | HUD telemetry |
| layout (focus/hyperbolic) | < 1 ms | HUD |
| layout (force, 350 ticks) | ~70 ms | HUD |
| scene build, dense-smallworld | 194 ms | HUD — MMR + bridge checks dominate; see performance.md |
| scene build, scaled 1M corpus | 25 ms | HUD — cost tracks budget, not corpus ✔ |
| e2e navigation loop (focus→back→forward) | green | 11 Playwright specs |
| unit suite | 44 green | budgets, quotas, trails, determinism |

## Visual findings (screenshot inspection)

**P1 focus atlas vs P0 force.** The focus layout delivers what the
force baseline structurally cannot: a *reserved, labelled* discovery
ring ("more like this / adjacent / bridges / contrasts / surprises /
unexplored", each with "+N more"), aggregates with counts as
first-class citizens, and stable type sectors (facts consistently
east, concepts/entities west across focus changes — verified by the
anchor-stability unit test and visible across screenshots). The force
view remains a good "shape of the neighbourhood" view but its
geography changes per relayout and it spends the whole budget on
individuals.

**Explainability works end-to-end.** Bridge candidates carry concrete
reasons ("Connects gene expression and homeostasis, which aren't
directly linked"); the planted contradiction pair is found by the
contrast class via shared-sources-without-link; the planted hidden
connection surfaces either as a derived co-cited neighbour or a
contrast candidate (test-locked). Right-click explanations return
shared-source evidence.

**P3 hyperbolic verdict — keep, not default.** With ~53-node scenes
the compression buys modestly more visible context (more labels fit
inside the disc) and the isometric re-centring feels continuous, but
rim labels start colliding (visible at the bottom of the hyperbolic
screenshot) and aggregate chips shrink into ambiguity. At current
scene budgets the Euclidean focus layout is more legible; hyperbolic
should win only when budgets grow (denser hop-2 shells). Recommend:
retained as a mode, **not** the default; revisit when budgets exceed
~120 nodes/scene.

**P4 scale.** The 1M-leaf corpus renders indistinguishably from the
small fixture (53 nodes, 25 ms) — the bounded-scene contract holds by
construction and by test. Sibling aggregates ("2 entities near …")
crowd their sector when many singleton groups form; a grouping
refinement (merge singleton sibling-aggregates) is the top polish
item.

**Known rough edges** (all recorded as future work, none blocking):
crowded hop-1 sector when one type dominates (concepts in the
workspace fixture); sibling-aggregate label overlap in the scaled
corpus; aggregate-anchored sectors can place two aggregates near each
other; no edge bundling curves yet (bundles render as straight
weighted lines).

### Iteration 6 (2026-08-09) — stable core, faithful squircles, tooltip polish, chrome-free API

- **Stability gradient**: refocusing on a node that was already inside
  the core no longer reorganises the graph zone — survivors keep their
  positions verbatim and only newcomers settle in around them (collide
  + weak anchors); a focus arriving from the rim/off-screen does the
  full re-layout, since that click asked for a different portion of
  the graph. Test-locked (< 20 px mean survivor movement).
- **Squircle fidelity**: the core boundary is now drawn (and hit-
  tested) as the same superellipse family as the rim — no circles
  left in hybrid modes.
- **Tooltip**: text-selection/callout gesture clash killed
  (`user-select`/`touch-callout` none on canvas, host and tooltip);
  haptic tick via `navigator.vibrate` where supported (web has no
  force-click API); **clicking the tooltip centres the community**;
  tapping anywhere else collapses it.
- **Chrome-free engine**: confirmed no panel chrome renders from the
  engine/adapters (the tooltip is viewer interaction, not chrome). The
  CE glue facade now exposes `subscribe(cb)` / `getSnapshot()` /
  `controller` so host chrome renders telemetry from the API.

### Iteration 7 (2026-08-10) — the cosmological atlas (P9)

Full redesign of the hybrid's scale model per feedback
([img/09-mega-4k-shells.png](img/09-mega-4k-shells.png),
[img/10-fullgraph-304.png](img/10-fullgraph-304.png)):

- **≤ 360 nodes = exactly the classic viewer.** Wikis at or under the
  core capacity render as ONE force graph with the original CE
  constants, filling the viewport — no boundary, no aggregation, no
  horizon ring. Refocusing keeps every surviving node put (< 5 px
  mean, test-locked).
- **Exponential shells beyond.** The corpus wraps the core in layers
  keyed to log₁₀ size bands (360–1k granular fringe + clusters,
  1k–10k clustered, 10k–100k smeared, beyond that per-type totals),
  built in `scene/shells.ts`. Fully-known wikis enumerate the
  beyond-harvest remainder into real, enterable "N × type beyond the
  horizon" clusters; cloud-scale corpora get estimated far smears.
- **Visible-universe compression.** Shells occupy exponentially
  thinner radial bands of the core→wall gap (50/30/15/5%); the gap is
  wider toward the screen corners, so compression is hardest along
  the viewport edges — and high-shell aggregates render as
  tangentially stretched, fading ellipses (the lensing smear).
- **Stability grades.** Core click: survivors pinned, newcomers
  settle. Shell-1 click: the lens *shifts* — the field translates so
  the clicked node slides into the core and the far side drifts out.
  Deep click: full re-layout. Adaptive-hybrid inherits all of it
  (columnar core only when ≤ 80 nodes and the topology fits).
- **Known gaps for next round**: dragging a shell node continuously to
  steer the lens (currently click-graded only); shell-1 discovery
  candidates should feed the direct/adjacent shelves when the core
  absorbs the whole neighbourhood; the first full-graph solve costs
  ~400 ms at 304 nodes (one-time; refocuses are ~0 ms).

### Iteration 8 (2026-08-07) — lens-drag traversal with momentum

Closes iteration 7's first gap: **dragging centerward from outside the
squircle now moves the graph through the viewing lens** (`src/
interaction/traversal.ts` + a motion overlay in the renderer).

- **Depth = gearing.** The start point of the drag fixes its
  docs-per-pixel ratio from the shell band it lands in: shell k holds
  ~10× shell k−1 in an exponentially thinner pixel band, so a drag
  from the wall moves orders of magnitude more corpus per pixel than
  one from just outside the boundary (`docsPerPixel`, test-locked to
  > 100× between the fringe and the outermost band, and corner-aware —
  the squircle gap is deeper diagonally, so edges gear harder than
  corners).
- **Speed limits.** Drag velocity × gearing gives a docs/second flow,
  hard-capped at `MAX_DOCS_PER_SECOND` (250k) — the "scrolling speed
  limit" that keeps the machine (and a cloud backend) ahead of the
  gesture no matter how violently one flicks from the extremes. Real
  scene commits are separately rate-limited to one per 400 ms
  (`COMMIT_INTERVAL_MS`, ≤ 2.5 scene builds/sec), each stepping the
  lens one commit into the drag sector.
- **Motion abstraction.** Between commits the renderer does NOT try to
  animate hundreds of thousands of subgraphs: above a small flow
  threshold it dims the scene and overlays deterministic directional
  streaks along the traversal axis plus a docs odometer ("≈ 45k docs
  streaming past"). Streak count/length/opacity scale with
  log-normalised flow so a 400-doc wiki and a 100M corpus both read.
- **Momentum.** Release keeps the flow decaying under exponential
  friction (`FRICTION_PER_MS`, half-life ≈ 200 ms — the iOS flick
  feel); commits continue at the limited rate until flow drops below
  the stop threshold and the final scene resolves. Taps (no movement)
  cancel cleanly and fall through to normal click handling; pinches
  and pointer-cancel abort the traversal.
- Inside-core drags stay camera pans; full-graph (≤ 360) scenes have
  no boundary and are untouched. Both adapters (React + IIFE) share
  the same wiring. 9 new physics unit tests (61 total).
- **Compute at cloud scale**: see the new "Traversal at cloud scale"
  section in [performance.md](performance.md) for the 100M-doc
  switchbay-cloud analysis this iteration was sized against.

## Next iteration (P8) — host integration spec

Agreed direction for the next session:

1. **Curiosity Engine wiki-view**: atlas replaces the D3 force graph
   as the graph pane (flag default flips to atlas; classic kept as
   fallback flag), all other viewer functionality unchanged (sidebar,
   modal, subgraph navigator, editing, hash routing). Add a
   **collapsible telemetry bar** rendered by wiki-view chrome (not the
   engine) fed from `AtlasViewer.subscribe`: docked right on
   landscape/large screens, bottom on portrait/phones; shows scene
   stats + discovery shelves + trail, collapsible to a slim toggle.
2. **Switch Bay power mode**: telemetry panel mounts in the rail slot
   with a toggle button switching between the chat rail and the atlas
   telemetry panel (host-side component consuming `AtlasEvent`s from
   the React adapter's `onEvent`).
3. **Switch Bay Zen mode**: telemetry becomes a registered tab surface
   placeable on the right-hand side.

The engine API is ready for all three (events + snapshot + controller);
the work is host chrome only.

## Definition-of-done check (PLAN §19)

- CE swaps its graph canvas via a small adapter: ✅ flag-gated
  `atlas.js` glue + vendored IIFE; e2e-proven against a real payload.
- Switchbay mounts the React component without a new shell: ✅
  component + example adapter (`examples/switchbay/`); actual wiring
  is a Switchbay-repo change.
- Local and cloud-style sources share one API: ✅ fixture, CE,
  scaled, remote-sim all through `AtlasDataSource`.
- Scenes bounded independent of graph size: ✅ tested at 1M.
- Focus/history/horizon/explanations: ✅ e2e.
- Euclidean vs hyperbolic on identical data: ✅ harness toggle.
- Discovery beyond a file browser: ✅ planted contradiction + hidden
  connection surfaced with reasons (machine-checked).
- Communities never the default geography: ✅ type/source landmarks
  only; `showCommunities` lens flag exists and defaults off.
- Clean separation engine/renderer/adapters/harness: ✅ import rules.

## Iteration 2 — first human feedback (2026-08-08)

The maintainer tested the harness (desktop + phone artifact). Verdicts
and the changes that followed, all landed behind `layout: "hybrid"`:

- **Doc-type spatial organisation "works really well"** — the
  prior-finding bet (explicit types over algorithmic communities as
  geography) is confirmed by first human use.
- **Hyperbolic felt most natural; adaptive good** — both retained, as
  requested.
- **Semantic zoom too step-like** → in the new hybrid mode the wheel
  no longer drives semantic bands directly: it is geometric zoom over
  the central graph (classic-viewer feel) and a *lens* over the rim.
  Band stepping remains available via the toolbar buttons.
- **Requested hybrid mode** → `hybrid` (P6,
  [img/04-layout-hybrid.png](img/04-layout-hybrid.png)): a
  force-directed core holding ~67% of the scene's plain nodes
  (`CORE_SHARE`), transitioning at a visible boundary into the
  hyperbolic doc-type rim (aggregates + discovery horizon). Now the
  default in the harness and the CE glue.
- **Lens interaction**: wheel/pinch inside the core = zoom the graph;
  over the rim = pull that angular sector inward (positions
  interpolate toward the core), committing at full pull by focusing
  the sector's dominant item. Implemented in
  `src/interaction/lens.ts`, shared by both adapters.
- **Rim aggregates now selectable**: clicking (or lens-committing) a
  grouping bubble focuses its top-ranked member, so the region unfolds
  into the graph zone via the existing transition-map animation.

Open questions for the next round: should the lens pull be continuous
with the wheel delta (currently stepped at 0.34/notch with visual
feedback between steps), and should the core's share be
lens-adjustable?

### Iteration 3 (same day)

Further feedback on hybrid: broaden the graph zone with more nodes in
it, and use the screen corners — with a **gridlike** rim arrangement.
Changes ([img/04-layout-hybrid.png](img/04-layout-hybrid.png)):

- core radius 0.26 → 0.32 of the short viewport side; `CORE_SHARE`
  0.67 → 0.82; default node budget 60 → 72. The uniform clamp that
  shrank the whole force cloud was replaced with a per-node radial
  clamp, so the interior keeps natural spacing and actually fills the
  broadened zone.
- the rim boundary is now a superellipse ("squircle",
  `core/geometry.ts:rimRadiusAt`) inscribed in the viewport instead of
  the inscribed circle, and rim items are laid out as **gridlike
  shelves per type sector** — ordered rows anchored against the outer
  wall, stacking inward, highest-relevance row innermost — so the
  corner areas fill with aligned rows.
- labels near the right wall flip to the node's left instead of
  clipping off-canvas.

### Iteration 4 (same day) — adaptive hybrid (P7)

The maintainer liked the gridlike columnar view the adaptive P5 mode
produced under some topologies and asked for it inside the hybrid
frame. New `layout: "adaptive-hybrid"`
([img/07-adaptive-hybrid-tree.png](img/07-adaptive-hybrid-tree.png)):
identical core/rim partition, squircle shelves and lens as hybrid, but
the CORE's internal arrangement adapts — when the core subgraph
classifies as star/chain/tree/bipartite, it renders as typed columns
(focus left, hop-1 fan, one gridlike hop-2+ column grouped by type);
dense meshes keep the force cloud (verified byte-identical to plain
hybrid on the workspace fixture). Both hybrids share
`partitionCore()`, so lens and aggregate-unfold behave identically.

### Iteration 5 (2026-08-09) — network core, squarer rim, self-explaining bubbles

Feedback with phone screenshots (dense-smallworld, hybrid + adaptive-
hybrid) and the classic viewer-graph reference:

- **Squircle exponent raised** (3.2 → 4.6): the rim boundary now reads
  as a rounded rectangle hugging the viewport, not an ellipse.
- **More nodes in the graph zone**: default budget 72 → 90 nodes,
  CORE_SHARE 0.82 → 0.9.
- **Network look, not radial**: the focus is no longer pinned at the
  core centre (its accent ring identifies it); force distances are
  sized from the zone and node count so the mesh fills the core at
  natural spacing; a collide-only relaxation pass de-overlaps nodes in
  screen space; fit uses the 92nd-percentile radius. Known remaining
  gap vs the reference: the mesh still clusters somewhat rather than
  filling the zone wall-to-wall — candidate next knob is a weak radial
  outward force.
- **Aggregate bubbles explain themselves**: RenderAggregate now
  carries `memberTitles`; hovering a bubble (or touch-and-hold ~500 ms)
  shows a tooltip with the group label, five member titles, the
  residual count, and what a click does. Clicking still focuses the
  top member — the confusion ("a similar bubble with a similar number
  is still there") is expected behaviour, now annotated: the new scene
  re-aggregates the *new* fold.
- **Double-click reliability**: the first click of a double-click
  refocuses and shifts the layout under the cursor, so dblclick/
  double-tap now falls back to opening the just-clicked node.

## Recommendation for the next production iteration

1. **Adopt the P1 focus atlas as the production default**, force kept
   as a comparison/legacy mode, hyperbolic behind a setting.
2. **Land the CE payload enrichment** (`--atlas-edges`: Cites +
   ProvisionalLink export) so contrast/bridge classes run on real
   curated signals instead of the adapter's derived co-citations.
3. **Human evaluation next**: the task harness is instrumented;
   run the retrieval/orientation/discovery/ideation task set from
   PLAN §16 with real users on a real workspace before deeper
   investment in geometry.
4. Polish order: singleton-aggregate merging → sector-crowding relief
   (adaptive fan width) → bundled edge curves → branch-compare UI.
5. Keep the Canvas renderer; measurements show scene build, not
   drawing, is the cost centre (optimise MMR/bridge checks if dense
   graphs become common).
