/**
 * GraphIndex — the in-memory indexed graph that local data sources
 * (Curiosity Engine payloads, fixtures, the scaled procedural corpus)
 * build scenes over. Deep-copies everything at ingest: nothing
 * downstream may mutate host data, and nothing here assumes host
 * object identity (PLAN §2.1: D3 mutates edges in place upstream).
 */

import type { KnowledgeItem } from "./types.ts";

export type IndexedEdge = {
  from: string;
  to: string;
  type: string;
  /** 0..1; provisional edges carry their score, curated edges 1. */
  confidence: number;
};

export type Neighbour = {
  id: string;
  type: string; // edge type
  dir: 1 | -1; // 1 = outgoing (from this node), -1 = incoming
  confidence: number;
};

export class GraphIndex {
  readonly items = new Map<string, KnowledgeItem>();
  readonly edges: IndexedEdge[] = [];
  private readonly adj = new Map<string, Neighbour[]>();
  private readonly degreeMap = new Map<string, number>();
  /** source path -> item ids citing it (provenance index). */
  readonly bySource = new Map<string, string[]>();
  /** type -> item ids. */
  readonly byType = new Map<string, string[]>();

  addItem(item: KnowledgeItem): void {
    const copy: KnowledgeItem = {
      ...item,
      meta: { ...item.meta, properties: item.meta.properties ? { ...item.meta.properties } : undefined },
    };
    this.items.set(copy.id, copy);
    const list = this.byType.get(copy.type) ?? [];
    list.push(copy.id);
    this.byType.set(copy.type, list);
    for (const s of copy.meta.sources ?? []) {
      const cited = this.bySource.get(s) ?? [];
      cited.push(copy.id);
      this.bySource.set(s, cited);
    }
  }

  addEdge(from: string, to: string, type: string, confidence = 1): void {
    if (!this.items.has(from) || !this.items.has(to)) return;
    if (from === to) return;
    this.edges.push({ from, to, type, confidence });
    this.push(from, { id: to, type, dir: 1, confidence });
    this.push(to, { id: from, type, dir: -1, confidence });
    this.degreeMap.set(from, (this.degreeMap.get(from) ?? 0) + 1);
    this.degreeMap.set(to, (this.degreeMap.get(to) ?? 0) + 1);
  }

  private push(id: string, n: Neighbour): void {
    const list = this.adj.get(id) ?? [];
    list.push(n);
    this.adj.set(id, list);
  }

  neighbours(id: string): readonly Neighbour[] {
    return this.adj.get(id) ?? [];
  }

  degree(id: string): number {
    return this.degreeMap.get(id) ?? 0;
  }

  get size(): number {
    return this.items.size;
  }

  /** Shared provenance between two items (# of common source paths). */
  sharedSources(a: string, b: string): string[] {
    const sa = this.items.get(a)?.meta.sources;
    const sb = this.items.get(b)?.meta.sources;
    if (!sa?.length || !sb?.length) return [];
    const set = new Set(sb);
    return sa.filter((s) => set.has(s));
  }

  /** Jaccard similarity of neighbour sets (redundancy penalty input). */
  neighbourJaccard(a: string, b: string): number {
    const na = this.adj.get(a);
    const nb = this.adj.get(b);
    if (!na?.length || !nb?.length) return 0;
    const setA = new Set(na.map((n) => n.id));
    let inter = 0;
    const setB = new Set(nb.map((n) => n.id));
    for (const id of setB) if (setA.has(id)) inter++;
    const union = setA.size + setB.size - inter;
    return union === 0 ? 0 : inter / union;
  }

  /**
   * Budget-capped BFS harvest from a focus id. Returns hop distance,
   * the first edge type seen on a shortest path, and the parent —
   * enough for ranking and explanations without storing full paths.
   */
  harvest(
    focusId: string,
    opts: { maxHops?: number; visitCap?: number; relationTypes?: string[] } = {},
  ): Map<string, { hop: number; via: string; parent: string; confidence: number }> {
    const maxHops = opts.maxHops ?? 3;
    const visitCap = opts.visitCap ?? 4000;
    const allow = opts.relationTypes ? new Set(opts.relationTypes) : null;
    const out = new Map<string, { hop: number; via: string; parent: string; confidence: number }>();
    if (!this.items.has(focusId)) return out;
    let frontier = [focusId];
    const seen = new Set([focusId]);
    let visits = 0;
    for (let hop = 1; hop <= maxHops && frontier.length; hop++) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const n of this.neighbours(id)) {
          if (allow && !allow.has(n.type)) continue;
          if (seen.has(n.id)) continue;
          seen.add(n.id);
          out.set(n.id, { hop, via: n.type, parent: id, confidence: n.confidence });
          next.push(n.id);
          if (++visits >= visitCap) return out;
        }
      }
      frontier = next;
    }
    return out;
  }
}
