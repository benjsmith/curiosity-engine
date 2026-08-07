/** Scene pipeline: budgets, ranking invariants, discovery, aggregation. */

import { describe, expect, it } from "vitest";
import { buildScene } from "../src/core/scene/builder.ts";
import { hubPenalty } from "../src/core/scene/ranking.ts";
import { indexFromCEData } from "../src/datasources/curiosity.ts";
import { workspaceSmallData, denseSmallWorld, allFixtures } from "../fixtures/index.ts";
import { DEFAULT_BUDGET, DEFAULT_LENS, type SceneRequest } from "../src/core/types.ts";
import { GraphIndex } from "../src/core/graphindex.ts";
import { LocalSceneSource } from "../src/datasources/local.ts";

const g = indexFromCEData(workspaceSmallData());

function req(overrides: Partial<SceneRequest> = {}): SceneRequest {
  return {
    focusId: "concepts/attention",
    lens: DEFAULT_LENS,
    viewport: { width: 1200, height: 800 },
    semanticScale: 2,
    // Pipeline tests exercise ranking/aggregation/discovery logic at
    // the original scene scale; cosmological capacity has its own
    // tests in state.test.ts.
    coreCapacity: 90,
    budget: { ...DEFAULT_BUDGET, maxNodes: 110 },
    ...overrides,
  };
}

describe("budgets", () => {
  it("never exceeds the budget on any fixture/seed", async () => {
    for (const seed of [1, 42, 99]) {
      for (const f of allFixtures(seed)) {
        const scene = await f.source.getScene(
          req({ focusId: f.defaultFocus, budget: { maxNodes: 30, maxAggregates: 6, maxEdges: 40, maxBundles: 8, maxLabels: 20 } }),
        );
        expect(scene.nodes.length, f.name).toBeLessThanOrEqual(30);
        expect(scene.aggregates.length, f.name).toBeLessThanOrEqual(6);
        expect(scene.edges.length, f.name).toBeLessThanOrEqual(40);
        expect(scene.bundles.length, f.name).toBeLessThanOrEqual(8);
      }
    }
  });

  it("tiny budgets still include the focus", () => {
    const scene = buildScene(g, req({ budget: { maxNodes: 3, maxAggregates: 2, maxEdges: 5, maxBundles: 2, maxLabels: 2 } }), 42);
    expect(scene.nodes.some((n) => n.role === "focus")).toBe(true);
    expect(scene.nodes.length).toBeLessThanOrEqual(3 + 2); // + horizon min reserve
  });
});

describe("ranking invariants", () => {
  it("hub penalty is monotonically non-increasing in degree", () => {
    let prev = Infinity;
    for (const d of [1, 10, 30, 60, 120, 500]) {
      const p = hubPenalty(d);
      expect(p).toBeLessThanOrEqual(prev);
      prev = p;
    }
  });

  it("the generic hub is not promoted over specific neighbours", () => {
    const scene = buildScene(g, req(), 42);
    const hub = scene.nodes.find((n) => n.id === "concepts/machine-learning");
    // The hub may appear (it IS connected) but never as top-3 by score.
    const top3 = [...scene.nodes]
      .filter((n) => n.role !== "focus")
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      .map((n) => n.id);
    expect(top3).not.toContain("concepts/machine-learning");
    // And degree alone never beats a well-sourced close neighbour:
    if (hub) expect(hub.score).toBeLessThan(Math.max(...scene.nodes.filter((n) => n.ring === 1 && n.id !== hub.id).map((n) => n.score)));
  });
});

describe("aggregation", () => {
  it("groups by explicit type, stable ids, residual counts", () => {
    const scene = buildScene(g, req(), 42);
    for (const a of scene.aggregates) {
      expect(a.id).toMatch(/^agg:/);
      expect(a.count).toBeGreaterThanOrEqual(a.memberIds.length);
      expect(a.memberIds.length).toBeLessThanOrEqual(8);
    }
    // Same request twice -> identical aggregate ids (stability).
    const scene2 = buildScene(g, req(), 42);
    expect(scene2.aggregates.map((a) => a.id)).toEqual(scene.aggregates.map((a) => a.id));
  });

  it("transitionMap covers every aggregate", () => {
    const scene = buildScene(g, req(), 42);
    for (const a of scene.aggregates) {
      expect(scene.transitionMap?.[a.id]).toEqual(a.memberIds);
    }
  });
});

