/**
 * Geometry-independent hit testing (PLAN §5, AD-2): the core owns
 * semantics; renderers only forward raw pointer coordinates in scene
 * space. Linear scan is exact and fast at scene budgets (≤ ~10²
 * items); a spatial grid would only pay off far beyond them.
 */

import type { LayoutPoint } from "./types.ts";

export type HitKind = "node" | "aggregate";
export type Hit = { id: string; kind: HitKind };

export class HitTester {
  private nodes: Array<{ id: string; p: LayoutPoint }> = [];
  private aggregates: Array<{ id: string; p: LayoutPoint }> = [];

  update(
    positions: ReadonlyMap<string, LayoutPoint>,
    nodeIds: Iterable<string>,
    aggregateIds: Iterable<string>,
  ): void {
    this.nodes = [];
    this.aggregates = [];
    for (const id of nodeIds) {
      const p = positions.get(id);
      if (p) this.nodes.push({ id, p });
    }
    for (const id of aggregateIds) {
      const p = positions.get(id);
      if (p) this.aggregates.push({ id, p });
    }
  }

  /** Topmost hit at scene-space (x, y); nodes above aggregates. */
  pointAt(x: number, y: number, slack = 4): Hit | null {
    let best: { hit: Hit; d: number } | null = null;
    for (const { id, p } of this.nodes) {
      const d = Math.hypot(x - p.x, y - p.y);
      if (d <= p.r + slack && (!best || d < best.d)) best = { hit: { id, kind: "node" }, d };
    }
    if (best) return best.hit;
    for (const { id, p } of this.aggregates) {
      const d = Math.hypot(x - p.x, y - p.y);
      if (d <= p.r + slack && (!best || d < best.d)) best = { hit: { id, kind: "aggregate" }, d };
    }
    return best?.hit ?? null;
  }

  boxQuery(x0: number, y0: number, x1: number, y1: number): Hit[] {
    const [minX, maxX] = x0 < x1 ? [x0, x1] : [x1, x0];
    const [minY, maxY] = y0 < y1 ? [y0, y1] : [y1, y0];
    const out: Hit[] = [];
    for (const { id, p } of this.nodes) {
      if (p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY) out.push({ id, kind: "node" });
    }
    return out;
  }

  /** Nearest node in a direction (keyboard navigation). */
  nearestInDirection(fromId: string, dir: "up" | "down" | "left" | "right"): string | null {
    const from = this.nodes.find((n) => n.id === fromId)?.p;
    if (!from) return null;
    let best: { id: string; d: number } | null = null;
    for (const { id, p } of this.nodes) {
      if (id === fromId) continue;
      const dx = p.x - from.x;
      const dy = p.y - from.y;
      const along = dir === "right" ? dx : dir === "left" ? -dx : dir === "down" ? dy : -dy;
      const ortho = dir === "right" || dir === "left" ? Math.abs(dy) : Math.abs(dx);
      if (along <= 0 || ortho > along * 1.5) continue;
      const d = along + ortho * 0.5;
      if (!best || d < best.d) best = { id, d };
    }
    return best?.id ?? null;
  }
}
