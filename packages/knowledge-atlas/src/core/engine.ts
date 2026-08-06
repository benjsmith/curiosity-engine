/**
 * AtlasEngine — the framework-independent state machine implementing
 * AtlasController (PLAN §5). Commands in, events out; no DOM, no
 * network, no host globals (AD-6). Stale scene requests are aborted
 * and dropped by token.
 */

import { adaptiveLayout } from "./layout/adaptive.ts";
import { focusLayout } from "./layout/focus.ts";
import { forceLayout } from "./layout/force.ts";
import { hyperbolicLayout } from "./layout/hyperbolic.ts";
import { HitTester } from "./hittest.ts";
import { Trails } from "./trails.ts";
import { clampScale, nextBand } from "./zoom.ts";
import { DEFAULT_BUDGET, DEFAULT_LENS } from "./types.ts";
import type { LayoutAdapter } from "./layout/types.ts";
import type {
  AtlasConfig,
  AtlasController,
  AtlasDataSource,
  AtlasEvent,
  AtlasLens,
  AtlasState,
  DiscoveryClass,
  ExplanationRequest,
  LayoutKind,
  LayoutResult,
  SceneBudget,
  SceneData,
  SceneStats,
} from "./types.ts";

const ADAPTERS: Record<LayoutKind, LayoutAdapter> = {
  force: forceLayout,
  focus: focusLayout,
  hyperbolic: hyperbolicLayout,
  adaptive: adaptiveLayout,
};

export type EngineSnapshot = {
  scene: SceneData | null;
  layout: LayoutResult | null;
  state: AtlasState;
  stats: SceneStats | null;
};

export class AtlasEngine implements AtlasController {
  readonly hitTester = new HitTester();

  private readonly source: AtlasDataSource;
  private readonly config: Required<Pick<AtlasConfig, "seed" | "horizonShare" | "keyboard">> & AtlasConfig;
  private readonly budget: SceneBudget;
  private listeners = new Set<(e: AtlasEvent) => void>();

  private focusId?: string;
  private semanticScale = 2;
  private band = 2;
  private lens: AtlasLens = DEFAULT_LENS;
  private layoutKind: LayoutKind;
  private selection: string[] = [];
  private trails = new Trails();
  private viewport = { width: 1200, height: 800 };
  private status: AtlasState["status"] = "idle";
  private errorMsg?: string;

  private scene: SceneData | null = null;
  private layoutResult: LayoutResult | null = null;
  private stats: SceneStats | null = null;
  private requestToken = 0;
  private abortController: AbortController | null = null;
  private destroyed = false;
  /** Discovery classes of last scene, for discovery-engaged telemetry. */
  private horizonClassOf = new Map<string, DiscoveryClass>();

  constructor(source: AtlasDataSource, config: AtlasConfig = {}) {
    this.source = source;
    this.config = { seed: 42, horizonShare: 0.15, keyboard: true, ...config };
    this.budget = { ...DEFAULT_BUDGET, ...config.budget };
    this.layoutKind = config.layout ?? "focus";
  }

  // ── events ────────────────────────────────────────────────────────

