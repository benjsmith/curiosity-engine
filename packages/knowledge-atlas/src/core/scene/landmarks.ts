/**
 * Landmarks (PLAN §7.5) — the user's stable compass. Explicit types
 * and top sources, plus pins. Communities appear only when the lens
 * asks. Angular anchors are a pure function of landmark id (hash →
 * angle), so they are stable across scenes, sessions and layouts —
 * "sources are always north-west of here" (PLAN §8.5).
 */

import { hashString } from "../random.ts";
import type { GraphIndex } from "../graphindex.ts";
import type { AtlasLens, Landmark } from "../types.ts";

/** Curiosity Engine's sidebar TYPE_ORDER, used as the default. */
export const DEFAULT_TYPE_ORDER = [
  "project", "analysis", "concept", "entity", "evidence", "fact",
  "figure", "table", "source", "note", "todo-list", "unclassified",
];

export function anchorAngle(landmarkId: string): number {
  // Golden-angle striding over the hash spreads nearby hashes apart.
  const GOLDEN = 2.399963229728653; // radians
  return ((hashString(landmarkId) % 4096) * GOLDEN) % (2 * Math.PI);
}

export function buildLandmarks(
  g: GraphIndex,
  presentTypes: ReadonlySet<string>,
  pinned: readonly string[],
  lens: AtlasLens,
  typeOrder: readonly string[] = DEFAULT_TYPE_ORDER,
): Landmark[] {
  const landmarks: Landmark[] = [];
  const ordered = [...presentTypes].sort((a, b) => {
    const ia = typeOrder.indexOf(a);
    const ib = typeOrder.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || (a < b ? -1 : 1);
  });
  for (const type of ordered) {
    const id = `type:${type}`;
    landmarks.push({ id, kind: "type", label: type, type, anchor: { angle: anchorAngle(id) } });
  }
  // Top shared sources among scene items (secondary landmarks).
  const counts = new Map<string, number>();
  for (const [src, citers] of g.bySource) {
    if (citers.length >= 3 && citers.length <= 50) counts.set(src, citers.length);
  }
  const topSources = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
    .slice(0, 3);
  for (const [src] of topSources) {
    const id = `source:${src}`;
    landmarks.push({
      id,
      kind: "source",
      label: src.split("/").pop() ?? src,
      anchor: { angle: anchorAngle(id) },
    });
  }
  for (const pid of pinned) {
    const item = g.items.get(pid);
    if (!item) continue;
    const id = `pin:${pid}`;
    landmarks.push({ id, kind: "pinned", label: item.title, anchor: { angle: anchorAngle(id) } });
  }
  // Communities only on explicit request (prior-finding constraint).
  if (lens.showCommunities) {
    // Local, cheap, deterministic: connected components of the scene
    // subgraph would go here; deferred until a lens actually asks.
  }
  return landmarks;
}
