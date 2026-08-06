/**
 * Semantic zoom bands with hysteresis (PLAN §10). Continuous scale in
 * [0, 3]; the representation band changes only when the scale moves
 * ≥ 0.75 past the current band, so oscillation at a boundary never
 * flickers the scene.
 */

export const MIN_SCALE = 0;
export const MAX_SCALE = 3;
export const HYSTERESIS = 0.75;

export function clampScale(s: number): number {
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, s));
}

/** Next band given the current band and the new continuous scale. */
export function nextBand(currentBand: number, scale: number): number {
  let band = currentBand;
  while (band < MAX_SCALE && scale >= band + HYSTERESIS) band++;
  while (band > MIN_SCALE && scale <= band - HYSTERESIS) band--;
  return band;
}
