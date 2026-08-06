/**
 * Neighbourhood ranking (PLAN §7.2).
 *
 * rank = hopDecay × relWeight × typeWeight × hubPenalty × exposure
 * Redundancy (MMR) is applied greedily at selection time in the
 * builder, not here — it depends on what's already selected.
 *
 * Invariant (tested): degree is never a *positive* factor — a node
 * can only lose score for being a hub, never gain.
 */

import type { GraphIndex } from "../graphindex.ts";
import type { AtlasLens } from "../types.ts";

export const HOP_DECAY = [1, 1, 0.45, 0.2]; // index = hop (0 unused)
export const HUB_DEGREE = 30;

export const DEFAULT_RELATION_WEIGHTS: Record<string, number> = {
  wikilink: 1.0,
  depicts: 0.9,
  cites: 0.8,
  "co-cited": 0.5,
  provisional: 0.5,
};

export function relWeight(edgeType: string, lens: AtlasLens): number {
  return lens.relationWeights?.[edgeType] ?? DEFAULT_RELATION_WEIGHTS[edgeType] ?? 0.7;
}

export function typeWeight(nodeType: string, lens: AtlasLens): number {
  return lens.typeWeights?.[nodeType] ?? 1;
}

export function hubPenalty(degree: number): number {
  if (degree <= HUB_DEGREE) return 1;
  return 1 / (1 + Math.log2(degree / HUB_DEGREE));
}

/** Repeated-exposure decay: seen n times recently -> 1/(1+0.5n). */
export function exposureFactor(id: string, history: readonly string[]): number {
  let n = 0;
  for (const h of history) if (h === id) n++;
  return 1 / (1 + 0.5 * n);
}

export type RankedCandidate = {
  id: string;
  hop: number;
  via: string;
  parent: string;
  confidence: number;
  score: number;
};

export function rankHarvest(
  g: GraphIndex,
  harvest: Map<string, { hop: number; via: string; parent: string; confidence: number }>,
  lens: AtlasLens,
  history: readonly string[],
): RankedCandidate[] {
  const out: RankedCandidate[] = [];
  for (const [id, h] of harvest) {
    const item = g.items.get(id);
    if (!item) continue;
    const score =
      (HOP_DECAY[Math.min(h.hop, HOP_DECAY.length - 1)] ?? 0.1) *
      relWeight(h.via, lens) *
      h.confidence *
      typeWeight(item.type, lens) *
      hubPenalty(g.degree(id)) *
      exposureFactor(id, history);
    out.push({ id, ...h, score });
  }
  // Deterministic order: score desc, then id asc.
  out.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
  return out;
}

/**
 * Greedy MMR selection: each pick's effective score is discounted by
 * its worst neighbour-overlap with what's already picked, so the
 * visible set stays diverse. O(n·k) with k = budget — fine at scene
 * scale.
 */
export function selectDiverse(
  g: GraphIndex,
  ranked: readonly RankedCandidate[],
  count: number,
): RankedCandidate[] {
  const picked: RankedCandidate[] = [];
  const pool = [...ranked];
  while (picked.length < count && pool.length) {
    let bestIdx = 0;
    let bestScore = -Infinity;
    for (let i = 0; i < pool.length; i++) {
      const c = pool[i];
      let maxSim = 0;
      for (const p of picked) {
        const sim = g.neighbourJaccard(c.id, p.id);
        if (sim > maxSim) maxSim = sim;
      }
      const eff = c.score * (1 - 0.5 * maxSim);
      if (eff > bestScore) {
        bestScore = eff;
        bestIdx = i;
      }
    }
    picked.push(pool[bestIdx]);
    pool.splice(bestIdx, 1);
  }
  return picked;
}
