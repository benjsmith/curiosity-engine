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
import { coreRadius, coreRadiusAt, rimRadiusAt, type Viewport } from "../geometry.ts";
import {
  aggregateRadius,
  meanDisplacement,
  nodeRadius,
  type LayoutAdapter,
  type LayoutContext,
} from "./types.ts";
import { DEFAULT_PHYSICS, type DiscoveryClass, type LayoutPoint, type LayoutResult, type SceneData } from "../types.ts";

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

/**
 * Number of populated shell bands (0 for full-graph scenes). Shared by
 * the engine (capacity), the layout, the renderer, and the adapters so
 * geometry always agrees: empty outer bands hand their space back to
 * the core (iteration-11).
 */
export function populatedShellBands(scene: SceneData): number {
  if (isFullGraphScene(scene)) return 0;
  let bands = 1;
  for (const n of scene.nodes) if (n.shell) bands = Math.max(bands, Math.min(4, n.shell));
  for (const a of scene.aggregates) if (a.shell) bands = Math.max(bands, Math.min(4, a.shell));
  return bands;
}

type SimNode = { id: string; r: number; x?: number; y?: number };
type AnchoredNode = SimNode & { x0: number; y0: number; survivor: boolean };

export const hybridLayout: LayoutAdapter = {
  id: "hybrid",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    if (isFullGraphScene(scene)) return fullGraphLayout(scene, ctx);

    // Geometry (iteration-11): the core squircle is the wall inset by
    // the populated shell depth — anisotropic, screen-filling.
    const bands = populatedShellBands(scene);
    const shape = ctx.boundaryShape;
    const Rcore = coreRadius(ctx.viewport, bands);
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

    // ── stability grade (per-angle: the core is anisotropic) ─────────
    const focusNode = scene.nodes.find((n) => n.role === "focus");
    const prevFocusPos = focusNode ? ctx.previous?.positions.get(focusNode.id) : undefined;
    const prevRho = prevFocusPos ? Math.hypot(prevFocusPos.x, prevFocusPos.y) : Infinity;
    const focusAngle = prevFocusPos ? Math.atan2(prevFocusPos.y, prevFocusPos.x) : 0;
    const coreAtFocus = prevFocusPos ? coreRadiusAt(focusAngle, ctx.viewport, bands, shape) : 0;
    const cumMax = SHELL_CUM[Math.max(1, Math.min(4, bands))];
    const shell1Outer = prevFocusPos
      ? coreAtFocus * 1.03 +
        (rimRadiusAt(focusAngle, ctx.viewport, shape) - coreAtFocus * 1.03) * (SHELL_CUM[1] / cumMax)
      : 0;

    if (ctx.previous && prevRho <= coreAtFocus) {
      // Grade 1 — core click: survivors pinned, newcomers settle in.
      settleCore(coreNodes, coreLinks, ctx, Rcore, bands, positions, { dx: 0, dy: 0 }, prevFocusPos);
      haloSizes(positions, coreSet, ctx.viewport, bands, shape);
      placeShells(scene, coreSet, horizonIds, positions, ctx, bands);
      return { positions, displacement: meanDisplacement(positions, ctx.previous) };
    }
    if (ctx.previous && prevFocusPos && prevRho <= shell1Outer) {
      // Grade 2 — shell-1 click: shift the lens. The whole core field
      // translates opposite the click so the selected node slides
      // inside; the far side drifts toward the boundary.
      const inTarget = coreAtFocus * 0.55;
      const shift = prevRho - inTarget;
      const dx = -Math.cos(focusAngle) * shift;
      const dy = -Math.sin(focusAngle) * shift;
      settleCore(coreNodes, coreLinks, ctx, Rcore, bands, positions, { dx, dy }, prevFocusPos);
      haloSizes(positions, coreSet, ctx.viewport, bands, shape);
      placeShells(scene, coreSet, horizonIds, positions, ctx, bands);
      return { positions, displacement: meanDisplacement(positions, ctx.previous) };
    }

    // Grade 3 — deep click / first layout: full mesh solve.
    fullCoreSolve(coreNodes, coreLinks, ctx, Rcore, bands, positions);
    haloSizes(positions, coreSet, ctx.viewport, bands, shape);
    placeShells(scene, coreSet, horizonIds, positions, ctx, bands);
    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};

