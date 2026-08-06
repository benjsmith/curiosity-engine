# Knowledge Atlas Viewer — Implementation Plan

Status: **planning complete — implementation not started**
Branch: `claude/knowledge-atlas-viewer-plan-o2rff9`
Written: 2026-08-06, after inspection of `benjsmith/curiosity-engine` (this repo)
and `benjsmith/switchbay` (read-only clone).

This document is the single source of truth for the coding session(s) that
follow. A fresh agent should be able to implement the module from this file
plus the two repos, without re-deriving any decision recorded here.

---

## 0. How to use this document (handoff)

1. Work on this branch (or a fresh branch off it, keeping this file).
2. Everything lives under `packages/knowledge-atlas/` in this repo. Nothing
   outside that directory changes except the two integration touch-points in
   §14.4 (additive, behind a flag) and a `RELEASE_CHECKLIST.md` row.
3. Implement in prototype order (§17): **P0 → P1 are the required core**;
   P2–P5 continue only while each stays a working, testable comparison.
4. Decisions marked **AD-n** (§3) are made. Do not re-litigate them during
   implementation; if one proves wrong, record why in the ADR
   (`docs/adr-001-renderer-and-geometry.md`) and change it there.
5. Facts about the host repos in §2 were verified against source on
   2026-08-06 with file/line references. Trust them, but if code moved,
   the cited file wins.

---

## 1. Goal and non-goals

Build a **self-contained, embeddable knowledge-graph viewer engine** —
framework-independent TypeScript core + Canvas renderer + thin React
adapter + Curiosity Engine data adapter — that can replace the current
D3 force graph in Curiosity Engine's wiki viewer and Switchbay's Graph
tab, and later serve Curiosity Cloud / Switchbay Cloud through the same
`AtlasDataSource` interface.

Product goal (from the brief): let users comprehend and navigate
substantially more structured knowledge per screen than folders, file
browsers, search results, or ordinary graph viewers. Specifically improve:

1. discovery of relevant content the user did not know to search for;
2. discovery of non-obvious, useful connections;
3. development, branching and recovery of trains of thought;
4. accurate orientation and a justified feeling of mastery;
5. explainable serendipity (the physical-library effect, improved on).

**Non-goals.** No application shell, document browser, chat interface,
sidebars, authentication, cloud services, or dashboards — the hosts
provide those. No 3D. No backend binding (kuzu, Neptune, Memgraph,
Postgres are all behind `AtlasDataSource`). The module must be
installable/vendorable without the experiment harness.

**Critical prior finding (binding constraint).** Louvain-community
geography failed users in earlier treemap experiments: unstable mental
map, hard travel, no sense of place. Therefore: explicit **types and
source metadata are the visible landmarks**; communities are used only
internally (aggregation, layout seeding, edge bundling, retrieval) and
surface only when locally interpretable or explicitly requested.
Hyperbolic geometry is a space-allocation candidate (P3), not the
product metaphor.

---

## 2. Integration notes — what inspection found

### 2.1 Curiosity Engine (this repo)

**Repo shape.** A Claude Code skill: Python scripts under
`skills/curiosity-engine/scripts/`, a vanilla-JS static viewer under
`skills/curiosity-engine/template/wiki-view/`. **Zero JS/TS tooling** —
no package.json anywhere; D3 7.9.0 and Fuse 7.0.0 are vendored with
sha256 pins in `RELEASE_CHECKLIST.md`. Tests are stdlib `unittest` in
`tests/` (`python3 -m unittest discover tests`). Explicit design intent
(docs/viewers.md): *"No Node.js dependency — pure Python build + vanilla
JS frontend"* — that applies to the **runtime**, not to development; see
AD-1/AD-4.

**Viewer payload (`data.json`).** Built by
`scripts/wiki_render.py` (`_build_graph`, ~line 380; assembly ~line 510)
into `~/.cache/curiosity-engine/wiki-view/<workspace>/data.json`:

```ts
type CEData = {
  workspace: string;
  generated_at: string;                       // ISO8601 UTC — only nondeterministic field
  palette: Record<string, string>;            // type -> hex, singular+plural aliases
  nodes: { id: string; path: string; type: string; title: string; degree: number }[];
  edges: { source: string; target: string; type: "wikilink" | "depicts" }[];
  pages: Record<string, {
    id: string; title: string; type: string; path: string;
    properties: Record<string, unknown>;      // open-ended frontmatter minus title/type
    body_html: string;                        // pre-rendered HTML
  }>;
  scan_staleness: object | null;
};
```

Sharp edges the adapter must handle (all verified):

- `id` = wiki-relative path **without** `.md`; `path` keeps `.md`.
  (`graph.py retrieve` and Switchbay's agent tools use `.md`-suffixed
  ids — the adapter normalises to the suffix-less form.)
- `nodes` ⊆ `pages` when kuzu is stale: a page on disk but not in kuzu
  gets a `pages` entry and **no node**. Treat `pages` as the item store,
  `nodes` as the graph membership; synthesise degree-0 nodes for
  page-only entries so nothing becomes unreachable.
- Only `wikilink` and `depicts` edges are exported. **`Cites`
  (page→vault source) and `ProvisionalLink` (co-citation / embedding,
  with `origin` + `score`) exist in kuzu
  (`scripts/graph.py:_init_schema`, ~line 128) but never reach the
  viewer.** These are exactly the relations the discovery horizon needs
  (§7.4); P1 ships without them, a small additive export enrichment
  lands in P2 (§14.3).
