/**
 * Scene builder (PLAN §7): SceneRequest → SceneData over a GraphIndex.
 * Runtime cost depends on the scene budget, never on corpus size —
 * the harvest is visit-capped and everything downstream is budgeted.
 */

import { buildAggregates } from "./aggregate.ts";
import { computeHorizon } from "./discovery.ts";
import { buildLandmarks } from "./landmarks.ts";
import { rankHarvest, selectDiverse } from "./ranking.ts";
import type { GraphIndex } from "../graphindex.ts";
import type {
  OmittedSummary,
  RenderAggregate,
  RenderBundle,
  RenderEdge,
  RenderNode,
  SceneBudget,
  SceneData,
  SceneRequest,
} from "../types.ts";

/** Semantic band from continuous scale (hysteresis lives in zoom.ts). */
export function bandOf(semanticScale: number): number {
  return Math.max(0, Math.min(3, Math.round(semanticScale)));
}

/** Budget shrinks at coarse bands: fewer individuals, more aggregation. */
function effectiveBudget(budget: SceneBudget, band: number): SceneBudget {
  const nodeFactor = band === 0 ? 0.1 : band === 1 ? 0.5 : 1;
  return {
    ...budget,
    maxNodes: Math.max(1, Math.floor(budget.maxNodes * nodeFactor)),
    maxEdges: band === 0 ? Math.floor(budget.maxEdges * 0.25) : budget.maxEdges,
  };
}

export function buildScene(g: GraphIndex, req: SceneRequest, seed: number): SceneData {
  const band = bandOf(req.semanticScale);
  const budget = effectiveBudget(req.budget, band);
  const horizonShare = 0.15;
  const history = req.history ?? [];
  const pinned = (req.pinned ?? []).filter((id) => g.items.has(id));

  if (!req.focusId || !g.items.has(req.focusId)) {
    return overviewScene(g, req, budget);
  }
  const focusId = req.focusId;
  const focus = g.items.get(focusId)!;

  // 1. harvest
  const harvest = g.harvest(focusId, {
    maxHops: 3,
    visitCap: 40 * budget.maxNodes,
    relationTypes: req.relationTypes,
  });

  // 2. rank
  const ranked = rankHarvest(g, harvest, req.lens, history);

  // 3. select individually-visible nodes (MMR-diverse)
  const horizonReserve = Math.max(2, Math.floor(budget.maxNodes * horizonShare));
  const pinCount = pinned.filter((id) => id !== focusId).length;
  const nodeSlots = Math.max(0, budget.maxNodes - 1 - horizonReserve - pinCount);
  const picked = selectDiverse(g, ranked, nodeSlots);
  const selected = new Set(picked.map((p) => p.id));

  // roles
  const nodes: RenderNode[] = [{ id: focusId, item: focus, role: "focus", score: Infinity, ring: 0 }];
  for (const pid of pinned) {
    if (pid === focusId || selected.has(pid)) continue;
    const item = g.items.get(pid)!;
    nodes.push({ id: pid, item, role: "pinned", score: 1, ring: harvest.get(pid)?.hop ?? 3 });
  }
  for (const c of picked) {
    const item = g.items.get(c.id)!;
    const role = c.hop === 1 ? "neighbour" : spansGap(g, c.id, selected) ? "bridge" : "context";
    nodes.push({ id: c.id, item, role, score: c.score, ring: c.hop });
  }

  // 4. aggregates from the fold
  const nodeIds = new Set(nodes.map((n) => n.id));
  const leftovers = ranked.filter((c) => !nodeIds.has(c.id));
  const { aggregates, residualByType } = buildAggregates(g, leftovers, nodeIds, budget.maxAggregates);

  // 5. discovery horizon (reserved slots)
  const horizon =
    band >= 1
      ? computeHorizon(g, focusId, ranked, nodeIds, req.lens, history, horizonReserve, seed)
      : [];
  for (const grp of horizon) {
    for (const c of grp.candidates) {
      if (!nodeIds.has(c.id)) {
        nodes.push({
          id: c.id,
          item: c.item,
          role: "context",
          score: c.score,
          ring: harvest.get(c.id)?.hop ?? 3,
        });
        nodeIds.add(c.id);
      }
    }
  }

  // 6. landmarks
  const presentTypes = new Set(nodes.map((n) => n.item.type));
  const landmarks = buildLandmarks(g, presentTypes, pinned, req.lens);

  // 7. edges by priority tier
  const { edges, bundles, omittedEdges } = buildEdges(g, nodes, aggregates, pinned, budget);

  // 8. omitted summaries
  const omitted: OmittedSummary[] = [];
  for (const grp of horizon) {
    if (grp.omittedCount > 0) {
      omitted.push({ cls: grp.cls, count: grp.omittedCount, label: `${grp.omittedCount} more ${grp.cls}` });
    }
  }
  for (const [type, count] of residualByType) {
    omitted.push({ cls: "nodes", count, label: `${count} more ${type} beyond view` });
  }
  if (omittedEdges > 0) omitted.push({ cls: "edges", count: omittedEdges, label: `${omittedEdges} links hidden` });

  const transitionMap: Record<string, string[]> = {};
  for (const a of aggregates) transitionMap[a.id] = a.memberIds;

  return {
    focus,
    nodes,
    aggregates,
    edges,
    bundles,
    horizon,
    landmarks,
    transitionMap,
    stats: { totalNodes: g.size, omitted },
  };
}

