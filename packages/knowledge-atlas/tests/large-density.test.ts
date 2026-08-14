import { describe, expect, it } from "vitest";
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
    const started = performance.now();
    const layout = hybridLayout.layout(scene, {
      viewport: { width: 1280, height: 800 },
      seed: 42,
      physics: DEFAULT_PHYSICS,
    });
    expect(scene.nodes).toHaveLength(10_000);
    expect(scene.edges).toHaveLength(0);
    expect(layout.positions.size).toBe(10_000);
    // Broad regression guard: catches an accidental return to 350
    // force ticks without pretending CI timing is a product benchmark.
    expect(performance.now() - started).toBeLessThan(5_000);
  });
});
