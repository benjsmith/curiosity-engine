/**
 * Cosmological shells (iteration-7 / P9): beyond the core capacity
 * (~360 nodes — the size the classic CE viewer handles as one force
 * graph), the rest of the corpus wraps the core in exponentially
 * scaled layers, like a visible-universe plot:
 *
 *   next milestone — corpus ranks C … 1 000 (when C < 1k)
 *   next milestone — ranks up to 10 000    (when C < 10k)
 *   next milestone — ranks up to 100 000   (when C < 100k)
 *   final layer    — everything beyond
 *
 * A shell only exists when the knowledge base is large enough to
 * populate it, so a growing wiki visibly grows structure outward. A
 * milestone already inside the zoom-dependent core is removed, making
 * the corresponding layer recede instead of consuming screen space.
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
/** Production default. Hosts may opt into the 100k experimental cap. */
export const MAX_CORE_CAPACITY = 10_000;
export const EXPERIMENTAL_MAX_CORE_CAPACITY = 100_000;

/**
 * Screen-scaled core capacity. `viewScale` is the geometric zoom:
 * zooming OUT (scale < 1) shrinks nodes, so more corpus fits at the
 * same legible density and material streams in from the boundary;
 * zooming in shows fewer, larger nodes. `bands` is the number of
 * populated shell bands (0 = whole-viewport full-graph mode).
 */
/** Geometric zoom so `totalNodes` fit as one full-graph scene.
 *  First paint used the default scale=1 capacity (~360 on a laptop),
 *  so large wikis opened as type-cluster shells and only became the
 *  individual-node log-boundary after the user zoomed out. */
export function viewScaleToFit(
  totalNodes: number,
  viewport: { width: number; height: number },
  maxCapacity = MAX_CORE_CAPACITY,
): number {
  const needed = Math.max(MIN_CORE_CAPACITY, Math.min(maxCapacity, Math.max(1, totalNodes)));
  const area = Math.max(1, viewport.width * viewport.height);
  const s = Math.sqrt(area / (TARGET_PX_PER_NODE * needed));
  return Math.max(0.08, Math.min(1, s));
}

export function coreCapacityFor(
  viewport: { width: number; height: number },
  viewScale = 1,
  bands = 1,
  boundaryShape?: number,
  maxCapacity = MAX_CORE_CAPACITY,
): number {
  const s = Math.min(4, Math.max(0.05, viewScale));
  const area =
    bands <= 0 ? viewport.width * viewport.height : coreArea(viewport, bands, boundaryShape);
  const capacity = area / (TARGET_PX_PER_NODE * s * s);
  const cap = Math.max(MIN_CORE_CAPACITY, Math.min(EXPERIMENTAL_MAX_CORE_CAPACITY, maxCapacity));
  return Math.round(Math.max(MIN_CORE_CAPACITY, Math.min(cap, capacity)));
}

/** Rank boundaries of the exponential layers (log₁₀ steps). Milestones
 * already absorbed by the core disappear, so the 1k and 10k layers
 * visibly recede as geometric zoom raises capacity past them. */
export function shellOfRank(rank: number, coreCapacity: number): number {
  if (rank < coreCapacity) return 0; // core
  const activeMilestones = [1_000, 10_000, 100_000, Infinity].filter(
    (limit) => limit > coreCapacity,
  );
  const i = activeMilestones.findIndex((limit) => rank < limit);
  return Math.max(1, i + 1);
}

/** How many shells a corpus of this size populates. */
export function shellCount(totalNodes: number, coreCapacity: number): number {
  if (totalNodes <= coreCapacity) return 0;
  return shellOfRank(totalNodes - 1, coreCapacity);
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
