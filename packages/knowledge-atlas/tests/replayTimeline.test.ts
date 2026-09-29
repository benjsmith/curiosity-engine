import { describe, expect, it } from "vitest";
import { playReplayTimeline, type ReplayEvent } from "../src/animation/replayTimeline.ts";

describe("playReplayTimeline", () => {
  it("emits events in time order and calls onDone", () => {
    const events: ReplayEvent[] = [
      { t: 0, op: "node", id: "a", title: "A", type: "entity" },
      { t: 10, op: "node", id: "b", title: "B", type: "concept" },
      { t: 15, op: "edge", source: "a", target: "b" },
    ];
    let wall = 0;
    const seen: string[] = [];
    const timers: Array<{ id: number; ms: number; fn: () => void }> = [];
    let nextId = 1;

    const cancel = playReplayTimeline(
      { duration: 20, events, source: "test" },
      {
        now: () => wall,
        schedule: (fn, ms) => {
          const id = nextId++;
          timers.push({ id, ms, fn });
          return id;
        },
        cancel: (id) => {
          const i = timers.findIndex((t) => t.id === id);
          if (i >= 0) timers.splice(i, 1);
        },
        onEvent: (ev) => {
          seen.push(ev.op === "node" ? `n:${ev.id}` : `e:${ev.source}->${ev.target}`);
        },
        onDone: () => seen.push("done"),
      },
    );

    // Drain: run scheduled callbacks, advancing wall to cover delays.
    while (timers.length) {
      const t = timers.shift()!;
      wall += t.ms;
      t.fn();
    }
    expect(seen).toEqual(["n:a", "n:b", "e:a->b", "done"]);
    cancel();
  });

  it("cancel stops further events", () => {
    let wall = 0;
    const seen: string[] = [];
    const timers: Array<{ id: number; ms: number; fn: () => void }> = [];
    let nextId = 1;
    const stop = playReplayTimeline(
      {
        duration: 100,
        source: "test",
        events: [
          { t: 0, op: "node", id: "a", title: "A", type: "entity" },
          { t: 50, op: "node", id: "b", title: "B", type: "entity" },
        ],
      },
      {
        now: () => wall,
        schedule: (fn, ms) => {
          const id = nextId++;
          timers.push({ id, ms, fn });
          return id;
        },
        cancel: (id) => {
          const i = timers.findIndex((t) => t.id === id);
          if (i >= 0) timers.splice(i, 1);
        },
        onEvent: (ev) => {
          if (ev.op === "node") seen.push(ev.id);
        },
      },
    );
    // First tick at t=0
    const first = timers.shift()!;
    wall += first.ms;
    first.fn();
    expect(seen).toEqual(["a"]);
    stop();
    // Remaining timers cleared by cancel path — drain whatever is left should not add b after stop if cancel removed them
    while (timers.length) {
      const t = timers.shift()!;
      wall += t.ms;
      t.fn();
    }
    expect(seen).toEqual(["a"]);
  });
});
