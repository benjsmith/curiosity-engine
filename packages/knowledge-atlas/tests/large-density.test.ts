import { describe, expect, it, vi } from "vitest";

const ticks = vi.hoisted(() => ({ n: 0 }));

vi.mock("d3-force", async () => {
  const actual = await vi.importActual<typeof import("d3-force")>("d3-force");
  const orig = actual.forceSimulation;
  return {
    ...actual,
    forceSimulation: ((...args: Parameters<typeof orig>) => {
      const sim = orig(...args);
      const tick = sim.tick.bind(sim);
      sim.tick = ((iterations?: number) => {
        ticks.n += iterations ?? 1;
        return tick(iterations);
      }) as typeof sim.tick;
      return sim;
    }) as typeof orig,
  };
});

import { GraphIndex } from "../src/core/graphindex.ts";
import { hybridLayout } from "../src/core/layout/hybrid.ts";
import { buildScene } from "../src/core/scene/builder.ts";
import { DEFAULT_LENS, DEFAULT_PHYSICS } from "../src/core/types.ts";

describe("10k overview envelope", () => {
  it("lays out 10,000 unlabelled points with edges omitted", () => {
    const graph = new GraphIndex();
    for (let i = 0; i < 10_000; i++) {
      graph.addItem({
        id: `n${i}`,
        type: `cluster-${i % 12}`,
        title: `node ${i}`,
        meta: { degree: 0 },
      });
    }
    const scene = buildScene(
      graph,
      {
        focusId: "n0",
        lens: DEFAULT_LENS,
        viewport: { width: 1280, height: 800 },
        semanticScale: 2,
        coreCapacity: 10_000,
        fullGraphCapacity: 10_000,
        budget: {
          maxNodes: 10_000,
          maxAggregates: 0,
          maxEdges: 0,
          maxBundles: 0,
          maxLabels: 0,
        },
      },
      42,
    );
    ticks.n = 0;
    const layout = hybridLayout.layout(scene, {
      viewport: { width: 1280, height: 800 },
      seed: 42,
      physics: DEFAULT_PHYSICS,
    });
    expect(scene.nodes).toHaveLength(10_000);
    expect(scene.edges).toHaveLength(0);
    expect(layout.positions.size).toBe(10_000);
    // Tick-band contract, not a wall-clock budget. Exactly 10,000 nodes
    // is the >2,000 band (60). >10,000 is 32; 350 is only for ≤800.
    // Counting sim.tick() calls fails if the solver returns to 350.
    expect(ticks.n).toBe(60);
  });
});
