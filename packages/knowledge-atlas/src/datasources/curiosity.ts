/**
 * Curiosity Engine data adapter (PLAN §14).
 *
 * Consumes the viewer payload emitted by wiki_render.py (data.json) —
 * the same shape Switchbay's /api/graph/data serves — and exposes it
 * as an AtlasDataSource. Handles the payload's documented sharp edges:
 *   - `nodes ⊆ pages` under kuzu drift: pages is the item store, nodes
 *     the graph membership; page-only entries become degree-0 items
 *     (server fixed in CE v1.2.1, but older cached bundles persist);
 *   - ids without `.md` vs paths with it;
 *   - edges whose source/target may be objects {id} (D3 mutation);
 *   - `[con]`-style title prefixes split into meta.titlePrefix;
 *   - type canonicalisation (plural collapse + table variants +
 *     prefix backfill), shared with Switchbay's semantics.
 */

import { GraphIndex } from "../core/graphindex.ts";
import { LocalSceneSource } from "./local.ts";
import type { KnowledgeItem } from "../core/types.ts";

/** Schema of wiki_render.py's data.json (see PLAN §2.1). */
export type CEData = {
  workspace: string;
  generated_at: string;
  palette: Record<string, string>;
  nodes: Array<{
    id: string;
    path: string;
    type: string;
    title: string;
    degree: number;
  }>;
  edges: Array<{
    source: string | { id: string };
    target: string | { id: string };
    type: string;
    /** Optional --atlas-edges enrichment fields (PLAN §14.3). */
    confidence?: number;
    origin?: string;
  }>;
  pages: Record<
    string,
    {
      id: string;
      title: string;
      type: string;
      path: string;
      properties: Record<string, unknown>;
      body_html: string;
    }
  >;
  scan_staleness?: unknown;
};

/** Union of wiki-view's TYPE_CANONICAL and Switchbay's prefix backfill. */
const TYPE_CANONICAL: Record<string, string> = {
  analysis: "analysis", analyses: "analysis",
  concept: "concept", concepts: "concept",
  entity: "entity", entities: "entity",
  evidence: "evidence",
  fact: "fact", facts: "fact",
  figure: "figure", figures: "figure",
  table: "table", tables: "table",
  "extracted-table": "table", "summary-table": "table",
  source: "source", sources: "source",
  note: "note", notes: "note",
  todo: "todo-list", "todo-list": "todo-list",
  project: "project", projects: "project",
  unclassified: "unclassified",
};

const PREFIX_TO_TYPE: Record<string, string> = {
  con: "concept", ent: "entity", ana: "analysis", src: "source",
  evi: "evidence", fact: "fact", tbl: "table", tab: "table",
  fig: "figure", note: "note", todo: "todo-list", proj: "project",
};

const TITLE_PREFIX_RE = /^(\[[^\]]+\])\s+(.+)$/;

export function canonicalType(raw: string, titlePrefix?: string): string {
  const t = TYPE_CANONICAL[raw];
  if (t && t !== "unclassified") return t;
  if (titlePrefix) {
    const stem = titlePrefix.replace(/^\[|\]$/g, "");
    const backfilled = PREFIX_TO_TYPE[stem];
    if (backfilled) return backfilled;
  }
  return t ?? "unclassified";
}

/** Strip a trailing `.md`; the canonical atlas id is suffix-less. */
export function normalizeId(idOrPath: string): string {
  return idOrPath.endsWith(".md") ? idOrPath.slice(0, -3) : idOrPath;
}

function edgeEndpoint(e: string | { id: string }): string {
  return normalizeId(typeof e === "object" ? e.id : e);
}

export function ceItem(p: {
  id: string;
  title: string;
  type: string;
  path?: string;
  degree?: number;
  properties?: Record<string, unknown>;
}): KnowledgeItem {
  const m = TITLE_PREFIX_RE.exec(p.title);
  const titlePrefix = m ? m[1] : undefined;
  const title = m ? m[2] : p.title;
  const props = p.properties ?? {};
  const sources = Array.isArray(props.sources)
    ? (props.sources as unknown[]).filter((s): s is string => typeof s === "string")
    : undefined;
  return {
    id: normalizeId(p.id),
    type: canonicalType(p.type, titlePrefix),
    title,
    meta: {
      titlePrefix,
      path: p.path,
      sources,
      created: typeof props.created === "string" ? props.created : undefined,
      updated: typeof props.updated === "string" ? props.updated : undefined,
      degree: p.degree,
      properties: props,
    },
  };
}

export function indexFromCEData(data: CEData): GraphIndex {
  const g = new GraphIndex();
  // pages is the item store; nodes only add degree info.
  const nodeById = new Map(data.nodes.map((n) => [normalizeId(n.id), n]));
  for (const page of Object.values(data.pages)) {
    const node = nodeById.get(normalizeId(page.id));
    g.addItem(ceItem({ ...page, degree: node?.degree }));
  }
  // Nodes without a pages entry shouldn't exist, but tolerate them.
  for (const n of data.nodes) {
    if (!g.items.has(normalizeId(n.id))) g.addItem(ceItem(n));
  }
  for (const e of data.edges) {
    g.addEdge(edgeEndpoint(e.source), edgeEndpoint(e.target), e.type, e.confidence ?? 1);
  }
  // Derive shared-source edges when the payload lacks --atlas-edges
  // enrichment: pages citing >=2 common vault sources get a low-
  // confidence "co-cited" relation (discovery signal, tier-5 edge).
  const hasEnrichment = data.edges.some((e) => e.type !== "wikilink" && e.type !== "depicts");
  if (!hasEnrichment) {
    const seen = new Set<string>();
    for (const [, citers] of g.bySource) {
      if (citers.length < 2 || citers.length > 50) continue; // hub sources are uninformative
      for (let i = 0; i < citers.length; i++) {
        for (let j = i + 1; j < citers.length; j++) {
          const a = citers[i];
          const b = citers[j];
          const key = a < b ? `${a}|${b}` : `${b}|${a}`;
          if (seen.has(key)) continue;
          if (g.sharedSources(a, b).length >= 2) {
            seen.add(key);
            g.addEdge(a, b, "co-cited", 0.5);
          }
        }
      }
    }
  }
  return g;
}

/**
 * The data source. Construct from an already-fetched CEData object —
 * hosts own fetching (the engine performs no network I/O, PLAN AD-6).
 */
export class CuriosityDataSource extends LocalSceneSource {
  readonly palette: Record<string, string>;
  constructor(data: CEData, opts: { seed?: number } = {}) {
    super(indexFromCEData(data), opts);
    this.palette = { ...data.palette };
  }
}
