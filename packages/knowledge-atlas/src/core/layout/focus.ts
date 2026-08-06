/**
 * Focus-centred Euclidean layout (P1, the default; PLAN §8.2).
 *
 * Focus fixed at the centre; hop-1 neighbours on ring 1; hop-2 /
 * context on ring 2; aggregates on ring 3; discovery-horizon
 * candidates on the outer band grouped into per-class arcs. Angular
 * position comes from the stable landmark anchor of each node's TYPE
 * (PLAN §8.5) — sectors are geography, so "notes are always in the
 * same direction" and back() returns to a familiar arrangement.
 * Overlap is resolved by fanning within a sector and staggering
 * radius, never by crossing sectors.
 */

import { anchorAngle } from "../scene/landmarks.ts";
import { aggregateRadius, meanDisplacement, nodeRadius, type LayoutAdapter, type LayoutContext } from "./types.ts";
import type { DiscoveryClass, LayoutPoint, LayoutResult, SceneData } from "../types.ts";

const CLASS_ORDER: DiscoveryClass[] = ["direct", "adjacent", "bridge", "contrast", "surprise", "unexplored"];

export function ringRadii(viewport: { width: number; height: number }) {
  const base = Math.min(viewport.width, viewport.height);
  return { r1: base * 0.17, r2: base * 0.29, r3: base * 0.40, horizon: base * 0.485 };
}

type Placement = { id: string; sector: number; ring: number; r: number; order: number };

export const focusLayout: LayoutAdapter = {
  id: "focus",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    const { r1, r2, r3, horizon } = ringRadii(ctx.viewport);
    const positions = new Map<string, LayoutPoint>();
    const horizonIds = new Map<string, DiscoveryClass>();
    for (const grp of scene.horizon) {
      for (const c of grp.candidates) horizonIds.set(c.id, grp.cls);
    }

    const placements: Placement[] = [];
    for (const n of scene.nodes) {
      if (n.role === "focus") {
        positions.set(n.id, { x: 0, y: 0, r: nodeRadius(n.item.meta.degree) + 3 });
        continue;
      }
      const cls = horizonIds.get(n.id);
      if (cls) {
        // Horizon band: per-class arcs on the outer ring, fixed order.
        const arc = (2 * Math.PI) / CLASS_ORDER.length;
        const sector = CLASS_ORDER.indexOf(cls) * arc + arc / 2 - Math.PI / 2;
        placements.push({ id: n.id, sector, ring: horizon, r: nodeRadius(n.item.meta.degree), order: n.score });
        continue;
      }
      const sector = anchorAngle(`type:${n.item.type}`);
      const ring = n.role === "pinned" ? r2 : (n.ring ?? 2) <= 1 ? r1 : r2;
      placements.push({ id: n.id, sector, ring, r: nodeRadius(n.item.meta.degree), order: n.score });
    }
    for (const a of scene.aggregates) {
      const sector = a.anchorId !== undefined && positions.has(a.anchorId)
        ? Math.atan2(positions.get(a.anchorId)!.y, positions.get(a.anchorId)!.x)
        : anchorAngle(`type:${a.type}`);
      placements.push({ id: a.id, sector, ring: r3, r: aggregateRadius(a.count), order: a.count });
    }

    // Group by (sector bucket, ring) and fan members apart.
    const groups = new Map<string, Placement[]>();
    for (const p of placements) {
      const key = `${p.sector.toFixed(3)}|${p.ring.toFixed(1)}`;
      const list = groups.get(key) ?? [];
      list.push(p);
      groups.set(key, list);
    }
    for (const list of groups.values()) {
      list.sort((a, b) => b.order - a.order || (a.id < b.id ? -1 : 1));
      const n = list.length;
      // Enough arc for the group, capped so sectors stay distinct.
      const spread = Math.min(0.9, 0.16 * n);
      for (let i = 0; i < n; i++) {
        const p = list[i];
        const offset = n === 1 ? 0 : (i / (n - 1) - 0.5) * spread;
        // Radial stagger avoids same-arc collisions without leaving
        // the sector.
        const radius = p.ring * (1 + (i % 3) * 0.055);
        const angle = p.sector + offset;
        positions.set(p.id, {
          x: Math.cos(angle) * radius,
          y: Math.sin(angle) * radius,
          r: p.r,
        });
      }
    }
    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};
