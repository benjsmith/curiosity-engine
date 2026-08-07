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
