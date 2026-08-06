/**
 * Protected discovery horizon (PLAN §7.4).
 *
 * Six distinct, explainable classes — never merged into one score.
 * Every candidate states why it appears; every class reports how many
 * more were omitted. Within a class the balance is
 *   relevance × novelty × explanatoryStrength × sourceQuality × bridgingValue
 * with penalties for hubs, redundancy, repeated exposure, and
 * excessive conceptual distance. Degree alone can never promote a
 * candidate (tested).
 */

import { hubPenalty } from "./ranking.ts";
import { childSeed, splitmix32 } from "../random.ts";
import type { GraphIndex } from "../graphindex.ts";
import type {
  AtlasLens,
  DiscoveryCandidate,
  DiscoveryClass,
  Explanation,
  ExplanationSummary,
  HorizonGroup,
} from "../types.ts";
import type { RankedCandidate } from "./ranking.ts";

const DEFAULT_MIX: Record<DiscoveryClass, number> = {
  direct: 0.2,
  adjacent: 0.2,
  bridge: 0.2,
  contrast: 0.15,
  surprise: 0.15,
  unexplored: 0.1,
};

function novelty(id: string, history: readonly string[]): number {
  let n = 0;
  for (const h of history) if (h === id) n++;
  return 1 - n / (1 + n);
}

function sourceQuality(g: GraphIndex, id: string): number {
  const s = g.items.get(id)?.meta.sources?.length ?? 0;
  // Weak provenance penalised; rich provenance saturates quickly.
  return 0.5 + 0.5 * Math.min(1, s / 3);
}

/** Selected neighbours of `id` that are not directly linked to each other. */
function bridgeSpan(g: GraphIndex, id: string, selected: ReadonlySet<string>): string[] {
  const selNbrs = g.neighbours(id).filter((n) => selected.has(n.id)).map((n) => n.id);
  if (selNbrs.length < 2) return [];
  // Any pair without a direct edge between them => this node spans a gap.
  for (let i = 0; i < selNbrs.length; i++) {
    const nbrsOfI = new Set(g.neighbours(selNbrs[i]).map((n) => n.id));
    for (let j = i + 1; j < selNbrs.length; j++) {
      if (!nbrsOfI.has(selNbrs[j])) return [selNbrs[i], selNbrs[j]];
    }
  }
  return [];
}

function directlyLinked(g: GraphIndex, a: string, b: string): boolean {
  return g.neighbours(a).some((n) => n.id === b);
}

