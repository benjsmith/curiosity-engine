/**
 * Iteration-9: screen-density core capacity, the zoom coupling, and
 * the flat-centre lensing halo near the core boundary.
 */

import { describe, expect, it } from "vitest";
import {
  EXPERIMENTAL_MAX_CORE_CAPACITY,
  MAX_CORE_CAPACITY,
  MIN_CORE_CAPACITY,
  coreCapacityFor,
  shellCount,
  shellOfRank,
  viewScaleToFit,
} from "../src/core/scene/shells.ts";
import { buildScene } from "../src/core/scene/builder.ts";
import { hybridLayout, populatedShellBands } from "../src/core/layout/hybrid.ts";
import { nodeRadius } from "../src/core/layout/types.ts";
import { coreArea, coreRadiusAt, rimRadiusAt, shapeExponent } from "../src/core/geometry.ts";
import { indexFromCEData } from "../src/datasources/curiosity.ts";
import { workspaceSmallData } from "../fixtures/index.ts";
import { absorbTowardType, applyCorePan, commitLensTarget, ABSORB_CAP } from "../src/interaction/lens.ts";
import type { AtlasEngine } from "../src/core/engine.ts";
import { DEFAULT_BUDGET, DEFAULT_LENS, type SceneRequest } from "../src/core/types.ts";
import { projectCamera, responsiveNodeScale, wheelZoomFactor } from "../src/interaction/camera.ts";
import { boundaryHoverDelay, projectedBoundaryDepth } from "../src/interaction/hover.ts";

describe("viewScaleToFit", () => {
  it("zooms out so a 2k wiki fits as one full graph on a desktop", () => {
    const vp = { width: 1280, height: 800 };
    const s = viewScaleToFit(2000, vp);
    expect(s).toBeLessThan(1);
    expect(coreCapacityFor(vp, s, 0)).toBeGreaterThanOrEqual(2000);
  });

  it("stays at 1 when the wiki already fits", () => {
    expect(viewScaleToFit(80, { width: 1280, height: 800 })).toBe(1);
  });
});

describe("coreCapacityFor (legible density)", () => {
  it("scales with the CORE's screen area: phone ~50, desktop ~250, big screens more", () => {
    const phone = coreCapacityFor({ width: 390, height: 450 });
    const desktop = coreCapacityFor({ width: 1280, height: 800 });
    const wall = coreCapacityFor({ width: 2560, height: 1440 });
    expect(phone).toBeGreaterThanOrEqual(MIN_CORE_CAPACITY);
    expect(phone).toBeLessThan(80);
    expect(desktop).toBeGreaterThan(220);
    expect(desktop).toBeLessThan(300);
    expect(wall).toBeGreaterThan(1000);
    expect(wall).toBeLessThanOrEqual(MAX_CORE_CAPACITY);
    // Full-graph mode (bands = 0) fills the WHOLE viewport — the
    // classic 360 on a desktop.
    const full = coreCapacityFor({ width: 1280, height: 800 }, 1, 0);
    expect(full).toBeGreaterThan(330);
    expect(full).toBeLessThan(390);
  });

  it("zoom-out raises capacity (smaller nodes → more fit), zoom-in lowers it", () => {
    const v = { width: 1280, height: 800 };
    const base = coreCapacityFor(v, 1);
    expect(coreCapacityFor(v, 0.7)).toBeGreaterThan(base * 1.5);
    expect(coreCapacityFor(v, 1.5)).toBeLessThan(base * 0.6);
    // Production zoom-out reaches the 10k display envelope and stops.
    expect(coreCapacityFor(v, 0.1)).toBe(MAX_CORE_CAPACITY);
    expect(coreCapacityFor(v, 10)).toBe(coreCapacityFor(v, 4));
    // 100k is an explicit experimental opt-in, never the default.
    expect(coreCapacityFor({ width: 8000, height: 8000 }, 0.1, 0, undefined, EXPERIMENTAL_MAX_CORE_CAPACITY))
      .toBe(EXPERIMENTAL_MAX_CORE_CAPACITY);
  });

  it("never leaves the legibility clamp", () => {
    expect(coreCapacityFor({ width: 100, height: 100 })).toBe(MIN_CORE_CAPACITY);
    expect(coreCapacityFor({ width: 8000, height: 8000 })).toBe(MAX_CORE_CAPACITY);
  });
});

