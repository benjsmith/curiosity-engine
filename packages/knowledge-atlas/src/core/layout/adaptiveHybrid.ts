/**
 * Adaptive-hybrid layout (P7, iteration-4 feedback): the hybrid's
 * core/rim/lens structure, but the CORE's internal arrangement adapts
 * to the local topology. When the focus neighbourhood is star-, chain-,
 * tree- or bipartite-shaped, the core becomes a gridlike columnar view
 * (focus at the left, typed columns by hop — the arrangement the
 * adaptive P5 mode produced that read well); dense meshes keep the
 * force cloud. The doc-type squircle rim shelves and the lens are
 * identical to plain hybrid.
 */

import { classifyTopology } from "./adaptive.ts";
import { coreRadius } from "../geometry.ts";
import { hybridLayout, partitionCore } from "./hybrid.ts";
import { nodeRadius, type LayoutAdapter, type LayoutContext } from "./types.ts";
import type { LayoutResult, SceneData } from "../types.ts";

/** Column x-positions inside the core, as fractions of its radius.
 * Three columns only (focus / hop-1 / hop-2+): a fourth sat too close
 * to the third and its labels overlapped the next column's dots. */
const COLUMN_X = [-0.78, -0.12, 0.55];

export const adaptiveHybridLayout: LayoutAdapter = {
  id: "adaptive-hybrid",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    // Base: full hybrid (force core + rim shelves).
    const base = hybridLayout.layout(scene, ctx);

    // Classify ONLY the core subgraph — the rim always stays shelved.
    const coreSet = partitionCore(scene);
    const coreNodes = scene.nodes.filter((n) => coreSet.has(n.id));
    const coreScene: SceneData = {
      ...scene,
      nodes: coreNodes,
      edges: scene.edges.filter((e) => coreSet.has(e.source) && coreSet.has(e.target)),
      aggregates: [],
      bundles: [],
      horizon: [],
    };
    const topo = classifyTopology(coreScene);
    if (topo !== "chain" && topo !== "tree" && topo !== "bipartite") {
      return base; // mesh/mixed: keep the force cloud
    }

    // Columnar core: focus left, then one typed column per hop band.
    const Rcore = coreRadius(ctx.viewport);
    const byColumn = new Map<number, typeof coreNodes>();
    for (const n of coreNodes) {
      const col = n.role === "focus" ? 0 : Math.min(2, Math.max(1, n.ring ?? 1));
      (byColumn.get(col) ?? byColumn.set(col, []).get(col)!).push(n);
    }
    for (const [col, list] of byColumn) {
      // Typed runs read best: group by type, then score desc (this is
      // what made the P5 screenshot legible).
      list.sort(
        (a, b) =>
          (a.item.type < b.item.type ? -1 : a.item.type > b.item.type ? 1 : 0) ||
          b.score - a.score ||
          (a.id < b.id ? -1 : 1),
      );
      const x = COLUMN_X[col] * Rcore;
      // Fit the column inside the core circle at this x.
      const half = Math.sqrt(Math.max(0, Rcore * Rcore - x * x)) * 0.88;
      const n = list.length;
      const step = n > 1 ? Math.min(30, (2 * half) / (n - 1)) : 0;
      for (let i = 0; i < n; i++) {
        const y = n === 1 ? 0 : -((n - 1) * step) / 2 + i * step;
        base.positions.set(list[i].id, {
          x,
          y,
          r: nodeRadius(list[i].item.meta.degree) + (list[i].role === "focus" ? 3 : 0),
        });
      }
    }
    return base;
  },
};
