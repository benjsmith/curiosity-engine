/**
 * Public types of the Knowledge Atlas engine.
 *
 * The engine is framework-independent: no DOM types leak into the core
 * contract except the plain geometry the renderer needs. Types here
 * follow PLAN.md §5–§6; changes to this file are API changes.
 */

// ── data ────────────────────────────────────────────────────────────

export interface KnowledgeItem {
  id: string;
  /** Canonical type string — engine treats it as opaque. */
  type: string;
  title: string;
  meta: {
    /** "[con]" etc., split from a Curiosity Engine title. */
    titlePrefix?: string;
    /** Host-side path (CE: with .md). */
    path?: string;
    /** Provenance (CE frontmatter `sources:`). */
    sources?: string[];
    created?: string;
    updated?: string;
    degree?: number;
    properties?: Record<string, unknown>;
  };
}

export type AtlasLens = {
  id: string;
  /** Per edge-type score multiplier. */
  relationWeights?: Record<string, number>;
  /** Per node-type score multiplier. */
  typeWeights?: Record<string, number>;
  /** Discovery-class quota balance (fractions; normalised internally). */
  discoveryMix?: Partial<Record<DiscoveryClass, number>>;
  /** Reveal internal community groupings as landmarks. Off by default. */
  showCommunities?: boolean;
};

export type SceneBudget = {
  maxNodes: number;
  maxAggregates: number;
  maxEdges: number;
  maxBundles: number;
  maxLabels: number;
};

export type SceneRequest = {
  focusId?: string;
  lens: AtlasLens;
  relationTypes?: string[];
  viewport: { width: number; height: number };
  /** Continuous semantic scale; bands documented in PLAN §10. */
  semanticScale: number;
  /** Recent focus ids, most recent last (repeat-exposure penalty). */
  history?: string[];
  /** Pinned item ids — always survive selection. */
  pinned?: string[];
  budget: SceneBudget;
};

export type NodeRole = "focus" | "neighbour" | "bridge" | "pinned" | "context";

export type RenderNode = {
  id: string;
  item: KnowledgeItem;
  role: NodeRole;
  /** Ranking score, for LOD/label ordering. */
  score: number;
  /** Hop distance from focus (layout band hint). */
  ring?: number;
};

export type RenderAggregate = {
  /** Stable across consecutive scenes: "agg:<type>:<anchor>". */
  id: string;
  label: string;
  /** Dominant member type. */
  type: string;
  count: number;
  /** Capped member sample; full size in `count`. */
  memberIds: string[];
  /** Members beyond the sample. */
  residual?: number;
  /** Nearest selected node this aggregate clusters around, if any. */
  anchorId?: string;
};

export type RenderEdge = {
  source: string;
  target: string;
  type: string;
  direction?: "forward" | "back" | "none";
  /** 0..1 (provisional edges carry their score). */
  confidence?: number;
  /** Priority tier 1 (highest) .. 5 — see PLAN §11. */
  priority: number;
};

export type RenderBundle = {
  id: string;
  /** Node-or-aggregate ids. */
  source: string;
  target: string;
  type: string;
  count: number;
};

export type DiscoveryClass =
  | "direct"
  | "adjacent"
  | "bridge"
  | "contrast"
  | "surprise"
  | "unexplored";

export const DISCOVERY_CLASSES: readonly DiscoveryClass[] = [
  "direct",
  "adjacent",
  "bridge",
  "contrast",
  "surprise",
  "unexplored",
];

export type ExplanationSummary = {
  kind: DiscoveryClass | "edge" | "path" | "aggregate";
  /** One line, host-renderable. */
  text: string;
  /** Supporting items (bridge path, shared source pages…). */
  viaIds?: string[];
};

export type DiscoveryCandidate = {
  id: string;
  item: KnowledgeItem;
  score: number;
  /** ALWAYS present — why this candidate appears. */
  reason: ExplanationSummary;
};

export type HorizonGroup = {
  cls: DiscoveryClass;
  candidates: DiscoveryCandidate[];
  /** Explicit "N more …" beyond the shown candidates. */
  omittedCount: number;
};

export type Landmark = {
  id: string;
  kind: "type" | "source" | "pinned" | "community";
  label: string;
  type?: string;
  /** Stable angular home in radians (PLAN §8.5). */
  anchor: { angle: number };
};

export type OmittedSummary = {
  cls: DiscoveryClass | "edges" | "nodes";
  count: number;
  label: string;
};

export type SceneData = {
  focus?: KnowledgeItem;
  nodes: RenderNode[];
  aggregates: RenderAggregate[];
  edges: RenderEdge[];
  bundles: RenderBundle[];
  horizon: HorizonGroup[];
  landmarks: Landmark[];
  /** Aggregate id -> member ids (unfold/collapse continuity). */
  transitionMap?: Record<string, string[]>;
  stats?: { totalNodes?: number; omitted?: OmittedSummary[] };
};

// ── data source ─────────────────────────────────────────────────────

export type ExplanationRequest =
  | { kind: "candidate"; id: string; focusId: string; cls: DiscoveryClass }
  | { kind: "edge"; source: string; target: string; type: string }
  | { kind: "aggregate"; id: string };

export type Explanation = {
  summary: ExplanationSummary;
  evidence?: ExplanationSummary[];
};

