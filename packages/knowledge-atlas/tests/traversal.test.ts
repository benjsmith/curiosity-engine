/**
 * Lens-traversal physics (iteration-8): docs-per-pixel shell scaling,
 * the hard flow speed limit, rate-limited scene commits, iOS-flick
 * momentum decay, and odometer monotonicity. Pure-physics tests — the
 * engine is a counting stub where a commit target is needed.
 */

import { describe, expect, it } from "vitest";
import {
  COMMIT_INTERVAL_MS,
  FRICTION_PER_MS,
  LensTraversal,
  MAX_DOCS_PER_SECOND,
  MIN_HYPERSPACE_RATE,
  docsPerPixel,
  flowCapFor,
  shellTotalsFromLayout,
  shellTotalsFromScene,
} from "../src/interaction/traversal.ts";
import { coreRadiusAt, rimRadiusAt } from "../src/core/geometry.ts";
import type { AtlasEngine } from "../src/core/engine.ts";

const VIEW = { width: 1200, height: 800 };
// Exponential corpus: shell k holds ~10× shell k−1 (640/6.4k/64k/640k).
const TOTALS = [0, 640, 6_400, 64_000, 640_000] as const;

/** Radius fraction f of the way from the core edge to the wall at
 * angle, for a corpus populating `bands` shells (geometry follows
 * content — iteration-11). */
function rhoAt(f: number, angle = 0, bands = 4): number {
  const inner = coreRadiusAt(angle, VIEW, bands) * 1.03;
  return inner + f * (rimRadiusAt(angle, VIEW) - inner);
}

/** Engine stub: one committable node far out in the +x sector. */
function stubEngine(): { engine: AtlasEngine; commits: () => number } {
  let count = 0;
  const layout = {
    positions: new Map([["n1", { x: rhoAt(0.4), y: 0, r: 4 }]]),
    displacement: 0,
  };
  const scene = {
    nodes: [{ id: "n1", score: 1 }],
    aggregates: [],
  };
  const engine = {
    snapshot: () => ({ scene, layout }),
    focus: () => {
      count += 1;
    },
  } as unknown as AtlasEngine;
  return { engine, commits: () => count };
}

describe("docsPerPixel", () => {
  it("grows with start depth — deeper shells pack more corpus per pixel", () => {
    const shallow = docsPerPixel(rhoAt(0.1), 0, VIEW, TOTALS);
    const mid = docsPerPixel(rhoAt(0.65), 0, VIEW, TOTALS);
    const deep = docsPerPixel(rhoAt(0.99), 0, VIEW, TOTALS);
    expect(mid).toBeGreaterThan(shallow);
    expect(deep).toBeGreaterThan(mid);
    // Band depth also SHRINKS with shell, so the growth outpaces the
    // raw 10× totals — the "visible universe" compression.
    expect(deep / shallow).toBeGreaterThan(100);
  });

  it("small corpora renormalise: populated bands share the whole gap", () => {
    // A 4k-doc corpus fills shells 1–2 only; those two bands now span
    // the entire core→wall gap (iteration-11) — the wall belongs to
    // shell 2, and there is no empty space to free-spin over.
    const small = [0, 640, 3_000, 0, 0] as const;
    const fromWall = docsPerPixel(rhoAt(0.99, 0, 2), 0, VIEW, small);
    const fromShell2 = docsPerPixel(rhoAt(0.7, 0, 2), 0, VIEW, small);
    expect(fromWall).toBe(fromShell2); // same (deepest) band
    expect(fromWall).toBeGreaterThan(1);
    const fromFringe = docsPerPixel(rhoAt(0.2, 0, 2), 0, VIEW, small);
    expect(fromFringe).toBeLessThan(fromWall); // shell 1 gears gentler
  });

  it("is corner-aware: gentler density diagonally than on the short axis", () => {
    // The squircle wall sits farther out on the diagonal than on the
    // short (vertical) axis, so each band is deeper in pixels there →
    // fewer docs per pixel; edges compress harder than corners.
    const shortAxis = docsPerPixel(rhoAt(0.65, Math.PI / 2), Math.PI / 2, VIEW, TOTALS);
    const diagonal = docsPerPixel(rhoAt(0.65, Math.PI / 4), Math.PI / 4, VIEW, TOTALS);
    expect(diagonal).toBeLessThan(shortAxis);
  });
});

