/** Intent timing for dense Atlas boundary inspection. */

import { coreRadiusAt, rimRadiusAt } from "../core/geometry.ts";
import type { LayoutPoint } from "../core/types.ts";

/**
 * Normalised depth through the visible boundary, 0 at the central
 * squircle and 1 at the wall. Projected coordinates are used because
 * hover intent is a screen-space interaction.
 */
export function projectedBoundaryDepth(
  point: LayoutPoint,
  viewport: { width: number; height: number },
  boundaryShape?: number,
  bands = 1,
): number {
  const angle = Math.atan2(point.y, point.x);
  const rho = Math.hypot(point.x, point.y);
  const core = coreRadiusAt(angle, viewport, Math.max(1, bands), boundaryShape);
  const rim = rimRadiusAt(angle, viewport, boundaryShape);
  return Math.max(0, Math.min(1, (rho - core) / Math.max(1, rim - core)));
}

/**
 * Boundary labels are not instant: a pointer crossing a dense field
 * must settle on one target before it wins. Near-boundary inspection
 * stays around 45–60 ms; deep/dense layers rise gradually but cap at
 * 110 ms, still fast enough for deliberate visual scanning.
 */
export function boundaryHoverDelay(depth: number, representedNodes: number): number {
  const d = Math.max(0, Math.min(1, depth));
  const densityPenalty = Math.min(
    14,
    Math.max(0, Math.log10(Math.max(1, representedNodes) / 1_000) * 7),
  );
  return Math.round(Math.min(110, 45 + 42 * d ** 1.35 + densityPenalty));
}
