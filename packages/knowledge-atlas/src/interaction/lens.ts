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

/**
 * Commit a saturated pull: focus the dominant rim item in the lens
 * sector. Aggregates enter through their top-ranked member so the
 * unfold animation carries the region into the core. Returns true if
 * something was focused.
 */
export function commitLensTarget(engine: AtlasEngine, lens: LensState, rCore: number): boolean {
  const snap = engine.snapshot();
  if (!snap.scene || !snap.layout) return false;
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
  for (const a of snap.scene.aggregates) {
    if (a.memberIds.length === 0) continue; // far smears can't be entered
    consider(a.id, a.count * 0.5, true);
  }
  const target = best as { id: string; weight: number; isAggregate: boolean } | null;
  if (!target) return false;
  if (target.isAggregate) {
    const agg = snap.scene.aggregates.find((a) => a.id === target.id);
    const member = agg?.memberIds[0];
    if (!member) return false;
    engine.focus(member, "user");
  } else {
    engine.focus(target.id, "user");
  }
  return true;
}
