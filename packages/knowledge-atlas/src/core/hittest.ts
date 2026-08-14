/**
 * Geometry-independent hit testing (PLAN §5, AD-2): the core owns
 * semantics; renderers only forward raw pointer coordinates in scene
 * space. A rebuilt uniform grid keeps pointer scans local when zoomed
 * overview scenes contain 10k marks.
 */

import type { LayoutPoint } from "./types.ts";

export type HitKind = "node" | "aggregate";
export type Hit = { id: string; kind: HitKind };
type IndexedHit = { id: string; kind: HitKind; p: LayoutPoint };

export class HitTester {
  private nodes: Array<{ id: string; p: LayoutPoint }> = [];
  private aggregates: Array<{ id: string; p: LayoutPoint }> = [];
  private grid = new Map<string, IndexedHit[]>();
  private cellSize = 32;
  private maxRadius = 0;

  update(
    positions: ReadonlyMap<string, LayoutPoint>,
    nodeIds: Iterable<string>,
    aggregateIds: Iterable<string>,
  ): void {
    this.nodes = [];
    this.aggregates = [];
    this.grid.clear();
    this.maxRadius = 0;
    for (const id of nodeIds) {
      const p = positions.get(id);
      if (p) {
        this.nodes.push({ id, p });
        this.maxRadius = Math.max(this.maxRadius, p.r);
      }
    }
    for (const id of aggregateIds) {
      const p = positions.get(id);
      if (p) {
        this.aggregates.push({ id, p });
        this.maxRadius = Math.max(this.maxRadius, p.r);
      }
    }
    this.cellSize = Math.max(20, Math.min(72, this.maxRadius * 2 + 12));
    for (const { id, p } of this.nodes) this.insert({ id, kind: "node", p });
    for (const { id, p } of this.aggregates) this.insert({ id, kind: "aggregate", p });
  }

  private key(ix: number, iy: number): string {
    return `${ix},${iy}`;
  }

  private insert(hit: IndexedHit): void {
    const key = this.key(Math.floor(hit.p.x / this.cellSize), Math.floor(hit.p.y / this.cellSize));
    const bucket = this.grid.get(key) ?? [];
    bucket.push(hit);
    this.grid.set(key, bucket);
  }

  private near(x: number, y: number, slack: number): IndexedHit[] {
    const cx = Math.floor(x / this.cellSize);
    const cy = Math.floor(y / this.cellSize);
    const reach = Math.max(1, Math.ceil((this.maxRadius + slack) / this.cellSize));
    const out: IndexedHit[] = [];
    for (let iy = cy - reach; iy <= cy + reach; iy++) {
      for (let ix = cx - reach; ix <= cx + reach; ix++) {
        const bucket = this.grid.get(this.key(ix, iy));
        if (bucket) out.push(...bucket);
      }
    }
    return out;
  }

  /** Topmost hit at scene-space (x, y); nodes above aggregates. */
  pointAt(x: number, y: number, slack = 4): Hit | null {
    let best: { hit: Hit; d: number } | null = null;
    const nearby = this.near(x, y, slack);
    for (const { id, kind, p } of nearby) {
      if (kind !== "node") continue;
      const d = Math.hypot(x - p.x, y - p.y);
      if (d <= p.r + slack && (!best || d < best.d)) best = { hit: { id, kind: "node" }, d };
    }
    if (best) return best.hit;
    for (const { id, kind, p } of nearby) {
      if (kind !== "aggregate") continue;
      const d = Math.hypot(x - p.x, y - p.y);
      if (d <= p.r + slack && (!best || d < best.d)) best = { hit: { id, kind: "aggregate" }, d };
    }
    return best?.hit ?? null;
  }

  boxQuery(x0: number, y0: number, x1: number, y1: number): Hit[] {
    const [minX, maxX] = x0 < x1 ? [x0, x1] : [x1, x0];
    const [minY, maxY] = y0 < y1 ? [y0, y1] : [y1, y0];
    const out: Hit[] = [];
    const seen = new Set<string>();
    const ix0 = Math.floor(minX / this.cellSize);
    const ix1 = Math.floor(maxX / this.cellSize);
    const iy0 = Math.floor(minY / this.cellSize);
    const iy1 = Math.floor(maxY / this.cellSize);
    for (let iy = iy0; iy <= iy1; iy++) {
      for (let ix = ix0; ix <= ix1; ix++) {
        for (const hit of this.grid.get(this.key(ix, iy)) ?? []) {
          if (hit.kind !== "node" || seen.has(hit.id)) continue;
          if (hit.p.x >= minX && hit.p.x <= maxX && hit.p.y >= minY && hit.p.y <= maxY) {
            seen.add(hit.id);
            out.push({ id: hit.id, kind: "node" });
          }
        }
      }
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
