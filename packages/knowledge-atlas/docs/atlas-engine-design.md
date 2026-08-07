# Atlas Engine — a scalable atlas layout engine and navigation system

**Status: design only — extraction deferred.** This documents the
standalone package the knowledge-atlas engine should become, and the
seam contract the current CE implementation keeps so the standalone
engine can be swapped in later without rework. Nothing here changes
current behaviour.

## 1. Why extract it

Everything hard-won in `packages/knowledge-atlas` is about **navigating
a space with far more points than a screen can legibly show** — none of
it is really about documents:

- bounded scenes (`SceneRequest`/`SceneData`): client cost scales with
  the budget, never the corpus;
- screen-density core capacity (one point per ~2850 px² — a phone
  shows ~50, a desktop ~360, a video wall thousands);
- cosmological shells: the remainder of the space wrapped around the
  focus neighbourhood in log-banded, corner-aware radial compression,
  granular → clustered → smeared;
- graded stability (in-view clicks never reorganise; the further out
  an interaction starts, the more the view is allowed to move);
- lens traversal: drag the boundary to stream the space through the
  view, with corpus-scaled speed limits, a motion abstraction instead
  of impossible per-point animation, and iOS-flick momentum;
- an explainable discovery horizon (recommendation shelves with
  mandatory reasons and honest omitted-counts);
- inquiry trails (history, branches, pins, compare);
- a chrome-free events-out/commands-in API and a swappable renderer.

Those properties are exactly what one wants when rendering **projections
of chemical space** (UMAP/TMAP of millions of compounds), **protein or
genome embedding atlases**, **astronomical catalogues** (the
visible-universe metaphor goes home), single-cell embeddings, or any
other structured point space. The engine should be a standalone
package; Curiosity Engine and Switch Bay become two consumers among
many.

## 2. The three view families

The extraction target supports three families on one navigation model.
What differs is only **how proximity, harvesting, and aggregation are
defined** — the scene pipeline, capacity rules, shells, traversal,
trails, and renderer are shared.

### 2.1 Graph view (what CE is today)

Points with first-class edges; no given coordinates. Proximity = graph
distance from the focus (BFS + scoring). Layout computed (force mesh in
the core). Aggregation by categorical type. This is the current
implementation, unchanged.

### 2.2 Scatter view (projections)

Points arrive **with coordinates** — a 2-D projection (UMAP, t-SNE,
PCA, TMAP xy, sky coordinates). Proximity = spatial distance in
projection space. The core shows the focus point's neighbourhood *at
its true projected geometry*, fit to the core zone at legible density;
the rest of the projection wraps it in shells, compressed radially in
the *projection's own* directions (a far-away cluster appears as a
smear in the true bearing of that cluster). Layout is a viewport fit,
not a solve — scatter scenes are cheap. Semantic zoom = density
re-selection (which points are visible at this capacity: top-weight
first), exactly the current capacity rule.

New requirements scatter adds:

- a **spatial index** for harvesting (kd-tree/ball-tree locally; HNSW
  or tile pyramid server-side) instead of BFS;
- **density-based aggregation** (grid or cluster-id rollups per shell
  band and bearing) instead of type-only grouping;
- **anchor field** is user-chosen: any categorical column (scaffold
  family, taxon, spectral class) plays the role CE doc-types play.

### 2.3 Tree view (TMAP and friends)

Points on a spanning tree (TMAP's MST over millions of compounds,
phylogenies, file trees). Proximity = tree distance. The core holds the
subtree around the focus laid out as the tree's own geometry; shells
hold the remaining branches, aggregated **by branch** (a collapsed
branch is an enterable aggregate whose count is its subtree size —
the transitionMap unfold animation already models exactly this).

## 3. The SpaceAdapter seam

One new abstraction carries all three families. It generalises what
`GraphIndex` + BFS harvesting + type aggregation do today:

```ts
interface SpaceAdapter {
  /** Nearest-first enumeration around a focus, budget-capped.
   *  graph: BFS · scatter: kNN via spatial index · tree: subtree walk */
  harvest(focusId: string, budget: number): Iterable<RankedPoint>;

  /** Total points (or estimate) — drives shellCount and flow caps. */
  size(): number;

  /** Group a set of far points into labelled, countable aggregates.
   *  graph: by type · scatter: by density cell / cluster id · tree: by branch */
  aggregate(points: Iterable<PointRef>, shell: number): AggregateSpec[];

  /** Angular anchor geography for a category (landmark stability). */
  anchorAngle(category: string): number;

  /** Optional: points that carry their own coordinates (scatter/tree
   *  geometry is data, not solve). */
  position?(id: string): { x: number; y: number } | null;
}
```