describe("receding milestone shells", () => {
  it("drops the 1k and 10k layers as geometric zoom absorbs them", () => {
    expect(shellCount(80_000, 360)).toBe(3);
    expect(shellCount(80_000, 1_200)).toBe(2);
    expect(shellCount(80_000, 12_000)).toBe(1);
    expect(shellOfRank(9_000, 1_200)).toBe(1);
    expect(shellOfRank(20_000, 12_000)).toBe(1);
  });
});

describe("relational boundary placement", () => {
  it("does not use document type as a spatial coordinate", () => {
    const item = (id: string) => ({ id, type: "same-type", title: id, meta: {} });
    const boundaryScene = {
      focus: item("left"),
      nodes: [
        { id: "left", item: item("left"), role: "focus", score: 2 },
        { id: "right", item: item("right"), role: "context", score: 1 },
      ],
      aggregates: [
        { id: "agg-left", label: "left group", type: "same-type", count: 10, memberIds: [], residual: 10, shell: 1 },
        { id: "agg-right", label: "right group", type: "same-type", count: 10, memberIds: [], residual: 10, shell: 1 },
      ],
      edges: [],
      bundles: [
        { id: "b-left", source: "agg-left", target: "left", type: "related", count: 3 },
        { id: "b-right", source: "agg-right", target: "right", type: "related", count: 3 },
      ],
      horizon: [],
      landmarks: [],
    } as unknown as Parameters<typeof hybridLayout.layout>[0];
    const previous = {
      positions: new Map([
        ["left", { x: -90, y: 0, r: 8 }],
        ["right", { x: 90, y: 0, r: 8 }],
      ]),
      displacement: 0,
    };
    const placed = hybridLayout.layout(boundaryScene, { viewport, seed: 42, previous });
    const left = placed.positions.get("agg-left")!;
    const right = placed.positions.get("agg-right")!;
    expect(left.x).toBeLessThan(0);
    expect(right.x).toBeGreaterThan(0);
  });
});

describe("fixed-boundary camera", () => {
  it("uses a cancellable reading-speed dwell that rises with depth and corpus density", () => {
    const near = boundaryHoverDelay(0, 383);
    const denseNear = boundaryHoverDelay(0, 1_000_000);
    const denseFar = boundaryHoverDelay(1, 1_000_000);
    expect(near).toBeGreaterThanOrEqual(45);
    expect(near).toBeLessThanOrEqual(50);
    expect(denseNear).toBeGreaterThan(near);
    expect(denseFar).toBeGreaterThan(denseNear);
    expect(denseFar).toBeLessThanOrEqual(110);
  });

  it("measures hover depth in projected screen geography", () => {
    const v = { width: 1000, height: 700 };
    const angle = 0;
    const core = coreRadiusAt(angle, v, 1);
    const rim = rimRadiusAt(angle, v);
    expect(projectedBoundaryDepth({ x: core, y: 0, r: 2 }, v)).toBeCloseTo(0, 5);
    expect(projectedBoundaryDepth({ x: rim, y: 0, r: 2 }, v)).toBeCloseTo(1, 5);
  });

  it("maps wheel deltas continuously at lower gain", () => {
    expect(wheelZoomFactor(0)).toBe(1);
    expect(wheelZoomFactor(2)).toBeGreaterThan(0.998);
    expect(wheelZoomFactor(100)).toBeGreaterThan(0.94);
    expect(wheelZoomFactor(100)).toBeLessThan(1);
    expect(wheelZoomFactor(-100)).toBeCloseTo(1 / wheelZoomFactor(100), 6);
  });

  it("zooms core nodes while shell geography stays fixed", () => {
    const mini = {
      nodes: [
        { id: "core", shell: undefined },
        { id: "rim", shell: 1 },
      ],
      aggregates: [{ id: "agg" }],
    } as unknown as Parameters<typeof projectCamera>[1];
    const raw = {
      positions: new Map([
        ["core", { x: 10, y: 20, r: 8 }],
        ["rim", { x: 100, y: 120, r: 5 }],
        ["agg", { x: 140, y: 150, r: 12 }],
      ]),
      displacement: 0,
    };
    const out = projectCamera(raw, mini, { x: 3, y: -2, scale: 2 }, true, 0.75);
    expect(out.positions.get("core")).toEqual({ x: 23, y: 38, r: 12 });
    expect(out.positions.get("rim")).toEqual({ x: 100, y: 120, r: 3.75 });
    expect(out.positions.get("agg")).toEqual({ x: 140, y: 150, r: 12 });
  });

  it("uses smaller default nodes on a phone viewport", () => {
    expect(responsiveNodeScale({ width: 390, height: 844 })).toBe(0.62);
    expect(responsiveNodeScale({ width: 1280, height: 800 })).toBe(1);
  });

  it("warps a resident full graph at the rim without breaking reversible pan", () => {
    const mini = {
      nodes: [
        { id: "centre", shell: undefined },
        { id: "far", shell: undefined },
      ],
      aggregates: [],
    } as unknown as Parameters<typeof projectCamera>[1];
    const raw = {
      positions: new Map([
        ["centre", { x: 0, y: 0, r: 8 }],
        ["far", { x: 900, y: 0, r: 8 }],
      ]),
      displacement: 0,
    };
    const camera = { x: 0, y: 0, scale: 1 };
    const before = projectCamera(raw, mini, camera, false, 1, { width: 800, height: 600 }, undefined, true);
    expect(before.boundaryIds?.has("far")).toBe(true);
    expect(before.positions.get("far")!.r).toBeLessThan(before.positions.get("centre")!.r * 0.4);
    camera.x += 73;
    projectCamera(raw, mini, camera, false, 1, { width: 800, height: 600 }, undefined, true);
    camera.x -= 73;
    const returned = projectCamera(raw, mini, camera, false, 1, { width: 800, height: 600 }, undefined, true);
    expect(returned.positions.get("centre")).toEqual(before.positions.get("centre"));
    expect(returned.positions.get("far")).toEqual(before.positions.get("far"));
  });
});

