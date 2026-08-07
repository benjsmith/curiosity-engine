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
  forceX,
  forceY,
} from "d3-force";
import { anchorAngle } from "../scene/landmarks.ts";
import { coreRadius, rimRadiusAt } from "../geometry.ts";
import {
  aggregateRadius,
  meanDisplacement,
  nodeRadius,
  type LayoutAdapter,
  type LayoutContext,
} from "./types.ts";
import type { DiscoveryClass, LayoutPoint, LayoutResult, SceneData } from "../types.ts";

export { coreRadius, rimRadiusAt } from "../geometry.ts";

const CLASS_ORDER: DiscoveryClass[] = ["direct", "adjacent", "bridge", "contrast", "surprise", "unexplored"];

/** Share of non-horizon, non-focus nodes kept in the force core
 * (iteration-3: raised from 0.67). */
export const CORE_SHARE = 0.9;

/** Core membership — shared with the adaptive-hybrid variant so both
 * modes agree on who is core and who is rim. */
export function partitionCore(scene: SceneData): Set<string> {
  const horizonIds = new Set(scene.horizon.flatMap((g) => g.candidates.map((c) => c.id)));
  const focus = scene.nodes.find((n) => n.role === "focus");
  const candidates = scene.nodes.filter((n) => n.role !== "focus" && !horizonIds.has(n.id));
  const sorted = [...candidates].sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
  const coreSet = new Set(sorted.slice(0, Math.ceil(sorted.length * CORE_SHARE)).map((n) => n.id));
  if (focus) coreSet.add(focus.id);
  return coreSet;
}

type SimNode = { id: string; r: number; x?: number; y?: number };