  on(cb: (e: AtlasEvent) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  private emit(e: AtlasEvent): void {
    for (const cb of this.listeners) cb(e);
  }

  snapshot(): EngineSnapshot {
    return { scene: this.scene, layout: this.layoutResult, state: this.getState(), stats: this.stats };
  }

  // ── scene lifecycle ───────────────────────────────────────────────

  private effectiveBudget(): SceneBudget {
    // Budgets scale mildly with viewport area (sqrt), clamped ×[0.5, 2].
    const ref = 1200 * 800;
    const k = Math.max(0.5, Math.min(2, Math.sqrt((this.viewport.width * this.viewport.height) / ref)));
    return {
      maxNodes: Math.round(this.budget.maxNodes * k),
      maxAggregates: Math.round(this.budget.maxAggregates * k),
      maxEdges: Math.round(this.budget.maxEdges * k),
      maxBundles: this.budget.maxBundles,
      maxLabels: Math.round(this.budget.maxLabels * k),
    };
  }

  private requestScene(): void {
    if (this.destroyed) return;
    const token = ++this.requestToken;
    this.abortController?.abort();
    const ac = new AbortController();
    this.abortController = ac;
    this.status = "loading";
    const started = performance.now();
    this.source
      .getScene(
        {
          focusId: this.focusId,
          lens: this.lens,
          viewport: { ...this.viewport },
          semanticScale: this.band,
          history: this.trails.historyIds(),
          pinned: [...this.trails.pinned],
          budget: this.effectiveBudget(),
        },
        ac.signal,
      )
      .then((scene) => {
        if (token !== this.requestToken || this.destroyed) return; // stale — drop
        const sceneBuildMs = performance.now() - started;
        this.applyScene(scene, sceneBuildMs);
      })
      .catch((err: unknown) => {
        if (token !== this.requestToken || this.destroyed) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        this.status = "error";
        this.errorMsg = err instanceof Error ? err.message : String(err);
        this.emit({ kind: "error", message: this.errorMsg });
      });
  }

  private applyScene(scene: SceneData, sceneBuildMs: number): void {
    this.scene = scene;
    this.horizonClassOf.clear();
    for (const grp of scene.horizon) {
      for (const c of grp.candidates) this.horizonClassOf.set(c.id, grp.cls);
    }
    const layoutStart = performance.now();
    const adapter = ADAPTERS[this.layoutKind];
    this.layoutResult = adapter.layout(scene, {
      viewport: this.viewport,
      previous: this.layoutResult ?? undefined,
      seed: this.config.seed,
    });
    const layoutMs = performance.now() - layoutStart;
    this.hitTester.update(
      this.layoutResult.positions,
      scene.nodes.map((n) => n.id),
      scene.aggregates.map((a) => a.id),
    );
    this.status = "idle";
    this.errorMsg = undefined;
    this.stats = {
      nodeCount: scene.nodes.length,
      aggregateCount: scene.aggregates.length,
      edgeCount: scene.edges.length,
      bundleCount: scene.bundles.length,
      labelCount: Math.min(scene.nodes.length, this.budget.maxLabels),
      horizonCount: scene.horizon.reduce((s, g) => s + g.candidates.length, 0),
      sceneBuildMs,
      layoutMs,
      layoutDisplacement: this.layoutResult.displacement,
    };
    this.emit({ kind: "scene-ready", stats: this.stats });
    this.emit({
      kind: "telemetry",
      sample: {
        kind: "scene",
        data: {
          sceneBuildMs: Math.round(sceneBuildMs * 100) / 100,
          layoutMs: Math.round(layoutMs * 100) / 100,
          nodes: this.stats.nodeCount,
          aggregates: this.stats.aggregateCount,
          edges: this.stats.edgeCount,
          displacement: Math.round(this.layoutResult.displacement),
        },
      },
    });
  }

  // ── AtlasController ───────────────────────────────────────────────

  focus(id: string, origin: "user" | "system" = "user"): void {
    if (this.destroyed) return;
    const via = this.horizonClassOf.get(id);
    if (via) {
      this.emit({ kind: "discovery-engaged", id, cls: via });
      this.emit({ kind: "telemetry", sample: { kind: "discovery-engaged", data: { id, cls: via } } });
    }
    this.focusId = id;
    this.trails.push({
      focusId: id,
      origin,
      via: via ? { cls: via } : undefined,
      sceneStamp: { semanticScale: this.semanticScale, lensId: this.lens.id },
    });
    this.emit({ kind: "focus-changed", id, origin });
    this.emit({ kind: "trail-changed", trail: this.trails.state() });
    this.requestScene();
  }

  back(): void {
    const step = this.trails.back();
    if (!step) return;
    this.focusId = step.focusId;
    this.emit({ kind: "focus-changed", id: step.focusId, origin: "history" });
    this.emit({ kind: "trail-changed", trail: this.trails.state() });
    this.emit({ kind: "telemetry", sample: { kind: "back", data: { id: step.focusId } } });
    this.requestScene();
  }

  forward(): void {
    const step = this.trails.forward();
    if (!step) return;
    this.focusId = step.focusId;
    this.emit({ kind: "focus-changed", id: step.focusId, origin: "history" });
    this.emit({ kind: "trail-changed", trail: this.trails.state() });
    this.emit({ kind: "telemetry", sample: { kind: "forward", data: { id: step.focusId } } });
    this.requestScene();
  }

  zoomTo(level: number): void {
    this.semanticScale = clampScale(level);
    const newBand = nextBand(this.band, this.semanticScale);
    const changed = newBand !== this.band;
    this.band = newBand;
    this.emit({ kind: "zoom-changed", semanticScale: this.semanticScale, band: this.band });
    if (changed) this.requestScene();
  }

  setLens(lens: AtlasLens): void {
    this.lens = lens;
    this.requestScene();
  }

  setLayout(layout: LayoutKind): void {
    if (this.layoutKind === layout) return;
    this.layoutKind = layout;
    if (this.scene) {
      // Re-layout the same scene — identical data, different geometry.
      this.applyScene(this.scene, this.stats?.sceneBuildMs ?? 0);
    }
  }

  pin(id: string): void {
    if (!this.trails.pinned.includes(id)) {
      this.trails.pinned.push(id);
      this.emit({ kind: "trail-changed", trail: this.trails.state() });
      this.requestScene();
    }
  }

  unpin(id: string): void {
    const i = this.trails.pinned.indexOf(id);
    if (i >= 0) {
      this.trails.pinned.splice(i, 1);
      this.emit({ kind: "trail-changed", trail: this.trails.state() });
      this.requestScene();
    }
  }

  compare(ids: string[]): void {
    const { shared, unique } = this.trails.compare(ids);
    this.selection = shared;
    this.emit({ kind: "selection-changed", ids: shared });
    this.emit({
      kind: "telemetry",
      sample: {
        kind: "compare",
        data: { branches: ids.join(","), shared: shared.length, unique: [...unique.values()].flat().length },
      },
    });
  }

  branch(fromStepId?: string): string {
    const id = this.trails.branch(fromStepId);
    this.emit({ kind: "trail-changed", trail: this.trails.state() });
    this.emit({ kind: "telemetry", sample: { kind: "branch", data: { id } } });
    return id;
  }

  switchBranch(branchId: string): void {
    if (!this.trails.switchTo(branchId)) return;
    const step = this.trails.currentStep;
    if (step) {
      this.focusId = step.focusId;
      this.emit({ kind: "focus-changed", id: step.focusId, origin: "history" });
      this.requestScene();
    }
    this.emit({ kind: "trail-changed", trail: this.trails.state() });
  }

  select(ids: string[], mode: "replace" | "add" | "toggle" = "replace"): void {
    if (mode === "replace") this.selection = [...ids];
    else if (mode === "add") this.selection = [...new Set([...this.selection, ...ids])];
    else {
      const set = new Set(this.selection);
      for (const id of ids) (set.has(id) ? set.delete(id) : set.add(id));
      this.selection = [...set];
    }
    this.emit({ kind: "selection-changed", ids: [...this.selection] });
  }

  openItem(id: string): void {
    this.emit({ kind: "item-open-requested", id });
  }

  hover(id: string | null): void {
    this.emit({ kind: "hover", id });
  }

  requestExplanation(request: ExplanationRequest): void {
    this.emit({ kind: "explanation-requested", request });
    this.emit({ kind: "telemetry", sample: { kind: "explanation", data: { kind: request.kind } } });
    this.source
      .getExplanation(request)
      .then((explanation) => {
        if (this.destroyed) return;
        this.emit({ kind: "explanation-ready", request, explanation });
      })
      .catch(() => undefined);
  }

  getState(): AtlasState {
    return {
      focusId: this.focusId,
      semanticScale: this.semanticScale,
      lens: this.lens,
      pinned: [...this.trails.pinned],
      selection: [...this.selection],
      trail: this.trails.state(),
      scene: this.scene
        ? {
            nodeCount: this.scene.nodes.length,
            aggregateCount: this.scene.aggregates.length,
            edgeCount: this.scene.edges.length,
          }
        : undefined,
      status: this.status,
      error: this.errorMsg,
    };
  }

  serializeTrail(): string {
    return this.trails.serialize();
  }

  restoreTrail(json: string): void {
    this.trails.restore(json);
    const step = this.trails.currentStep;
    if (step) {
      this.focusId = step.focusId;
      this.requestScene();
    }
    this.emit({ kind: "trail-changed", trail: this.trails.state() });
  }

  resize(w: number, h: number): void {
    if (w <= 0 || h <= 0) return;
    const changed = Math.abs(w - this.viewport.width) > 4 || Math.abs(h - this.viewport.height) > 4;
    this.viewport = { width: w, height: h };
    if (changed && this.scene) this.requestScene();
  }

  destroy(): void {
    this.destroyed = true;
    this.abortController?.abort();
    this.listeners.clear();
  }

  /** Kick off the first scene (host calls once after wiring events). */
  start(initialFocus?: string): void {
    if (initialFocus) this.focus(initialFocus, "system");
    else this.requestScene();
  }
}