// Shared scene: workspace-small (~420 items) exceeds the core
// capacity, so it has a real boundary for the halo and shell tests.
const viewport = { width: 1200, height: 800 };
const g = indexFromCEData(workspaceSmallData());
const req: SceneRequest = {
  focusId: "concepts/attention",
  lens: DEFAULT_LENS,
  viewport,
  semanticScale: 2,
  budget: { ...DEFAULT_BUDGET },
};
const scene = buildScene(g, req, 42);
const layout = hybridLayout.layout(scene, { viewport, seed: 42 });
const BANDS = populatedShellBands(scene);
/** Halo normalisation: the core boundary at a point's own bearing. */
const coreLimAt = (x: number, y: number) => coreRadiusAt(Math.atan2(y, x), viewport, BANDS);

describe("boundaryShape (iteration-12: circle ↔ near-rectangle)", () => {
  const square = { width: 800, height: 800 };

  it("maps 0 → circle family, 1 → near-rectangle", () => {
    expect(shapeExponent(0)).toBe(2);
    expect(shapeExponent(1)).toBe(16);
    expect(shapeExponent(-3)).toBe(2); // clamped
    expect(shapeExponent(9)).toBe(16);
  });

  it("shape 0 on a square region is a perfect circle", () => {
    const r0 = rimRadiusAt(0, square, 0);
    for (const th of [0.3, Math.PI / 4, 1.1, 2.5]) {
      expect(rimRadiusAt(th, square, 0)).toBeCloseTo(r0, 6);
    }
  });

  it("shape 1 reaches almost into the corner; shape 0 does not", () => {
    const axis = rimRadiusAt(0, square, 1);
    const cornerFull = Math.hypot(axis, axis); // the true rectangle corner
    const diagSquare = rimRadiusAt(Math.PI / 4, square, 1);
    const diagCircle = rimRadiusAt(Math.PI / 4, square, 0);
    expect(diagSquare / cornerFull).toBeGreaterThan(0.93); // slightly rounded
    expect(diagCircle / cornerFull).toBeLessThan(0.72); // ≈ 1/√2
  });

  it("squarer shapes hold more points (capacity follows area)", () => {
    const v = { width: 1280, height: 800 };
    expect(coreArea(v, 1, 1)).toBeGreaterThan(coreArea(v, 1, 0.19));
    expect(coreArea(v, 1, 0.19)).toBeGreaterThan(coreArea(v, 1, 0));
    expect(coreCapacityFor(v, 1, 1, 1)).toBeGreaterThan(coreCapacityFor(v, 1, 1, 0));
  });

  it("the core boundary follows the same shape parameter", () => {
    const diagSquare = coreRadiusAt(Math.PI / 4, square, 1, 1);
    const diagCircle = coreRadiusAt(Math.PI / 4, square, 1, 0);
    expect(diagSquare).toBeGreaterThan(diagCircle * 1.25);
  });
});

