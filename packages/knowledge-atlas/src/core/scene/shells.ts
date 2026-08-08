/**
 * Cosmological shells (iteration-7 / P9): beyond the core capacity
 * (~360 nodes — the size the classic CE viewer handles as one force
 * graph), the rest of the corpus wraps the core in exponentially
 * scaled layers, like a visible-universe plot:
 *
 *   shell 1 — corpus ranks  C … 1 000    (granular: nodes + small clusters)
 *   shell 2 — ranks     1 000 … 10 000   (clustered aggregates)
 *   shell 3 — ranks    10 000 … 100 000  (smeared aggregates)
 *   shell 4 — everything beyond          (per-type smears / totals)
 *
 * A shell only exists when the knowledge base is large enough to
 * populate it, so a growing wiki visibly grows structure outward.
 * Rank = graph proximity to the current focus (BFS + score), so the
 * shells answer "how far from here" — space compresses with distance.
 */

import { pluralize } from "./aggregate.ts";
import { coreArea } from "../geometry.ts";
import type { GraphIndex } from "../graphindex.ts";
import type { RenderAggregate, RenderNode } from "../types.ts";
import type { RankedCandidate } from "./ranking.ts";

export const DEFAULT_CORE_CAPACITY = 360;

/**
 * Legibility density: one node per ~2850 px² (≈53 px pitch — the
 * classic viewer's density at 360 nodes on 1280×800). Iteration-11:
 * capacity is computed from the CORE squircle's actual area (which
 * grows when outer shells are empty and shrinks when they fill), not
 * from the whole viewport — previously the two disagreed and the
 * middle packed several times denser than the target.
 */
export const TARGET_PX_PER_NODE = 2850;
export const MIN_CORE_CAPACITY = 40;
export const MAX_CORE_CAPACITY = 1600;

/**
 * Screen-scaled core capacity. `viewScale` is the geometric zoom:
 * zooming OUT (scale < 1) shrinks nodes, so more corpus fits at the
 * same legible density and material streams in from the boundary;
 * zooming in shows fewer, larger nodes. `bands` is the number of
 * populated shell bands (0 = whole-viewport full-graph mode).
 */
export function coreCapacityFor(
  viewport: { width: number; height: number },
  viewScale = 1,
  bands = 1,
  boundaryShape?: number,
): number {
  const s = Math.min(2, Math.max(0.66, viewScale));
  const area =
    bands <= 0 ? viewport.width * viewport.height : coreArea(viewport, bands, boundaryShape);
  const capacity = area / (TARGET_PX_PER_NODE * s * s);
  return Math.round(Math.max(MIN_CORE_CAPACITY, Math.min(MAX_CORE_CAPACITY, capacity)));
}

/** Rank boundaries of the exponential layers (log₁₀ steps). */
export function shellOfRank(rank: number, coreCapacity: number): number {
  if (rank < coreCapacity) return 0; // core
  if (rank < 1_000) return 1;
  if (rank < 10_000) return 2;
  if (rank < 100_000) return 3;
  return 4;
}

/** How many shells a corpus of this size populates. */
export function shellCount(totalNodes: number, coreCapacity: number): number {
  if (totalNodes <= coreCapacity) return 0;
  if (totalNodes <= 1_000) return 1;
  if (totalNodes <= 10_000) return 2;
  if (totalNodes <= 100_000) return 3;
  return 4;
}

/** Per-shell display quotas: granular near, smeared far. */
const SHELL_NODE_QUOTA = [0, 36, 0, 0, 0]; // individual nodes only in shell 1
const SHELL_AGG_QUOTA = [0, 10, 10, 8, 6];

export type ShellContent = {
  nodes: RenderNode[];
  aggregates: RenderAggregate[];
};

/**
 * Assign everything beyond the core to shells. `ranked` is the
 * focus-ordered tail of the *known* corpus (rank position = index +
 * coreCount). When the true corpus (`totalNodes`) exceeds the known
 * items (cloud/scaled sources), the outermost populated shell gains
 * per-type "far smear" aggregates carrying the estimated remainder —
 * structure without pretending to enumerate it.
 */
