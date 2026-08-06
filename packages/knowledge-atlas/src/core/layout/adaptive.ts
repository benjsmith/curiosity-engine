/**
 * Adaptive layout (P5; PLAN §8.4): classify the local scene topology
 * and route to the geometry that represents it best. Relations the
 * chosen geometry represents poorly stay visible as horizon shortcuts
 * (the scene builder already surfaces them) rather than distorting the
 * whole map.
 *
 * Routing:
 *   tree-like        → hyperbolic (radial focus+context)
 *   directed chain   → layered (left→right by ring)
 *   bipartite        → two-sided
 *   dense mesh       → force (constrained Euclidean)
 *   otherwise        → focus rings
 */

import { focusLayout } from "./focus.ts";
import { forceLayout } from "./force.ts";
import { hyperbolicLayout } from "./hyperbolic.ts";
import { meanDisplacement, nodeRadius, type LayoutAdapter, type LayoutContext } from "./types.ts";
import type { LayoutPoint, LayoutResult, SceneData } from "../types.ts";

export type TopologyClass = "tree" | "chain" | "bipartite" | "mesh" | "mixed";

export function classifyTopology(scene: SceneData): TopologyClass {
  const ids = new Set(scene.nodes.map((n) => n.id));
  const edges = scene.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const n = ids.size;
  const m = edges.length;
  if (n < 3) return "mixed";
  const density = m / n;

  const deg = new Map<string, number>();
  for (const e of edges) {
    deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
    deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
  }
  const degs = [...ids].map((id) => deg.get(id) ?? 0);
  const chainish = degs.filter((d) => d <= 2).length / n;
  if (chainish > 0.9 && density <= 1.05) return "chain";

  // Bipartite: 2-colour BFS over the scene subgraph.
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    (adj.get(e.source) ?? adj.set(e.source, []).get(e.source)!).push(e.target);
    (adj.get(e.target) ?? adj.set(e.target, []).get(e.target)!).push(e.source);
  }
  const colour = new Map<string, 0 | 1>();
  let bipartite = m > 0;
  for (const id of ids) {
    if (colour.has(id) || !adj.has(id)) continue;
    colour.set(id, 0);
    const q = [id];
    while (q.length && bipartite) {
      const u = q.shift()!;
      for (const v of adj.get(u) ?? []) {
        if (!colour.has(v)) {
          colour.set(v, colour.get(u) === 0 ? 1 : 0);
          q.push(v);
        } else if (colour.get(v) === colour.get(u)) {
          bipartite = false;
          break;
        }
      }
    }
  }
  if (bipartite && m >= n - 1 && n >= 6) return "bipartite";
  if (density <= 1.1) return "tree";
  if (density >= 2.2) return "mesh";
  return "mixed";
}

function layeredLayout(scene: SceneData, ctx: LayoutContext): LayoutResult {
  // Left→right by ring (BFS depth), vertical order by score.
  const byRing = new Map<number, typeof scene.nodes[number][]>();
  for (const n of scene.nodes) {
    const ring = n.role === "focus" ? 0 : Math.max(1, n.ring ?? 1);
    (byRing.get(ring) ?? byRing.set(ring, []).get(ring)!).push(n);
  }
  const positions = new Map<string, LayoutPoint>();
  const rings = [...byRing.keys()].sort((a, b) => a - b);
  const w = ctx.viewport.width * 0.8;
  const h = ctx.viewport.height * 0.8;
  for (let i = 0; i < rings.length; i++) {
    const list = byRing.get(rings[i])!;
    list.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
    const x = rings.length === 1 ? 0 : (i / (rings.length - 1) - 0.5) * w;
    for (let j = 0; j < list.length; j++) {
      const y = list.length === 1 ? 0 : (j / (list.length - 1) - 0.5) * h;
      positions.set(list[j].id, { x, y, r: nodeRadius(list[j].item.meta.degree) });
    }
  }
  // Aggregates below the chain.
  for (let i = 0; i < scene.aggregates.length; i++) {
    const a = scene.aggregates[i];
    positions.set(a.id, {
      x: (i / Math.max(1, scene.aggregates.length - 1) - 0.5) * w,
      y: h * 0.62,
      r: 12 + Math.sqrt(a.count) * 2,
    });
  }
  return { positions, displacement: meanDisplacement(positions, ctx.previous) };
}

function twoSidedLayout(scene: SceneData, ctx: LayoutContext): LayoutResult {
  // Evidence-style bipartite: focus side vs other side by 2-colouring
  // approximation — type parity of ring keeps it deterministic.
  const positions = new Map<string, LayoutPoint>();
  const left: typeof scene.nodes = [];
  const right: typeof scene.nodes = [];
  for (const n of scene.nodes) {
    if (n.role === "focus") {
      positions.set(n.id, { x: 0, y: 0, r: nodeRadius(n.item.meta.degree) + 3 });
    } else if ((n.ring ?? 1) % 2 === 1) left.push(n);
    else right.push(n);
  }
  const h = ctx.viewport.height * 0.8;
  const x = ctx.viewport.width * 0.28;
  const place = (list: typeof scene.nodes, sign: 1 | -1) => {
    list.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
    for (let j = 0; j < list.length; j++) {
      const y = list.length === 1 ? 0 : (j / (list.length - 1) - 0.5) * h;
      positions.set(list[j].id, { x: sign * x, y, r: nodeRadius(list[j].item.meta.degree) });
    }
  };
  place(left, -1);
  place(right, 1);
  for (let i = 0; i < scene.aggregates.length; i++) {
    const a = scene.aggregates[i];
    positions.set(a.id, {
      x: 0,
      y: h * 0.55 + i * 8,
      r: 12 + Math.sqrt(a.count) * 2,
    });
  }
  return { positions, displacement: meanDisplacement(positions, ctx.previous) };
}

export const adaptiveLayout: LayoutAdapter = {
  id: "adaptive",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    switch (classifyTopology(scene)) {
      case "tree":
        return hyperbolicLayout.layout(scene, ctx);
      case "chain":
        return layeredLayout(scene, ctx);
      case "bipartite":
        return twoSidedLayout(scene, ctx);
      case "mesh":
        return forceLayout.layout(scene, ctx);
      default:
        return focusLayout.layout(scene, ctx);
    }
  },
};
