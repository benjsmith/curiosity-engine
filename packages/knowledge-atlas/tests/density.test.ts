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
import { DEFAULT_BUDGET, DEFAULT_LENS, type SceneRequest } from "../src/core/types.ts";

describe("coreCapacityFor (legible density)", () => {
  it("scales with screen area: phone ~50, desktop ~360, big screens more", () => {
    const phone = coreCapacityFor({ width: 390, height: 450 });
    const desktop = coreCapacityFor({ width: 1280, height: 800 });
    const wall = coreCapacityFor({ width: 2560, height: 1440 });
    expect(phone).toBeGreaterThanOrEqual(MIN_CORE_CAPACITY);
    expect(phone).toBeLessThan(80);
    expect(desktop).toBeGreaterThan(320);
    expect(desktop).toBeLessThan(400); // ≈ the classic 360
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

describe("lensing halo (flat middle, eased edge)", () => {
  const viewport = { width: 1200, height: 800 };
  // workspace-small (~420 items) exceeds the 360-capacity core, so the
  // scene has a real boundary for the halo to ease toward.
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
});