export interface AtlasDataSource {
  /** MUST honour request.budget — never returns an unbounded graph. */
  getScene(request: SceneRequest, signal?: AbortSignal): Promise<SceneData>;
  getItem(id: string): Promise<KnowledgeItem | null>;
  getExplanation(request: ExplanationRequest): Promise<Explanation>;
}

// ── trails (PLAN §12) ───────────────────────────────────────────────

export type TrailStep = {
  id: string;
  focusId: string;
  /** Logical counter, not wall clock (determinism). */
  t: number;
  origin: "user" | "system" | "history";
  via?: { cls?: DiscoveryClass; edgeType?: string };
  sceneStamp: { semanticScale: number; lensId: string };
};

export type TrailBranch = {
  id: string;
  name?: string;
  steps: TrailStep[];
  parent?: { branchId: string; stepId: string };
};

export type TrailState = {
  branches: TrailBranch[];
  activeBranchId: string;
  /** Index into the active branch's steps; -1 = before first step. */
  cursor: number;
  pinned: string[];
};

// ── engine control & events ─────────────────────────────────────────

export type AtlasState = {
  focusId?: string;
  semanticScale: number;
  lens: AtlasLens;
  pinned: string[];
  selection: string[];
  trail: TrailState;
  scene?: { nodeCount: number; aggregateCount: number; edgeCount: number };
  status: "idle" | "loading" | "error";
  error?: string;
};

export type SceneStats = {
  nodeCount: number;
  aggregateCount: number;
  edgeCount: number;
  bundleCount: number;
  labelCount: number;
  horizonCount: number;
  sceneBuildMs: number;
  layoutMs: number;
  layoutDisplacement: number;
};

export type TelemetrySample = {
  kind:
    | "scene"
    | "focus"
    | "back"
    | "forward"
    | "discovery-engaged"
    | "explanation"
    | "branch"
    | "compare"
    | "frame";
  data: Record<string, number | string | undefined>;
};

export type AtlasEvent =
  | { kind: "focus-changed"; id: string; origin: "user" | "system" | "history" }
  | { kind: "scene-ready"; stats: SceneStats }
  | { kind: "item-open-requested"; id: string }
  | { kind: "selection-changed"; ids: string[] }
  | { kind: "hover"; id: string | null }
  | { kind: "explanation-requested"; request: ExplanationRequest }
  | { kind: "explanation-ready"; request: ExplanationRequest; explanation: Explanation }
  | { kind: "discovery-engaged"; id: string; cls: DiscoveryClass }
  | { kind: "trail-changed"; trail: TrailState }
  | { kind: "zoom-changed"; semanticScale: number; band: number }
  | { kind: "error"; message: string }
  | { kind: "telemetry"; sample: TelemetrySample };

export interface AtlasController {
  focus(id: string, origin?: "user" | "system"): void;
  back(): void;
  forward(): void;
  zoomTo(level: number): void;
  setLens(lens: AtlasLens): void;
  setLayout(layout: LayoutKind): void;
  pin(id: string): void;
  unpin(id: string): void;
  compare(ids: string[]): void;
  branch(fromStepId?: string): string;
  select(ids: string[], mode?: "replace" | "add" | "toggle"): void;
  requestExplanation(request: ExplanationRequest): void;
  getState(): AtlasState;
  serializeTrail(): string;
  restoreTrail(json: string): void;
  resize(w: number, h: number): void;
  destroy(): void;
}

// ── config & theme ──────────────────────────────────────────────────

export type LayoutKind = "force" | "focus" | "hybrid" | "hyperbolic" | "adaptive";

export type AtlasConfig = {
  /** Seed for every stochastic step (PLAN AD-7). Default 42. */
  seed?: number;
  /** Default "focus". */
  layout?: LayoutKind;
  budget?: Partial<SceneBudget>;
  /** Fraction of node budget reserved for the horizon. Default 0.15. */
  horizonShare?: number;
  reducedMotion?: boolean;
  /** Default true. */
  keyboard?: boolean;
  typeMeta?: Record<string, { label?: string; landmark?: boolean; order?: number }>;
};

export type AtlasThemeToken =
  | "bg"
  | "text"
  | "textMuted"
  | "line"
  | "accent"
  | "aggregateFill"
  | "horizonBg";

export type AtlasTheme = {
  /** type -> colour; falls back to the data palette, then defaults. */
  palette?: Record<string, string>;
  tokens?: Partial<Record<AtlasThemeToken, string>>;
};

// ── layout results (shared between core and renderer) ───────────────

export type LayoutPoint = { x: number; y: number; r: number };

export type LayoutResult = {
  /** Positions for nodes AND aggregates, keyed by id. */
  positions: Map<string, LayoutPoint>;
  /** Mean movement vs previous layout for shared ids (telemetry). */
  displacement: number;
};

export const DEFAULT_BUDGET: SceneBudget = {
  maxNodes: 60,
  maxAggregates: 12,
  maxEdges: 120,
  maxBundles: 16,
  maxLabels: 40,
};

export const DEFAULT_LENS: AtlasLens = { id: "default" };

/** Semantic-zoom band edges with hysteresis margins (PLAN §10). */
export const ZOOM_BANDS = 4;