/** Seed force nodes independently of input/type ordering. D3's default
 * phyllotaxis preserves array runs, and CE data arrives grouped by page
 * type; disconnected nodes therefore formed misleading type arcs even
 * though no explicit type force existed. Stable id seeding lets links
 * and repulsion determine the visible organization instead. */
function seedGraphPositions(nodes: SimNode[], radius: number): void {
  for (const node of nodes) {
    if (node.x !== undefined && node.y !== undefined) continue;
    const angle = hash01(node.id) * 2 * Math.PI;
    const rho = Math.sqrt(hash01(`${node.id}:radius`)) * radius * 0.72;
    node.x = Math.cos(angle) * rho;
    node.y = Math.sin(angle) * rho;
  }
}

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
 * each pass, so this is idempotent; positions are untouched here).
 * Normalised against the ANISOTROPIC core boundary at each node's own
 * bearing, so the halo hugs the squircle, not a circle. */
function haloSizes(
  positions: Map<string, LayoutPoint>,
  coreSet: ReadonlySet<string>,
  viewport: Viewport,
  bands: number,
  shape: number | undefined,
): void {
  for (const id of coreSet) {
    const p = positions.get(id);
    if (!p) continue;
    const lim = coreRadiusAt(Math.atan2(p.y, p.x), viewport, bands, shape);
    const u = haloU(Math.hypot(p.x, p.y), lim);
    if (u > 0) positions.set(id, { x: p.x, y: p.y, r: p.r * (1 - 0.38 * u * u) });
  }
}

// ── whole-wiki mode (≤ core capacity) ─────────────────────────────────

