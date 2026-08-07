/**
 * Hybrid layout (iteration-2 user feedback): a classic force-directed
 * graph in a central zone — holding roughly 67% of the scene's
 * individually-visible nodes — that transitions at its boundary into
 * the hyperbolic, doc-type-organised rim (type sectors, aggregates,
 * discovery horizon). The centre feels like the original viewer; the
 * rim keeps the stable type geography and the "field of possible
 * directions".
 *
 * The lens interaction lives in the adapters: camera zoom inside the
 * core radius, sector pull-in over the rim. This module only decides
 * who is core, who is rim, and where they sit.
 */

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
} from "d3-force";
import { anchorAngle } from "../scene/landmarks.ts";
import {
  aggregateRadius,
  meanDisplacement,
  nodeRadius,
  type LayoutAdapter,
  type LayoutContext,
} from "./types.ts";
import type { DiscoveryClass, LayoutPoint, LayoutResult, SceneData } from "../types.ts";

const CLASS_ORDER: DiscoveryClass[] = ["direct", "adjacent", "bridge", "contrast", "surprise", "unexplored"];

/** Share of non-horizon, non-focus nodes kept in the force core. */
export const CORE_SHARE = 0.67;

/** Core-zone radius for a viewport (shared with the adapters' lens). */
export function coreRadius(viewport: { width: number; height: number }): number {
  return Math.min(viewport.width, viewport.height) * 0.26;
}

export function discRadius(viewport: { width: number; height: number }): number {
  return Math.min(viewport.width, viewport.height) * 0.47;
}

type SimNode = { id: string; r: number; x?: number; y?: number };

export const hybridLayout: LayoutAdapter = {
  id: "hybrid",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    const Rcore = coreRadius(ctx.viewport);
    const R = discRadius(ctx.viewport);
    const rimInner = Rcore * 1.22;
    const positions = new Map<string, LayoutPoint>();

    const horizonIds = new Map<string, DiscoveryClass>();
    for (const grp of scene.horizon) for (const c of grp.candidates) horizonIds.set(c.id, grp.cls);

    // ── partition ────────────────────────────────────────────────────
    const focus = scene.nodes.find((n) => n.role === "focus");
    const candidates = scene.nodes.filter((n) => n.role !== "focus" && !horizonIds.has(n.id));
    const sorted = [...candidates].sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
    const coreCount = Math.ceil(sorted.length * CORE_SHARE);
    const coreSet = new Set(sorted.slice(0, coreCount).map((n) => n.id));
    if (focus) coreSet.add(focus.id);

    // ── core: force-directed, then clamped into the core disc ───────
    const coreNodes: SimNode[] = scene.nodes
      .filter((n) => coreSet.has(n.id))
      .map((n) => ({ id: n.id, r: nodeRadius(n.item.meta.degree) }));
    for (const sn of coreNodes) {
      const prev = ctx.previous?.positions.get(sn.id);
      if (prev) {
        sn.x = prev.x;
        sn.y = prev.y;
      }
    }
    const coreLinks = scene.edges
      .filter((e) => coreSet.has(e.source) && coreSet.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }));
    const sim = forceSimulation(coreNodes as never[])
      .force(
        "link",
        forceLink(coreLinks as never[])
          .id((d) => (d as unknown as SimNode).id)
          .distance(52)
          .strength(0.6),
      )
      .force("charge", forceManyBody().strength(-180).distanceMax(Rcore * 2))
      .force("center", forceCenter(0, 0).strength(0.08))
      .force("collide", forceCollide((d) => (d as unknown as SimNode).r + 6))
      .stop();
    for (let i = 0; i < 300; i++) sim.tick();

    // Pin the focus at the exact centre, shift the cloud accordingly.
    const focusSim = focus ? coreNodes.find((n) => n.id === focus.id) : undefined;
    const cx = focusSim?.x ?? 0;
    const cy = focusSim?.y ?? 0;
    let maxR = 1;
    for (const sn of coreNodes) {
      sn.x = (sn.x ?? 0) - cx;
      sn.y = (sn.y ?? 0) - cy;
      const d = Math.hypot(sn.x, sn.y);
      if (d > maxR) maxR = d;
    }
    const clamp = Math.min(1, (Rcore * 0.92) / maxR);
    for (const sn of coreNodes) {
      positions.set(sn.id, { x: sn.x! * clamp, y: sn.y! * clamp, r: sn.r });
    }

    // ── rim: hyperbolic doc-type sectors ─────────────────────────────
    type P = { id: string; sector: number; band: number; r: number; order: number };
    const placements: P[] = [];
    for (const n of scene.nodes) {
      if (coreSet.has(n.id)) continue;
      const cls = horizonIds.get(n.id);
      if (cls) {
        const arc = (2 * Math.PI) / CLASS_ORDER.length;
        const sector = CLASS_ORDER.indexOf(cls) * arc + arc / 2 - Math.PI / 2;
        placements.push({ id: n.id, sector, band: 1, r: nodeRadius(n.item.meta.degree), order: n.score });
        continue;
      }
      placements.push({
        id: n.id,
        sector: anchorAngle(`type:${n.item.type}`),
        band: Math.min(1, Math.max(0, ((n.ring ?? 2) - 1) / 2.4)),
        r: nodeRadius(n.item.meta.degree),
        order: n.score,
      });
    }
    for (const a of scene.aggregates) {
      placements.push({
        id: a.id,
        sector: anchorAngle(`type:${a.type}`),
        band: 0.55,
        r: aggregateRadius(a.count),
        order: a.count,
      });
    }

    const groups = new Map<string, P[]>();
    for (const p of placements) {
      const key = `${p.sector.toFixed(3)}|${p.band.toFixed(2)}`;
      (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
    }
    for (const list of groups.values()) {
      list.sort((a, b) => b.order - a.order || (a.id < b.id ? -1 : 1));
      const n = list.length;
      const spread = Math.min(1.0, 0.17 * n);
      for (let i = 0; i < n; i++) {
        const p = list[i];
        const offset = n === 1 ? 0 : (i / (n - 1) - 0.5) * spread;
        // tanh compression from the core boundary out to the disc rim.
        const t = Math.tanh(1.4 * (p.band + (i % 3) * 0.09));
        const rho = rimInner + (R - rimInner) * t;
        const shrink = 1 - 0.5 * (rho / R) ** 2;
        const angle = p.sector + offset;
        positions.set(p.id, {
          x: Math.cos(angle) * rho,
          y: Math.sin(angle) * rho,
          r: Math.max(2.5, p.r * shrink),
        });
      }
    }

    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};
