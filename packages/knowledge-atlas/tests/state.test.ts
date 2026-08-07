/** Trails, zoom hysteresis, layouts, hit testing, engine lifecycle. */

import { describe, expect, it } from "vitest";
import { Trails } from "../src/core/trails.ts";
import { nextBand } from "../src/core/zoom.ts";
import { HitTester } from "../src/core/hittest.ts";
import { AtlasEngine } from "../src/core/engine.ts";
import { focusLayout } from "../src/core/layout/focus.ts";
import { hyperbolicLayout } from "../src/core/layout/hyperbolic.ts";
import { forceLayout } from "../src/core/layout/force.ts";
import { classifyTopology } from "../src/core/layout/adaptive.ts";
import { buildScene } from "../src/core/scene/builder.ts";
import { indexFromCEData } from "../src/datasources/curiosity.ts";
import { workspaceSmallData, ontologyTree, workspaceSmall } from "../fixtures/index.ts";
import { ScaledDataSource, SCALED_TOTAL_LEAVES } from "../src/datasources/scaled.ts";
import { DEFAULT_BUDGET, DEFAULT_LENS, type SceneRequest } from "../src/core/types.ts";

const g = indexFromCEData(workspaceSmallData());
const req = (focusId: string): SceneRequest => ({
  focusId,
  lens: DEFAULT_LENS,
  viewport: { width: 1200, height: 800 },
  semanticScale: 2,
  budget: { ...DEFAULT_BUDGET },
});

