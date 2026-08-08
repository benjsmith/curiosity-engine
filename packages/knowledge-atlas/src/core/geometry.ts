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
 *
 * Iteration-12: the disc↔rectangle look is a configurable parameter
 * (`boundaryShape`, 0..1). 0 is the circular family (a perfect circle
 * in a square region, an ellipse otherwise); 1 is almost a rectangle
 * with slightly rounded corners. Hosts set it via
 * `AtlasConfig.boundaryShape`; every geometry function threads it.
 */

export type Viewport = { width: number; height: number };

const RIM_MARGIN = 10;

/** Default shape ≈ superellipse exponent 4.6 — the tuned squircle. */
export const BOUNDARY_SHAPE_DEFAULT = 0.186;

/** Map shape 0..1 → superellipse exponent: 2 (circle/ellipse) … 16
 * (near-rectangle, slightly rounded corners). */
export function shapeExponent(shape: number): number {
  const s = Math.min(1, Math.max(0, shape));
  return 2 + 14 * s;
}

/** Superellipse area factor k in area = k·a·b. Exact at both ends
 * (π at n=2, 4 as n→∞); within ~1.5% between. */
function areaFactor(n: number): number {
  return 4 - (4 - Math.PI) * (2 / n);
}

/**
 * Rim boundary radius at an angle: a superellipse inscribed in the
 * viewport, reaching into the screen corners as `shape` rises.
 */
export function rimRadiusAt(
  angle: number,
  viewport: Viewport,
  shape = BOUNDARY_SHAPE_DEFAULT,
): number {
  const a = Math.max(60, viewport.width / 2 - RIM_MARGIN);
  const b = Math.max(60, viewport.height / 2 - RIM_MARGIN);
  return superellipseRadius(angle, a, b, shapeExponent(shape));
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
 * Core boundary radius at an angle: the rim superellipse inset by the
 * shell depth — anisotropic, following the screen shape.
 */
export function coreRadiusAt(
  angle: number,
  viewport: Viewport,
  bands = 1,
  shape = BOUNDARY_SHAPE_DEFAULT,
): number {
  const { a, b } = coreSemiAxes(viewport, bands);
  return superellipseRadius(angle, a, b, shapeExponent(shape));
}

/** Scalar core radius (the SHORT semi-axis) for isotropic physics —
 * charge strengths, lens pull distances, grade thresholds. */
export function coreRadius(viewport: Viewport, bands = 1): number {
  const { a, b } = coreSemiAxes(viewport, bands);
  return Math.min(a, b);
}

/** Area of the core boundary — drives the legible-density capacity.
 * Squarer shapes hold more (π·a·b at shape 0 → ~4·a·b near 1). */
export function coreArea(viewport: Viewport, bands = 1, shape = BOUNDARY_SHAPE_DEFAULT): number {
  const { a, b } = coreSemiAxes(viewport, bands);
  return areaFactor(shapeExponent(shape)) * a * b;
}

/** Is a layout-space point inside the core zone? */
export function inCoreZone(
  x: number,
  y: number,
  viewport: Viewport,
  bands = 1,
  shape = BOUNDARY_SHAPE_DEFAULT,
): boolean {
  return Math.hypot(x, y) <= coreRadiusAt(Math.atan2(y, x), viewport, bands, shape);
}

function superellipseRadius(angle: number, a: number, b: number, n: number): number {
  const c = Math.abs(Math.cos(angle));
  const s = Math.abs(Math.sin(angle));
  return 1 / Math.pow(Math.pow(c / a, n) + Math.pow(s / b, n), 1 / n);
}
