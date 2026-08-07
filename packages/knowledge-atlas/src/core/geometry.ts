/**
 * Shared viewport geometry for the hybrid mode — used by the layout
 * (placement) and the renderer (boundary/caption drawing), so it lives
 * outside both.
 */

export type Viewport = { width: number; height: number };

/** Core-zone radius: the central force-graph disc (iteration-3: broadened). */
export function coreRadius(viewport: Viewport): number {
  return Math.min(viewport.width, viewport.height) * 0.32;
}

const RIM_MARGIN = 34;
const SQUIRCLE_N = 3.2;

/**
 * Rim boundary radius at an angle: a superellipse ("squircle")
 * inscribed in the viewport, so the rim reaches into the screen
 * corners instead of stopping at the inscribed circle
 * (iteration-3 feedback: the corners were underused).
 */
export function rimRadiusAt(angle: number, viewport: Viewport): number {
  const a = Math.max(60, viewport.width / 2 - RIM_MARGIN);
  const b = Math.max(60, viewport.height / 2 - RIM_MARGIN);
  const c = Math.abs(Math.cos(angle));
  const s = Math.abs(Math.sin(angle));
  return 1 / Math.pow(Math.pow(c / a, SQUIRCLE_N) + Math.pow(s / b, SQUIRCLE_N), 1 / SQUIRCLE_N);
}