describe("trails", () => {
  it("push/back/forward with cursor semantics", () => {
    const t = new Trails();
    t.push({ focusId: "a", origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    t.push({ focusId: "b", origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    t.push({ focusId: "c", origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    expect(t.back()?.focusId).toBe("b");
    expect(t.back()?.focusId).toBe("a");
    expect(t.forward()?.focusId).toBe("b");
  });

  it("preserves truncated forward steps as an auto-branch (no detour lost)", () => {
    const t = new Trails();
    for (const id of ["a", "b", "c", "d"]) {
      t.push({ focusId: id, origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    }
    t.back();
    t.back(); // cursor at "b"
    t.push({ focusId: "e", origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    const state = t.state();
    expect(state.branches.length).toBe(2);
    const detour = state.branches.find((b) => b.id !== state.activeBranchId)!;
    expect(detour.steps.map((s) => s.focusId)).toEqual(["c", "d"]);
  });

  it("branch + compare shared/unique", () => {
    const t = new Trails();
    for (const id of ["a", "b"]) t.push({ focusId: id, origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    const b1 = t.state().activeBranchId;
    const b2 = t.branch();
    t.push({ focusId: "x", origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    const { shared, unique } = t.compare([b1, b2]);
    expect(shared).toEqual(["a", "b"]);
    expect(unique.get(b2)).toEqual(["x"]);
  });

  it("serialise/restore round-trip", () => {
    const t = new Trails();
    for (const id of ["a", "b", "c"]) t.push({ focusId: id, origin: "user", sceneStamp: { semanticScale: 2, lensId: "default" } });
    t.pinned.push("b");
    t.back();
    const json = t.serialize();
    const t2 = new Trails();
    t2.restore(json);
    expect(t2.serialize()).toBe(json);
    expect(t2.currentStep?.focusId).toBe("b");
  });
});

describe("zoom hysteresis", () => {
  it("no flicker when oscillating around a boundary", () => {
    let band = 2;
    // Oscillate around 2.5: never crosses the ±0.75 threshold.
    for (const s of [2.5, 2.4, 2.6, 2.45, 2.55]) band = nextBand(band, s);
    expect(band).toBe(2);
  });
  it("commits after crossing the threshold", () => {
    expect(nextBand(2, 2.8)).toBe(3);
    expect(nextBand(2, 1.2)).toBe(1);
    expect(nextBand(2, 0.1)).toBe(0);
  });
});

describe("layouts", () => {
  const scene = buildScene(g, req("concepts/attention"), 42);
  const ctx = { viewport: { width: 1200, height: 800 }, seed: 42 };

  it("focus layout: focus at origin, all scene items placed, deterministic", () => {
    const a = focusLayout.layout(scene, ctx);
    const b = focusLayout.layout(scene, ctx);
    expect(a.positions.get("concepts/attention")).toEqual({ x: 0, y: 0, r: a.positions.get("concepts/attention")!.r });
    for (const n of scene.nodes) expect(a.positions.has(n.id), n.id).toBe(true);
    for (const agg of scene.aggregates) expect(a.positions.has(agg.id), agg.id).toBe(true);
    expect(JSON.stringify([...a.positions])).toBe(JSON.stringify([...b.positions]));
  });

  it("hyperbolic layout: same ids, radial compression inside the disc", () => {
    const h = hyperbolicLayout.layout(scene, ctx);
    const R = Math.min(1200, 800) * 0.47;
    for (const [id, p] of h.positions) {
      expect(Math.hypot(p.x, p.y), id).toBeLessThanOrEqual(R + 1);
    }
  });

  it("force layout: deterministic with identical input", () => {
    const a = forceLayout.layout(scene, ctx);
    const b = forceLayout.layout(scene, ctx);
    expect(JSON.stringify([...a.positions])).toBe(JSON.stringify([...b.positions]));
  });

  it("stable anchors: same type sector across different focuses", () => {
    const s1 = buildScene(g, req("concepts/attention"), 42);
    const s2 = buildScene(g, req("concepts/gradient-descent"), 42);
    const l1 = focusLayout.layout(s1, ctx);
    const l2 = focusLayout.layout(s2, { ...ctx, previous: l1 });
    // A shared non-focus node keeps its angular sector (within the fan).
    const sharedIds = s1.nodes
      .filter((n) => n.role !== "focus" && s2.nodes.some((m) => m.id === n.id && m.role !== "focus"))
      .map((n) => n.id);
    let checked = 0;
    for (const id of sharedIds) {
      const p1 = l1.positions.get(id)!;
      const p2 = l2.positions.get(id)!;
      const a1 = Math.atan2(p1.y, p1.x);
      const a2 = Math.atan2(p2.y, p2.x);
      const diff = Math.abs(Math.atan2(Math.sin(a1 - a2), Math.cos(a1 - a2)));
      if (diff < 1.0) checked++;
    }
    if (sharedIds.length) {
      expect(checked / sharedIds.length).toBeGreaterThan(0.6);
    }
  });
});

describe("hybrid layout (P6)", () => {
  const scene = buildScene(g, req("concepts/attention"), 42);
  const ctx = { viewport: { width: 1200, height: 800 }, seed: 42 };

  it("keeps focus inside the core, CORE_SHARE of plain nodes in it, rest on the rim", async () => {
    const { hybridLayout, coreRadius, CORE_SHARE } = await import("../src/core/layout/hybrid.ts");
    const l = hybridLayout.layout(scene, ctx);
    const rCore = coreRadius(ctx.viewport);
    // The focus is no longer pinned at the origin (network look, not a
    // radial star) but must stay inside the core zone.
    const focusP = l.positions.get("concepts/attention")!;
    expect(Math.hypot(focusP.x, focusP.y)).toBeLessThan(rCore);

    const horizonIds = new Set(scene.horizon.flatMap((h) => h.candidates.map((c) => c.id)));
    const plain = scene.nodes.filter((n) => n.role !== "focus" && !horizonIds.has(n.id));
    const inCore = plain.filter((n) => {
      const p = l.positions.get(n.id)!;
      return Math.hypot(p.x, p.y) <= rCore;
    });
    const expected = Math.ceil(plain.length * CORE_SHARE);
    expect(inCore.length).toBeGreaterThanOrEqual(Math.floor(expected * 0.95));
    expect(inCore.length).toBeLessThanOrEqual(expected + 1);

    // Aggregates and horizon candidates live on the rim.
    for (const a of scene.aggregates) {
      const p = l.positions.get(a.id)!;
      expect(Math.hypot(p.x, p.y), a.id).toBeGreaterThan(rCore);
    }
    for (const id of horizonIds) {
      const p = l.positions.get(id);
      if (p) expect(Math.hypot(p.x, p.y), id).toBeGreaterThan(rCore);
    }
  });

  it("is deterministic", async () => {
    const { hybridLayout } = await import("../src/core/layout/hybrid.ts");
    const a = hybridLayout.layout(scene, ctx);
    const b = hybridLayout.layout(scene, ctx);
    expect(JSON.stringify([...a.positions])).toBe(JSON.stringify([...b.positions]));
  });
});

describe("adaptive-hybrid layout (P7)", () => {
  const ctx = { viewport: { width: 1200, height: 800 }, seed: 42 };

  it("tree-like core becomes columnar (gridlike): few distinct x values", async () => {
    const { adaptiveHybridLayout } = await import("../src/core/layout/adaptiveHybrid.ts");
    const { partitionCore } = await import("../src/core/layout/hybrid.ts");
    const f = ontologyTree();
    const scene = await f.source.getScene(req(f.defaultFocus));
    const l = adaptiveHybridLayout.layout(scene, ctx);
    const coreSet = partitionCore(scene);
    const xs = new Set(
      [...coreSet]
        .map((id) => l.positions.get(id))
        .filter((p): p is NonNullable<typeof p> => !!p)
        .map((p) => Math.round(p.x)),
    );
    // Columns: focus + ≤3 hop bands => at most 4 distinct x positions.
    expect(xs.size).toBeLessThanOrEqual(4);
    // Every scene item is still placed (rim included).
    for (const n of scene.nodes) expect(l.positions.has(n.id), n.id).toBe(true);
    for (const a of scene.aggregates) expect(l.positions.has(a.id), a.id).toBe(true);
  });

  it("meshy core falls back to the hybrid force cloud", async () => {
    const { adaptiveHybridLayout } = await import("../src/core/layout/adaptiveHybrid.ts");
    const { hybridLayout } = await import("../src/core/layout/hybrid.ts");
    const scene = buildScene(g, req("concepts/attention"), 42);
    const a = adaptiveHybridLayout.layout(scene, ctx);
    const b = hybridLayout.layout(scene, ctx);
    expect(JSON.stringify([...a.positions])).toBe(JSON.stringify([...b.positions]));
  });

  it("is deterministic", async () => {
    const { adaptiveHybridLayout } = await import("../src/core/layout/adaptiveHybrid.ts");
    const f = ontologyTree();
    const scene = await f.source.getScene(req(f.defaultFocus));
    const a = adaptiveHybridLayout.layout(scene, ctx);
    const b = adaptiveHybridLayout.layout(scene, ctx);
    expect(JSON.stringify([...a.positions])).toBe(JSON.stringify([...b.positions]));
  });
});

describe("lens", () => {
  it("pull moves only the targeted rim sector inward; commit focuses its dominant item", async () => {
    const { hybridLayout, coreRadius } = await import("../src/core/layout/hybrid.ts");
    const { applyLens, commitLensTarget } = await import("../src/interaction/lens.ts");
    const { AtlasEngine } = await import("../src/core/engine.ts");
    const scene = buildScene(g, req("concepts/attention"), 42);
    const ctx = { viewport: { width: 1200, height: 800 }, seed: 42 };
    const layout = hybridLayout.layout(scene, ctx);
    const rCore = coreRadius(ctx.viewport);

    // Pick a real rim item's angle as the lens direction.
    const rimEntry = [...layout.positions.entries()].find(([, p]) => Math.hypot(p.x, p.y) > rCore * 1.3)!;
    const angle = Math.atan2(rimEntry[1].y, rimEntry[1].x);
    const displaced = applyLens(layout, { pull: 0.8, angle }, rCore);
    const before = Math.hypot(rimEntry[1].x, rimEntry[1].y);
    const afterP = displaced.positions.get(rimEntry[0])!;
    expect(Math.hypot(afterP.x, afterP.y)).toBeLessThan(before);
    // The opposite sector is untouched.
    const opposite = [...layout.positions.entries()].find(([, p]) => {
      const rho = Math.hypot(p.x, p.y);
      const d = Math.abs(Math.atan2(Math.sin(Math.atan2(p.y, p.x) - (angle + Math.PI)), Math.cos(Math.atan2(p.y, p.x) - (angle + Math.PI))));
      return rho > rCore * 1.3 && d < 0.3;
    });
    if (opposite) {
      expect(displaced.positions.get(opposite[0])).toEqual(opposite[1]);
    }

    // Commit against a live engine: focus changes to something.
    const f = workspaceSmall();
    const engine = new AtlasEngine(f.source, { seed: 42, layout: "hybrid" });
    engine.resize(1200, 800);
    engine.start("concepts/attention");
    await new Promise((r) => setTimeout(r, 60));
    const beforeFocus = engine.getState().focusId;
    const ok = commitLensTarget(engine, { pull: 1, angle }, rCore);
    expect(ok).toBe(true);
    expect(engine.getState().focusId).not.toBe(beforeFocus);
    engine.destroy();
  });
});

describe("adaptive topology classifier", () => {
  it("classifies the tree fixture as tree-like", async () => {
    const f = ontologyTree();
    const scene = await f.source.getScene(req(f.defaultFocus));
    expect(["tree", "chain", "mixed"]).toContain(classifyTopology(scene));
  });
});

describe("hit testing", () => {
  it("point and directional queries", () => {
    const ht = new HitTester();
    const positions = new Map([
      ["a", { x: 0, y: 0, r: 10 }],
      ["b", { x: 100, y: 0, r: 10 }],
      ["c", { x: 0, y: 100, r: 10 }],
    ]);
    ht.update(positions, ["a", "b", "c"], []);
    expect(ht.pointAt(2, 2)?.id).toBe("a");
    expect(ht.pointAt(500, 500)).toBeNull();
    expect(ht.nearestInDirection("a", "right")).toBe("b");
    expect(ht.nearestInDirection("a", "down")).toBe("c");
    expect(ht.nearestInDirection("a", "left")).toBeNull();
  });
});

describe("engine", () => {
  it("focus/back/forward drive scenes; stale requests dropped", async () => {
    const f = workspaceSmall();
    const engine = new AtlasEngine(f.source, { seed: 42 });
    const events: string[] = [];
    engine.on((e) => events.push(e.kind));
    engine.resize(1200, 800);
    engine.start("concepts/attention");
    // Rapid re-focus: only the last request may land.
    engine.focus("concepts/transformers");
    engine.focus("concepts/embeddings");
    await new Promise((r) => setTimeout(r, 50));
    const state = engine.getState();
    expect(state.focusId).toBe("concepts/embeddings");
    expect(state.scene?.nodeCount ?? 0).toBeGreaterThan(0);
    engine.back();
    await new Promise((r) => setTimeout(r, 30));
    expect(engine.getState().focusId).toBe("concepts/transformers");
    expect(events).toContain("scene-ready");
    expect(events).toContain("trail-changed");
    engine.destroy();
  });

  it("discovery-engaged fires when focusing a horizon candidate", async () => {
    const f = workspaceSmall();
    const engine = new AtlasEngine(f.source, { seed: 42 });
    engine.resize(1200, 800);
    engine.start("concepts/attention");
    await new Promise((r) => setTimeout(r, 50));
    const scene = engine.snapshot().scene!;
    const candidate = scene.horizon[0]?.candidates[0];
    expect(candidate).toBeTruthy();
    let engaged: string | null = null;
    engine.on((e) => {
      if (e.kind === "discovery-engaged") engaged = e.cls;
    });
    engine.focus(candidate!.id);
    expect(engaged).toBe(scene.horizon[0].cls);
    engine.destroy();
  });
});

describe("scaled source (P4)", () => {
  it("serves bounded scenes from a million-leaf corpus", async () => {
    const s = new ScaledDataSource({ seed: 42 });
    expect(SCALED_TOTAL_LEAVES).toBe(1_000_000);
    const t0 = performance.now();
    const scene = await s.getScene(req("s:7.42.13"));
    const ms = performance.now() - t0;
    expect(scene.nodes.length).toBeLessThanOrEqual(DEFAULT_BUDGET.maxNodes);
    expect(scene.edges.length).toBeLessThanOrEqual(DEFAULT_BUDGET.maxEdges);
    expect(scene.stats?.totalNodes).toBe(1_000_000);
    expect(ms).toBeLessThan(250); // bounded build latency
  });

  it("abort is honoured under latency", async () => {
    const s = new ScaledDataSource({ seed: 42, latencyMs: 100 });
    const ac = new AbortController();
    const p = s.getScene(req("s:1.2.3"), ac.signal);
    ac.abort();
    await expect(p).rejects.toThrow();
  });

  it("every generated id is a valid corpus path (no negative indices)", async () => {
    const s = new ScaledDataSource({ seed: 42 });
    const scene = await s.getScene(req("s:7.42.13"));
    for (const n of scene.nodes) {
      expect(await s.getItem(n.id), n.id).not.toBeNull();
    }
  });

  it("deterministic neighbourhoods", async () => {
    const a = await new ScaledDataSource({ seed: 9 }).getScene(req("s:3.14.15"));
    const b = await new ScaledDataSource({ seed: 9 }).getScene(req("s:3.14.15"));
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});
