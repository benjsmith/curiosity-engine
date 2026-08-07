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

    // ── stability gradient (iteration-6 feedback) ────────────────────
    // Refocusing on a node that was already INSIDE the core must not
    // reorganise the whole graph zone: surviving nodes keep their
    // positions and only newcomers settle in around them. A focus that
    // came from the rim/off-screen legitimately re-organises — that
    // click asked to bring a different portion of the graph in.
    const focusNode = scene.nodes.find((n) => n.role === "focus");
    const prevFocusPos = focusNode ? ctx.previous?.positions.get(focusNode.id) : undefined;
    const stable =
      !!prevFocusPos && Math.hypot(prevFocusPos.x, prevFocusPos.y) <= Rcore;
    if (stable && ctx.previous) {
      type StableNode = SimNode & { x0: number; y0: number; survivor: boolean };
      const rMaxS = Rcore * 0.93;
      const nodes: StableNode[] = coreNodes.map((sn) => {
        const prev = ctx.previous!.positions.get(sn.id);
        if (prev && Math.hypot(prev.x, prev.y) <= Rcore) {
          return { ...sn, x: prev.x, y: prev.y, x0: prev.x, y0: prev.y, survivor: true };
        }
        // Newcomer (or arrived from the rim): seed near the mean of its
        // already-placed neighbours, else near the focus.
        let sx = 0;
        let sy = 0;
        let k = 0;
        for (const e of coreLinks) {
          const other = e.source === sn.id ? e.target : e.target === sn.id ? e.source : null;
          if (!other) continue;
          const p = ctx.previous!.positions.get(other);
          if (p && Math.hypot(p.x, p.y) <= Rcore) {
            sx += p.x;
            sy += p.y;
            k++;
          }
        }
        if (k === 0 && prevFocusPos) {
          sx = prevFocusPos.x;
          sy = prevFocusPos.y;
          k = 1;
        }
        const jitter = 18 + (sn.r ?? 6);
        const seed = {
          x: (k ? sx / k : 0) + Math.cos(hash01(sn.id) * 2 * Math.PI) * jitter,
          y: (k ? sy / k : 0) + Math.sin(hash01(sn.id) * 2 * Math.PI) * jitter,
        };
        return { ...sn, x: seed.x, y: seed.y, x0: seed.x, y0: seed.y, survivor: false };
      });
      const settle = forceSimulation(nodes as never[])
        .force("collide", forceCollide((d) => (d as unknown as StableNode).r + 6).strength(1))
        .force("x", forceX((d: unknown) => (d as StableNode).x0).strength((d: unknown) => ((d as StableNode).survivor ? 0.55 : 0.08)))
        .force("y", forceY((d: unknown) => (d as StableNode).y0).strength((d: unknown) => ((d as StableNode).survivor ? 0.55 : 0.08)))
        .stop();
      for (let i = 0; i < 120; i++) settle.tick();
      for (const fn of nodes) {
        let x = fn.x ?? 0;
        let y = fn.y ?? 0;
        const d = Math.hypot(x, y);
        if (d > rMaxS) {
          x *= rMaxS / d;
          y *= rMaxS / d;
        }
        positions.set(fn.id, { x, y, r: fn.r });
      }
      placeRim(scene, coreSet, horizonIds, positions, ctx, rimInner);
      return { positions, displacement: meanDisplacement(positions, ctx.previous) };
    }
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

    placeRim(scene, coreSet, horizonIds, positions, ctx, rimInner);
    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};

/** Deterministic per-id 0..1 (newcomer seed jitter). */
function hash01(id: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 4294967296;
}

/**
 * Rim shelves (shared by the full and the stable/incremental core
 * paths): gridlike rows per doc-type sector, anchored against the
 * squircle wall so the corners fill (iteration-3).
 */
function placeRim(
  scene: SceneData,
  coreSet: ReadonlySet<string>,
  horizonIds: ReadonlyMap<string, DiscoveryClass>,
  positions: Map<string, LayoutPoint>,
  ctx: LayoutContext,
  rimInner: number,
): void {
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

  const ROW_GAP = 34;
  const COL_GAP = 34;
  const SECTOR_ARC = 0.78; // max tangential fan per sector (radians)
  const groups = new Map<string, P[]>();
  for (const p of placements) {
    const key = p.sector.toFixed(3);
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => a.band - b.band || b.order - a.order || (a.id < b.id ? -1 : 1));
    const sector = list[0].sector;
    const Router = rimRadiusAt(sector, ctx.viewport);
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
}