export function buildShells(
  g: GraphIndex,
  ranked: readonly RankedCandidate[],
  shownIds: ReadonlySet<string>,
  coreCapacity: number,
  totalNodes: number,
): ShellContent {
  const nodes: RenderNode[] = [];
  const aggregates: RenderAggregate[] = [];
  const shells = shellCount(totalNodes, coreCapacity);
  if (shells === 0) return { nodes, aggregates };

  // Bucket the known tail by shell.
  const coreCount = shownIds.size;
  const byShell = new Map<number, RankedCandidate[]>();
  for (let i = 0; i < ranked.length; i++) {
    const shell = Math.min(shells, Math.max(1, shellOfRank(coreCount + i, coreCapacity)));
    (byShell.get(shell) ?? byShell.set(shell, []).get(shell)!).push(ranked[i]);
  }

  for (let shell = 1; shell <= shells; shell++) {
    const members = byShell.get(shell) ?? [];
    // Shell 1: a granular fringe of individual nodes first.
    let rest = members;
    if (SHELL_NODE_QUOTA[shell] > 0) {
      const fringe = members.slice(0, SHELL_NODE_QUOTA[shell]);
      rest = members.slice(SHELL_NODE_QUOTA[shell]);
      for (const c of fringe) {
        const item = g.items.get(c.id);
        if (!item) continue;
        nodes.push({ id: c.id, item, role: "context", score: c.score, ring: c.hop, shell });
      }
    }
    // Cluster the remainder by type.
    const byType = new Map<string, RankedCandidate[]>();
    for (const c of rest) {
      const t = g.items.get(c.id)?.type ?? "unclassified";
      (byType.get(t) ?? byType.set(t, []).get(t)!).push(c);
    }
    const groups = [...byType.entries()].sort(
      (a, b) => b[1].length - a[1].length || (a[0] < b[0] ? -1 : 1),
    );
    for (const [type, list] of groups.slice(0, SHELL_AGG_QUOTA[shell])) {
      const sample = list.slice(0, 8).map((c) => c.id);
      aggregates.push({
        id: `agg:s${shell}:${type}`,
        label: `${list.length} ${pluralize(type, list.length)}`,
        type,
        count: list.length,
        memberIds: sample,
        memberTitles: sample.map((id) => g.items.get(id)?.title ?? id),
        residual: list.length - sample.length,
        shell,
      });
    }
  }

  // Beyond the harvest but still local (a fully-known wiki): enumerate
  // the unreached remainder into REAL outermost-shell clusters — the
  // structure out there reflects actual knowledge, and each cluster is
  // enterable through its members.
  const seenIds = new Set<string>([...shownIds, ...ranked.map((c) => c.id)]);
  if (totalNodes <= g.size && g.size <= 20_000) {
    const outer = shells;
    const byTypeUnseen = new Map<string, string[]>();
    for (const [id, item] of g.items) {
      if (seenIds.has(id)) continue;
      (byTypeUnseen.get(item.type) ?? byTypeUnseen.set(item.type, []).get(item.type)!).push(id);
    }
    const groups = [...byTypeUnseen.entries()].sort(
      (a, b) => b[1].length - a[1].length || (a[0] < b[0] ? -1 : 1),
    );
    for (const [type, ids] of groups.slice(0, SHELL_AGG_QUOTA[outer] || 6)) {
      const sample = ids.slice(0, 8);
      aggregates.push({
        id: `agg:s${outer}:beyond:${type}`,
        label: `${ids.length} ${pluralize(type, ids.length)} beyond the horizon`,
        type,
        count: ids.length,
        memberIds: sample,
        memberTitles: sample.map((id) => g.items.get(id)?.title ?? id),
        residual: ids.length - sample.length,
        shell: outer,
      });
    }
    return { nodes, aggregates };
  }

  // Far smear: corpus beyond what we can enumerate locally.
  const known = coreCount + ranked.length;
  const unseen = totalNodes - known;
  if (unseen > 0) {
    const outer = shells;
    // Split the estimate by the known type distribution.
    const typeCounts = [...g.byType.entries()].sort((a, b) => b[1].length - a[1].length);
    const totalKnown = Math.max(1, g.size);
    for (const [type, ids] of typeCounts.slice(0, SHELL_AGG_QUOTA[outer] || 6)) {
      const est = Math.round((unseen * ids.length) / totalKnown);
      if (est < 1) continue;
      aggregates.push({
        id: `agg:s${outer}:far:${type}`,
        label: `~${est.toLocaleString()} ${pluralize(type, est)} beyond`,
        type,
        count: est,
        memberIds: [],
        memberTitles: [],
        shell: outer,
      });
    }
  }

  return { nodes, aggregates };
}
