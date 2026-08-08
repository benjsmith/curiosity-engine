/**
 * Shared viewport geometry for the hybrid mode — used by the layout
 * (placement), the renderer (boundary/caption drawing), the capacity
 * rule, and the traversal physics, so it lives outside all of them.
 *
 * Iteration-11 model: the atlas fills the screen. The outer wall is a
 * superellipse hugging the viewport (margin 10 px); the core boundary
 * is the SAME squircle inset by the radial depth of the populated
 * shell bands — anisotropic, so a phone's core is tall with squared
 * corners, not a disc. When outer shells are empty their space goes
 * back to the core; when they fill up, the core shrinks to make room.
 */

export type Viewport = { width: number; height: number };

const RIM_MARGIN = 10;
const SQUIRCLE_N = 4.6;
/** Superellipse (n=4.6) area ≈ 3.62·a·b (4ab·Γ(1+1/n)²/Γ(1+2/n)). */
const SQUIRCLE_AREA_K = 3.62;

/**
 * Rim boundary radius at an angle: a superellipse ("squircle")
 * inscribed in the viewport, so the rim reaches into the screen
 * corners instead of stopping at the inscribed circle.
 */
export function rimRadiusAt(angle: number, viewport: Viewport): number {
  const a = Math.max(60, viewport.width / 2 - RIM_MARGIN);
  const b = Math.max(60, viewport.height / 2 - RIM_MARGIN);
  return superellipseRadius(angle, a, b);
}

/**
 * Radial depth reserved for `bands` populated shell bands. Pixel-based
 * (a smear needs room regardless of screen), but capped so shells
 * never consume more than half the short semi-axis.
 */
export function shellDepth(viewport: Viewport, bands: number): number {
  if (bands <= 0) return 0;
  const minHalf = Math.min(viewport.width, viewport.height) / 2 - RIM_MARGIN;
  const per = Math.max(30, Math.min(54, Math.min(viewport.width, viewport.height) * 0.09));
  return Math.min(per * bands, minHalf * 0.5);
}

function coreSemiAxes(viewport: Viewport, bands: number): { a: number; b: number } {
  const d = shellDepth(viewport, bands);
  return {
    a: Math.max(40, viewport.width / 2 - RIM_MARGIN - d),
    b: Math.max(40, viewport.height / 2 - RIM_MARGIN - d),
  };
}

/**
 * Core boundary radius at an angle: the rim squircle inset by the
 * shell depth — anisotropic, following the screen shape.
 */
export function coreRadiusAt(angle: number, viewport: Viewport, bands = 1): number {
  const { a, b } = coreSemiAxes(viewport, bands);
  return superellipseRadius(angle, a, b);
}

/** Scalar core radius (the SHORT semi-axis) for isotropic physics —
 * charge strengths, lens pull distances, grade thresholds. */
export function coreRadius(viewport: Viewport, bands = 1): number {
  const { a, b } = coreSemiAxes(viewport, bands);
  return Math.min(a, b);
}

/** Area of the core squircle — drives the legible-density capacity. */
export function coreArea(viewport: Viewport, bands = 1): number {
  const { a, b } = coreSemiAxes(viewport, bands);
  return SQUIRCLE_AREA_K * a * b;
}

/** Is a layout-space point inside the (squircle) core zone? */
export function inCoreZone(x: number, y: number, viewport: Viewport, bands = 1): boolean {
  return Math.hypot(x, y) <= coreRadiusAt(Math.atan2(y, x), viewport, bands);
}

function superellipseRadius(angle: number, a: number, b: number): number {
  const c = Math.abs(Math.cos(angle));
  const s = Math.abs(Math.sin(angle));
  return 1 / Math.pow(Math.pow(c / a, SQUIRCLE_N) + Math.pow(s / b, SQUIRCLE_N), 1 / SQUIRCLE_N);
}
