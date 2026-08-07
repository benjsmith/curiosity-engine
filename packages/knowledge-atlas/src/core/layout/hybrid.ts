/**
 * Hybrid "visible universe" layout (P6 → P9).
 *
 * ≤ coreCapacity (~360) nodes: the whole wiki is ONE classic force
 * graph filling the viewport — identical in feel to the original CE
 * viewer, no boundary structure at all. Beyond that, the central
 * squircle zone holds the focus neighbourhood as a classic mesh, and
 * the rest of the corpus wraps it in exponentially compressed shells
 * (built in scene/shells.ts): granular fringe first, then clustered,
 * then smeared — space compresses toward the horizon like a
 * visible-universe plot, with more radial room in the screen corners
 * (the squircle gap is wider there) and harder compression along the
 * edges.
 *
 * Stability grades (iteration-6/7):
 *   click in the core        → nothing reorganises (survivors pinned)
 *   click in shell 1         → the lens SHIFTS: the field translates so
 *                              the clicked node slides into the core and
 *                              the far side drifts out
 *   click deeper (horizon)   → full re-layout — that click asked for a
 *                              substantially different portion
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

/** Cumulative radial fractions of the core→wall gap per shell. The
 * exponential compression: shell 1 gets half the gap, the outermost
 * sliver holds everything beyond 100k. */
const SHELL_CUM = [0, 0.5, 0.8, 0.95, 1.0];

/** Core membership: nodes the builder did NOT assign to a shell. */
export function partitionCore(scene: SceneData): Set<string> {
  return new Set(scene.nodes.filter((n) => !n.shell).map((n) => n.id));
}

/** True when the scene is a whole-wiki graph with no boundary content. */
export function isFullGraphScene(scene: SceneData): boolean {
  return (
    scene.aggregates.length === 0 &&
    scene.horizon.length === 0 &&
    !scene.nodes.some((n) => n.shell)
  );
}

type SimNode = { id: string; r: number; x?: number; y?: number };
type AnchoredNode = SimNode & { x0: number; y0: number; survivor: boolean };

export const hybridLayout: LayoutAdapter = {
  id: "hybrid",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    if (isFullGraphScene(scene)) return fullGraphLayout(scene, ctx);

    const Rcore = coreRadius(ctx.viewport);
    const rimInner = Rcore * 1.12;
    const positions = new Map<string, LayoutPoint>();
    const horizonIds = new Map<string, DiscoveryClass>();
    for (const grp of scene.horizon) for (const c of grp.candidates) horizonIds.set(c.id, grp.cls);

    const coreSet = partitionCore(scene);
    const coreNodes: SimNode[] = scene.nodes
      .filter((n) => coreSet.has(n.id))
      .map((n) => ({ id: n.id, r: nodeRadius(n.item.meta.degree) }));
    const coreLinks = scene.edges
      .filter((e) => coreSet.has(e.source) && coreSet.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }));

    // ── stability grade ──────────────────────────────────────────────
    const focusNode = scene.nodes.find((n) => n.role === "focus");
    const prevFocusPos = focusNode ? ctx.previous?.positions.get(focusNode.id) : undefined;
    const prevRho = prevFocusPos ? Math.hypot(prevFocusPos.x, prevFocusPos.y) : Infinity;
    const focusAngle = prevFocusPos ? Math.atan2(prevFocusPos.y, prevFocusPos.x) : 0;
    const shell1Outer = prevFocusPos
      ? rimInner + (rimRadiusAt(focusAngle, ctx.viewport) - rimInner) * SHELL_CUM[1]
      : 0;

    if (ctx.previous && prevRho <= Rcore) {
      // Grade 1 — core click: survivors pinned, newcomers settle in.
      settleCore(coreNodes, coreLinks, ctx, Rcore, positions, { dx: 0, dy: 0 }, prevFocusPos);
      haloSizes(positions, coreSet, Rcore);
      placeShells(scene, coreSet, horizonIds, positions, ctx, Rcore, rimInner);
      return { positions, displacement: meanDisplacement(positions, ctx.previous) };
    }
    if (ctx.previous && prevFocusPos && prevRho <= shell1Outer) {
      // Grade 2 — shell-1 click: shift the lens. The whole core field
      // translates opposite the click so the selected node slides
      // inside; the far side drifts toward the boundary.
      const inTarget = Rcore * 0.55;
      const shift = prevRho - inTarget;
      const dx = -Math.cos(focusAngle) * shift;
      const dy = -Math.sin(focusAngle) * shift;
      settleCore(coreNodes, coreLinks, ctx, Rcore, positions, { dx, dy }, prevFocusPos);
      haloSizes(positions, coreSet, Rcore);
      placeShells(scene, coreSet, horizonIds, positions, ctx, Rcore, rimInner);
      return { positions, displacement: meanDisplacement(positions, ctx.previous) };
    }

    // Grade 3 — deep click / first layout: full mesh solve.
    fullCoreSolve(coreNodes, coreLinks, ctx, Rcore, positions);
    haloSizes(positions, coreSet, Rcore);
    placeShells(scene, coreSet, horizonIds, positions, ctx, Rcore, rimInner);
    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};

