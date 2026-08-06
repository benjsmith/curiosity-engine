/**
 * Hyperbolic 2D focus-plus-context layout (P3; PLAN §8.3).
 *
 * Same sector/ring assignment as the focus layout — the comparison is
 * geometry only (identical scene data and controls) — mapped through a
 * Poincaré-disc-style radial compression: rho = R·tanh(k·hopDist).
 * Item sizes shrink toward the rim (apparent metric), which buys more
 * visible context in the same viewport at the cost of rim legibility;
 * whether that trade wins is exactly what P3 measures.
 */

import { anchorAngle } from "../scene/landmarks.ts";
import { aggregateRadius, meanDisplacement, nodeRadius, type LayoutAdapter, type LayoutContext } from "./types.ts";
import type { DiscoveryClass, LayoutPoint, LayoutResult, SceneData } from "../types.ts";

const CLASS_ORDER: DiscoveryClass[] = ["direct", "adjacent", "bridge", "contrast", "surprise", "unexplored"];
const K = 0.62; // hyperbolic compression rate

export const hyperbolicLayout: LayoutAdapter = {
  id: "hyperbolic",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    const R = Math.min(ctx.viewport.width, ctx.viewport.height) * 0.47;
    const positions = new Map<string, LayoutPoint>();
    const horizonIds = new Map<string, DiscoveryClass>();
    for (const grp of scene.horizon) for (const c of grp.candidates) horizonIds.set(c.id, grp.cls);

    type P = { id: string; sector: number; hop: number; r: number; order: number };
    const placements: P[] = [];
    for (const n of scene.nodes) {
      if (n.role === "focus") {
        positions.set(n.id, { x: 0, y: 0, r: nodeRadius(n.item.meta.degree) + 3 });
        continue;
      }
      const cls = horizonIds.get(n.id);
      if (cls) {
        const arc = (2 * Math.PI) / CLASS_ORDER.length;
        const sector = CLASS_ORDER.indexOf(cls) * arc + arc / 2 - Math.PI / 2;
        placements.push({ id: n.id, sector, hop: 3.4, r: nodeRadius(n.item.meta.degree), order: n.score });
        continue;
      }
      placements.push({
        id: n.id,
        sector: anchorAngle(`type:${n.item.type}`),
        hop: Math.max(1, n.ring ?? 2),
        r: nodeRadius(n.item.meta.degree),
        order: n.score,
      });
    }
    for (const a of scene.aggregates) {
      placements.push({
        id: a.id,
        sector: anchorAngle(`type:${a.type}`),
        hop: 2.6,
        r: aggregateRadius(a.count),
        order: a.count,
      });
    }

    const groups = new Map<string, P[]>();
    for (const p of placements) {
      const key = `${p.sector.toFixed(3)}|${p.hop.toFixed(1)}`;
      (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
    }
    for (const list of groups.values()) {
      list.sort((a, b) => b.order - a.order || (a.id < b.id ? -1 : 1));
      const n = list.length;
      const spread = Math.min(1.0, 0.18 * n);
      for (let i = 0; i < n; i++) {
        const p = list[i];
        const offset = n === 1 ? 0 : (i / (n - 1) - 0.5) * spread;
        const hopJitter = p.hop + (i % 3) * 0.18;
        const rho = R * Math.tanh(K * hopJitter);
        const angle = p.sector + offset;
        // Apparent size shrinks toward the rim (Poincaré metric feel).
        const shrink = 1 - 0.65 * (rho / R) ** 2;
        positions.set(p.id, {
          x: Math.cos(angle) * rho,
          y: Math.sin(angle) * rho,
          r: Math.max(2, p.r * shrink),
        });
      }
    }
    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};