export function computeHorizon(
  g: GraphIndex,
  focusId: string,
  ranked: readonly RankedCandidate[],
  selected: ReadonlySet<string>,
  lens: AtlasLens,
  history: readonly string[],
  reserve: number,
  seed: number,
): HorizonGroup[] {
  const focus = g.items.get(focusId);
  if (!focus || reserve <= 0) return [];
  const rng = splitmix32(childSeed(seed, `horizon:${focusId}`));
  const used = new Set<string>(selected);
  used.add(focusId);

  const pools = new Map<DiscoveryClass, DiscoveryCandidate[]>();
  const put = (cls: DiscoveryClass, c: DiscoveryCandidate) => {
    const list = pools.get(cls) ?? [];
    list.push(c);
    pools.set(cls, list);
  };

  const unselected = ranked.filter((c) => !used.has(c.id));

  // direct — best of the budget fold.
  for (const c of unselected.slice(0, 12)) {
    const item = g.items.get(c.id);
    if (!item) continue;
    put("direct", {
      id: c.id,
      item,
      score: c.score * novelty(c.id, history) * sourceQuality(g, c.id),
      reason: {
        kind: "direct",
        text: `Strongly related to the focus via ${c.via} (${c.hop} hop${c.hop > 1 ? "s" : ""}) — didn't fit the main view.`,
        viaIds: [c.parent],
      },
    });
  }

  // adjacent — 2–3 hops, different type from focus.
  for (const c of unselected) {
    if (c.hop < 2) continue;
    const item = g.items.get(c.id);
    if (!item || item.type === focus.type) continue;
    put("adjacent", {
      id: c.id,
      item,
      score: c.score * novelty(c.id, history) * sourceQuality(g, c.id),
      reason: {
        kind: "adjacent",
        text: `A ${item.type} ${c.hop} hops out, reached through ${g.items.get(c.parent)?.title ?? c.parent}.`,
        viaIds: [c.parent],
      },
    });
  }

  // bridge — spans otherwise-unlinked parts of the visible scene.
  for (const c of unselected) {
    const span = bridgeSpan(g, c.id, selected);
    if (!span.length) continue;
    const item = g.items.get(c.id);
    if (!item) continue;
    const bridging = 1 + 0.25 * span.length;
    put("bridge", {
      id: c.id,
      item,
      score: c.score * bridging * novelty(c.id, history) * hubPenalty(g.degree(c.id)),
      reason: {
        kind: "bridge",
        text: `Connects ${g.items.get(span[0])?.title ?? span[0]} and ${g.items.get(span[1])?.title ?? span[1]}, which aren't directly linked.`,
        viaIds: span,
      },
    });
  }

  // contrast — shared evidence but no direct link, or flagged material.
  for (const [id] of g.items) {
    if (used.has(id) || id === focusId) continue;
    const item = g.items.get(id);
    if (!item) continue;
    const props = item.meta.properties ?? {};
    const planted = props.contradicts === focusId || props.contrasts === focusId;
    const shared = g.sharedSources(focusId, id);
    const evidentiary = shared.length >= 2 && !directlyLinked(g, focusId, id);
    if (!planted && !evidentiary) continue;
    put("contrast", {
      id,
      item,
      score: (planted ? 2 : 1) * sourceQuality(g, id) * novelty(id, history),
      reason: planted
        ? { kind: "contrast", text: `Marked as contradicting the focus — worth checking against it.` }
        : {
            kind: "contrast",
            text: `Cites ${shared.length} of the same sources as the focus but is never linked to it — possible disagreement.`,
            viaIds: shared.slice(0, 3),
          },
    });
  }

  // surprise — seeded sample from the mid-rank band (percentiles 40–70).
  const lo = Math.floor(unselected.length * 0.4);
  const hi = Math.floor(unselected.length * 0.7);
  const band = unselected.slice(lo, hi);
  for (const c of band) {
    if (rng() > 0.35) continue; // sparse, seeded sampling
    const item = g.items.get(c.id);
    if (!item) continue;
    put("surprise", {
      id: c.id,
      item,
      score: c.score * (1 + novelty(c.id, history)) * sourceQuality(g, c.id),
      reason: {
        kind: "surprise",
        text: `Off the beaten path from here (${c.hop} hops via ${c.via}) but well-sourced — a library-shelf neighbour.`,
        viaIds: [c.parent],
      },
    });
  }

  // unexplored — whole type regions the trail has never visited.
  const visited = new Set(history);
  for (const [type, ids] of g.byType) {
    if (type === focus.type) continue;
    if (ids.some((id) => visited.has(id))) continue;
    // Representative: best-sourced member, hub-penalised (degree must
    // not be the sole positive factor).
    let best: { id: string; s: number } | null = null;
    for (const id of ids) {
      if (used.has(id)) continue;
      const s = sourceQuality(g, id) * hubPenalty(g.degree(id));
      if (!best || s > best.s || (s === best.s && id < best.id)) best = { id, s };
    }
    if (!best) continue;
    const item = g.items.get(best.id);
    if (!item) continue;
    put("unexplored", {
      id: best.id,
      item,
      score: best.s,
      reason: {
        kind: "unexplored",
        text: `You haven't visited any of the ${ids.length} ${type} pages yet — this is a good entry point.`,
      },
    });
  }

  // Quotas: normalise the mix over classes that actually have members.
  const mix = { ...DEFAULT_MIX, ...lens.discoveryMix };
  const present = [...pools.keys()].filter((c) => (pools.get(c)?.length ?? 0) > 0);
  const totalMix = present.reduce((s, c) => s + (mix[c] ?? 0), 0) || 1;
  const groups: HorizonGroup[] = [];
  const taken = new Set<string>();
  for (const cls of present) {
    const list = (pools.get(cls) ?? [])
      .filter((c) => !taken.has(c.id))
      .sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
    const quota = Math.max(1, Math.round((reserve * (mix[cls] ?? 0)) / totalMix));
    const chosen = list.slice(0, quota);
    for (const c of chosen) taken.add(c.id);
    if (chosen.length) {
      groups.push({ cls, candidates: chosen, omittedCount: list.length - chosen.length });
    }
  }
  // Deterministic class order.
  const order: DiscoveryClass[] = ["direct", "adjacent", "bridge", "contrast", "surprise", "unexplored"];
  groups.sort((a, b) => order.indexOf(a.cls) - order.indexOf(b.cls));
  return groups;
}

// ── explanations (LocalSceneSource hooks) ───────────────────────────

export function explainCandidate(
  g: GraphIndex,
  id: string,
  focusId: string,
  cls: DiscoveryClass,
): Explanation {
  const item = g.items.get(id);
  const focus = g.items.get(focusId);
  if (!item || !focus) {
    return { summary: { kind: cls, text: "Item no longer available." } };
  }
  const shared = g.sharedSources(focusId, id);
  const evidence: ExplanationSummary[] = [];
  if (shared.length) {
    evidence.push({
      kind: "path",
      text: `Shares ${shared.length} source${shared.length > 1 ? "s" : ""} with ${focus.title}: ${shared.slice(0, 2).join(", ")}${shared.length > 2 ? ", …" : ""}`,
    });
  }
  const link = g.neighbours(focusId).find((n) => n.id === id);
  if (link) {
    evidence.push({ kind: "edge", text: `Directly linked to the focus (${link.type}).` });
  }
  return {
    summary: {
      kind: cls,
      text: `${item.title} (${item.type}) appears in the ${cls} shelf relative to ${focus.title}.`,
    },
    evidence,
  };
}

export function explainEdge(g: GraphIndex, source: string, target: string, type: string): Explanation {
  const a = g.items.get(source);
  const b = g.items.get(target);
  const shared = g.sharedSources(source, target);
  return {
    summary: {
      kind: "edge",
      text: `${a?.title ?? source} —${type}→ ${b?.title ?? target}`,
    },
    evidence: shared.length
      ? [{ kind: "path", text: `Both cite: ${shared.slice(0, 3).join(", ")}` }]
      : undefined,
  };
}