function fullGraphLayout(scene: SceneData, ctx: LayoutContext): LayoutResult {
  const physics = ctx.physics ?? DEFAULT_PHYSICS;
  const positions = new Map<string, LayoutPoint>();
  const nodes: SimNode[] = scene.nodes.map((n) => ({ id: n.id, r: nodeRadius(n.item.meta.degree) }));

  // A Classic focus is purely visual: opening a page does not perturb
  // the simulation. Preserve the canonical whole-wiki coordinates
  // byte-for-byte when the same node set is requested again.
  if (ctx.previous && nodes.every((sn) => ctx.previous!.positions.has(sn.id))) {
    for (const sn of nodes) {
      const prev = ctx.previous.positions.get(sn.id)!;
      positions.set(sn.id, { x: prev.x, y: prev.y, r: sn.r });
    }
    return { positions, displacement: 0 };
  }

  const links = scene.edges
    .map((e) => ({ source: e.source, target: e.target }))
    .filter((l) => scene.nodes.some((n) => n.id === l.source) && scene.nodes.some((n) => n.id === l.target));
  // Classic parity is intentionally literal here: D3's deterministic
  // phyllotaxis seed, input order, force constants, and 350 ticks. Do
  // not viewport-fit or id-hash-seed this result. Atlas is a projection
  // of the same flat coordinate field, not a second graph layout.
  const sim = forceSimulation(nodes as never[])
    .force(
      "link",
      forceLink(links as never[])
        .id((d) => (d as unknown as SimNode).id)
        .distance(physics.link)
        .strength(0.55),
    )
    .force("charge", forceManyBody().strength(physics.charge).distanceMax(500))
    .force("center", forceCenter(0, 0).strength(0.04))
    .force("collide", forceCollide((d) => (d as unknown as SimNode).r + physics.collide))
    .alpha(1)
    .alphaDecay(0.05)
    .stop();
  // Preserve literal Classic parity for ordinary wikis. At overview
  // scale, progressively reduce solver work; labels/edges are already
  // absent there and the stable global clustering matters more than a
  // fully converged individual-node packing.
  const ticks = nodes.length > 10_000 ? 24 : nodes.length > 2_000 ? 60 : nodes.length > 800 ? 140 : 350;
  for (let i = 0; i < ticks; i++) sim.tick();
  for (const sn of nodes) {
    positions.set(sn.id, { x: sn.x ?? 0, y: sn.y ?? 0, r: sn.r });
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
  bands: number,
  positions: Map<string, LayoutPoint>,
): void {
  for (const sn of coreNodes) {
    const prev = ctx.previous?.positions.get(sn.id);
    if (prev) {
      sn.x = prev.x;
      sn.y = prev.y;
    }
  }
  seedGraphPositions(coreNodes, Rcore);
  const n = Math.max(1, coreNodes.length);
  const physics = ctx.physics ?? DEFAULT_PHYSICS;
  const linkDist = Math.max(12, (Rcore * 2.2) / Math.sqrt(n)) * (physics.link / DEFAULT_PHYSICS.link);
  const charge = -Rcore * 1.15 * (Math.abs(physics.charge) / Math.abs(DEFAULT_PHYSICS.charge));
  const sim = forceSimulation(coreNodes as never[])
    .force(
      "link",
      forceLink(coreLinks as never[])
        .id((d) => (d as unknown as SimNode).id)
        .distance(linkDist)
        .strength(0.55),
    )
    .force("charge", forceManyBody().strength(charge).distanceMax(Rcore * 2))
    .force("center", forceCenter(0, 0).strength(0.05))
    .force("collide", forceCollide((d) => (d as unknown as SimNode).r + physics.collide))
    .stop();
  const ticks = n > 10_000 ? 24 : n > 2_000 ? 60 : n > 800 ? 140 : 350;
  for (let i = 0; i < ticks; i++) sim.tick();

  let cx = 0;
  let cy = 0;
  for (const sn of coreNodes) {
    cx += sn.x ?? 0;
    cy += sn.y ?? 0;
  }
  cx /= n;
  cy /= n;
  // Anisotropic fit (iteration-11): fill the core squircle in both
  // axes — a phone's tall core gets a tall mesh — capped at 1.6× so
  // narrow topologies don't distort.
  const p92 = (arr: number[]) => {
    const s = [...arr].sort((x, y) => x - y);
    return s[Math.min(s.length - 1, Math.floor(s.length * 0.92))] || 1;
  };
  const ca = coreRadiusAt(0, ctx.viewport, bands, ctx.boundaryShape) * 0.93;
  const cb = coreRadiusAt(Math.PI / 2, ctx.viewport, bands, ctx.boundaryShape) * 0.93;
  const fitX = ca / p92(coreNodes.map((sn) => Math.abs((sn.x ?? 0) - cx)));
  const fitY = cb / p92(coreNodes.map((sn) => Math.abs((sn.y ?? 0) - cy)));
  const base = Math.min(fitX, fitY);
  const kx = Math.min(fitX, base * 1.6);
  const ky = Math.min(fitY, base * 1.6);
  const anchored: AnchoredNode[] = coreNodes.map((sn) => {
    const x = ((sn.x ?? 0) - cx) * kx;
    const y = ((sn.y ?? 0) - cy) * ky;
    return { id: sn.id, r: sn.r, x, y, x0: x, y0: y, survivor: true };
  });
  relaxAnchored(anchored, 80, physics.collide);
  clampIntoCore(anchored, ctx.viewport, bands, ctx.boundaryShape, positions);
  // Halo position compression — fresh solves only, so survivor grades
  // (which inherit these positions verbatim) never re-compress them.
  for (const sn of coreNodes) {
    const p = positions.get(sn.id);
    if (!p) continue;
    const lim = coreRadiusAt(Math.atan2(p.y, p.x), ctx.viewport, bands, ctx.boundaryShape);
    const u = haloU(Math.hypot(p.x, p.y), lim);
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
  bands: number,
  positions: Map<string, LayoutPoint>,
  shift: { dx: number; dy: number },
  prevFocusPos: LayoutPoint | undefined,
): void {
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
  relaxAnchored(anchored, 120, (ctx.physics ?? DEFAULT_PHYSICS).collide);
  clampIntoCore(anchored, ctx.viewport, bands, ctx.boundaryShape, positions);
}

function relaxAnchored(nodes: AnchoredNode[], ticks: number, collide = 6): void {
  const relax = forceSimulation(nodes as never[])
    .force("collide", forceCollide((d) => (d as unknown as AnchoredNode).r + collide).strength(1))
    .force("x", forceX((d: unknown) => (d as AnchoredNode).x0).strength((d: unknown) => ((d as AnchoredNode).survivor ? 0.55 : 0.08)))
    .force("y", forceY((d: unknown) => (d as AnchoredNode).y0).strength((d: unknown) => ((d as AnchoredNode).survivor ? 0.55 : 0.08)))
    .stop();
  for (let i = 0; i < ticks; i++) relax.tick();
}

/** Clamp into the anisotropic core squircle (95% of the boundary at
 * each node's own bearing). */
function clampIntoCore(
  nodes: AnchoredNode[],
  viewport: Viewport,
  bands: number,
  shape: number | undefined,
  positions: Map<string, LayoutPoint>,
): void {
  for (const fn of nodes) {
    let x = fn.x ?? 0;
    let y = fn.y ?? 0;
    const d = Math.hypot(x, y);
    const lim = coreRadiusAt(Math.atan2(y, x), viewport, bands, shape) * 0.95;
    if (d > lim) {
      x *= lim / d;
      y *= lim / d;
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
 * tangentially). Document type is visual encoding only, never a
 * coordinate: individual nodes and hollow aggregates inherit the angle
 * of their links/bundles into the core. Unconnected residuals receive a
 * stable neutral spread.
 */
function placeShells(
  scene: SceneData,
  coreSet: ReadonlySet<string>,
  horizonIds: ReadonlyMap<string, DiscoveryClass>,
  positions: Map<string, LayoutPoint>,
  ctx: LayoutContext,
  bands: number,
): void {
  type P = { id: string; sector: number; shell: number; r: number; order: number; isAgg: boolean };
  const placements: P[] = [];
  const relatedAngle = (id: string): number => {
    let vx = 0;
    let vy = 0;
    let count = 0;
    const addCore = (other: string) => {
      if (!coreSet.has(other)) return;
      const point = positions.get(other);
      if (!point) return;
      const angle = Math.atan2(point.y, point.x);
      vx += Math.cos(angle);
      vy += Math.sin(angle);
      count++;
    };
    for (const edge of scene.edges) {
      if (edge.source === id) addCore(edge.target);
      else if (edge.target === id) addCore(edge.source);
    }
    for (const bundle of scene.bundles) {
      if (bundle.source === id) addCore(bundle.target);
      else if (bundle.target === id) addCore(bundle.source);
    }
    if (count > 0 && (vx !== 0 || vy !== 0)) return Math.atan2(vy, vx);
    const previous = ctx.previous?.positions.get(id);
    if (previous && (previous.x !== 0 || previous.y !== 0)) return Math.atan2(previous.y, previous.x);
    return hash01(`boundary:${id}`) * 2 * Math.PI - Math.PI;
  };
  for (const n of scene.nodes) {
    if (coreSet.has(n.id)) continue;
    const shell = Math.max(1, Math.min(4, n.shell ?? 1));
    const cls = horizonIds.get(n.id);
    const sector = cls
      ? CLASS_ORDER.indexOf(cls) * ((2 * Math.PI) / CLASS_ORDER.length) + Math.PI / CLASS_ORDER.length - Math.PI / 2
      : relatedAngle(n.id);
    placements.push({ id: n.id, sector, shell, r: nodeRadius(n.item.meta.degree), order: n.score, isAgg: false });
  }
  for (const a of scene.aggregates) {
    placements.push({
      id: a.id,
      sector: relatedAngle(a.id),
      shell: Math.max(1, Math.min(4, a.shell ?? 1)),
      r: aggregateRadius(a.count),
      order: a.count,
      isAgg: true,
    });
  }

  const COL_GAP = 34;
  const groups = new Map<string, P[]>();
  for (const p of placements) {
    // Quantise only enough for linked material to share a local shelf;
    // there are no global document-type lanes.
    const key = `${p.sector.toFixed(2)}|${p.shell}`;
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(p);
  }
  const SECTOR_ARC = 0.78;
  type Placed = { id: string; angle: number; rho: number; r: number; isAgg: boolean; shell: number };
  const placed: Placed[] = [];
  const cumMax = SHELL_CUM[Math.max(1, Math.min(4, bands))];
  for (const list of groups.values()) {
    list.sort((a, b) => b.order - a.order || (a.id < b.id ? -1 : 1));
    const { sector, shell } = list[0];
    const wall = rimRadiusAt(sector, ctx.viewport, ctx.boundaryShape);
    // Populated bands share the whole core→wall gap (iteration-11):
    // SHELL_CUM renormalised so empty outer bands leave no dead ring.
    const rimInner = coreRadiusAt(sector, ctx.viewport, bands, ctx.boundaryShape) * 1.03;
    const gap = Math.max(24, wall - rimInner);
    const bandIn = rimInner + gap * (SHELL_CUM[shell - 1] / cumMax);
    const bandOut = rimInner + gap * (SHELL_CUM[shell] / cumMax);
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
      const wallHere = rimRadiusAt(angle, ctx.viewport, ctx.boundaryShape);
      const clampedRho = Math.min(rho, wallHere - 10);
      // Hyperbolic distance cue: apparent size falls exponentially by
      // shell and again toward the wall. Individual far-layer nodes
      // become sub-2px marks; large aggregates remain readable because
      // their count-derived base radius is larger.
      const wallDepth = clampedRho / Math.max(wallHere, 1);
      const shrink = Math.exp(-0.32 * (shell - 1)) * (1 - 0.35 * wallDepth ** 2);
      placed.push({
        id: p.id,
        angle,
        rho: clampedRho,
        r: Math.max(0.9, p.r * Math.max(0.16, shrink)),
        isAgg: p.isAgg,
        shell,
      });
    }
  }
  relaxShellBands(placed);
  for (const q of placed) {
    // Re-clamp at the item's FINAL bearing: the relax pass moves items
    // tangentially, and both boundaries are anisotropic — an item slid
    // toward the wide axis must not end up inside the core squircle.
    const wallHere = rimRadiusAt(q.angle, ctx.viewport, ctx.boundaryShape);
    const innerHere = coreRadiusAt(q.angle, ctx.viewport, bands, ctx.boundaryShape) * 1.03 + 4;
    const rho = Math.min(Math.max(q.rho, innerHere), wallHere - 10);
    positions.set(q.id, { x: Math.cos(q.angle) * rho, y: Math.sin(q.angle) * rho, r: q.r });
  }
}

/**
 * Periphery de-overlap (iteration-10): different type sectors and the
 * smear ellipses were piling onto each other. Within each shell band,
 * push radially-close items apart tangentially — packing may be much
 * tighter than the core (2 px pad), but not on top of one another.
 * Extents mirror the renderer: aggregates stretch tangentially by
 * (1 + 0.9·shell) and squash radially; nodes are circles.
 */
function relaxShellBands(
  placed: Array<{ angle: number; rho: number; r: number; isAgg: boolean; shell: number }>,
): void {
  const PAD = 2;
  const tang = (q: { r: number; isAgg: boolean; shell: number }) =>
    q.r * (q.isAgg ? 1 + q.shell * 0.9 : 1) + PAD;
  const radial = (q: { r: number; isAgg: boolean; shell: number }) =>
    q.r * (q.isAgg ? Math.max(0.4, 1 - q.shell * 0.16) : 1) + PAD;
  for (let k = 1; k <= 4; k++) {
    const band = placed.filter((q) => q.shell === k);
    if (band.length < 2) continue;
    for (let pass = 0; pass < 4; pass++) {
      band.sort((a, b) => a.angle - b.angle);
      for (let i = 0; i < band.length; i++) {
        for (let w = 1; w <= 3 && w < band.length; w++) {
          const a = band[i];
          const b = band[(i + w) % band.length];
          let dAng = b.angle - a.angle;
          while (dAng < 0) dAng += 2 * Math.PI;
          while (dAng >= 2 * Math.PI) dAng -= 2 * Math.PI;
          if (Math.abs(a.rho - b.rho) >= radial(a) + radial(b)) continue; // different rows
          const need = (tang(a) + tang(b)) / Math.max(1, (a.rho + b.rho) / 2);
          if (dAng >= need) continue;
          const push = (need - dAng) / 2;
          a.angle -= push;
          b.angle += push;
        }
      }
    }
  }
}
