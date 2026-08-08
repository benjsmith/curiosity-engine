/**
 * Scene builder (PLAN §7): SceneRequest → SceneData over a GraphIndex.
 * Runtime cost depends on the scene budget, never on corpus size —
 * the harvest is visit-capped and everything downstream is budgeted.
 */

import { buildAggregates } from "./aggregate.ts";
import { computeHorizon } from "./discovery.ts";
import { buildLandmarks } from "./landmarks.ts";
import { hubPenalty, rankHarvest, selectDiverse } from "./ranking.ts";
import { buildShells, DEFAULT_CORE_CAPACITY } from "./shells.ts";
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

export function buildScene(
  g: GraphIndex,
  req: SceneRequest,
  seed: number,
  totalNodes = g.size,
): SceneData {
  const band = bandOf(req.semanticScale);
  const budget = effectiveBudget(req.budget, band);
  const history = req.history ?? [];
  const pinned = (req.pinned ?? []).filter((id) => g.items.has(id));
  // Central-graph capacity: at or below it the whole wiki renders as
  // ONE classic force graph, nothing in the boundary (iteration-7).
  const coreCapacity = Math.min(req.coreCapacity ?? DEFAULT_CORE_CAPACITY, budget.maxNodes);

  if (!req.focusId || !g.items.has(req.focusId)) {
    return overviewScene(g, req, budget);
  }
  const focusId = req.focusId;
  const focus = g.items.get(focusId)!;

  // Full-graph eligibility (iteration-11): with no boundary the graph
  // fills the whole screen, so the engine passes a larger
  // whole-viewport capacity; a pinned coreCapacity without one keeps
  // the old single-threshold behaviour.
  const fullCapacity = Math.min(
    budget.maxNodes,
    Math.max(coreCapacity, req.fullGraphCapacity ?? coreCapacity),
  );
  if (g.size <= fullCapacity && totalNodes <= fullCapacity && band >= 2) {
    return fullGraphScene(g, req, budget, focusId, pinned);
  }

  // 1. harvest
  const harvest = g.harvest(focusId, {
    maxHops: 3,
    visitCap: 40 * budget.maxNodes,
    relationTypes: req.relationTypes,
  });

  // 2. rank
  const ranked = rankHarvest(g, harvest, req.lens, history);

  // 2b. absorption (iteration-10): a type the lens strongly boosts
  // (> 2×, i.e. the user is steering toward its sector) pulls its
  // un-harvested members into the pool too — repeated pulls should be
  // able to absorb the WHOLE type into the core, not just the part
  // within harvest range. Score grows with the boost, so the harder
  // the pull, the more of the type ranks in. Local indexes only; a
  // cloud source implements the same rule server-side.
  const boostedTypes = Object.entries(req.lens.typeWeights ?? {}).filter(([, w]) => w > 2);
  if (boostedTypes.length) {
    const have = new Set(ranked.map((r) => r.id));
    for (const [t, w] of boostedTypes) {
      for (const item of g.items.values()) {
        if (item.type !== t || have.has(item.id) || item.id === focusId) continue;
        ranked.push({
          id: item.id,
          hop: 3,
          via: "wikilink",
          parent: focusId,
          confidence: 0.6,
          score: 0.25 * w * hubPenalty(g.degree(item.id)),
        });
      }
    }
    ranked.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
  }

  // 3. select the central graph (MMR diversity at small budgets; plain
  //    rank order at CE-viewer scale, where MMR is O(n²) and the local
  //    neighbourhood is already diverse)
  const horizonShare = 0.15;
  const horizonReserve = Math.max(2, Math.floor(Math.min(60, budget.maxNodes * horizonShare)));
  const pinCount = pinned.filter((id) => id !== focusId).length;
  const coreSlots = Math.min(coreCapacity, Math.max(1, budget.maxNodes - horizonReserve));
  const nodeSlots = Math.max(0, coreSlots - 1 - pinCount);
  const picked = nodeSlots <= 120 ? selectDiverse(g, ranked, nodeSlots) : ranked.slice(0, nodeSlots);
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

  // 4. cosmological shells: everything beyond the core wraps it in
  //    exponentially scaled layers (granular → smeared); the residual
  //    corpus far beyond local knowledge becomes far-smear aggregates.
  const nodeIds = new Set(nodes.map((n) => n.id));
  const leftovers = ranked.filter((c) => !nodeIds.has(c.id));
  const shellContent = buildShells(g, leftovers, nodeIds, coreCapacity, Math.max(totalNodes, g.size));
  for (const sn of shellContent.nodes) {
    if (nodes.length >= budget.maxNodes - horizonReserve) break;
    if (!nodeIds.has(sn.id)) {
      nodes.push(sn);
      nodeIds.add(sn.id);
    }
  }
  // Small near-core clusters (type × anchor) for the immediate fold,
  // then the shell aggregates; both budgeted together.
  const shellMemberIds = new Set(shellContent.nodes.map((n) => n.id));
  const nearLeftovers = leftovers.filter(
    (c) => !shellMemberIds.has(c.id) && (harvest.get(c.id)?.hop ?? 3) <= 1,
  );
  const { aggregates, residualByType } = buildAggregates(
    g,
    nearLeftovers,
    nodeIds,
    Math.max(0, budget.maxAggregates - shellContent.aggregates.length),
  );
  for (const a of shellContent.aggregates) {
    if (aggregates.length >= budget.maxAggregates) break;
    aggregates.push(a);
  }

  // 5. discovery horizon (reserved slots)
  const horizon =
    band >= 1
      ? computeHorizon(g, focusId, ranked, nodeIds, req.lens, history, horizonReserve, seed)
      : [];
  for (const grp of horizon) {
    for (const c of grp.candidates) {
      if (nodes.length >= budget.maxNodes) break;
      if (!nodeIds.has(c.id)) {
        nodes.push({
          id: c.id,
          item: c.item,
          role: "context",
          score: c.score,
          ring: harvest.get(c.id)?.hop ?? 3,
          shell: 1,
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
    stats: { totalNodes: Math.max(totalNodes, g.size), omitted },
  };
}

/**
 * Whole-wiki scene (≤ coreCapacity): identical in spirit to the
 * classic CE viewer — every node, every edge (within the edge budget),
 * no aggregation, no boundary structure, no horizon.
 */
function fullGraphScene(
  g: GraphIndex,
  req: SceneRequest,
  budget: SceneBudget,
  focusId: string,
  pinned: readonly string[],
): SceneData {
  const harvest = g.harvest(focusId, { maxHops: 6, visitCap: g.size * 4 });
  const pinnedSet = new Set(pinned);
  const nodes: RenderNode[] = [];
  for (const item of g.items.values()) {
    const h = harvest.get(item.id);
    const role: RenderNode["role"] =
      item.id === focusId
        ? "focus"
        : pinnedSet.has(item.id)
          ? "pinned"
          : h?.hop === 1
            ? "neighbour"
            : "context";
    nodes.push({
      id: item.id,
      item,
      role,
      score: item.id === focusId ? Infinity : 1 / (1 + (h?.hop ?? 6)),
      ring: h?.hop ?? 6,
    });
  }
  const focusEdges: RenderEdge[] = [];
  const restEdges: RenderEdge[] = [];
  const seen = new Set<string>();
  for (const e of g.edges) {
    const key = `${e.from}|${e.to}|${e.type}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const edge: RenderEdge = {
      source: e.from,
      target: e.to,
      type: e.type,
      direction: "forward",
      confidence: e.confidence,
      priority: e.from === focusId || e.to === focusId ? 1 : 5,
    };
    (edge.priority === 1 ? focusEdges : restEdges).push(edge);
  }
  const edges = [...focusEdges, ...restEdges].slice(0, budget.maxEdges);
  const landmarks = buildLandmarks(g, new Set(nodes.map((n) => n.item.type)), pinned, req.lens);
  return {
    focus: g.items.get(focusId),
    nodes,
    aggregates: [],
    edges,
    bundles: [],
    horizon: [],
    landmarks,
    transitionMap: {},
    stats: { totalNodes: g.size, omitted: [] },
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
