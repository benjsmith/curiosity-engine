import { describe, expect, it } from "vitest";
import {
  buildHistoryFromGraph,
  indexAtTime,
  snapshotAt,
  sortReplayEvents,
} from "../src/animation/historyFromGraph.ts";
import type { ReplayEvent } from "../src/animation/replayTimeline.ts";

describe("buildHistoryFromGraph", () => {
  it("emits nodes then edges with both endpoints present", () => {
    const hist = buildHistoryFromGraph(
      [
        { id: "b", title: "B", type: "concept", degree: 1 },
        { id: "a", title: "A", type: "source", degree: 0 },
      ],
      [{ source: "a", target: "b" }],
      { duration: 10, source: "test" },
    );
    expect(hist.source).toBe("test");
    expect(hist.events.filter((e) => e.op === "node").map((e) => e.op === "node" && e.id)).toEqual([
      "a",
      "b",
    ]);
    const edge = hist.events.find((e) => e.op === "edge");
    expect(edge).toEqual({ t: expect.any(Number), op: "edge", source: "a", target: "b" });
    expect(hist.degree).toEqual({ a: 1, b: 1 });
  });

  it("returns empty for no nodes", () => {
    const hist = buildHistoryFromGraph([], []);
    expect(hist.events).toEqual([]);
    expect(hist.source).toBe("empty");
  });
});

describe("snapshotAt / indexAtTime", () => {
  const events: ReplayEvent[] = [
    { t: 0, op: "node", id: "a", title: "A", type: "entity" },
    { t: 5, op: "node", id: "b", title: "B", type: "entity" },
    { t: 5, op: "edge", source: "a", target: "b" },
  ];
  const hist = { duration: 10, events: sortReplayEvents(events), source: "t" };

  it("scrubs by index", () => {
    expect(snapshotAt(hist, 0).nodes).toEqual([]);
    expect(snapshotAt(hist, 1).nodes.map((n) => n.id)).toEqual(["a"]);
    expect(snapshotAt(hist, 3).edges).toEqual([{ source: "a", target: "b" }]);
  });

  it("maps wall time to index", () => {
    expect(indexAtTime(hist, 0)).toBe(1);
    expect(indexAtTime(hist, 4.9)).toBe(1);
    expect(indexAtTime(hist, 5)).toBe(3);
  });
});
