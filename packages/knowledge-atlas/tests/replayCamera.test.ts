import { describe, expect, it } from "vitest";
import {
  blendAtlasCamera,
  blendZoom,
  boundsFromPositions,
  compareSyntheticNodes,
  computeAtlasCameraFit,
  computeFitTransform,
  typeTier,
} from "../src/animation/replayCamera.ts";
import { buildHistoryFromGraph } from "../src/animation/historyFromGraph.ts";

describe("computeFitTransform / blendZoom", () => {
  it("centres a bounds cloud in the view", () => {
    const t = computeFitTransform(
      { minX: 0, minY: 0, maxX: 200, maxY: 100 },
      { viewW: 1000, viewH: 600 },
    );
    expect(t.k).toBeGreaterThan(0);
    // Centre of cloud (100, 50) maps near view centre after transform.
    const sx = 100 * t.k + t.x;
    const sy = 50 * t.k + t.y;
    expect(sx).toBeCloseTo(500, 0);
    expect(sy).toBeCloseTo(300, 0);
  });

  it("blends toward a target", () => {
    const a = { x: 0, y: 0, k: 1 };
    const b = { x: 100, y: 50, k: 2 };
    const mid = blendZoom(a, b, 0.5);
    expect(mid).toEqual({ x: 50, y: 25, k: 1.5 });
  });

  it("boundsFromPositions returns null for empty", () => {
    expect(boundsFromPositions([])).toBeNull();
    expect(boundsFromPositions([{ x: 1, y: 2 }, { x: 3, y: 4 }])).toEqual({
      minX: 1,
      minY: 2,
      maxX: 3,
      maxY: 4,
    });
  });
});

describe("compareSyntheticNodes / created ordering", () => {
  it("orders by created when present", () => {
    const nodes = [
      { id: "late", type: "source", degree: 99, created: "2024-06-01" },
      { id: "early", type: "concept", degree: 0, created: "2023-01-01" },
    ];
    nodes.sort(compareSyntheticNodes);
    expect(nodes.map((n) => n.id)).toEqual(["early", "late"]);
  });

  it("falls back to type tier then degree", () => {
    expect(typeTier("source")).toBe(0);
    expect(typeTier("concept")).toBe(3);
    const nodes = [
      { id: "c", type: "concept", degree: 1 },
      { id: "s", type: "source", degree: 0 },
    ];
    nodes.sort(compareSyntheticNodes);
    expect(nodes.map((n) => n.id)).toEqual(["s", "c"]);
  });

  it("buildHistoryFromGraph respects created", () => {
    const hist = buildHistoryFromGraph(
      [
        { id: "b", title: "B", type: "source", degree: 10, created: "2025-01-01" },
        { id: "a", title: "A", type: "note", degree: 0, created: "2020-01-01" },
      ],
      [],
      { duration: 10, source: "test" },
    );
    const ids = hist.events.filter((e) => e.op === "node").map((e) => (e.op === "node" ? e.id : ""));
    expect(ids).toEqual(["a", "b"]);
  });
});

describe("computeAtlasCameraFit / blendAtlasCamera", () => {
  it("centres cloud at scene origin", () => {
    const cam = computeAtlasCameraFit(
      { minX: 0, minY: 0, maxX: 200, maxY: 100 },
      { viewW: 1000, viewH: 600 },
    );
    expect(cam.scale).toBeGreaterThan(0);
    // World centre (100, 50) → screen 0 after camera.
    expect(100 * cam.scale + cam.x).toBeCloseTo(0, 5);
    expect(50 * cam.scale + cam.y).toBeCloseTo(0, 5);
  });

  it("blends atlas cameras", () => {
    const mid = blendAtlasCamera(
      { x: 0, y: 0, scale: 1 },
      { x: 100, y: 50, scale: 2 },
      0.5,
    );
    expect(mid).toEqual({ x: 50, y: 25, scale: 1.5 });
  });
});
