/**
 * Rubber-band (marquee) selection helpers for workspace split targeting.
 *
 * Pure geometry — hosts map node positions into the same coordinate
 * space as the drag rect (screen px for Classic SVG; scene px for Atlas
 * hitTester.boxQuery). Used by wiki-view Graph.splitEnter and tests.
 */

export type RubberPoint = {
  id: string;
  x: number;
  y: number;
  /** Page type — drives default move|copy policy when seeding. */
  type?: string;
};

export type RubberRect = {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
};

export type NormalizedRect = {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  width: number;
  height: number;
};

/** Axis-aligned normalize (drag may go any direction). */
export function normalizeRubberRect(rect: RubberRect): NormalizedRect {
  const minX = Math.min(rect.x0, rect.x1);
  const maxX = Math.max(rect.x0, rect.x1);
  const minY = Math.min(rect.y0, rect.y1);
  const maxY = Math.max(rect.y0, rect.y1);
  return {
    minX,
    maxX,
    minY,
    maxY,
    width: maxX - minX,
    height: maxY - minY,
  };
}

/** True when the gesture is a click, not a band (Switchbay uses 4px). */
export function isRubberClick(rect: RubberRect, minPx = 4): boolean {
  const n = normalizeRubberRect(rect);
  return n.width < minPx && n.height < minPx;
}

/** Ids whose centers lie inside the rubber-band (inclusive edges). */
export function idsInRubberBand(
  points: readonly RubberPoint[],
  rect: RubberRect,
): string[] {
  if (isRubberClick(rect)) return [];
  const { minX, maxX, minY, maxY } = normalizeRubberRect(rect);
  const out: string[] = [];
  const seen = new Set<string>();
  for (const p of points) {
    const id = (p.id || "").trim();
    if (!id || seen.has(id)) continue;
    if (p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

/**
 * Charter / Switchbay default: entities & concepts copy to both sides;
 * everything else moves.
 */
export function defaultPartitionPolicyForType(
  type?: string | null,
): "move" | "copy" {
  const t = (type || "").toLowerCase();
  return t === "entity" || t === "concept" ? "copy" : "move";
}

/** Canvas client rect (DOM getBoundingClientRect shape). */
export type CanvasClientRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

/**
 * Map a client (viewport) point into Atlas scene space — origin at the
 * canvas centre, matching hitTester / iife `toScene`.
 */
export function clientToScenePoint(
  clientX: number,
  clientY: number,
  canvasRect: CanvasClientRect,
): { x: number; y: number } {
  return {
    x: clientX - canvasRect.left - canvasRect.width / 2,
    y: clientY - canvasRect.top - canvasRect.height / 2,
  };
}

/** Map a client-space rubber rect into scene space for `boxQuery`. */
export function clientRubberToScene(
  rect: RubberRect,
  canvasRect: CanvasClientRect,
): RubberRect {
  const a = clientToScenePoint(rect.x0, rect.y0, canvasRect);
  const b = clientToScenePoint(rect.x1, rect.y1, canvasRect);
  return { x0: a.x, y0: a.y, x1: b.x, y1: b.y };
}