// ── lensing halo (iteration-9) ───────────────────────────────────────
// A wide flat region in the middle of the core with slight compression
// near the boundary — not a fully convex fisheye. Node size scales
// with degree everywhere (classic radii), but overall scale eases down
// as positions approach the squircle, giving a subtle halo at the rim.

const HALO_FLAT = 0.62; // fraction of Rcore that stays undistorted

/** 0 inside the flat region, easing 0→1 toward the core boundary. */
function haloU(rho: number, Rcore: number): number {
  const t = rho / Math.max(1, Rcore);
  if (t <= HALO_FLAT) return 0;
  return Math.min(1, (t - HALO_FLAT) / (1 - HALO_FLAT));
}

/** Size falloff — applied every layout (radii are rebuilt from degree
 * each pass, so this is idempotent; positions are untouched here). */
function haloSizes(
  positions: Map<string, LayoutPoint>,
  coreSet: ReadonlySet<string>,
  Rcore: number,
): void {
  for (const id of coreSet) {
    const p = positions.get(id);
    if (!p) continue;
    const u = haloU(Math.hypot(p.x, p.y), Rcore);
    if (u > 0) positions.set(id, { x: p.x, y: p.y, r: p.r * (1 - 0.38 * u * u) });
  }
}

// ── whole-wiki mode (≤ core capacity) ─────────────────────────────────