describe("discovery horizon", () => {
  const scene = buildScene(g, req(), 42);

  it("reserves budget and produces multiple distinct classes", () => {
    expect(scene.horizon.length).toBeGreaterThanOrEqual(3);
    const classes = new Set(scene.horizon.map((h) => h.cls));
    expect(classes.size).toBe(scene.horizon.length); // no duplicate classes
  });

  it("every candidate carries a reason", () => {
    for (const grp of scene.horizon) {
      for (const c of grp.candidates) {
        expect(c.reason.text.length, `${grp.cls}:${c.id}`).toBeGreaterThan(10);
        expect(c.reason.kind).toBe(grp.cls);
      }
    }
  });

  it("surfaces the planted contrast pair (contrast shelf or visible)", () => {
    const contrastScene = buildScene(g, req({ focusId: "analyses/scaling-helps" }), 42);
    const contrast = contrastScene.horizon.find((h) => h.cls === "contrast");
    const inContrast =
      contrast?.candidates.some((c) => c.id === "analyses/scaling-hurts-downstream") ?? false;
    const inScene = contrastScene.nodes.some((n) => n.id === "analyses/scaling-hurts-downstream");
    expect(inContrast || inScene).toBe(true);
  });

  it("surfaces the planted hidden connection (scene or horizon)", () => {
    // The adapter derives a co-cited edge from the 3 shared sources, so
    // the hidden partner may arrive as a visible hop-1 neighbour — that
    // counts: the connection a file browser would never show is on
    // screen. If it isn't selected, it must appear as a contrast
    // candidate instead. Either way it is surfaced and explainable.
    const s = buildScene(g, req({ focusId: "evidence/ml-hidden-a" }), 42);
    const inScene = s.nodes.some((n) => n.id === "evidence/ml-hidden-b");
    const contrast = s.horizon.find((h) => h.cls === "contrast");
    const inContrast = contrast?.candidates.some((c) => c.id === "evidence/ml-hidden-b") ?? false;
    expect(inScene || inContrast).toBe(true);
    expect(g.sharedSources("evidence/ml-hidden-a", "evidence/ml-hidden-b").length).toBe(3);
  });

  it("reports omitted counts instead of silently truncating", () => {
    const total = scene.horizon.reduce((s, h) => s + h.candidates.length + h.omittedCount, 0);
    const shown = scene.horizon.reduce((s, h) => s + h.candidates.length, 0);
    expect(total).toBeGreaterThanOrEqual(shown);
    expect(scene.stats?.omitted?.length ?? 0).toBeGreaterThan(0);
  });

  it("discovery quota responds to the lens mix", () => {
    const surpriseHeavy = buildScene(
      g,
      req({ lens: { id: "s", discoveryMix: { surprise: 0.9, direct: 0.02, adjacent: 0.02, bridge: 0.02, contrast: 0.02, unexplored: 0.02 } } }),
      42,
    );
    const surprise = surpriseHeavy.horizon.find((h) => h.cls === "surprise");
    const direct = surpriseHeavy.horizon.find((h) => h.cls === "direct");
    if (surprise && direct) {
      expect(surprise.candidates.length).toBeGreaterThanOrEqual(direct.candidates.length);
    }
  });
});

describe("landmarks", () => {
  it("exposes explicit types, no communities by default", () => {
    const scene = buildScene(g, req(), 42);
    expect(scene.landmarks.some((l) => l.kind === "type")).toBe(true);
    expect(scene.landmarks.some((l) => l.kind === "community")).toBe(false);
  });
});

describe("edge tiers", () => {
  it("focus edges outrank the rest and fit the budget", () => {
    const scene = buildScene(g, req({ budget: { ...DEFAULT_BUDGET, maxEdges: 10 } }), 42);
    expect(scene.edges.length).toBeLessThanOrEqual(10);
    // With a squeezed budget, surviving edges are dominated by tier 1.
    const tier1 = scene.edges.filter((e) => e.priority === 1).length;
    expect(tier1).toBeGreaterThan(0);
  });
});

describe("determinism", () => {
  it("same seed => identical scene JSON", () => {
    const a = buildScene(g, req(), 42);
    const b = buildScene(g, req(), 42);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});

describe("hub-only graphs", () => {
  it("degree is never the sole positive factor", () => {
    // Construct: hub with 100 neighbours + one well-sourced specific
    // node; from a probe, both at hop 1. The specific one must rank higher.
    const gh = new GraphIndex();
    const item = (id: string, type: string, sources: string[] = []) =>
      gh.addItem({ id, type, title: id, meta: { sources } });
    item("probe", "concept");
    item("hub", "concept", []);
    item("specific", "evidence", ["s1", "s2", "s3"]);
    gh.addEdge("probe", "hub", "wikilink");
    gh.addEdge("probe", "specific", "wikilink");
    for (let i = 0; i < 100; i++) {
      item(`n${i}`, "fact");
      gh.addEdge("hub", `n${i}`, "wikilink");
    }
    const src = new LocalSceneSource(gh, { seed: 1 });
    void src;
    const scene = buildScene(gh, req({ focusId: "probe" }), 1);
    const hub = scene.nodes.find((n) => n.id === "hub");
    const specific = scene.nodes.find((n) => n.id === "specific");
    expect(specific).toBeTruthy();
    if (hub && specific) expect(specific.score).toBeGreaterThan(hub.score);
  });
});

describe("dense small-world stress", () => {
  it("keeps scenes bounded despite hubs", async () => {
    const f = denseSmallWorld();
    const scene = await f.source.getScene(req({ focusId: f.defaultFocus }));
    expect(scene.nodes.length).toBeLessThanOrEqual(DEFAULT_BUDGET.maxNodes);
    expect(scene.edges.length).toBeLessThanOrEqual(DEFAULT_BUDGET.maxEdges);
  });
});