- Types: render-time normalisation to `KNOWN_TYPES` or `"unclassified"`
  (`wiki_render.py` ~line 83). Known live inconsistency:
  `summary-table` / `extracted-table` frontmatter values are not in
  `KNOWN_TYPES`, so those pages arrive `"unclassified"`; the current
  frontend (and Switchbay's `cebridge._backfill_unclassified_types`)
  re-canonicalise from the `[tbl]`/`[tab]` title prefix. The adapter
  must apply the same canonicalisation map (singular/plural collapse +
  prefix backfill) — see §14.2.
- `edges` have no backlink index (frontends build adjacency
  client-side), and D3's `forceLink` **mutates** edge `source`/`target`
  from strings into node refs in place. The engine deep-copies all
  ingested data at the adapter boundary; nothing downstream may assume
  strings-or-objects.
- Titles carry a `[con]`/`[src]`/… prefix (`naming.py:TYPE_PREFIX`);
  both current frontends split it with `/^(\[[^\]]+\])\s+(.+)$/` for
  two-tone labels. The adapter extracts it into
  `KnowledgeItem.meta.titlePrefix` and strips it from the display title.
- Citations in `body_html` are unstructured `<span class="cite">`; the
  only structured citation surface is frontmatter `sources:` (an array
  of vault paths) inside `properties` — that is the P1 provenance signal.

**Current viewer behaviour to reproduce as the P0 baseline** (all in
`template/wiki-view/static/`, ~1,700 LOC vanilla JS): D3
force graph (`PHYSICS_DEFAULTS = {charge:-420, link:110, collide:10}`,
350 pre-warm ticks, zoom extent [0.15, 4]); node radius
`4 + sqrt(degree+1)*1.6`; colour from `data.palette`; visibility states
`data-vis ∈ {focus, neighbour, dim, hidden}`; label modes auto/on/off
with greedy bbox collision placement; hash routing
(`#page=<id>`) as the single navigation bus; modal with a 1-hop
subgraph navigator (`subgraph.js` — centre pinned, ~280 static ticks,
string-built SVG). Theming: CSS custom properties on
`:root[data-theme="dark"|"light"]`; type colours as `--type-*` vars
mirroring the payload palette (runtime palette wins at fill time).

### 2.2 Switchbay

**Stack.** React 18.3 + TS 5.5 **strict** (`noUnusedLocals`,
`noUnusedParameters`), Vite 5, pnpm; no state library, no eslint/prettier
(gate is `tsc -b`); no unit-test tooling on the JS side, Playwright
1.60 for e2e (dev servers must already be running; Chromium forced onto
**SwiftShader** software GL). No path aliases; hand-synced types; heavy
narrative header comments. House rule (CLAUDE.md): no heavy UI
frameworks; curiosity-engine is a **read-only upstream dependency**
(shell out / read its outputs, never re-implement).

**The graph surface today is a vendored fork of this repo's
`wiki-view`** (`frontend/src/widgets/graph/` — `static/graph.js` 944
LOC + IIFE siblings, wrapped by `GraphTab.tsx`). Its `types.ts`
`GraphData` is byte-compatible with `CEData` above (daemon endpoint
`GET /api/graph/data`, served stale-while-revalidate from
`cebridge.py`, which shells out to this repo's `viewer.sh build`).
Switchbay post-processes the payload (`resync_types_from_disk`, prefix
backfill, palette override, deck-node injection) — evidence that type
canonicalisation belongs **inside the shared adapter**, once.

**Mount seam.** Tabs register via
`center/tabRegistry.ts:registerTabKind(kind, component)`; the graph tab
adapter receives `{ data: GraphData | null; error: string | null;
suppressDocModal?; showAddFile? }` inside a `position:relative`
100%×100% host, keyed by workspace in Zen mode. Replacing the graph =
one `registerTabKind("graph", …)` swap in `center/builtinTabs.tsx`,
**but** the fork also owns `window.Graph/Modal/Sidebar/Subgraph/Edit`
globals called imperatively from ~8 sites in `App.tsx`, plus hash
routing and split mode (`splitEnter/splitExit`,
`POST /api/workspaces/split`). The React adapter therefore exposes an
imperative `AtlasController` handle (§5) sufficient to shim those call
sites; split-mode multi-select is supported generically as engine
selection + events (§5), with Switchbay keeping its own split chrome.
Packs (pre-built ESM served by the daemon, dynamic-imported and
registered) are an alternative distribution path requiring zero
build-system change in Switchbay.

**Duplication argument.** Switchbay contains **four** hand-copied
force-layout implementations (graph.js, subgraph.js, MiniGraph.tsx,
CurationReplay.tsx) sharing constants by copy-paste. The atlas engine
should be able to collapse all four eventually (the 1-hop subgraph is
just a scene with a tiny budget).

### 2.3 Consequences baked into the design

1. One shared payload contract already exists across both hosts
   (`CEData` ≡ `GraphData`) — the CE adapter written here serves both.
2. Runtime bundles must be self-contained static files (CE vendoring
   ritual; Switchbay packs) → build to dependency-free ESM + IIFE.
3. Renderer must run acceptably under SwiftShader (e2e) → Canvas 2D
   (AD-2) sidesteps GPU variance entirely.
4. TS strict everywhere; React is a **peer** dependency of the adapter
   only — the core must not import it.
5. Scale today is ~500 nodes / ~2,000 edges; the bounded-scene model is
   what makes tens of millions reachable later, not renderer heroics.

---

## 3. Architectural decisions (pre-ADRs)

**AD-1 — Package location & toolchain.** New isolated directory
`packages/knowledge-atlas/` in this repo, self-contained: own
`package.json` (name `@curiosity/knowledge-atlas`, private for now),
pnpm, Vite 5, TypeScript 5.5 strict (mirror Switchbay's tsconfig
flags), Vitest for unit tests, Playwright for harness e2e. This is the
repo's first JS tooling — acceptable because it is entirely contained
in `packages/` and the **shipped artefact is a built, dependency-free
bundle** vendored like d3.min.js (AD-4), preserving the "no Node at
runtime" property. Root `.gitignore` gains `packages/*/node_modules/`
and `packages/*/dist/`.

**AD-2 — Renderer: Canvas 2D**, wrapped behind a `SceneRenderer`
interface so a WebGL implementation can be added later without touching
the engine. Rationale: scene budgets cap draw items at ~10²–10³
(§6), well within Canvas 2D at 60fps; simplest tech that meets targets
(brief's requirement); no GPU-driver variance (SwiftShader e2e);
crisp text labels (the hard part in WebGL) are free; zero dependencies.
DPR-aware (`devicePixelRatio` scaling), offscreen label measurement
cache, dirty-flag redraw (only on state/animation change — no
continuous rAF when idle). Hit testing lives in the **core** (geometry-
independent: items carry resolved positions + radii; point/box queries
over a uniform grid), not in the renderer. This is ADR-001's content;
write the ADR file during P0.

**AD-3 — Layout dependencies.** `d3-force` + `d3-quadtree` (npm, tree-
shaken into the bundle, ~25 KB) for the **baseline** layout adapter
only — d3-force ≥ v2 uses a seeded LCG, so it is deterministic for
identical input order, satisfying the seeded-determinism constraint.
The focus-centred Euclidean layout (P1), hyperbolic layout (P3), and
layered/bipartite variants (P5) are hand-written (they are
constraint/projection math, not simulations). No other runtime
dependencies in the core. React adapter: `react` peer ≥18.
Justify any further dependency by measured value (brief constraint).

**AD-4 — Distribution.**
- *Build outputs:* `dist/knowledge-atlas.mjs` (ESM, core+renderer+CE
  adapter), `dist/knowledge-atlas-react.mjs` (adapter, React external),
  `dist/knowledge-atlas.iife.js` (`window.KnowledgeAtlas`, for the
  script-tag wiki-view), `dist/*.d.ts`, `dist/knowledge-atlas.css`
  (theme token defaults only).
- *Curiosity Engine:* vendor the IIFE into
  `template/wiki-view/static/vendor/knowledge-atlas.js` + version/sha256
  row in `RELEASE_CHECKLIST.md`, with a small glue file
  `static/atlas.js` (§14.4). Additive and flag-gated; the existing
  graph.js stays until the comparison verdict (§17 P-final).
- *Switchbay:* consumes the ESM + React adapter — as an npm/vendored
  package in `frontend/`, or zero-build as a **pack**. An integration
  example ships in `examples/switchbay/` here (§13.3); actual Switchbay
  wiring is a separate change in that repo, out of scope for this one.

**AD-5 — One id/type normalisation, in the adapter.** Canonical item id
= CE page id (path sans `.md`). Canonical types = the singular set
`project, analysis, concept, entity, evidence, fact, figure, table,
source, note, todo-list, unclassified` via one exported
`canonicalType()` (plural collapse + `summary-table`/`extracted-table`
→ `table` + title-prefix backfill). Engine core has **no fixed type
assumptions** — types are opaque strings with metadata (colour, label,
landmark priority) supplied by the scene/theme.

**AD-6 — No network and no host-global state in engine or renderer.**
All data through `AtlasDataSource` (which may fetch — that's the
host's/adapter's business). No `window.location`, no `localStorage`, no
timers except rAF driven by the mounted adapter. Events out, commands
in. Hash routing and persistence are host shims.

**AD-7 — Determinism.** Every stochastic step (layout seeds, jitter,
fixture generation, surprise sampling) takes an explicit seed from
`AtlasConfig.seed` (default 42) through one splitmix/LCG utility.
Identical (data, config, command sequence) ⇒ identical scenes and
positions. `generated_at`-style wall-clock never enters layout or
ranking.

---

## 4. Package layout

```
packages/knowledge-atlas/
  PLAN.md                       ← this file
  package.json  tsconfig.json  vite.config.ts  vitest.config.ts
  playwright.config.ts
  src/
    core/                       # framework-independent engine (no DOM writes)
      types.ts                  # public types: KnowledgeItem, SceneRequest, SceneData, events…
      engine.ts                 # AtlasEngine: state machine, command surface (AtlasController impl)
      scene/
        builder.ts              # SceneRequest -> SceneData orchestration (via data source)
        ranking.ts              # neighbourhood scoring (§7.2)
        aggregate.ts            # secondary-context aggregation (§7.3)
        discovery.ts            # horizon candidate selection + explanations (§7.4)
        landmarks.ts            # type/source landmark extraction (§7.5)
      layout/
        types.ts                # LayoutAdapter interface (§8)
        force.ts                # baseline d3-force adapter (P0)
        focus.ts                # focus-centred Euclidean rings (P1)
        hyperbolic.ts           # Poincaré-disc focus+context (P3)
        adaptive.ts             # topology-routed composite (P5)
        anchors.ts              # shared: stable angular assignment, continuity (§8.5)
      zoom.ts                   # semantic-scale bands + hysteresis (§10)
      transitions.ts            # object-correspondence animation plans (§10.3)
      trails.ts                 # inquiry-trail tree, serialisation (§12)
      hittest.ts                # grid-based point/box hit testing, selection state
      telemetry.ts              # instrumented counters/events (§16)
      random.ts  lru.ts  abort.ts   # utilities (seeded RNG, caches, request tokens)
    renderer/
      types.ts                  # SceneRenderer interface (frame-in, events-out)
      canvas.ts                 # Canvas 2D implementation (AD-2)
      labels.ts                 # measurement cache, placement, DPR handling
      theme.ts                  # AtlasTheme tokens -> resolved paint values
    datasources/
      types.ts                  # AtlasDataSource (re-export), shared helpers
      curiosity.ts              # CE data.json adapter (§14) — static object or URL
      fixture.ts                # deterministic in-memory source over fixture graphs
      scaled.ts                 # simulated million-node procedural source (§15.5)
      remote.ts                 # thin cloud-style source: paginated/streamed getScene (P4)
    react/
      KnowledgeAtlas.tsx        # <KnowledgeAtlas {...KnowledgeAtlasProps}/>
      useAtlas.ts               # hook: controller ref + state subscription
    index.ts  react.ts  iife.ts # entry points per bundle
  fixtures/                     # generated JSON + the generator (§15)
    generate.ts  workspace-small.json  ontology-tree.json  dense-smallworld.json
    mixed-multiscale.json       # (scaled corpus is procedural — no JSON)
  harness/                      # experiment harness (excluded from package files)
    index.html  main.tsx  panels/…   # fixture picker, mode switcher, telemetry HUD, task runner
  examples/
    curiosity-engine/README.md + atlas-glue.js     # §14.4
    switchbay/README.md + AtlasTab.tsx             # §13.3
  tests/                        # vitest unit suites
  e2e/                          # playwright specs against the harness
  docs/
    adr-001-renderer-and-geometry.md
    results.md                  # mode-comparison results (filled through P1–P5)
    performance.md  extension-points.md
```

Dependency rule (enforce with an eslint-free lint script or dependency-
cruiser later; by review for now): `core/` imports nothing from
`renderer/`, `react/`, `datasources/`; `renderer/` imports `core/types`
only; `react/` imports core + renderer; `datasources/` import core
types only.

---

## 5. Public API

As specified in the brief, concretised. All types live in
`src/core/types.ts` and are exported from the package root.

```ts
// ── data ────────────────────────────────────────────────────────────
export interface KnowledgeItem {
  id: string;
  type: string;                       // canonical, but engine treats as opaque
  title: string;
  meta: {
    titlePrefix?: string;             // "[con]" etc., split from title
    path?: string;                    // host-side path (CE: with .md)
    sources?: string[];               // provenance (CE frontmatter sources:)
    created?: string; updated?: string;
    degree?: number;
    properties?: Record<string, unknown>;   // passthrough
  };
}

export type AtlasLens = {
  id: string;                         // "default" | host-defined
  relationWeights?: Record<string, number>; // per edge-type multiplier
  typeWeights?: Record<string, number>;     // per node-type multiplier
  discoveryMix?: Partial<Record<DiscoveryClass, number>>; // §7.4 balance
};

export type SceneRequest = {
  focusId?: string;
  lens: AtlasLens;
  relationTypes?: string[];
  viewport: { width: number; height: number };
  semanticScale: number;              // continuous; bands in §10
  history?: string[];                 // recent focus ids (repeat-exposure penalty)
  budget: SceneBudget;
};
export type SceneBudget = {
  maxNodes: number; maxAggregates: number; maxEdges: number;
  maxBundles: number; maxLabels: number;
};

export type SceneData = {
  focus?: KnowledgeItem;
  nodes: RenderNode[];                // individually visible items
  aggregates: RenderAggregate[];      // grouped secondary context
  edges: RenderEdge[];
  bundles: RenderBundle[];            // typed aggregate flows
  horizon: HorizonGroup[];            // §7.4
  landmarks: Landmark[];              // §7.5
  transitionMap?: Record<string, string[]>;  // aggregate id -> member ids (unfold continuity)
  stats?: { totalNodes?: number; omitted?: OmittedSummary[] };  // "12 more contrasting sources"
};

export type RenderNode = {
  id: string; item: KnowledgeItem;
  role: "focus" | "neighbour" | "bridge" | "pinned" | "context";
  score: number;                      // ranking score for LOD ordering
  ring?: number;                      // hop distance / layout band hint
};
export type RenderAggregate = {
  id: string;                         // stable: "agg:<type>:<anchor>" (§7.3)
  label: string; type: string;        // dominant member type
  count: number; memberIds: string[]; // capped sample; full count in `count`
  residual?: number;                  // members beyond memberIds
};
export type RenderEdge = {
  source: string; target: string; type: string;
  direction?: "forward" | "back" | "none";
  confidence?: number;                // 0..1 (provisional edges)
  priority: number;                   // §11 tiers
};
export type RenderBundle = {
  id: string; source: string; target: string;  // node-or-aggregate ids
  type: string; count: number;
};
export type DiscoveryClass =
  | "direct" | "adjacent" | "bridge" | "contrast" | "surprise" | "unexplored";
export type HorizonGroup = {
  cls: DiscoveryClass;
  candidates: DiscoveryCandidate[];
  omittedCount: number;               // explicit "N more …"
};
export type DiscoveryCandidate = {
  id: string; item: KnowledgeItem;
  score: number;
  reason: ExplanationSummary;         // ALWAYS present — why it appears
};
export type ExplanationSummary = {
  kind: DiscoveryClass | "edge" | "path";
  text: string;                       // one-line, host-renderable
  viaIds?: string[];                  // supporting items (bridge path, shared source…)
};
export type Landmark = {
  id: string; kind: "type" | "source" | "pinned" | "community";
  label: string; type?: string;
  anchor: { angle: number };          // stable angular home (§8.5)
};
export type OmittedSummary = { cls: DiscoveryClass | "edges" | "nodes"; count: number; label: string };

// ── data source ─────────────────────────────────────────────────────
export interface AtlasDataSource {
  getScene(request: SceneRequest): Promise<SceneData>;   // MUST honour request.budget
  getItem(id: string): Promise<KnowledgeItem | null>;
  getExplanation(request: ExplanationRequest): Promise<Explanation>;
}
export type ExplanationRequest =
  | { kind: "candidate"; id: string; focusId: string; cls: DiscoveryClass }
  | { kind: "edge"; source: string; target: string; type: string }
  | { kind: "aggregate"; id: string };
export type Explanation = { summary: ExplanationSummary; evidence?: ExplanationSummary[] };

// ── engine control & events ─────────────────────────────────────────
export interface AtlasController {
  focus(id: string): void;
  back(): void; forward(): void;
  zoomTo(level: number): void;                  // semantic scale
  setLens(lens: AtlasLens): void;
  pin(id: string): void; unpin(id: string): void;
  compare(ids: string[]): void;                  // trail-branch compare (§12)
  branch(fromStepId?: string): string;           // returns new branch id
  select(ids: string[], mode?: "replace" | "add" | "toggle"): void;
  getState(): AtlasState;
  serializeTrail(): string; restoreTrail(json: string): void;
  resize(w: number, h: number): void;
  destroy(): void;
}
export type AtlasState = {
  focusId?: string; semanticScale: number; lens: AtlasLens;
  pinned: string[]; selection: string[];
  trail: TrailState;                             // §12
  scene?: { nodeCount: number; aggregateCount: number; edgeCount: number };
  status: "idle" | "loading" | "error"; error?: string;
};

export type AtlasEvent =
  | { kind: "focus-changed"; id: string; origin: "user" | "system" | "history" }
  | { kind: "scene-ready"; stats: SceneStats }               // incl. build latency
  | { kind: "item-open-requested"; id: string }              // host opens its doc panel
  | { kind: "selection-changed"; ids: string[] }
  | { kind: "hover"; id: string | null }
  | { kind: "explanation-requested"; request: ExplanationRequest }
  | { kind: "discovery-engaged"; id: string; cls: DiscoveryClass }
  | { kind: "trail-changed"; trail: TrailState }
  | { kind: "zoom-changed"; semanticScale: number; band: number }
  | { kind: "telemetry"; sample: TelemetrySample };          // §16

export type KnowledgeAtlasProps = {
  dataSource: AtlasDataSource;
  initialFocus?: string;
  config?: AtlasConfig;
  theme?: AtlasTheme;
  onEvent?: (event: AtlasEvent) => void;
  onOpenItem?: (id: string) => void;
  controllerRef?: React.Ref<AtlasController>;   // Switchbay imperative shim (§2.2)
};

export type AtlasConfig = {
  seed?: number;                                 // AD-7, default 42
  layout?: "force" | "focus" | "hyperbolic" | "adaptive";   // default "focus"
  budget?: Partial<SceneBudget>;                 // defaults §6
  horizonShare?: number;                         // fraction of node budget reserved, default 0.15
  reducedMotion?: boolean;                       // also honours prefers-reduced-motion
  keyboard?: boolean;                            // default true
  typeMeta?: Record<string, { label?: string; landmark?: boolean; order?: number }>;
};
export type AtlasTheme = {
  palette?: Record<string, string>;              // type -> colour; else data palette; else CSS vars
  tokens?: Partial<Record<AtlasThemeToken, string>>;  // --atlas-bg, --atlas-text, … (§9.4)
};
```

Contract notes:

- `onOpenItem` fires on activate (double-click/Enter); single click =
  focus. The engine never renders document content — `body_html` stays
  the host's concern.
- Events are the only outbound channel; commands the only inbound.
  Hosts supply search, breadcrumbs, doc panels, split chrome on top.
- **Abort semantics:** every `focus`/`zoomTo`/`setLens` issues a new
  scene request token; stale `getScene` resolutions are dropped, and an
  `AbortSignal` is threaded to the data source via an optional second
  argument `getScene(req, signal?)` (sources may ignore it).

---

## 6. Bounded scene model

The client never receives an unbounded graph; runtime cost depends on
the budget, not corpus size.

Default budget (tuned for ~1200×800 viewport):

| knob | default | note |
|---|---|---|
| maxNodes | 60 | includes focus + pinned |
| maxAggregates | 12 | |
| maxEdges | 120 | raw edges after prioritisation |
| maxBundles | 16 | |
| maxLabels | 40 | renderer may show fewer (collision) |

`horizonShare` (default 0.15) of `maxNodes` is **reserved** for
discovery-horizon candidates before relevance ranking fills the rest —
the protected-serendipity guarantee. Budgets scale mildly with viewport
area (sqrt), clamped to [0.5×, 2×] defaults.

Scene identity: `RenderAggregate.id` and `Landmark.anchor` are stable
across consecutive scenes with the same focus/lens (see §7.3, §8.5) so
transitions can animate object correspondence.

---

## 7. Scene construction pipeline (core/scene/)

`builder.ts` orchestrates; for local sources the pipeline runs client-
side over the adapter's indexed graph. Cloud sources may run the same
steps server-side and return `SceneData` directly — the engine treats
`getScene` as opaque either way. Stages:

### 7.1 Neighbourhood harvest
BFS from focus over allowed relation types to hop ≤ 3 (configurable),
capped at `HARVEST_CAP = 40 × maxNodes` visits. Collect per-candidate
features: hop distance, edge types on path, degree, shared sources with
focus (when the adapter exposes citations), type, last-visit recency
(from trail), pin state.

### 7.2 Ranking (`ranking.ts`)
Score per candidate, weights lens-adjustable:

```
rank = w_hop·hopDecay(hop)            // 1, 0.45, 0.20 for hops 1..3
     × w_rel·relWeight(edgeTypes)     // wikilink 1.0, depicts 0.9, cites 0.8, provisional 0.5·score
     × w_type·typeWeight(type)
     × hubPenalty(degree)             // 1/(1+log2(max(1,deg/HUB_DEG))), HUB_DEG=30
     × redundancyPenalty              // MMR-style: ×(1−0.5·maxSimToSelected), sim = shared-neighbour Jaccard
```

Deterministic tie-break: score desc, then id asc. Pinned items always
survive. Top `(maxNodes − horizonReserve − pinned − 1)` become
individual `RenderNode`s with `role` assigned (`bridge` = connects two
otherwise-disconnected selected clusters — cheap check via union-find
over selected-node induced edges).

### 7.3 Aggregation (`aggregate.ts`)
Everything harvested but not individually selected is grouped for
secondary context. Grouping key precedence — **explicit metadata first**
(the prior-finding constraint): (1) type × nearest selected neighbour;
(2) type × shared source; (3) plain type. Internal community labels
(connected components / label propagation over the harvest subgraph —
cheap, local, deterministic) may only *subdivide* an explicit group
when it exceeds `AGG_SPLIT = 25` members, and are labelled by their
top-degree member's title, never "community 3". Aggregates get stable
ids `agg:<type>:<anchorNodeId>` so re-scenes reuse them. Cap at
`maxAggregates` by member count; the rest fold into per-type residual
counts on the nearest landmark (`stats.omitted`). Never emit thousands
of boundary nodes — members beyond the id sample are `residual`.

### 7.4 Discovery horizon (`discovery.ts`)
Reserved-budget candidates, classed and **never merged into one
score**. Classes and their base signals (all computable from graph +
metadata; embedding similarity slots in when the adapter provides it):

| class | signal |
|---|---|
| direct | high rank score but cut by node budget (best of the fold) |
| adjacent | 2–3 hops, moderate rank, type ≠ focus type |
| bridge | high betweenness-lite: connects focus component to a region with no other selected path (union-find + shared-source bridging, cf. `graph.py bridge-candidates`) |
| contrast | shares ≥2 sources or ≥3 neighbours with focus but no direct link; or metadata contradiction markers when present (fixture-encoded; CE `review_required`/`verdict` fields) |
| surprise | seeded sample from mid-rank band (percentile 40–70), weighted by `noveltyBoost` = 1 + unseen-region bonus; never from the bottom band (excessive-distance guard) |
| unexplored | aggregate-level: regions (type/source groups) with 0 visits in trail, surfaced as group candidates |

Each candidate's balance score (within its class only):
`relevance × novelty × explanatoryStrength × sourceQuality × bridgingValue`
where novelty = 1 − exposureCount/(1+exposureCount) (trail-tracked),
explanatoryStrength = 1/pathLen × pathEdgeConfidence, sourceQuality =
provenance richness (has sources / citation count, weak-provenance
penalised), bridgingValue = distinct-region count linked. Penalties:
generic hubs (§7.2 hubPenalty, squared here), redundancy vs already-
selected candidates, repeated exposure, popularity-only rank
(degree may never be the sole positive factor — enforced in tests),
excessive conceptual distance (hop > 3 or sim < floor → excluded).
Per-class quotas from `lens.discoveryMix` (default: direct 0.2,
adjacent 0.2, bridge 0.2, contrast 0.15, surprise 0.15, unexplored 0.1).
**Every candidate carries `reason`** (templated `ExplanationSummary`
with `viaIds`), and every class reports `omittedCount` ("12 more
contrasting sources").

### 7.5 Landmarks (`landmarks.ts`)
Visible stable geography = explicit types + top sources + pinned items:
one landmark per type present in the harvest (ordered by
`typeMeta.order`, CE order = sidebar's `TYPE_ORDER`), top-N shared
sources as secondary landmarks, pins always. Community landmarks only
when `lens` explicitly requests them. Landmarks carry stable angular
anchors (§8.5) — the user's compass.

### 7.6 Edge selection
See §11; runs after node/aggregate selection, emits `edges` + `bundles`
within budget.

Performance target: full pipeline ≤ 16 ms at default budget on the
400-node fixture, ≤ 60 ms against the scaled source (measured in P4;
telemetry `sceneBuildMs`).

---

## 8. Layout adapters (core/layout/)

```ts
interface LayoutAdapter {
  id: "force" | "focus" | "hyperbolic" | "adaptive";
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult;  // pure, seeded
}
type LayoutContext = {
  viewport: { width: number; height: number };
  previous?: LayoutResult;            // for continuity
  anchors: AngularAnchors;            // §8.5
  seed: number;
};
type LayoutResult = {
  positions: Map<string, { x: number; y: number; r: number }>;  // nodes + aggregates
  edgePaths?: Map<string, EdgePath>;  // optional curved/bundled paths
  displacement: number;               // telemetry: mean movement vs previous
};
```

1. **`force.ts` (P0 baseline).** d3-force with the current viewer's
   constants (charge −420, link 110, collide 10, 350 pre-warm ticks,
   radius `4+sqrt(deg+1)*1.6`) so the P0 harness is visually
   comparable to today's viewer. Deterministic via input ordering +
   d3's seeded LCG; positions computed synchronously (budget-bounded
   node counts make this cheap), then handed to the renderer — no
   live simulation in the default mode.
2. **`focus.ts` (P1 — default).** Focus-centred rings: focus fixed at
   centre; ring 1 = hop-1 nodes; ring 2 = hop-2/bridge; ring 3 =
   aggregates; horizon groups on the outer band. Angular position from
   the stable anchor system (§8.5); within a sector, order by rank;
   one relaxation pass resolves overlaps radially (never across
   sectors — sector = landmark = geography). Pinned items keep a
   persistent sector.
3. **`hyperbolic.ts` (P3).** Same ring/sector assignment mapped through
   a Poincaré-disc projection: radial coordinate `tanh(k·hop)`,
   translation-based re-centring on focus change so the transition is a
   hyperbolic isometry (object correspondence preserved). Identical
   scene data and controls as `focus.ts` — the comparison is geometry
   only.
4. **`adaptive.ts` (P5).** Classifies the local scene (tree-like →
   radial/hyperbolic; dense mesh → constrained Euclidean; directed
   chain → layered; bipartite evidence → two-sided) using cheap
   topology stats (density, reciprocity, layering ratio,
   bipartite-ness test) and routes to the matching adapter per region;
   relations that the active geometry represents poorly become
   explicit shortcut links in the horizon rather than map distortions.

### 8.5 Stable angular anchors (`anchors.ts`)
The anti-disorientation mechanism shared by all adapters: each landmark
(type/source/pin) receives a persistent angle — assigned at first
appearance by hashing landmark id into free arc space, then **kept for
the session** and serialised with the trail. Nodes inherit their
sector's arc. Consequences: "sources are always north-west of here",
back() returns to a visually familiar arrangement, and layout
displacement between consecutive scenes is minimised by constraint
rather than by tweening tricks. Displacement is telemetry (§16).

---

## 9. Renderer (renderer/)

`SceneRenderer` interface: `mount(canvas)`, `render(frame)`,
`destroy()`; a `Frame` = scene + layout + interaction state + animation
progress + resolved theme. Implementation `canvas.ts` (AD-2):

1. **Draw order:** bundles → edges → aggregates (rounded-rect chips
   with count) → nodes (circle, type colour, role ring) → horizon
   band → labels → focus/selection overlays.
2. **LOD:** label set chosen by score order under `maxLabels` with
   greedy AABB collision (port of current viewer's `recomputeAutoVisible`
   logic into `labels.ts`); below zoom thresholds aggregates render as
   density dots; hysteresis from §10.
3. **Interaction:** renderer emits raw pointer/wheel/key events to the
   engine; engine owns hit testing (`hittest.ts`) and semantics. Wheel
   = semantic zoom (§10); drag = pan; click = focus; dblclick/Enter =
   open; hover = highlight + `hover` event.
4. **Theme tokens** (`--atlas-bg`, `--atlas-text`, `--atlas-line`,
   `--atlas-accent`, `--atlas-muted`, per-type via palette): resolved
   from `AtlasTheme.tokens` → CSS custom properties on the host element
   → defaults. Works under both hosts' `:root[data-theme]` scheme
   unchanged.
5. **Accessibility & environment:** high-DPI via DPR transform;
   `ResizeObserver` in adapters calling `controller.resize`;
   reduced-motion (config or media query) collapses animations to
   ≤120 ms fades; keyboard: arrows = move selection among neighbours
   (spatial), Enter = focus, Shift+Enter = open, Backspace = back,
   `p` = pin. Focused item mirrored to an offscreen ARIA live region by
   the React adapter.

Performance targets (documented in `docs/performance.md`, measured in
harness): 60 fps during transitions at default budget; ≤ 4 ms/frame
draw at 2× budget; zero rAF when idle.

---

## 10. Semantic zoom (`zoom.ts`, `transitions.ts`)

Continuous `semanticScale` ∈ [0, 3] with four representation bands:

| band | shows |
|---|---|
| 0 | type groups / top aggregates only |
| 1 | topic/local structural groups + landmarks |
| 2 | individual pages/concepts/entities/sources (default) |
| 3 | claims/relations/provenance detail where the source supplies it |

Band switch uses **hysteresis** (enter at x.25, exit at x.75 — no
flicker at boundaries); within a band, scale interpolates size/label
density only. Zoom changes semantic resolution — the scene is
**re-requested** with the new `semanticScale`, not merely re-scaled.

### 10.3 Transitions
Focus change and aggregate expand/collapse animate object
correspondence using `transitionMap`: an expanding aggregate keeps its
centre/orientation/colour identity and unfolds members outward from it;
collapsing reverses; departing nodes fade toward their aggregate's
position; edges re-route continuously (`edge-flow continuity`: an
edge to an aggregate splits into member edges along the same corridor).
Duration 300 ms (reduced-motion: 100 ms fade). Lower-priority members
remain as `residual` density on the aggregate chip.

---

## 11. Edge strategy

Priority tiers (fill `maxEdges` in order, stop when spent):

1. edges incident to focus, selection, pins;
2. active explanatory paths (from an engaged discovery candidate's
   `viaIds`);
3. high-value bridge edges (bridge-role nodes' connecting edges);
4. typed aggregate flows (rendered as `bundles`, not raw edges);
5. remaining harvest edges by `confidence × relWeight`.

Styling: per-type colour/dash (depicts dashed — parity with today),
direction as a subtle arrow at ≥ band 2, confidence as alpha, bundles
as tapered ribbons with count. Progressive disclosure: hover/selection
reveals tier-5 edges local to the pointer. Shortest paths through
high-degree generic nodes are **not** treated as explanations —
explanation paths inherit the hub penalty (§7.2), preferring longer
paths through specific nodes over 2-hop paths through hubs.

---

## 12. Inquiry trails (`trails.ts`)

Branching tree, not flat history:

```ts
type TrailStep = {
  id: string; focusId: string; t: number;       // t = logical counter, not wall clock
  origin: "user" | "system" | "history";        // intentional vs suggested transitions
  via?: { cls?: DiscoveryClass; edgeType?: string };  // discovery origin (telemetry §16)
  sceneStamp: { semanticScale: number; lensId: string };
};
type TrailBranch = { id: string; name?: string; steps: TrailStep[]; parent?: { branchId: string; stepId: string } };
type TrailState = { branches: TrailBranch[]; activeBranchId: string; cursor: number; pinned: string[] };
```

- `focus()` appends to the active branch (truncating forward steps —
  but truncated steps are preserved as an auto-branch, so **no detour
  is ever lost**); `back()/forward()` move the cursor without editing.
- `branch()` forks explicitly; `compare(ids)` computes shared/unique
  items across branches and emits a `trail-changed` + sets a compare
  lens (visual: items tinted by branch membership) — minimal but the
  state model is extensible (brief requirement).
- Pins live on the trail state and are serialised.
- `serializeTrail()/restoreTrail()` round-trip the whole structure +
  angular anchors (§8.5) as versioned JSON (`{v: 1, …}`).

---

## 13. React adapter (react/)

- `<KnowledgeAtlas>`: owns the canvas element + `ResizeObserver` +
  engine lifecycle; forwards `controllerRef`; subscribes to engine
  events → `onEvent`/`onOpenItem`. No context requirement, no global
  state (AD-6). Styling via the host-provided wrapper — the component
  fills its container (`width/height: 100%`), matching Switchbay's
  `position:relative` host contract.
- `useAtlas(dataSource, config)` for hosts that want the engine without
  the prefab component.
- StrictMode-safe (idempotent mount/destroy), React 18 peer.

### 13.3 Switchbay integration example (`examples/switchbay/`)
`AtlasTab.tsx` showing: `registerTabKind("atlas", …)` with a
`TabContext → KnowledgeAtlasProps` adapter that (a) wraps the tab's
`graphData: GraphData` in the CE data source (§14), (b) maps
`item-open-requested` to the existing hash contract
(`location.hash = '#page='+id`) so `window.Modal` keeps working, (c)
exposes `controllerRef` for the `window.Graph.focus/clearFocus` shim
sites, and (d) reads Switchbay's `--type-*` CSS vars into the theme.
README documents the pack-based alternative. (Actual switchbay-repo
changes are out of scope here.)

---

## 14. Curiosity Engine adapter (`datasources/curiosity.ts`)

### 14.1 Input
Constructed from a `CEData` object (already fetched by the host) or a
URL (harness convenience). Immediately deep-copies and indexes:
id→item, forward/reverse adjacency, per-type lists, source→pages index
(from `properties.sources`). Handles every sharp edge in §2.1
(page-only nodes, `.md` normalisation, mutated edge refs, prefix split).

### 14.2 Type canonicalisation
Exported `canonicalType(raw, titlePrefix?)` implementing the union of
`sidebar.js:TYPE_CANONICAL` and Switchbay's `_PREFIX_TO_TYPE` backfill.
Property-based test locks it against the fixture corpus.

### 14.3 Payload enrichment (P2, additive, optional)
To feed the discovery horizon real signals, extend `wiki_render.py`
with an **opt-in flag** `--atlas-edges` that additionally exports:
`cites` edges (page→page via shared vault source, with `sharedCount`)
and `provisional` edges (`origin`, `score`) from the existing kuzu rel
tables — appended to `edges` with new `type` values so the current
viewer (which only styles known kinds) is unaffected, and gated so the
default build is byte-identical to today. Canonical markdown and graph
semantics unchanged (brief constraint). Python side gets a stdlib
unittest for the flag's schema. The adapter uses these when present and
degrades gracefully when absent (P1 works on today's payload:
contrast/bridge classes then rely on shared-`sources` frontmatter only).

### 14.4 CE viewer embedding (`examples/curiosity-engine/`)
`atlas-glue.js`: a `wiki-view` script that, when
`localStorage['curiosity-engine.viewer'] === 'atlas'` (or `?viewer=atlas`),
replaces the `#graph` pane init with
`KnowledgeAtlas.mount(container, { dataSource: CE(data), … })` from the
vendored IIFE, wiring `item-open-requested` → `location.hash` (modal,
sidebar, editing all keep working untouched). The P0 embedding proof =
this glue running against a real workspace bundle. Vendoring follows
the ritual: built file + version/sha256 row in `RELEASE_CHECKLIST.md`.

---

## 15. Fixtures (`fixtures/`)

`generate.ts` (seeded, AD-7) emits committed JSON for 1–4; the scaled
corpus (5) is procedural at runtime. All include typed nodes,
provenance (`sources`), generic hubs, bridge concepts, planted
contradictions, and **intentionally useful hidden connections** with a
`fixtures/*.expected.json` answer key (used by discovery tests §16 and
harness tasks).

1. **workspace-small** — realistic CE workspace: ~50 sources, ~400
   nodes across all 11 CE types, CE-shaped ids/titles/prefixes;
   doubles as the CE-adapter conformance fixture (serialised as a
   valid `CEData` payload).
2. **ontology-tree** — deep tree + sparse cross-links (radial/
   hyperbolic best case).
3. **dense-smallworld** — high clustering, low diameter, several
   degree-100+ hubs (hub-penalty and edge-budget stress).
4. **mixed-multiscale** — overlapping communities at 3 scales with
   typed membership (aggregation + landmark stress; communities overlap
   deliberately so type-first grouping is visibly better than
   community-first).
5. **scaled corpus** (`datasources/scaled.ts`) — ≥ 1,000,000 logical
   nodes generated lazily from the seed (hierarchical stochastic block
   structure, Zipf degrees, typed levels); implements `AtlasDataSource`
   directly with bounded `getScene` (server-side-style pipeline) and
   simulated latency knob — the P4 scale/latency testbed and the
   template for real cloud sources (`remote.ts` speaks the same
   request/response over fetch with pagination/streaming).

---

## 16. Telemetry & evaluation (`telemetry.ts`, harness)

Engine-internal instrumentation (no separate analytics app), emitted as
`telemetry` events and aggregated in the harness HUD:

- per scene: visible node/aggregate/edge/label counts, sceneBuildMs,
  layoutMs, frameMs (p50/p95), layout displacement, omitted counts;
- per interaction: focus transitions (origin: user/system/history),
  back/reset counts, discovery engagements by class (`via` on trail
  steps), explanation requests, branch create/switch/compare,
  recovery cost (steps from detour end back to a prior trail item);
- session export as JSON from the harness for comparison runs.

**Repeatable tasks** (harness task-runner panel, scripted per fixture
against the answer keys; identical across modes):
retrieval (locate item → open neighbourhood → return), orientation
(state current type/start/path; revisit prior item), discovery (find
planted unfamiliar item; identify planted non-obvious connection; find
contrasting evidence; read its explanation), ideation (develop two
branches; keep a detour; compare). Primary metrics per the brief:
useful discoveries/time, new connections/time, interpretable
entities+relations per screen, transition efficiency, recovery cost,
orientation accuracy, calibrated mastery. Results land in
`docs/results.md` per prototype mode. A visually impressive but
unintelligible map fails.

---

## 17. Prototype sequence — work items & acceptance criteria

All modes stay behind `AtlasConfig.layout`/lens config permanently
(controlled comparison, no mode deletion). Do not proceed to a
production architecture before the P1↔P3 comparison is recorded.

**P0 — Integration baseline** *(scaffold + parity)*
Work: package scaffold (AD-1); core types (§5); CE adapter (§14.1–.2);
fixture generator + fixtures 1–4; force layout adapter; canvas renderer
(nodes/edges/labels, no aggregates yet); React adapter shell; harness
with fixture picker; telemetry skeleton; ADR-001 written; CE glue
embedding proof (§14.4) against fixture 1's `CEData` serialisation.
Accept: harness renders fixture 1 visually comparable to today's
viewer (constants from §2.1); unit tests green (adapter conformance,
canonicalType, determinism: same seed ⇒ same positions); one Playwright
spec (load harness → click node → focus event).

**P1 — Focus-centred Euclidean atlas** *(the main baseline)*
Work: scene pipeline (§7 complete: ranking, aggregation, horizon with
all six classes + reasons + omitted counts, landmarks, edge tiers);
focus layout + anchors (§8.2/8.5); transitions (§10.3); back/forward +
pins + trails v1 (§12); hit testing + keyboard; semantic zoom bands +
hysteresis (§10); explanation hooks (`getExplanation` local impl).
Accept: budgets never exceeded (property test across fixtures/seeds);
click-to-focus animates correspondence; horizon shows ≥3 classes with
inspectable reasons on fixture 1; planted-connection discovery task
passes; back() restores familiar geometry (displacement below
threshold); Playwright: navigation loop (focus → open → back →
forward), pin, keyboard path.

**P2 — Explainable library effect**
Work: around-focus faceting (same-type/same-source/conceptual/graph/
bridge/contrast/peripheral exposed as distinct groups); lens
relevance/novelty/diversity balance controls in harness;
`--atlas-edges` payload enrichment + Python unittest (§14.3);
exposure/novelty tracking.
Accept: each facet independently inspectable with reasons; changing the
mix visibly re-balances the horizon without breaking budgets; contrast
class finds the planted contradictions in fixtures 1 and 4.

**P3 — Hyperbolic 2D adapter**
Work: `hyperbolic.ts` (§8.3); harness A/B toggle running **identical
scene data and controls** through focus vs hyperbolic; comparison run
of §16 tasks recorded in `docs/results.md`.
Accept: isometric re-centring (no correspondence break); verdict
recorded — keep only if context/discovery improves without
disorientation cost.

**P4 — Semantic zoom & scale**
Work: multilevel aggregation across bands 0–1 (aggregates of
aggregates); `scaled.ts` million-node source + `remote.ts`
cloud-style source; latency/packet-size measurements; abort/stale
handling under fast navigation; internal communities for aggregation
only (never default geography — verify by test: no community landmark
without explicit lens flag).
Accept: render packets bounded at every scale (property test);
sceneBuild p95 within targets (§7.6) on scaled source;
zoom-band flicker absent (hysteresis test); measurements in
`docs/performance.md`.

**P5 — Adaptive layout & residual discovery**
Work: `adaptive.ts` topology router (§8.4); geometry-mismatch
relations surfaced as explicit shortcuts in the horizon; final
comparison sweep (all modes × all fixtures × task set).
Accept: router picks expected geometry per fixture (tree→radial,
smallworld→constrained-Euclidean, chain→layered, bipartite→two-sided);
whole-map distortion bounded (displacement metric); `docs/results.md`
completed with the **production-iteration recommendation** (deliverable
12).

---

## 18. Testing

- **Vitest unit** (`tests/`): ranking (incl. "degree never sole
  positive factor", hub/redundancy penalties), aggregation stability +
  budget respect, discovery class quotas + reason presence, trails
  (branch/truncate-preserve/serialise round-trip), zoom hysteresis,
  transitions (correspondence maps), hit testing, anchors stability,
  CE adapter conformance against fixture 1 + sharp-edge cases (§2.1),
  determinism (seed ⇒ positions), abort/stale-drop.
- **Playwright** (`e2e/`, against `vite dev` harness with `webServer`
  configured — unlike Switchbay's, ours self-starts): P0 smoke, P1
  navigation loop, keyboard nav, mode toggle parity (P3), reduced-
  motion, resize, high-DPI (deviceScaleFactor: 2).
- **Python stdlib unittest** (`tests/` at repo root, matching repo
  convention): `--atlas-edges` export schema (P2) and a
  `data.json`-schema contract test (net-new, protects both hosts).
- CI note: repo has no workflow for JS today; add a
  `packages/knowledge-atlas` job (pnpm install, tsc, vitest,
  playwright) in the coding session if a workflow exists by then;
  otherwise document `pnpm test` in the package README.

---

## 19. Deliverables ↔ definition of done

| # | deliverable | where |
|---|---|---|
| 1 | engine package | `src/core` |
| 2 | renderer + React adapter | `src/renderer`, `src/react` |
| 3 | CE data adapter | `src/datasources/curiosity.ts` |
| 4 | standalone harness | `harness/` (excluded from `files`) |
| 5 | five deterministic fixtures | `fixtures/`, `datasources/scaled.ts` |
| 6 | configurable prototype modes | `AtlasConfig.layout` + lens |
| 7 | unit + interaction tests | `tests/`, `e2e/` |
| 8 | CE integration example | `examples/curiosity-engine/` + §14.4 glue |
| 9 | Switchbay integration example | `examples/switchbay/` |
| 10 | ADR (renderer + geometry) | `docs/adr-001-renderer-and-geometry.md` |
| 11 | mode-comparison results | `docs/results.md` |
| 12 | production recommendation | final section of `docs/results.md` |

Done when: CE can swap its graph canvas via the small glue adapter;
Switchbay can mount the React component with no new app shell; local,
fixture, scaled and remote sources share the one viewer API; scenes
stay bounded at any logical size; focus/history/horizon/explanations
work; Euclidean vs hyperbolic compare on identical data; discovery
beats a file browser / single ranked list on the harness tasks;
communities are never the default geography; engine / renderer /
adapters / harness are cleanly separated; package installs/vendors
without the harness.

---

## 20. Risks & open questions

- **First JS toolchain in this repo** — contained per AD-1, but if the
  maintainer prefers zero dev-time Node here, the fallback is moving
  `packages/knowledge-atlas` to its own repo and vendoring only the
  built bundle; nothing else in this plan changes.
- **`wiki_render.py` may be hash-guarded** — check `evolve_guard.sh`'s
  `GUARDED` array before the P2 enrichment; if listed, the change needs
  the guard snapshot updated as part of the same commit.
- **Discovery quality on today's payload** is capped by the missing
  `Cites`/`ProvisionalLink` export (hence §14.3); P1 acceptance uses
  fixtures (which carry full signals), not real workspaces.
- **Hyperbolic label legibility** near the disc rim is a known UX risk;
  the P3 verdict must weigh it (labels-first, geometry second).
- **Split mode (Switchbay)** is intentionally *not* re-implemented in
  the engine — generic multi-select + selection events must be shown
  sufficient during the Switchbay-side integration (not in this repo).
- **Million-node BFS locality**: the scaled source must generate
  neighbourhoods lazily from the seed without materialising the graph;
  the design (hierarchical blocks) supports it, but P4 measures it.