describe("LensTraversal", () => {
  it("does not start inside the core (those drags stay camera pans)", () => {
    const { engine } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    expect(t.start(10, 10, 0)).toBe(false);
    expect(t.start(coreRadiusAt(0, VIEW, 4) * 0.9, 0, 0)).toBe(false);
    expect(t.start(coreRadiusAt(0, VIEW, 4) * 0.95, 0, 0)).toBe(false);
    expect(t.start(rhoAt(0.5), 0, 0)).toBe(true);
  });

  it("honours the visual core bands so a full-graph rim does not shrink the pan zone", () => {
    const { engine } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    t.setCoreBands(1);
    const visualInner = coreRadiusAt(0, VIEW, 1) * 1.03;
    expect(t.start(visualInner * 0.98, 0, 0)).toBe(false);
    expect(t.start(visualInner * 1.05, 0, 0)).toBe(true);
  });

  it("withholds hyperspace commits and intensity below 100 docs/s", () => {
    const { engine, commits } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    t.start(rhoAt(0.15), 0, 0);
    t.drag(-1, 0, 200);
    const f = t.tick(COMMIT_INTERVAL_MS + 16);
    expect(f.rate).toBeLessThan(MIN_HYPERSPACE_RATE);
    expect(f.intensity).toBe(0);
    expect(commits()).toBe(0);
    t.cancel();
  });

  it("enforces the speed limit no matter how violent the drag", () => {
    const { engine } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    t.start(rhoAt(0.99), 0, 0); // deepest shell → extreme docs/px
    let now = 0;
    for (let i = 0; i < 60; i++) {
      now += 16;
      t.drag(-400, 0, 16); // 25k px/sec centerward — absurd
    }
    const frame = t.tick(now, false);
    // Flow is capped: one tick's odometer gain can't exceed the limit.
    const before = frame.odometer;
    const after = t.tick(now + 100, false).odometer;
    expect(after - before).toBeLessThanOrEqual((MAX_DOCS_PER_SECOND * 100) / 1000 + 1);
    expect(frame.intensity).toBeLessThanOrEqual(1);
  });

  it("outward drags do not build flow", () => {
    const { engine } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    t.start(rhoAt(0.5), 0, 0);
    t.drag(+300, 0, 16); // away from center
    const f = t.tick(16, false);
    expect(f.intensity).toBe(0);
  });

  it("release decays with friction until it settles (iOS flick)", () => {
    const { engine } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    t.start(rhoAt(0.8), 0, 0);
    let now = 0;
    for (let i = 0; i < 10; i++) {
      now += 16;
      t.drag(-60, 0, 16);
    }
    const atRelease = t.tick(now, false);
    expect(atRelease.intensity).toBeGreaterThan(0.3);
    t.release();
    let frames = 0;
    let prevIntensity = atRelease.intensity;
    let prevOdometer = atRelease.odometer;
    let f = atRelease;
    while (f.active && frames < 2000) {
      now += 16;
      f = t.tick(now, false);
      expect(f.intensity).toBeLessThanOrEqual(prevIntensity + 1e-9);
      expect(f.odometer).toBeGreaterThanOrEqual(prevOdometer); // monotonic
      prevIntensity = f.intensity;
      prevOdometer = f.odometer;
      frames++;
    }
    expect(f.active).toBe(false); // came to rest, not the frame cap
    expect(frames).toBeGreaterThan(10); // …but not instantly
    // Feels responsive (iteration-10): a full-speed flick settles in
    // well under a second, and the half-life is ~115ms.
    expect(frames * 16).toBeLessThan(1000);
    expect(Math.pow(FRICTION_PER_MS, 115)).toBeGreaterThan(0.45);
    expect(Math.pow(FRICTION_PER_MS, 115)).toBeLessThan(0.55);
  });

  it("rate-limits real scene commits to COMMIT_INTERVAL_MS", () => {
    const { engine, commits } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    t.start(rhoAt(0.5), 0, 0);
    let now = 0;
    // Two seconds of sustained fast drag, ticking at 60fps.
    for (let i = 0; i < 125; i++) {
      now += 16;
      t.drag(-80, 0, 16);
      t.tick(now);
    }
    const expected = Math.floor(2000 / COMMIT_INTERVAL_MS);
    expect(commits()).toBeGreaterThan(0);
    expect(commits()).toBeLessThanOrEqual(expected + 1);
    t.cancel();
  });

  it("cancel stops everything dead", () => {
    const { engine } = stubEngine();
    const t = new LensTraversal(engine, VIEW, () => TOTALS);
    t.start(rhoAt(0.5), 0, 0);
    t.drag(-100, 0, 16);
    t.cancel();
    const f = t.tick(32, false);
    expect(f.active).toBe(false);
    expect(f.intensity).toBe(0);
  });
});