function fullGraphLayout(scene: SceneData, ctx: LayoutContext): LayoutResult {
  const positions = new Map<string, LayoutPoint>();
  const nodes: SimNode[] = scene.nodes.map((n) => ({ id: n.id, r: nodeRadius(n.item.meta.degree) }));

  // Stability: when the node set barely changed (a refocus inside the
  // same wiki), keep every surviving position verbatim — the classic
  // viewer does not reorganise on click.
  if (ctx.previous) {
    let survivors = 0;
    for (const sn of nodes) if (ctx.previous.positions.has(sn.id)) survivors++;
    if (survivors >= nodes.length * 0.7) {
      const anchored: AnchoredNode[] = nodes.map((sn) => {
        const prev = ctx.previous!.positions.get(sn.id);
        if (prev) return { ...sn, x: prev.x, y: prev.y, x0: prev.x, y0: prev.y, survivor: true };
        return { ...sn, x: 0, y: 0, x0: 0, y0: 0, survivor: false };
      });
      relaxAnchored(anchored, 60);
      for (const a of anchored) positions.set(a.id, { x: a.x ?? 0, y: a.y ?? 0, r: a.r });
      return { positions, displacement: meanDisplacement(positions, ctx.previous) };
    }
  }

  const links = scene.edges
    .map((e) => ({ source: e.source, target: e.target }))
    .filter((l) => scene.nodes.some((n) => n.id === l.source) && scene.nodes.some((n) => n.id === l.target));
  // The classic CE viewer constants, verbatim (P0 parity at wiki scale).
  const sim = forceSimulation(nodes as never[])
    .force(
      "link",
      forceLink(links as never[])
        .id((d) => (d as unknown as SimNode).id)
        .distance(110)
        .strength(0.55),
    )
    .force("charge", forceManyBody().strength(-420).distanceMax(500))
    .force("center", forceCenter(0, 0).strength(0.04))
    .force("collide", forceCollide((d) => (d as unknown as SimNode).r + 10))
    .stop();
  for (let i = 0; i < 350; i++) sim.tick();

  // Fit the cloud to the viewport (mildly anisotropic so wide screens
  // fill like the reference; capped so the mesh doesn't distort).
  let cx = 0;
  let cy = 0;
  for (const sn of nodes) {
    cx += sn.x ?? 0;
    cy += sn.y ?? 0;
  }
  cx /= Math.max(1, nodes.length);
  cy /= Math.max(1, nodes.length);
  const xs = nodes.map((sn) => Math.abs((sn.x ?? 0) - cx)).sort((a, b) => a - b);
  const ys = nodes.map((sn) => Math.abs((sn.y ?? 0) - cy)).sort((a, b) => a - b);
  const p95 = (arr: number[]) => arr[Math.min(arr.length - 1, Math.floor(arr.length * 0.95))] || 1;
  const fx = (ctx.viewport.width / 2 - 60) / p95(xs);
  const fy = (ctx.viewport.height / 2 - 50) / p95(ys);
  const base = Math.min(fx, fy);
  const kx = Math.min(fx, base * 1.6);
  const ky = Math.min(fy, base * 1.6);
  for (const sn of nodes) {
    positions.set(sn.id, { x: ((sn.x ?? 0) - cx) * kx, y: ((sn.y ?? 0) - cy) * ky, r: sn.r });
  }
  return { positions, displacement: meanDisplacement(positions, ctx.previous) };
}

// ── core solvers ─────────────────────────────────────────────────────

/** Full mesh solve for the core zone (grade 3). */
function fullCoreSolve(
  coreNodes: SimNode[],
  coreLinks: Array<{ source: string; target: string }>,
  ctx: LayoutContext,
  Rcore: number,
  positions: Map<string, LayoutPoint>,
): void {
  for (const sn of coreNodes) {
    const prev = ctx.previous?.positions.get(sn.id);
    if (prev) {
      sn.x = prev.x;
      sn.y = prev.y;
    }
  }
  const n = Math.max(1, coreNodes.length);
  const linkDist = Math.max(30, (Rcore * 2.2) / Math.sqrt(n));
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
    .force("collide", forceCollide((d) => (d as unknown as SimNode).r + 6))
    .stop();
  for (let i = 0; i < 350; i++) sim.tick();

  let cx = 0;
  let cy = 0;
  for (const sn of coreNodes) {
    cx += sn.x ?? 0;
    cy += sn.y ?? 0;
  }
  cx /= n;
  cy /= n;
  const radii = coreNodes
    .map((sn) => Math.hypot((sn.x ?? 0) - cx, (sn.y ?? 0) - cy))
    .sort((a, b) => a - b);
  const p92 = radii[Math.min(radii.length - 1, Math.floor(radii.length * 0.92))] || 1;
  const rMax = Rcore * 0.93;
  const fit = rMax / p92;
  const anchored: AnchoredNode[] = coreNodes.map((sn) => {
    let x = ((sn.x ?? 0) - cx) * fit;
    let y = ((sn.y ?? 0) - cy) * fit;
    const d = Math.hypot(x, y);
    if (d > rMax) {
      x *= rMax / d;
      y *= rMax / d;
    }
    return { id: sn.id, r: sn.r, x, y, x0: x, y0: y, survivor: true };
  });
  relaxAnchored(anchored, 80);
  clampInto(anchored, rMax, positions);
  // Halo position compression — fresh solves only, so survivor grades
  // (which inherit these positions verbatim) never re-compress them.
  for (const sn of coreNodes) {
    const p = positions.get(sn.id);
    if (!p) continue;
    const u = haloU(Math.hypot(p.x, p.y), Rcore);
    if (u > 0) {
      const k = 1 - 0.12 * u * u;
      positions.set(sn.id, { x: p.x * k, y: p.y * k, r: p.r });
    }
  }
}

