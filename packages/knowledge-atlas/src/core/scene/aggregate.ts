/**
 * Secondary-context aggregation (PLAN §7.3).
 *
 * Grouping key precedence is EXPLICIT METADATA FIRST (the prior-
 * finding constraint): type × nearest-selected-neighbour, then plain
 * type. Internal communities may only subdivide an oversized explicit
 * group (not yet needed at current scales; the split hook is where
 * AGG_SPLIT is checked). Aggregate ids are stable across consecutive
 * scenes with the same focus: "agg:<type>:<anchor>".
 */

import type { GraphIndex } from "../graphindex.ts";
import type { RenderAggregate } from "../types.ts";
import type { RankedCandidate } from "./ranking.ts";

export const MEMBER_SAMPLE = 8;

const PLURAL: Record<string, string> = {
  analysis: "analyses",
  evidence: "evidence",
  entity: "entities",
  "todo-list": "todos",
};

export function pluralize(type: string, n: number): string {
  if (n === 1) return type;
  return PLURAL[type] ?? `${type}s`;
}

export function buildAggregates(
  g: GraphIndex,
  leftovers: readonly RankedCandidate[],
  selected: ReadonlySet<string>,
  maxAggregates: number,
): { aggregates: RenderAggregate[]; residualByType: Map<string, number> } {
  // Group by type × anchor (the selected node this candidate hangs off).
  const groups = new Map<string, { type: string; anchorId?: string; members: RankedCandidate[] }>();
  for (const c of leftovers) {
    const item = g.items.get(c.id);
    if (!item) continue;
    const anchorId = selected.has(c.parent) ? c.parent : undefined;
    const key = `${item.type}:${anchorId ?? "*"}`;
    const grp = groups.get(key) ?? { type: item.type, anchorId, members: [] };
    grp.members.push(c);
    groups.set(key, grp);
  }

  const ordered = [...groups.entries()].sort(
    (a, b) => b[1].members.length - a[1].members.length || (a[0] < b[0] ? -1 : 1),
  );

  const aggregates: RenderAggregate[] = [];
  const residualByType = new Map<string, number>();
  for (const [key, grp] of ordered) {
    if (aggregates.length >= maxAggregates) {
      // Beyond budget: fold into per-type residual counts (PLAN §7.3 —
      // never emit thousands of boundary nodes).
      residualByType.set(grp.type, (residualByType.get(grp.type) ?? 0) + grp.members.length);
      continue;
    }
    const members = [...grp.members].sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
    const sampleIds = members.slice(0, MEMBER_SAMPLE).map((m) => m.id);
    const anchorTitle = grp.anchorId ? g.items.get(grp.anchorId)?.title : undefined;
    aggregates.push({
      id: `agg:${key}`,
      label:
        `${grp.members.length} ${pluralize(grp.type, grp.members.length)}` +
        (anchorTitle ? ` near ${anchorTitle}` : ""),
      type: grp.type,
      count: grp.members.length,
      memberIds: sampleIds,
      memberTitles: sampleIds.map((id) => g.items.get(id)?.title ?? id),
      residual: Math.max(0, grp.members.length - sampleIds.length),
      anchorId: grp.anchorId,
    });
  }
  return { aggregates, residualByType };
}
