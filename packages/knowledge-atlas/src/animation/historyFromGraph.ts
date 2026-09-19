/**
 * Build / scrub helpers for curation-replay HistoryDoc.
 *
 * Synthesizes a deterministic timeline from CE `data.json` graph
 * membership when git-first-seen history is unavailable. Hosts
 * (wiki-view replay UI) own SVG rendering; this module stays pure.
 */

import type { HistoryDoc, ReplayEvent } from "./replayTimeline.ts";
import { compareSyntheticNodes } from "./replayCamera.ts";

export type GraphNodeIn = {
  id: string;
  title?: string;
  type?: string;
  degree?: number;
  path?: string;
  /** ISO date from frontmatter `created` when available. */
  created?: string;
};

export type GraphEdgeIn = {
  source: string | { id: string };
  target: string | { id: string };
};

export type BuildHistoryOptions = {
  /** Wall-clock span for event `t` values (seconds). */
  duration?: number;
  /** Cap total events (nodes kept; edges down-sampled). */
  maxEvents?: number;
  source?: string;
};

const DEFAULT_DURATION = 12;
const DEFAULT_MAX_EVENTS = 2500;

function edgeId(ref: string | { id: string } | null | undefined): string {
  if (ref == null) return "";
  if (typeof ref === "string") return ref;
  return typeof ref.id === "string" ? ref.id : "";
}

/** Stable sort: time asc, nodes before edges at equal t. */
export function sortReplayEvents(events: ReplayEvent[]): ReplayEvent[] {
  return events.slice().sort((a, b) => {
    if (a.t !== b.t) return a.t - b.t;
    const ao = a.op === "node" ? 0 : 1;
    const bo = b.op === "node" ? 0 : 1;
    return ao - bo;
  });
}

/**
 * Synthetic chronology: sources first, then by descending degree,
 * then id. Edges fire at the later endpoint's slot so both ends exist.
 */
export function buildHistoryFromGraph(
  nodesIn: readonly GraphNodeIn[] | null | undefined,
  edgesIn: readonly GraphEdgeIn[] | null | undefined,
  opts: BuildHistoryOptions = {},
): HistoryDoc {
  const duration = opts.duration && opts.duration > 0 ? opts.duration : DEFAULT_DURATION;
  const maxEvents = opts.maxEvents && opts.maxEvents > 0 ? opts.maxEvents : DEFAULT_MAX_EVENTS;
  const source = opts.source ?? "ce-data-json";

  const nodes = (nodesIn ?? [])
    .map((n) => ({
      id: String(n.id || "").trim(),
      title: String(n.title || n.id || "").trim() || String(n.id || ""),
      type: String(n.type || "unclassified"),
      degree: typeof n.degree === "number" ? n.degree : 0,
      created: typeof n.created === "string" ? n.created.trim() : "",
    }))
    .filter((n) => n.id);

  if (!nodes.length) {
    return { duration, events: [], source: "empty", node_count: 0, degree: {} };
  }

  // Prefer frontmatter `created` when present; else type-tier → degree → id.
  nodes.sort(compareSyntheticNodes);

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const indexOf = new Map(nodes.map((n, i) => [n.id, i]));
  const nCount = nodes.length;
  const tOf = (i: number) => (nCount <= 1 ? 0 : (i / (nCount - 1)) * duration);

  const events: ReplayEvent[] = nodes.map((n, i) => ({
    t: Math.round(tOf(i) * 1000) / 1000,
    op: "node" as const,
    id: n.id,
    title: n.title,
    type: n.type,
  }));

  const seenPairs = new Set<string>();
  const edgeEvents: ReplayEvent[] = [];
  for (const e of edgesIn ?? []) {
    const s = edgeId(e.source);
    const t = edgeId(e.target);
    if (!s || !t || s === t || !byId.has(s) || !byId.has(t)) continue;
    const pair = s < t ? `${s}|${t}` : `${t}|${s}`;
    if (seenPairs.has(pair)) continue;
    seenPairs.add(pair);
    const later = Math.max(indexOf.get(s)!, indexOf.get(t)!);
    edgeEvents.push({
      t: Math.round(tOf(later) * 1000) / 1000,
      op: "edge",
      source: s,
      target: t,
    });
  }

  let merged = sortReplayEvents([...events, ...edgeEvents]);
  if (merged.length > maxEvents) {
    const nodeEv = merged.filter((e) => e.op === "node");
    let edgeEv = merged.filter((e) => e.op === "edge");
    const keep = Math.max(0, maxEvents - nodeEv.length);
    if (keep < edgeEv.length) {
      const step = edgeEv.length / Math.max(1, keep);
      edgeEv = Array.from({ length: keep }, (_, i) => edgeEv[Math.floor(i * step)]!);
    }
    merged = sortReplayEvents([...nodeEv, ...edgeEv]);
  }

  const degree: Record<string, number> = {};
  for (const n of nodes) degree[n.id] = 0;
  for (const e of merged) {
    if (e.op !== "edge") continue;
    degree[e.source] = (degree[e.source] ?? 0) + 1;
    degree[e.target] = (degree[e.target] ?? 0) + 1;
  }

  return {
    duration,
    events: merged,
    source,
    generated_at: Date.now() / 1000,
    node_count: nodes.length,
    degree,
  };
}

export type ReplaySnapshot = {
  /** Exclusive end index into sorted events (0 = empty). */
  index: number;
  nodes: Array<{ id: string; title: string; type: string }>;
  edges: Array<{ source: string; target: string }>;
};

/** Apply events `[0, index)` into a structural snapshot for scrub/step. */
export function snapshotAt(
  history: HistoryDoc | null | undefined,
  index: number,
): ReplaySnapshot {
  const events = sortReplayEvents(history?.events ?? []);
  const n = events.length;
  const clamped = Math.max(0, Math.min(n, Math.floor(index)));
  const nodes: ReplaySnapshot["nodes"] = [];
  const edges: ReplaySnapshot["edges"] = [];
  const seen = new Set<string>();
  for (let i = 0; i < clamped; i++) {
    const ev = events[i]!;
    if (ev.op === "node") {
      if (seen.has(ev.id)) continue;
      seen.add(ev.id);
      nodes.push({ id: ev.id, title: ev.title, type: ev.type });
    } else if (seen.has(ev.source) && seen.has(ev.target)) {
      edges.push({ source: ev.source, target: ev.target });
    }
  }
  return { index: clamped, nodes, edges };
}

/** Largest index such that all events with `t <= wall` are included. */
export function indexAtTime(
  history: HistoryDoc | null | undefined,
  wall: number,
): number {
  const events = sortReplayEvents(history?.events ?? []);
  let i = 0;
  while (i < events.length && events[i]!.t <= wall) i += 1;
  return i;
}