describe("lensing halo (flat middle, eased edge)", () => {

  it("keeps classic degree-based radii in the flat middle", () => {
    let flatChecked = 0;
    for (const n of scene.nodes) {
      if (n.shell) continue;
      const p = layout.positions.get(n.id);
      if (!p) continue;
      const t = Math.hypot(p.x, p.y) / coreLimAt(p.x, p.y);
      if (t <= 0.6) {
        expect(p.r).toBeCloseTo(nodeRadius(n.item.meta.degree), 5);
        flatChecked++;
      }
    }
    expect(flatChecked).toBeGreaterThan(3);
  });

  it("eases node scale down approaching the boundary — never below 60%", () => {
    let edgeChecked = 0;
    for (const n of scene.nodes) {
      if (n.shell) continue;
      const p = layout.positions.get(n.id);
      if (!p) continue;
      const t = Math.hypot(p.x, p.y) / coreLimAt(p.x, p.y);
      if (t > 0.8) {
        const base = nodeRadius(n.item.meta.degree);
        expect(p.r).toBeLessThan(base);
        expect(p.r).toBeGreaterThan(base * 0.6);
        edgeChecked++;
      }
    }
    expect(edgeChecked).toBeGreaterThan(0);
  });

  it("halo does not compound across survivor re-layouts", () => {
    // Grade-1 refocuses inherit positions. If the halo re-compressed
    // them each pass, five re-layouts would shrink edge nodes to
    // ~0.5×; sizes and positions must stay put instead (settling
    // jitter of a px or two is fine).
    let prev = layout;
    for (let i = 0; i < 5; i++) {
      prev = hybridLayout.layout(scene, { viewport, seed: 42, previous: prev });
    }
    let moved = 0;
    let count = 0;
    for (const n of scene.nodes) {
      if (n.shell) continue;
      const a = layout.positions.get(n.id);
      const b = prev.positions.get(n.id);
      if (!a || !b) continue;
      // Idempotence: r is a pure function of the node's CURRENT
      // position (base radius × halo falloff), no matter how many
      // survivor passes ran — compounding would undershoot this.
      const t = Math.hypot(b.x, b.y) / coreLimAt(b.x, b.y);
      const u = Math.max(0, Math.min(1, (t - 0.62) / (1 - 0.62)));
      const expected = nodeRadius(n.item.meta.degree) * (1 - 0.38 * u * u);
      expect(b.r).toBeCloseTo(expected, 1);
      moved += Math.hypot(b.x - a.x, b.y - a.y);
      count++;
    }
    // Collide-settle jitter only: five passes stay inside the same
    // mean bound the stability test allows for a single grade-1 click.
    expect(moved / Math.max(1, count)).toBeLessThan(20);
  });

  it("periphery packs tight but does not overlap (iteration-10)", () => {
    // Same-shell items that are radially close must be tangentially
    // separated; ellipse smears count at their stretched width. The
    // relaxation is best-effort, so allow 50% slack.
    const aggIds = new Set(scene.aggregates.map((a) => a.id));
    type Q = { rho: number; angle: number; tang: number; rad: number; shell: number };
    const items: Q[] = [];
    const shellOf = new Map<string, number>();
    for (const n of scene.nodes) if (n.shell) shellOf.set(n.id, n.shell);
    for (const a of scene.aggregates) shellOf.set(a.id, Math.max(1, a.shell ?? 1));
    for (const [id, shell] of shellOf) {
      const p = layout.positions.get(id);
      if (!p) continue;
      const isAgg = aggIds.has(id);
      items.push({
        rho: Math.hypot(p.x, p.y),
        angle: Math.atan2(p.y, p.x),
        tang: p.r * (isAgg ? 1 + shell * 0.9 : 1),
        rad: p.r * (isAgg ? Math.max(0.4, 1 - shell * 0.16) : 1),
        shell,
      });
    }
    let overlapping = 0;
    for (let i = 0; i < items.length; i++) {
      for (let j = i + 1; j < items.length; j++) {
        const a = items[i];
        const b = items[j];
        if (a.shell !== b.shell) continue;
        if (Math.abs(a.rho - b.rho) >= (a.rad + b.rad) * 0.5) continue;
        const dAng = Math.abs(Math.atan2(Math.sin(a.angle - b.angle), Math.cos(a.angle - b.angle)));
        const sep = dAng * ((a.rho + b.rho) / 2);
        if (sep < (a.tang + b.tang) * 0.5) overlapping++;
      }
    }
    expect(overlapping).toBe(0);
  });
});