describe("flowCapFor (corpus-scaled speed limit)", () => {
  it("a sub-1k wiki can never read thousands of docs/sec", () => {
    // ~640 docs beyond the core → the cap is the corpus itself.
    expect(flowCapFor([0, 400, 240, 0, 0])).toBe(640);
    expect(flowCapFor([0, 0, 0, 0, 0])).toBe(30); // floor
  });

  it("cloud corpora keep the absolute limit", () => {
    expect(flowCapFor([0, 640, 6_400, 64_000, 640_000])).toBe(MAX_DOCS_PER_SECOND);
  });

  it("the traversal's flow respects the corpus cap", () => {
    const { engine } = stubEngine();
    const small = [0, 400, 240, 0, 0] as const; // 640-doc boundary
    const t = new LensTraversal(engine, VIEW, () => small);
    t.start(rhoAt(0.9), 0, 0);
    let now = 0;
    for (let i = 0; i < 40; i++) {
      now += 16;
      t.drag(-500, 0, 16); // absurd speed
    }
    const before = t.tick(now, false).odometer;
    let after = before;
    for (let i = 0; i < 10; i++) {
      now += 100; // one full second of sustained max flow
      after = t.tick(now, false).odometer;
    }
    expect(after - before).toBeLessThanOrEqual(640 + 1); // ≤ cap × 1s
  });
});

describe("shellTotalsFromScene", () => {
  it("sums shell nodes and aggregate counts per shell", () => {
    const totals = shellTotalsFromScene({
      nodes: [{ shell: 1 }, { shell: 1 }, {}, { shell: 2 }],
      aggregates: [
        { shell: 1, count: 10 },
        { shell: 3, count: 5_000 },
        { shell: 7, count: 9 }, // clamps into the outermost band
        { count: 99 }, // core aggregate — not a shell
      ],
    });
    expect(totals[1]).toBe(12);
    expect(totals[2]).toBe(1);
    expect(totals[3]).toBe(5_000);
    expect(totals[4]).toBe(9);
    expect(shellTotalsFromScene(null)).toEqual([0, 0, 0, 0, 0]);
  });
});

describe("shellTotalsFromLayout", () => {
  it("buckets rim positions by log depth and ignores the core", () => {
    const inner = coreRadiusAt(0, VIEW, 1) * 1.03;
    const wall = rimRadiusAt(0, VIEW);
    const pts = [
      { x: 10, y: 0 },
      { x: inner + 4, y: 0 },
      { x: inner + 0.9 * (wall - inner), y: 0 },
    ];
    const totals = shellTotalsFromLayout(pts, VIEW);
    expect(totals.reduce((a, b) => a + b, 0)).toBe(2);
    expect(totals[1]).toBeGreaterThan(0);
    expect(totals[4] + totals[3] + totals[2]).toBeGreaterThan(0);
  });
});
