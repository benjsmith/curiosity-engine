import { describe, expect, it } from "vitest";
import {
  addToPartitionSelection,
  buildPartitionSelection,
  partitionPayload,
  removeFromPartitionSelection,
  setPartitionPolicy,
} from "../src/partition/partitionSelection.ts";

describe("partitionSelection", () => {
  it("builds from mixed seeds with first-wins dedupe", () => {
    const sel = buildPartitionSelection([
      "a",
      { id: "b", policy: "copy" },
      { id: "a", policy: "copy" },
      "  ",
    ]);
    expect(sel).toEqual([
      { id: "a", policy: "move" },
      { id: "b", policy: "copy" },
    ]);
  });

  it("toggles policy and builds API payload", () => {
    let sel = buildPartitionSelection(["x", "y"]);
    sel = setPartitionPolicy(sel, "y", "copy");
    sel = addToPartitionSelection(sel, ["z"]);
    sel = removeFromPartitionSelection(sel, "x");
    expect(partitionPayload(sel)).toEqual({
      move: ["z"],
      copy: ["y"],
    });
  });
});