export const hybridLayout: LayoutAdapter = {
  id: "hybrid",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    const Rcore = coreRadius(ctx.viewport);
    const rimInner = Rcore * 1.18;
    const positions = new Map<string, LayoutPoint>();

    const horizonIds = new Map<string, DiscoveryClass>();
    for (const grp of scene.horizon) for (const c of grp.candidates) horizonIds.set(c.id, grp.cls);

    // ── partition ────────────────────────────────────────────────────
    const coreSet = partitionCore(scene);

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
    // Classic-viewer character (mesh, not star: focus NOT pinned — its
    // accent ring identifies it), but with distances scaled to the
    // zone: running the raw −420/110 constants and then shrinking to
    // fit visually crushed node spacing. Sizing link/charge from Rcore
    // and node count keeps the natural extent ≈ the zone, so the fit
    // factor stays near 1 and collide spacing survives on screen.
    const n = Math.max(1, coreNodes.length);
    const linkDist = Math.max(34, (Rcore * 2.2) / Math.sqrt(n));
    const sim = forceSimulation(coreNodes as never[])
      .force(
        "link",
        forceLink(coreLinks as never[])
          .id((d) => (d as unknown as SimNode).id)
          .distance(linkDist)
          .strength(0.55),
      )
      .force("charge", forceManyBody().strength(-Rcore * 1.15).distanceMax(Rcore * 2))
      .force("center", forceCenter(0, 0).strength(0.05))
      .force("collide", forceCollide((d) => (d as unknown as SimNode).r + 8))
      .stop();
    for (let i = 0; i < 350; i++) sim.tick();

    // Centre on the cloud's centroid, then FIT: uniform scale (up or
    // down) against the 92nd-percentile radius preserves the natural
    // mesh shape and fills the zone; the few stragglers beyond it are
    // projected onto the boundary.
    let cx = 0;
    let cy = 0;
    for (const sn of coreNodes) {
      cx += sn.x ?? 0;
      cy += sn.y ?? 0;
    }
    cx /= Math.max(1, coreNodes.length);
    cy /= Math.max(1, coreNodes.length);
    const radii = coreNodes
      .map((sn) => Math.hypot((sn.x ?? 0) - cx, (sn.y ?? 0) - cy))
      .sort((a, b) => a - b);
    const p92 = radii[Math.min(radii.length - 1, Math.floor(radii.length * 0.92))] || 1;
    const rMax = Rcore * 0.93;
    const fit = rMax / p92;
    type FitNode = SimNode & { x0: number; y0: number };
    const fitted: FitNode[] = coreNodes.map((sn) => {
      let x = ((sn.x ?? 0) - cx) * fit;
      let y = ((sn.y ?? 0) - cy) * fit;
      const d = Math.hypot(x, y);
      if (d > rMax) {
        x *= rMax / d;
        y *= rMax / d;
      }
      return { id: sn.id, r: sn.r, x, y, x0: x, y0: y };
    });
    // Screen-space de-overlap: collide-only relaxation with weak
    // anchors back to the fitted positions — resolves touching nodes
    // without changing the mesh's global shape.
    const relax = forceSimulation(fitted as never[])
      .force("collide", forceCollide((d) => (d as unknown as FitNode).r + 6).strength(1))
      .force("x", forceX((d: unknown) => (d as FitNode).x0).strength(0.25))
      .force("y", forceY((d: unknown) => (d as FitNode).y0).strength(0.25))
      .stop();
    for (let i = 0; i < 80; i++) relax.tick();
    for (const fn of fitted) {
      let x = fn.x ?? 0;
      let y = fn.y ?? 0;
      const d = Math.hypot(x, y);
      if (d > rMax) {
        x *= rMax / d;
        y *= rMax / d;
      }
      positions.set(fn.id, { x, y, r: fn.r });
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

    // Gridlike shelves per sector (iteration-3 feedback): items fill
    // ordered rows stacking OUTWARD from the core boundary toward the
    // squircle rim, columns fanned tangentially. Highest-relevance row
    // sits innermost; diagonal sectors have a deeper radial run, so
    // the screen corners fill with aligned rows instead of staying
    // empty. Grouped by sector only — the hop band now just biases the
    // ordering so nearer material lands on inner rows.
    const ROW_GAP = 34;
    const COL_GAP = 34;
    const SECTOR_ARC = 0.78; // max tangential fan per sector (radians)
    const groups = new Map<string, P[]>();
    for (const p of placements) {
      const key = p.sector.toFixed(3);
      (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
    }
    for (const list of groups.values()) {
      list.sort(
        (a, b) => a.band - b.band || b.order - a.order || (a.id < b.id ? -1 : 1),
      );
      const sector = list[0].sector;
      const Router = rimRadiusAt(sector, ctx.viewport);
      // Shelf block anchored against the OUTER squircle wall so the
      // rim content sits out in the corners; within the block, the
      // innermost row holds the highest-relevance items (nearer stays
      // nearer). Row capacity from the tangential arc at the wall.
      const capacity = Math.max(1, Math.floor((SECTOR_ARC * (Router - 14)) / COL_GAP));
      const rows = Math.ceil(list.length / capacity);
      const innermostRho = Math.max(rimInner + 8, Router - 14 - (rows - 1) * ROW_GAP);
      for (let i = 0; i < list.length; i++) {
        const p = list[i];
        const row = Math.floor(i / capacity);
        const rowItems = Math.min(capacity, list.length - row * capacity);
        const c = i - row * capacity;
        const rho = Math.min(Router - 14, innermostRho + row * ROW_GAP);
        const offset =
          rowItems === 1 ? 0 : (c - (rowItems - 1) / 2) * Math.min(COL_GAP / rho, SECTOR_ARC / rowItems);
        const angle = sector + offset;
        const RouterHere = rimRadiusAt(angle, ctx.viewport);
        const clampedRho = Math.min(rho, RouterHere - 12);
        const shrink = 1 - 0.45 * (clampedRho / Math.max(RouterHere, rimInner + 1)) ** 2;
        positions.set(p.id, {
          x: Math.cos(angle) * clampedRho,
          y: Math.sin(angle) * clampedRho,
          r: Math.max(2.5, p.r * shrink),
        });
      }
    }

    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};
