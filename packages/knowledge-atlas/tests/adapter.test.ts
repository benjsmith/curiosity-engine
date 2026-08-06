/** CE adapter conformance + sharp edges (PLAN §2.1, §14). */

import { describe, expect, it } from "vitest";
import { canonicalType, indexFromCEData, normalizeId, type CEData } from "../src/datasources/curiosity.ts";
import { workspaceSmallData } from "../fixtures/index.ts";

describe("canonicalType", () => {
  it("collapses plurals and table variants", () => {
    expect(canonicalType("analyses")).toBe("analysis");
    expect(canonicalType("summary-table")).toBe("table");
    expect(canonicalType("extracted-table")).toBe("table");
    expect(canonicalType("todo")).toBe("todo-list");
  });
  it("backfills from the title prefix when unclassified", () => {
    expect(canonicalType("unclassified", "[tab]")).toBe("table");
    expect(canonicalType("unclassified", "[con]")).toBe("concept");
    expect(canonicalType("made-up")).toBe("unclassified");
  });
});

describe("normalizeId", () => {
  it("strips .md, idempotent", () => {
    expect(normalizeId("concepts/a.md")).toBe("concepts/a");
    expect(normalizeId("concepts/a")).toBe("concepts/a");
  });
});

function minimalData(): CEData {
  return {
    workspace: "w",
    generated_at: "2026-01-01T00:00:00+00:00",
    palette: {},
    nodes: [
      { id: "concepts/a", path: "concepts/a.md", type: "concept", title: "[con] A", degree: 1 },
      { id: "concepts/b", path: "concepts/b.md", type: "concepts", title: "[con] B", degree: 1 },
    ],
    edges: [
      // Mixed endpoint encodings: string, object (D3-mutated), .md-suffixed.
      { source: "concepts/a", target: { id: "concepts/b" }, type: "wikilink" },
      { source: "concepts/b.md", target: "concepts/a", type: "wikilink" },
    ],
    pages: {
      "concepts/a": {
        id: "concepts/a", title: "[con] A", type: "concept", path: "concepts/a.md",
        properties: { sources: ["raw/x.md", "raw/y.md"] }, body_html: "",
      },
      "concepts/b": {
        id: "concepts/b", title: "[con] B", type: "concepts", path: "concepts/b.md",
        properties: { sources: ["raw/x.md", "raw/y.md"] }, body_html: "",
      },
      // Page with NO node entry (kuzu drift) — must still become an item.
      "notes/drifted": {
        id: "notes/drifted", title: "Drifted", type: "note", path: "notes/drifted.md",
        properties: {}, body_html: "",
      },
    },
  };
}

describe("indexFromCEData", () => {
  it("pages is the item store: drifted pages become items", () => {
    const g = indexFromCEData(minimalData());
    expect(g.items.has("notes/drifted")).toBe(true);
    expect(g.items.get("concepts/b")?.type).toBe("concept"); // plural collapsed
  });
  it("handles object and .md-suffixed edge endpoints", () => {
    const g = indexFromCEData(minimalData());
    expect(g.neighbours("concepts/a").filter((n) => n.id === "concepts/b").length).toBeGreaterThan(0);
  });
  it("splits title prefixes into meta", () => {
    const g = indexFromCEData(minimalData());
    const a = g.items.get("concepts/a")!;
    expect(a.meta.titlePrefix).toBe("[con]");
    expect(a.title).toBe("A");
  });
  it("does not mutate the input payload", () => {
    const data = minimalData();
    const snapshot = JSON.stringify(data);
    indexFromCEData(data);
    expect(JSON.stringify(data)).toBe(snapshot);
  });
});

describe("workspace-small fixture as CEData", () => {
  it("is a valid payload with ~400 nodes and all core types", () => {
    const data = workspaceSmallData();
    expect(data.nodes.length).toBeGreaterThan(350);
    expect(data.nodes.length).toBeLessThan(500);
    const g = indexFromCEData(data);
    const types = new Set([...g.items.values()].map((i) => i.type));
    for (const t of ["concept", "entity", "fact", "evidence", "analysis", "source", "figure", "table", "note", "project"]) {
      expect(types, `missing type ${t}`).toContain(t);
    }
  });
  it("is deterministic for the same seed", () => {
    expect(JSON.stringify(workspaceSmallData(7))).toBe(JSON.stringify(workspaceSmallData(7)));
    expect(JSON.stringify(workspaceSmallData(7))).not.toBe(JSON.stringify(workspaceSmallData(8)));
  });
});
