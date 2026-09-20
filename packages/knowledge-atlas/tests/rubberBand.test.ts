import { describe, expect, it } from "vitest";
import {
  clientRubberToScene,
  clientToScenePoint,
  defaultPartitionPolicyForType,
  idsInRubberBand,
  isRubberClick,
  normalizeRubberRect,
} from "../src/partition/rubberBand.ts";

describe("rubberBand", () => {
  it("normalizes inverted drag corners", () => {
    expect(normalizeRubberRect({ x0: 10, y0: 20, x1: 4, y1: 8 })).toEqual({
      minX: 4,
      maxX: 10,
      minY: 8,
      maxY: 20,
      width: 6,
      height: 12,
    });
  });

  it("treats tiny rects as clicks", () => {
    expect(isRubberClick({ x0: 0, y0: 0, x1: 3, y1: 3 })).toBe(true);
    expect(isRubberClick({ x0: 0, y0: 0, x1: 5, y1: 5 })).toBe(false);
  });

  it("selects node centers inside the band", () => {
    const points = [
      { id: "a", x: 10, y: 10 },
      { id: "b", x: 50, y: 50 },
      { id: "c", x: 100, y: 10 },
      { id: " ", x: 20, y: 20 },
    ];
    expect(idsInRubberBand(points, { x0: 0, y0: 0, x1: 60, y1: 60 })).toEqual([
      "a",
      "b",
    ]);
    expect(idsInRubberBand(points, { x0: 0, y0: 0, x1: 2, y1: 2 })).toEqual([]);
  });

  it("defaults entity/concept to copy", () => {
    expect(defaultPartitionPolicyForType("entity")).toBe("copy");
    expect(defaultPartitionPolicyForType("concept")).toBe("copy");
    expect(defaultPartitionPolicyForType("note")).toBe("move");
    expect(defaultPartitionPolicyForType(undefined)).toBe("move");
  });
});

describe("clientToScenePoint / clientRubberToScene", () => {
  const rect = { left: 100, top: 50, width: 400, height: 300 };

  it("maps client coords to centre-origin scene space", () => {
    expect(clientToScenePoint(300, 200, rect)).toEqual({ x: 0, y: 0 });
    expect(clientToScenePoint(100, 50, rect)).toEqual({ x: -200, y: -150 });
  });

  it("maps a client rubber rect into scene space", () => {
    const scene = clientRubberToScene(
      { x0: 100, y0: 50, x1: 300, y1: 200 },
      rect,
    );
    expect(scene).toEqual({ x0: -200, y0: -150, x1: 0, y1: 0 });
  });
});
