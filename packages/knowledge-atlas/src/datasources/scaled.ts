/**
 * ScaledDataSource — a simulated million-node corpus served through
 * bounded scenes (PLAN §15.5, exercised by prototype P4).
 *
 * The graph is never materialised. Structure is a 4-level hierarchy
 * (50 × 100 × 100 × 2 = 1,000,000 leaves plus interior nodes), and
 * every neighbourhood is derived lazily from the seed + node id via
 * hashing, so any focus resolves in O(local scene), independent of
 * corpus size. This is also the template for real cloud sources: the
 * same request/response over a network is RemoteDataSource.
 */

import { GraphIndex } from "../core/graphindex.ts";
import { buildScene } from "../core/scene/builder.ts";
import { explainCandidate, explainEdge } from "../core/scene/discovery.ts";
import { childSeed, hashString, splitmix32 } from "../core/random.ts";
import type {
  AtlasDataSource,
  Explanation,
  ExplanationRequest,
  KnowledgeItem,
  SceneData,
  SceneRequest,
} from "../core/types.ts";

const LEVEL_SIZES = [50, 100, 100, 2];
const LEVEL_TYPES = ["project", "concept", "entity", "fact"];

export const SCALED_TOTAL_LEAVES = LEVEL_SIZES.reduce((a, b) => a * b, 1);

type ParsedId = number[]; // path indices, length 1..4

function parseId(id: string): ParsedId | null {
  if (!id.startsWith("s:")) return null;
  const parts = id.slice(2).split(".").map(Number);
  if (parts.length < 1 || parts.length > LEVEL_SIZES.length) return null;
  for (let i = 0; i < parts.length; i++) {
    if (!Number.isInteger(parts[i]) || parts[i] < 0 || parts[i] >= LEVEL_SIZES[i]) return null;
  }
  return parts;
}

function idOf(path: readonly number[]): string {
  return `s:${path.join(".")}`;
}

export class ScaledDataSource implements AtlasDataSource {
  private readonly seed: number;
  /** Simulated per-request latency in ms (cloud-style testing). */
  latencyMs: number;

  constructor(opts: { seed?: number; latencyMs?: number } = {}) {
    this.seed = opts.seed ?? 42;
    this.latencyMs = opts.latencyMs ?? 0;
  }

  private makeItem(path: ParsedId): KnowledgeItem {
    const id = idOf(path);
    const level = path.length - 1;
    const h = hashString(`${this.seed}:${id}`);
    return {
      id,
      type: LEVEL_TYPES[level],
      title: `${LEVEL_TYPES[level]} ${path.join(".")}`,
      meta: { sources: [`vault/block-${path[0]}/doc-${h % 500}.md`] },
    };
  }

  /**
   * Materialise the bounded neighbourhood around a focus id into a
   * GraphIndex: ancestors, children (sampled), siblings (sampled),
   * plus deterministic cross-links to other blocks. Node count is a
   * few hundred regardless of where in the million-leaf corpus the
   * focus sits.
   */
  private materialize(focusPath: ParsedId): GraphIndex {
    const g = new GraphIndex();
    const rng = splitmix32(childSeed(this.seed, idOf(focusPath)));
    const added = new Set<string>();
    const addNode = (path: ParsedId) => {
      const id = idOf(path);
      if (!added.has(id)) {
        added.add(id);
        g.addItem(this.makeItem(path));
      }
      return id;
    };

    // Ancestor chain (hierarchy edges).
    let prev: string | null = null;
    for (let l = 1; l <= focusPath.length; l++) {
      const id = addNode(focusPath.slice(0, l));
      if (prev) g.addEdge(prev, id, "wikilink");
      prev = id;
    }
    const focusId = idOf(focusPath);

    // Children (sample up to 60).
    const level = focusPath.length - 1;
    if (level + 1 < LEVEL_SIZES.length) {
      const n = LEVEL_SIZES[level + 1];
      const step = Math.max(1, Math.floor(n / 60));
      for (let i = 0; i < n; i += step) {
        const cid = addNode([...focusPath, i]);
        g.addEdge(focusId, cid, "wikilink");
      }
    }

    // Siblings (sample up to 40) share the parent.
    if (focusPath.length > 1) {
      const parentPath = focusPath.slice(0, -1);
      const parentId = idOf(parentPath);
      const n = LEVEL_SIZES[level];
      const step = Math.max(1, Math.floor(n / 40));
      for (let i = 0; i < n; i += step) {
        if (i === focusPath[focusPath.length - 1]) continue;
        const sid = addNode([...parentPath, i]);
        g.addEdge(parentId, sid, "wikilink");
      }
    }

    // Deterministic cross-links: every node gets 0–2 far links derived
    // from its hash (small-world shortcuts; discovery material).
    for (const id of [...added]) {
      const h = hashString(`${this.seed}:x:${id}`);
      const linkCount = h % 3;
      for (let k = 0; k < linkCount; k++) {
        // Unsigned shifts — a signed >> flips the sign bit into the
        // modulo and mints impossible negative path indices.
        const target: ParsedId = [
          (h >>> (4 + k)) % LEVEL_SIZES[0],
          (h >>> (9 + k)) % LEVEL_SIZES[1],
          (h >>> (14 + k)) % LEVEL_SIZES[2],
        ];
        const tid = addNode(target);
        g.addEdge(id, tid, "co-cited", 0.5 + 0.5 * rng());
      }
    }
    return g;
  }

  private async delay(signal?: AbortSignal): Promise<void> {
    if (this.latencyMs <= 0) return;
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(resolve, this.latencyMs);
      signal?.addEventListener("abort", () => {
        clearTimeout(t);
        reject(new DOMException("aborted", "AbortError"));
      });
    });
  }

  async getScene(request: SceneRequest, signal?: AbortSignal): Promise<SceneData> {
    await this.delay(signal);
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    const path = request.focusId ? parseId(request.focusId) : null;
    const effectivePath = path ?? [0];
    const g = this.materialize(effectivePath);
    const scene = buildScene(
      g,
      { ...request, focusId: idOf(effectivePath) },
      this.seed,
      SCALED_TOTAL_LEAVES,
    );
    return scene;
  }

  async getItem(id: string): Promise<KnowledgeItem | null> {
    const path = parseId(id);
    return path ? this.makeItem(path) : null;
  }

  async getExplanation(request: ExplanationRequest): Promise<Explanation> {
    if (request.kind === "candidate") {
      const path = parseId(request.focusId);
      if (path) {
        const g = this.materialize(path);
        return explainCandidate(g, request.id, request.focusId, request.cls);
      }
    }
    if (request.kind === "edge") {
      const path = parseId(request.source);
      if (path) {
        const g = this.materialize(path);
        return explainEdge(g, request.source, request.target, request.type);
      }
    }
    return { summary: { kind: "aggregate", text: "No explanation available." } };
  }
}