describe("graph-zone pan (iteration-14)", () => {
  it("translates only core content; shells, aggregates and the frame stay fixed", () => {
    const panned = applyCorePan(layout, scene, { x: 40, y: -25 });
    let coreChecked = 0;
    let fixedChecked = 0;
    for (const n of scene.nodes) {
      const a = layout.positions.get(n.id);
      const b = panned.positions.get(n.id);
      if (!a || !b) continue;
      if (n.shell) {
        expect(b).toEqual(a);
        fixedChecked++;
      } else {
        expect(b.x).toBeCloseTo(a.x + 40, 6);
        expect(b.y).toBeCloseTo(a.y - 25, 6);
        coreChecked++;
      }
    }
    for (const g of scene.aggregates) {
      const a = layout.positions.get(g.id);
      const b = panned.positions.get(g.id);
      if (a && b) {
        expect(b).toEqual(a);
        fixedChecked++;
      }
    }
    expect(coreChecked).toBeGreaterThan(10);
    expect(fixedChecked).toBeGreaterThan(3);
  });

  it("zero pan is the identity (no allocation churn on idle frames)", () => {
    expect(applyCorePan(layout, scene, { x: 0, y: 0 })).toBe(layout);
  });
});

describe("sector absorption (iteration-10)", () => {
  function lensStub() {
    let lens: Record<string, unknown> = { id: "default" };
    const focused: string[] = [];
    const engine = {
      getState: () => ({ lens }),
      setLens: (l: Record<string, unknown>) => {
        lens = l;
      },
      focus: (id: string) => focused.push(id),
    } as unknown as AtlasEngine;
    return { engine, focused, lens: () => lens as { typeWeights?: Record<string, number> } };
  }

  it("each pull boosts the sector's type; the boost caps out", () => {
    const { engine, lens } = lensStub();
    absorbTowardType(engine, "fact");
    expect(lens().typeWeights?.fact).toBeCloseTo(1.6, 5);
    for (let i = 0; i < 10; i++) absorbTowardType(engine, "fact");
    expect(lens().typeWeights?.fact).toBe(ABSORB_CAP);
    // Steering elsewhere decays the old boost back toward neutral.
    for (let i = 0; i < 14; i++) absorbTowardType(engine, "note");
    expect(lens().typeWeights?.fact).toBeUndefined();
    expect(lens().typeWeights?.note).toBe(ABSORB_CAP);
  });

  it("a boosted type ranks more of itself into the core, shrinking the beyond-count", () => {
    const boosted = buildScene(
      g,
      { ...req, lens: { ...DEFAULT_LENS, typeWeights: { fact: ABSORB_CAP } } },
      42,
    );
    type S = typeof scene;
    const factsIn = (s: S) => s.nodes.filter((n) => !n.shell && n.item.type === "fact").length;
    const factsBeyond = (s: S) =>
      s.aggregates.filter((a) => a.type === "fact").reduce((sum, a) => sum + a.count, 0) +
      s.nodes.filter((n) => n.shell && n.item.type === "fact").length;
    expect(factsIn(boosted)).toBeGreaterThan(factsIn(scene));
    expect(factsBeyond(boosted)).toBeLessThan(factsBeyond(scene));
  });

  it("commitLensTarget on an aggregate boosts its type and enters via a member", () => {
    const agg = {
      id: "agg:test",
      type: "fact",
      count: 54,
      memberIds: ["facts/f1"],
      label: "54 facts",
      memberTitles: [],
    };
    const positions = new Map([["agg:test", { x: 400, y: 0, r: 20 }]]);
    let lensState: Record<string, unknown> = { id: "default" };
    const focused: string[] = [];
    const engine = {
      snapshot: () => ({
        scene: { nodes: [], aggregates: [agg] },
        layout: { positions, displacement: 0 },
      }),
      getState: () => ({ lens: lensState }),
      setLens: (l: Record<string, unknown>) => {
        lensState = l;
      },
      focus: (id: string) => focused.push(id),
    } as unknown as AtlasEngine;
    const res = commitLensTarget(engine, { pull: 1, angle: 0 }, 100);
    expect(res.ok).toBe(true);
    expect((lensState as { typeWeights?: Record<string, number> }).typeWeights?.fact).toBeCloseTo(1.6, 5);
    expect(focused).toEqual(["facts/f1"]);
  });
});
