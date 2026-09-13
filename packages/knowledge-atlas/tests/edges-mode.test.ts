import { describe, expect, it } from "vitest";
import {
  autoEdgeBudget,
  classifyEdgeDraw,
  cycleEdgeMode,
  edgeSampleKey,
} from "../src/renderer/edges.ts";

describe("edgeSampleKey", () => {
  it("is order-independent and stable", () => {
    expect(edgeSampleKey("a", "b")).toBe(edgeSampleKey("b", "a"));
    expect(edgeSampleKey("a", "b")).toBe(edgeSampleKey("a", "b"));
    expect(edgeSampleKey("a", "b")).not.toBe(edgeSampleKey("a", "c"));
  });
});

describe("autoEdgeBudget", () => {
  it("draws all edges on small graphs", () => {
    expect(autoEdgeBudget(400)).toBe(400);
    expect(autoEdgeBudget(1200)).toBe(1200);
  });

  it("caps large graphs sparsely but grows slowly", () => {
    const b2k = autoEdgeBudget(2000);
    const b40k = autoEdgeBudget(40_000);
    expect(b2k).toBeLessThan(2000);
    expect(b2k).toBeGreaterThan(900);
    expect(b40k).toBeLessThan(40_000);
    expect(b40k).toBeGreaterThan(b2k);
  });
});

describe("classifyEdgeDraw", () => {
  const empty = new Set<string>();
  const edge = { source: "a", target: "b", priority: 2 };

  it("always draws hover / selection / focus highlights", () => {
    expect(
      classifyEdgeDraw(edge, "off", { hoverId: "a", selection: empty, edgeCount: 5000 }),
    ).toBe("highlight");
    expect(
      classifyEdgeDraw(edge, "off", {
        hoverId: null,
        selection: new Set(["b"]),
        edgeCount: 5000,
      }),
    ).toBe("highlight");
    expect(
      classifyEdgeDraw(
        { ...edge, priority: 1 },
        "off",
        { hoverId: null, selection: empty, edgeCount: 5000 },
      ),
    ).toBe("highlight");
  });

  it("off skips non-highlight edges", () => {
    expect(
      classifyEdgeDraw(edge, "off", { hoverId: null, selection: empty, edgeCount: 100 }),
    ).toBe("skip");
  });

  it("on draws every non-highlight edge", () => {
    expect(
      classifyEdgeDraw(edge, "on", { hoverId: null, selection: empty, edgeCount: 50_000 }),
    ).toBe("normal");
  });

  it("auto draws all when under budget and a sparse subset when over", () => {
    expect(
      classifyEdgeDraw(edge, "auto", { hoverId: null, selection: empty, edgeCount: 100 }),
    ).toBe("normal");

    const n = 20_000;
    let drawn = 0;
    for (let i = 0; i < n; i++) {
      const e = { source: `s${i}`, target: `t${i}`, priority: 2 };
      if (
        classifyEdgeDraw(e, "auto", { hoverId: null, selection: empty, edgeCount: n }) ===
        "normal"
      ) {
        drawn++;
      }
    }
    const budget = autoEdgeBudget(n);
    // Hash-modulo is approximate; allow ±15%.
    expect(drawn).toBeGreaterThan(budget * 0.85);
    expect(drawn).toBeLessThan(budget * 1.15);
  });
});

describe("cycleEdgeMode", () => {
  it("cycles auto → on → off → auto", () => {
    expect(cycleEdgeMode("auto")).toBe("on");
    expect(cycleEdgeMode("on")).toBe("off");
    expect(cycleEdgeMode("off")).toBe("auto");
  });
});