/** Grades 1–2: survivors keep (optionally shifted) positions; only
 * newcomers settle in around their neighbours. */
function settleCore(
  coreNodes: SimNode[],
  coreLinks: Array<{ source: string; target: string }>,
  ctx: LayoutContext,
  Rcore: number,
  positions: Map<string, LayoutPoint>,
  shift: { dx: number; dy: number },
  prevFocusPos: LayoutPoint | undefined,
): void {
  const rMax = Rcore * 0.93;
  const anchored: AnchoredNode[] = coreNodes.map((sn) => {
    const prev = ctx.previous!.positions.get(sn.id);
    if (prev) {
      const x = prev.x + shift.dx;
      const y = prev.y + shift.dy;
      return { ...sn, x, y, x0: x, y0: y, survivor: true };
    }
    // Newcomer: seed near already-placed neighbours, else the focus.
    let sx = 0;
    let sy = 0;
    let k = 0;
    for (const e of coreLinks) {
      const other = e.source === sn.id ? e.target : e.target === sn.id ? e.source : null;
      if (!other) continue;
      const p = ctx.previous!.positions.get(other);
      if (p && Math.hypot(p.x, p.y) <= Rcore * 1.4) {
        sx += p.x + shift.dx;
        sy += p.y + shift.dy;
        k++;
      }
    }
    if (k === 0 && prevFocusPos) {
      sx = prevFocusPos.x + shift.dx;
      sy = prevFocusPos.y + shift.dy;
      k = 1;
    }
    const jitter = 18 + (sn.r ?? 6);
    const a = hash01(sn.id) * 2 * Math.PI;
    const seed = { x: (k ? sx / k : 0) + Math.cos(a) * jitter, y: (k ? sy / k : 0) + Math.sin(a) * jitter };
    return { ...sn, x: seed.x, y: seed.y, x0: seed.x, y0: seed.y, survivor: false };
  });
  relaxAnchored(anchored, 120);
  clampInto(anchored, rMax, positions);
}

function relaxAnchored(nodes: AnchoredNode[], ticks: number): void {
  const relax = forceSimulation(nodes as never[])
    .force("collide", forceCollide((d) => (d as unknown as AnchoredNode).r + 6).strength(1))
    .force("x", forceX((d: unknown) => (d as AnchoredNode).x0).strength((d: unknown) => ((d as AnchoredNode).survivor ? 0.55 : 0.08)))
    .force("y", forceY((d: unknown) => (d as AnchoredNode).y0).strength((d: unknown) => ((d as AnchoredNode).survivor ? 0.55 : 0.08)))
    .stop();
  for (let i = 0; i < ticks; i++) relax.tick();
}

function clampInto(nodes: AnchoredNode[], rMax: number, positions: Map<string, LayoutPoint>): void {
  for (const fn of nodes) {
    let x = fn.x ?? 0;
    let y = fn.y ?? 0;
    const d = Math.hypot(x, y);
    if (d > rMax) {
      x *= rMax / d;
      y *= rMax / d;
    }
    positions.set(fn.id, { x, y, r: fn.r });
  }
}

/** Deterministic per-id 0..1 (newcomer seed jitter). */
function hash01(id: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 4294967296;
}

