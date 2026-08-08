/**
 * Hybrid-mode lens (iteration-2 feedback): zoom acts as a lens over
 * the hyperbolic rim. Wheel/pinch over the central force zone is plain
 * geometric zoom; over the rim it pulls that angular sector inward —
 * visually first (positions interpolate toward the core), then, when
 * the pull saturates, committing: the dominant item of the sector is
 * focused (aggregates unfold via their top member), i.e. the region is
 * "brought into the graph zone".
 *
 * Shared by the React adapter and the IIFE mount.
 */

import type { AtlasEngine } from "../core/engine.ts";
import type { LayoutPoint, LayoutResult } from "../core/types.ts";

export type LensState = {
  /** 0..1 accumulated pull; ≥1 commits. */
  pull: number;
  /** Angular direction of the pull (radians, layout space). */
  angle: number;
};

export const LENS_ARC = 0.65; // radians of influence either side
export const LENS_STEP = 0.34; // pull per wheel notch

function angleDiff(a: number, b: number): number {
  return Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)));
}

/**
 * Displace rim positions toward the core according to the lens.
 * Returns the original layout untouched when the lens is idle.
 */
export function applyLens(layout: LayoutResult, lens: LensState, rCore: number): LayoutResult {
  if (lens.pull <= 0) return layout;
  const positions = new Map<string, LayoutPoint>();
  for (const [id, p] of layout.positions) {
    const rho = Math.hypot(p.x, p.y);
    if (rho <= rCore * 1.05) {
      positions.set(id, p);
      continue;
    }
    const diff = angleDiff(Math.atan2(p.y, p.x), lens.angle);
    if (diff >= LENS_ARC) {
      positions.set(id, p);
      continue;
    }
    const strength = lens.pull * (1 - diff / LENS_ARC);
    const newRho = rho * (1 - 0.5 * strength);
    const k = newRho / rho;
    positions.set(id, { x: p.x * k, y: p.y * k, r: p.r * (1 + 0.45 * strength) });
  }
  return { positions, displacement: layout.displacement };
}

/** Absorption gearing: per-commit boost toward a sector's type, and
 * the cap that keeps a runaway lens from monopolising the ranking. */
export const ABSORB_BOOST = 1.6;
export const ABSORB_CAP = 8;
export const ABSORB_DECAY = 0.85;

/**
 * Steer the ranking toward `type` (iteration-10): repeatedly pulling a
 * sector inward should feel like approaching it — each commit boosts
 * that type's lens weight (so more of it ranks into the core and its
 * beyond-the-horizon count genuinely drops toward zero) while other
 * boosted types decay back toward neutral.
 */
export function absorbTowardType(engine: AtlasEngine, type: string): boolean {
  const lens = engine.getState().lens;
  const weights: Record<string, number> = { ...(lens.typeWeights ?? {}) };
  const saturated = (weights[type] ?? 1) >= ABSORB_CAP;
  for (const k of Object.keys(weights)) {
    if (k !== type && weights[k] > 1) {
      const w = weights[k] * ABSORB_DECAY;
      if (w <= 1.05) delete weights[k];
      else weights[k] = w;
    }
  }
  weights[type] = Math.min(ABSORB_CAP, Math.max(1, weights[type] ?? 1) * ABSORB_BOOST);
  engine.setLens({ ...lens, typeWeights: weights });
  return saturated;
}

export type LensCommit = {
  ok: boolean;
  /** Set when the pull hit a type whose boost is already at cap — the
   * ranking can't absorb more at this zoom; the host should zoom out
   * a notch (smaller nodes → higher capacity → the count keeps
   * falling toward zero). */
  saturatedType?: string;
};

/**
 * Graph-zone pan (iteration-14): translate ONLY the core content —
 * shell nodes, aggregates, and therefore the boundary frame stay
 * fixed on screen. Used by the adapters while an in-core drag is
 * live; the accompanying low-dose lens commits absorb the shift into
 * real scene steps, and the residual springs back on release.
 */
export function applyCorePan(
  layout: LayoutResult,
  scene: { nodes: Array<{ id: string; shell?: number }>; aggregates: Array<{ id: string }> },
  pan: { x: number; y: number },
): LayoutResult {
  if (!pan.x && !pan.y) return layout;
  const fixed = new Set<string>();
  for (const n of scene.nodes) if (n.shell) fixed.add(n.id);
  for (const a of scene.aggregates) fixed.add(a.id);
  const positions = new Map<string, LayoutPoint>();
  for (const [id, p] of layout.positions) {
    positions.set(id, fixed.has(id) ? p : { x: p.x + pan.x, y: p.y + pan.y, r: p.r });
  }
  return { positions, displacement: layout.displacement };
}

/**
 * Commit a saturated pull: focus the dominant rim item in the lens
 * sector. Aggregates enter through their top-ranked member so the
 * unfold animation carries the region into the core — and steer the
 * ranking toward their type so repeated pulls absorb the whole group.
 * Returns true if the scene was retargeted.
 */
export function commitLensTarget(engine: AtlasEngine, lens: LensState, rCore: number): LensCommit {
  const snap = engine.snapshot();
  if (!snap.scene || !snap.layout) return { ok: false };
  let best: { id: string; weight: number; isAggregate: boolean } | null = null;
  const consider = (id: string, weight: number, isAggregate: boolean) => {
    const p = snap.layout!.positions.get(id);
    if (!p) return;
    const rho = Math.hypot(p.x, p.y);
    if (rho <= rCore * 1.05) return;
    const diff = angleDiff(Math.atan2(p.y, p.x), lens.angle);
    if (diff >= LENS_ARC) return;
    const w = weight * (1 - diff / LENS_ARC);
    if (!best || w > best.weight) best = { id, weight: w, isAggregate };
  };
  for (const n of snap.scene.nodes) consider(n.id, Math.max(0.001, n.score), false);
  // Far smears (empty memberIds) can't be focus-entered, but they CAN
  // be absorbed via the type boost — so they are targets too.
  for (const a of snap.scene.aggregates) consider(a.id, a.count * 0.5, true);
  const target = best as { id: string; weight: number; isAggregate: boolean } | null;
  if (!target) return { ok: false };
  if (target.isAggregate) {
    const agg = snap.scene.aggregates.find((a) => a.id === target.id);
    if (!agg) return { ok: false };
    // Boost first (its rebuild is superseded by the focus request when
    // there is an enterable member — the engine drops stale scenes).
    const wasSaturated = absorbTowardType(engine, agg.type);
    const member = agg.memberIds[0];
    if (member) engine.focus(member, "user");
    return wasSaturated ? { ok: true, saturatedType: agg.type } : { ok: true };
  }
  engine.focus(target.id, "user");
  return { ok: true };
}
