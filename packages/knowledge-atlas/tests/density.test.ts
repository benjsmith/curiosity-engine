/**
 * Iteration-9: screen-density core capacity, the zoom coupling, and
 * the flat-centre lensing halo near the core boundary.
 */

import { describe, expect, it } from "vitest";
import {
  MAX_CORE_CAPACITY,
  MIN_CORE_CAPACITY,
  coreCapacityFor,
} from "../src/core/scene/shells.ts";
import { buildScene } from "../src/core/scene/builder.ts";
import { hybridLayout } from "../src/core/layout/hybrid.ts";
import { nodeRadius } from "../src/core/layout/types.ts";
import { coreRadius } from "../src/core/geometry.ts";
import { indexFromCEData } from "../src/datasources/curiosity.ts";
import { workspaceSmallData } from "../fixtures/index.ts";
import { absorbTowardType, commitLensTarget, ABSORB_CAP } from "../src/interaction/lens.ts";
import type { AtlasEngine } from "../src/core/engine.ts";
import { DEFAULT_BUDGET, DEFAULT_LENS, type SceneRequest } from "../src/core/types.ts";

describe("coreCapacityFor (legible density)", () => {
  it("scales with screen area: phone ~50, desktop ~310, big screens more", () => {
    const phone = coreCapacityFor({ width: 390, height: 450 });
    const desktop = coreCapacityFor({ width: 1280, height: 800 });
    const wall = coreCapacityFor({ width: 2560, height: 1440 });
    expect(phone).toBeGreaterThanOrEqual(MIN_CORE_CAPACITY);
    expect(phone).toBeLessThan(80);
    expect(desktop).toBeGreaterThan(280);
    expect(desktop).toBeLessThan(360); // a notch under the classic 360 (iteration-10)
    expect(wall).toBeGreaterThan(1000);
    expect(wall).toBeLessThanOrEqual(MAX_CORE_CAPACITY);
  });

  it("zoom-out raises capacity (smaller nodes → more fit), zoom-in lowers it", () => {
    const v = { width: 1280, height: 800 };
    const base = coreCapacityFor(v, 1);
    expect(coreCapacityFor(v, 0.7)).toBeGreaterThan(base * 1.5);
    expect(coreCapacityFor(v, 1.5)).toBeLessThan(base * 0.6);
    // Extreme zooms clamp — capacity can't be driven unbounded.
    expect(coreCapacityFor(v, 0.1)).toBe(coreCapacityFor(v, 0.66));
    expect(coreCapacityFor(v, 10)).toBe(coreCapacityFor(v, 2));
  });

  it("never leaves the legibility clamp", () => {
    expect(coreCapacityFor({ width: 100, height: 100 })).toBe(MIN_CORE_CAPACITY);
    expect(coreCapacityFor({ width: 8000, height: 8000 })).toBe(MAX_CORE_CAPACITY);
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
const Rcore = coreRadius(viewport);

describe("lensing halo (flat middle, eased edge)", () => {

  it("keeps classic degree-based radii in the flat middle", () => {
    let flatChecked = 0;
    for (const n of scene.nodes) {
      if (n.shell) continue;
      const p = layout.positions.get(n.id);
      if (!p) continue;
      const t = Math.hypot(p.x, p.y) / Rcore;
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
      const t = Math.hypot(p.x, p.y) / Rcore;
      if (t > 0.85) {
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
      const t = Math.hypot(b.x, b.y) / Rcore;
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
    const ok = commitLensTarget(engine, { pull: 1, angle: 0 }, 100);
    expect(ok).toBe(true);
    expect((lensState as { typeWeights?: Record<string, number> }).typeWeights?.fact).toBeCloseTo(1.6, 5);
    expect(focused).toEqual(["facts/f1"]);
  });
});