// ── shells ───────────────────────────────────────────────────────────

/**
 * Boundary structure: each shell occupies an exponentially thinner
 * radial band of the core→wall gap (SHELL_CUM). The gap is wider
 * toward the screen corners, so compression is gentler there and
 * hardest along the viewport edges. Horizon candidates live in shell 1
 * under their class sectors; everything else sits under its doc-type
 * sector, rows stacked outward — granular near the core, smeared at
 * the wall (the renderer stretches high-shell aggregates
 * tangentially).
 */
function placeShells(
  scene: SceneData,
  coreSet: ReadonlySet<string>,
  horizonIds: ReadonlyMap<string, DiscoveryClass>,
  positions: Map<string, LayoutPoint>,
  ctx: LayoutContext,
  Rcore: number,
  rimInner: number,
): void {
  void Rcore;
  type P = { id: string; sector: number; shell: number; r: number; order: number };
  const placements: P[] = [];
  for (const n of scene.nodes) {
    if (coreSet.has(n.id)) continue;
    const shell = Math.max(1, Math.min(4, n.shell ?? 1));
    const cls = horizonIds.get(n.id);
    const sector = cls
      ? CLASS_ORDER.indexOf(cls) * ((2 * Math.PI) / CLASS_ORDER.length) + Math.PI / CLASS_ORDER.length - Math.PI / 2
      : anchorAngle(`type:${n.item.type}`);
    placements.push({ id: n.id, sector, shell, r: nodeRadius(n.item.meta.degree), order: n.score });
  }
  for (const a of scene.aggregates) {
    placements.push({
      id: a.id,
      sector: anchorAngle(`type:${a.type}`),
      shell: Math.max(1, Math.min(4, a.shell ?? 1)),
      r: aggregateRadius(a.count),
      order: a.count,
    });
  }

  const COL_GAP = 34;
  const groups = new Map<string, P[]>();
  for (const p of placements) {
    const key = `${p.sector.toFixed(3)}|${p.shell}`;
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
  }
  const SECTOR_ARC = 0.78;
  for (const list of groups.values()) {
    list.sort((a, b) => b.order - a.order || (a.id < b.id ? -1 : 1));
    const { sector, shell } = list[0];
    const wall = rimRadiusAt(sector, ctx.viewport);
    const gap = Math.max(24, wall - rimInner);
    const bandIn = rimInner + gap * SHELL_CUM[shell - 1];
    const bandOut = rimInner + gap * SHELL_CUM[shell];
    const bandMid = (bandIn + bandOut) / 2;
    const bandDepth = Math.max(10, bandOut - bandIn);
    const capacity = Math.max(1, Math.floor((SECTOR_ARC * bandMid) / COL_GAP));
    const rows = Math.max(1, Math.ceil(list.length / capacity));
    for (let i = 0; i < list.length; i++) {
      const p = list[i];
      const row = Math.floor(i / capacity);
      const rowItems = Math.min(capacity, list.length - row * capacity);
      const c = i - row * capacity;
      const rho = rows === 1 ? bandMid : bandIn + 6 + (row / Math.max(1, rows - 1)) * (bandDepth - 12);
      const offset =
        rowItems === 1 ? 0 : (c - (rowItems - 1) / 2) * Math.min(COL_GAP / rho, SECTOR_ARC / rowItems);
      const angle = p.sector + offset;
      const wallHere = rimRadiusAt(angle, ctx.viewport);
      const clampedRho = Math.min(rho, wallHere - 10);
      // Sizes shrink with shell — granular → smeared.
      const shrink = 1 - 0.18 * (shell - 1) - 0.25 * (clampedRho / Math.max(wallHere, 1)) ** 2;
      positions.set(p.id, {
        x: Math.cos(angle) * clampedRho,
        y: Math.sin(angle) * clampedRho,
        r: Math.max(2, p.r * Math.max(0.35, shrink)),
      });
    }
  }
}
