/** Payload edge colours reach the scene, and declared node types stay. */

import { describe, expect, it } from "vitest";
import { buildScene } from "../src/core/scene/builder.ts";
import { indexFromCEData, canonicalType, type CEData } from "../src/datasources/curiosity.ts";
import { DEFAULT_BUDGET, DEFAULT_LENS } from "../src/core/types.ts";
import { edgeStroke } from "../src/renderer/canvas.ts";
import { deterministicTypeColor, resolveTheme, typeColour } from "../src/renderer/theme.ts";

function payload(): CEData {
  return {
    workspace: "w",
    generated_at: "2026-01-01T00:00:00+00:00",
    palette: { hypothesis: "#7570b3", evidence: "#73af48", concept: "#38a6a5" },
    nodes: [
      { id: "evidence/cpu", path: "evidence/cpu.md", type: "evidence", title: "CPU", degree: 2 },
      { id: "hypotheses/queue", path: "hypotheses/queue.md", type: "hypothesis", title: "[hyp] Queue", degree: 2 },
      { id: "concepts/a", path: "concepts/a.md", type: "concept", title: "[con] A", degree: 1 },
      { id: "concepts/b", path: "concepts/b.md", type: "concept", title: "[con] B", degree: 1 },
    ],
    edges: [
      { source: "evidence/cpu", target: "hypotheses/queue", type: "supports", color: "#0072b2" },
      { source: "evidence/cpu", target: "hypotheses/queue", type: "contradicts", color: "#d55e00" },
      { source: "concepts/a", target: "concepts/b", type: "wikilink" },
    ],
    pages: {
      "evidence/cpu": {
        id: "evidence/cpu", title: "CPU", type: "evidence", path: "evidence/cpu.md",
        properties: { supports: ["hypotheses/queue"] }, body_html: "",
      },
      "hypotheses/queue": {
        id: "hypotheses/queue", title: "[hyp] Queue", type: "hypothesis",
        path: "hypotheses/queue.md", properties: {}, body_html: "",
      },
      "concepts/a": {
        id: "concepts/a", title: "[con] A", type: "concept", path: "concepts/a.md",
        properties: {}, body_html: "",
      },
      "concepts/b": {
        id: "concepts/b", title: "[con] B", type: "concept", path: "concepts/b.md",
        properties: {}, body_html: "",
      },
    },
  };
}

describe("edge colour from the payload", () => {
  it("keeps hypothesis and a palette-backed custom type", () => {
    expect(canonicalType("hypothesis")).toBe("hypothesis");
    expect(canonicalType("hypotheses")).toBe("hypothesis");
    expect(canonicalType("unclassified", "[hyp]")).toBe("hypothesis");
    expect(canonicalType("widget", undefined, { widget: "#abcdef" })).toBe("widget");
    expect(canonicalType("made-up")).toBe("unclassified");
    expect(canonicalType("analyses")).toBe("analysis");
  });

  it("colours hypothesis from the default palette and a custom type deterministically", () => {
    const theme = resolveTheme(undefined, { widget: deterministicTypeColor("widget") });
    expect(typeColour("hypothesis", theme)).toBe("#7570b3");
    expect(typeColour("concept", theme)).toBe("#38a6a5");
    expect(typeColour("widget", theme)).toBe(deterministicTypeColor("widget"));
    expect(typeColour("widget", resolveTheme(undefined, {}))).toBe(deterministicTypeColor("widget"));
    expect(deterministicTypeColor("widget")).not.toBe("#ffffff");
    expect(deterministicTypeColor("widget")).not.toBe("#7570b3");
    expect(deterministicTypeColor("gadget")).not.toBe(deterministicTypeColor("widget"));
    const overridden = resolveTheme(undefined, { hypothesis: "#123456" });
    expect(typeColour("hypothesis", overridden)).toBe("#123456");
  });

  it("stores the stroke on the index and the scene", () => {
    const g = indexFromCEData(payload());
    expect(g.items.get("hypotheses/queue")?.type).toBe("hypothesis");
    const supports = g.edges.find((e) => e.type === "supports");
    const plain = g.edges.find((e) => e.type === "wikilink");
    expect(supports?.color).toBe("#0072b2");
    expect(plain?.color).toBeUndefined();

    const scene = buildScene(g, {
      focusId: "evidence/cpu",
      lens: DEFAULT_LENS,
      viewport: { width: 800, height: 600 },
      semanticScale: 2,
      coreCapacity: 20,
      budget: { ...DEFAULT_BUDGET, maxNodes: 20, maxEdges: 20 },
    }, 1);
    const painted = scene.edges.find((e) => e.type === "supports");
    const wikilink = scene.edges.find((e) => e.type === "wikilink");
    expect(painted?.color).toBe("#0072b2");
    expect(wikilink?.color).toBeUndefined();
  });

  it("paints the payload colour at rest and the accent when highlighted", () => {
    expect(edgeStroke({ color: "#0072b2" }, false, "#3a3a46", "#7aa2ff")).toBe("#0072b2");
    expect(edgeStroke({ color: "#d55e00" }, true, "#3a3a46", "#7aa2ff")).toBe("#7aa2ff");
    expect(edgeStroke({}, false, "#3a3a46", "#7aa2ff")).toBe("#3a3a46");
  });
});
