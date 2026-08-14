/**
 * LayoutAdapter contract (PLAN §8). Adapters are pure and seeded:
 * (scene, ctx) -> positions, no DOM, no wall clock.
 */

import type { AtlasPhysics, LayoutKind, LayoutPoint, LayoutResult, SceneData } from "../types.ts";

export type LayoutContext = {
  viewport: { width: number; height: number };
  previous?: LayoutResult;
  seed: number;
  /** Boundary shape 0..1 (AtlasConfig.boundaryShape); default squircle. */
  boundaryShape?: number;
  /** Live classic-viewer force settings. */
  physics?: AtlasPhysics;
};

export interface LayoutAdapter {
  id: LayoutKind;
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult;
}

/** Node radius: identical to the current CE viewer for P0 parity. */
export function nodeRadius(degree: number | undefined): number {
  return 4 + Math.sqrt((degree ?? 0) + 1) * 1.6;
}

export function aggregateRadius(count: number): number {
  return Math.min(34, 10 + Math.sqrt(count) * 2.4);
}

export function meanDisplacement(
  positions: Map<string, LayoutPoint>,
  previous?: LayoutResult,
): number {
  if (!previous) return 0;
  let sum = 0;
  let n = 0;
  for (const [id, p] of positions) {
    const q = previous.positions.get(id);
    if (!q) continue;
    sum += Math.hypot(p.x - q.x, p.y - q.y);
    n++;
  }
  return n === 0 ? 0 : sum / n;
}