function spansGap(g: GraphIndex, id: string, selected: ReadonlySet<string>): boolean {
  const sel = g.neighbours(id).filter((n) => selected.has(n.id)).map((n) => n.id);
  if (sel.length < 2) return false;
  const nbrsOf0 = new Set(g.neighbours(sel[0]).map((n) => n.id));
  return sel.slice(1).some((s) => !nbrsOf0.has(s));
}

/** Edge selection in priority order (PLAN §11). */
function buildEdges(
  g: GraphIndex,
  nodes: readonly RenderNode[],
  aggregates: readonly RenderAggregate[],
  pinned: readonly string[],
  budget: SceneBudget,
): { edges: RenderEdge[]; bundles: RenderBundle[]; omittedEdges: number } {
  const nodeIds = new Set(nodes.map((n) => n.id));
  const focusId = nodes.find((n) => n.role === "focus")?.id;
  const pinnedSet = new Set(pinned);
  const bridgeIds = new Set(nodes.filter((n) => n.role === "bridge").map((n) => n.id));

  const seen = new Set<string>();
  const candidates: RenderEdge[] = [];
  for (const e of g.edges) {
    if (!nodeIds.has(e.from) || !nodeIds.has(e.to)) continue;
    const key = `${e.from}|${e.to}|${e.type}`;
    if (seen.has(key)) continue;
    seen.add(key);
    let priority = 5;
    if (e.from === focusId || e.to === focusId || pinnedSet.has(e.from) || pinnedSet.has(e.to)) {
      priority = 1;
    } else if (bridgeIds.has(e.from) || bridgeIds.has(e.to)) {
      priority = 3;
    }
    candidates.push({
      source: e.from,
      target: e.to,
      type: e.type,
      direction: "forward",
      confidence: e.confidence,
      priority,
    });
  }
  candidates.sort(
    (a, b) =>
      a.priority - b.priority ||
      (b.confidence ?? 1) - (a.confidence ?? 1) ||
      (a.source < b.source ? -1 : 1),
  );
  const edges = candidates.slice(0, budget.maxEdges);
  const omittedEdges = candidates.length - edges.length;

  // Typed aggregate flows (tier 4, rendered as bundles).
  const bundleMap = new Map<string, RenderBundle>();
  for (const agg of aggregates) {
    for (const mid of agg.memberIds) {
      for (const n of g.neighbours(mid)) {
        if (!nodeIds.has(n.id)) continue;
        const key = `${agg.id}|${n.id}|${n.type}`;
        const b = bundleMap.get(key);
        if (b) b.count++;
        else bundleMap.set(key, { id: key, source: agg.id, target: n.id, type: n.type, count: 1 });
      }
    }
  }
  const bundles = [...bundleMap.values()]
    .sort((a, b) => b.count - a.count || (a.id < b.id ? -1 : 1))
    .slice(0, budget.maxBundles);

  return { edges, bundles, omittedEdges };
}

/** No-focus overview: type aggregates + a few high-score entry points. */
function overviewScene(g: GraphIndex, req: SceneRequest, budget: SceneBudget): SceneData {
  const aggregates: RenderAggregate[] = [];
  const nodes: RenderNode[] = [];
  const types = [...g.byType.entries()].sort((a, b) => b[1].length - a[1].length);
  for (const [type, ids] of types.slice(0, budget.maxAggregates)) {
    const sample = ids.slice(0, 8);
    aggregates.push({
      id: `agg:${type}:*`,
      label: `${ids.length} ${type}${ids.length === 1 ? "" : "s"}`,
      type,
      count: ids.length,
      memberIds: sample,
      memberTitles: sample.map((id) => g.items.get(id)?.title ?? id),
      residual: Math.max(0, ids.length - 8),
    });
  }
  // Entry points: best-sourced, hub-penalised items overall.
  const scored = [...g.items.values()]
    .map((item) => ({
      item,
      s:
        (0.5 + 0.5 * Math.min(1, (item.meta.sources?.length ?? 0) / 3)) *
        (g.degree(item.id) > 0 ? 1 : 0.5),
    }))
    .sort((a, b) => b.s - a.s || (a.item.id < b.item.id ? -1 : 1))
    .slice(0, Math.min(12, budget.maxNodes));
  for (const { item, s } of scored) {
    nodes.push({ id: item.id, item, role: "context", score: s, ring: 1 });
  }
  const landmarks = buildLandmarks(g, new Set(types.map(([t]) => t)), [], req.lens);
  const transitionMap: Record<string, string[]> = {};
  for (const a of aggregates) transitionMap[a.id] = a.memberIds;
  return {
    nodes,
    aggregates,
    edges: [],
    bundles: [],
    horizon: [],
    landmarks,
    transitionMap,
    stats: { totalNodes: g.size, omitted: [] },
  };
}