The scene pipeline (`builder.ts`, `shells.ts`, ranking, discovery,
budgets) consumes a `SpaceAdapter` instead of `GraphIndex` directly.
`GraphSpace` (wrapping today's `GraphIndex`) is the first
implementation and is behaviour-identical to the current code —
that's the extraction's regression gate.

Discovery classes generalise cleanly: *direct/adjacent* = near
neighbours, *bridge* = points connecting regions (graph articulation /
scatter points between clusters / tree branch joins), *contrast* =
same-provenance divergence (any shared-key field), *surprise* =
distant-but-related, *unexplored* = unvisited regions. Reasons stay
mandatory; the reason *text* comes from the adapter.

### Data model generalisation

`KnowledgeItem` already treats `type` as an opaque string and keeps CE
fields optional. The standalone rename is cosmetic:

```
AtlasPoint { id, category, label, weight?, coords?, meta }
```

`weight` generalises `degree` (drives node radius: compound potency,
star magnitude, subtree size); `coords` is present for scatter/tree
spaces. Edges become optional (`SpaceAdapter` families that have none
simply return no edge tiers).

## 4. Scale architecture

Unchanged from `performance.md` — it is already domain-neutral:

| corpus | where the pipeline runs |
|---|---|
| ≤ ~10⁵ points | in the page (local adapter, in-memory index) |
| 10⁶–10⁸ | server-side harvest + precomputed shell rollups (category × log₁₀-band); client receives ~30–60 KB scenes at ≤ 2.5/sec |

The traversal speed limits, commit rate cap, prefetch-along-drag, and
motion abstraction transfer verbatim; only the rollup keys change per
domain (doc types → scaffold families → sky regions).

## 5. Packaging

```
@atlas-engine/core          engine, scene pipeline, layouts, SpaceAdapter,
                            GraphSpace/ScatterSpace/TreeSpace, traversal
@atlas-engine/render-canvas Canvas 2D renderer (Frame contract)
@atlas-engine/react         <Atlas /> component
@atlas-engine/webgl         (later) instanced renderer for 10⁵-point scatter cores
```

Only `core` is mandatory; renderers and adapters attach at the same
seams that exist today (`SceneRenderer`, `AtlasDataSource`). WebGL
matters only for scatter view at high zoom-out capacity — graph/tree
cores stay comfortably inside Canvas 2D's envelope, and the Frame
contract was designed for renderer swap from day one (ADR-001).

CE then depends on the package and keeps only what is genuinely CE:
`datasources/curiosity.ts` (data.json contract, id normalisation,
title-prefix rules, palette), the wiki-view glue (`atlas.js`), and the
vendored bundle ritual in `RELEASE_CHECKLIST.md`.

## 6. The seam contract (what the CE implementation keeps true)

These invariants make the future swap mechanical. They hold today —
verified in this audit — and changes that would break one should be
treated as API changes:

1. **`src/core/` imports nothing from `datasources/`, `react/`, or the
   harness.** The engine sees only `AtlasDataSource`. ✅ (verified by
   grep; no violations)
2. **Adapters depend on interfaces, not concrete sources.** The React
   adapter previously sniffed `instanceof CuriosityDataSource` for the
   palette — fixed: `palette` is now an optional member of
   `AtlasDataSource` itself. ✅
3. **The renderer sees only `Frame`.** No scene semantics, no engine,
   no DOM beyond its canvas. ✅
4. **`KnowledgeItem.type` stays opaque** — no engine logic branches on
   CE type names (the palette maps them, nothing else). ✅
5. **All geometry constants live in `geometry.ts`/`shells.ts`**
   (squircle, capacity density, shell bands) — the pieces the
   standalone engine parameterises per domain. ✅
6. **Determinism**: no wall clock or `Math.random` anywhere in
   core/layout/traversal (seeded splitmix32 + hash anchors). ✅
7. **Chrome-free**: hosts consume events/snapshot/controller; nothing
   in the engine renders UI. ✅ (iteration-6)

The one deliberate CE-ism inside `core/` is the *default* palette
fallback in `renderer/theme.ts` — acceptable: it is data, not logic,
and becomes the graph-space default theme in the standalone package.

## 7. Swap plan (when extraction happens)

1. Create the package; move `core/`, `renderer/`, `interaction/`,
   `react/` verbatim; rename `KnowledgeItem` → `AtlasPoint` behind a
   type alias so CE code keeps compiling.
2. Introduce `SpaceAdapter`; wrap `GraphIndex` as `GraphSpace`; run the
   full existing unit + e2e suites against it — byte-identical scenes
   for a fixed seed is the acceptance bar.
3. CE's `datasources/` become `@atlas-engine`-consumer code inside this
   repo; the IIFE build and vendor ritual continue unchanged (the
   vendored file just builds from the dependency).
4. Add `ScatterSpace` with a kd-tree + a projection fixture (e.g. a
   100k-point synthetic UMAP); then `TreeSpace` with an MST fixture.
5. New renderers/adapters only after a second real consumer exists.

## 8. Explicitly deferred

- The extraction itself, `ScatterSpace`/`TreeSpace`, WebGL renderer,
  npm publishing, docs site.
- Nothing in current CE work waits on any of this; the contract in §6
  is the only ongoing obligation.
