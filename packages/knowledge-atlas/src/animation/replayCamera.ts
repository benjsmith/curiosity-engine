/**
 * Pure camera / fit helpers for curation-replay SVG hosts.
 *
 * Mirrors Switchbay curationReplayAnim easeAutoFit math without d3 —
 * wiki-view replay.js and tests share the same contract.
 */

export type FitBounds = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
};

export type ZoomTransform = { x: number; y: number; k: number };

export type FitViewOpts = {
  viewW: number;
  viewH: number;
  /** Minimum cloud size so a tiny cluster does not over-zoom. */
  minCloud?: number;
  /** Cap scale so sparse graphs do not fill the viewport too aggressively. */
  maxScale?: number;
  padRatio?: number;
  minPad?: number;
};

const DEFAULT_MIN_CLOUD = 220;
const DEFAULT_MAX_SCALE = 1.5;
const DEFAULT_PAD_RATIO = 0.12;
const DEFAULT_MIN_PAD = 40;

const TYPE_TIER: Record<string, number> = {
  source: 0,
  sources: 0,
  project: 1,
  entity: 2,
  concept: 3,
  evidence: 4,
  fact: 4,
  figure: 5,
  table: 5,
  note: 6,
  todo: 6,
  "todo-list": 6,
  analysis: 7,
  unclassified: 8,
};

export function typeTier(type: string | undefined | null): number {
  const t = (type || "unclassified").toLowerCase();
  return TYPE_TIER[t] ?? 8;
}

/** Axis-aligned bounds -> zoomIdentity-style translate+scale that centres the cloud. */
export function computeFitTransform(
  bounds: FitBounds,
  opts: FitViewOpts,
): ZoomTransform {
  const viewW = opts.viewW;
  const viewH = opts.viewH;
  const minCloud = opts.minCloud ?? DEFAULT_MIN_CLOUD;
  const maxScale = opts.maxScale ?? DEFAULT_MAX_SCALE;
  const padRatio = opts.padRatio ?? DEFAULT_PAD_RATIO;
  const minPad = opts.minPad ?? DEFAULT_MIN_PAD;

  const { minX, minY, maxX, maxY } = bounds;
  const spanX = Math.max(0, maxX - minX);
  const spanY = Math.max(0, maxY - minY);
  const pad = Math.max(minPad, Math.min(spanX, spanY) * padRatio);
  const cloudW = Math.max(minCloud, spanX + pad * 2);
  const cloudH = Math.max(minCloud, spanY + pad * 2);
  const scale = Math.min(viewW / cloudW, viewH / cloudH, maxScale);
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  return {
    x: viewW / 2 - cx * scale,
    y: viewH / 2 - cy * scale,
    k: scale,
  };
}

/** Bounds from node positions; empty → null. */
export function boundsFromPositions(
  positions: ReadonlyArray<{ x?: number | null; y?: number | null }>,
  fallbackCx = 0,
  fallbackCy = 0,
): FitBounds | null {
  if (!positions.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of positions) {
    const x = n.x ?? fallbackCx;
    const y = n.y ?? fallbackCy;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  if (!Number.isFinite(minX)) return null;
  return { minX, minY, maxX, maxY };
}

/** Exponential blend toward a target zoom (Switchbay k≈0.08 per tick). */
export function blendZoom(
  current: ZoomTransform,
  target: ZoomTransform,
  k = 0.08,
): ZoomTransform {
  const t = Math.max(0, Math.min(1, k));
  return {
    x: current.x + (target.x - current.x) * t,
    y: current.y + (target.y - current.y) * t,
    k: current.k + (target.k - current.k) * t,
  };
}

/**
 * Comparator for synthetic node chronology.
 * Prefer ISO `created` (asc) when present on either side; else type tier →
 * degree desc → id. Deterministic.
 */
export function compareSyntheticNodes(
  a: { id: string; type?: string; degree?: number; created?: string | null },
  b: { id: string; type?: string; degree?: number; created?: string | null },
): number {
  const ac = (a.created || "").trim();
  const bc = (b.created || "").trim();
  if (ac || bc) {
    if (ac && !bc) return -1;
    if (!ac && bc) return 1;
    const at = Date.parse(ac) || 0;
    const bt = Date.parse(bc) || 0;
    if (at !== bt) return at - bt;
  }
  const ta = typeTier(a.type);
  const tb = typeTier(b.type);
  if (ta !== tb) return ta - tb;
  const da = -(a.degree || 0);
  const db = -(b.degree || 0);
  if (da !== db) return da - db;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}
