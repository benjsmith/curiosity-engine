/**
 * LocalSceneSource — shared base for data sources whose full graph is
 * client-side (CE payloads, fixtures, the scaled corpus's materialised
 * neighbourhood). Runs the core scene pipeline over a GraphIndex.
 * Cloud sources implement AtlasDataSource directly and run the same
 * pipeline server-side (PLAN §7).
 */

import { buildScene } from "../core/scene/builder.ts";
import { explainCandidate, explainEdge } from "../core/scene/discovery.ts";
import type { GraphIndex } from "../core/graphindex.ts";
import type {
  AtlasDataSource,
  Explanation,
  ExplanationRequest,
  KnowledgeItem,
  SceneData,
  SceneRequest,
} from "../core/types.ts";

export class LocalSceneSource implements AtlasDataSource {
  protected readonly graph: GraphIndex;
  protected readonly seed: number;

  constructor(graph: GraphIndex, opts: { seed?: number } = {}) {
    this.graph = graph;
    this.seed = opts.seed ?? 42;
  }

  getScene(request: SceneRequest, signal?: AbortSignal): Promise<SceneData> {
    if (signal?.aborted) return Promise.reject(new DOMException("aborted", "AbortError"));
    return Promise.resolve(buildScene(this.graph, request, this.seed));
  }

  getItem(id: string): Promise<KnowledgeItem | null> {
    return Promise.resolve(this.graph.items.get(id) ?? null);
  }

  getExplanation(request: ExplanationRequest): Promise<Explanation> {
    if (request.kind === "candidate") {
      return Promise.resolve(explainCandidate(this.graph, request.id, request.focusId, request.cls));
    }
    if (request.kind === "edge") {
      return Promise.resolve(explainEdge(this.graph, request.source, request.target, request.type));
    }
    return Promise.resolve({
      summary: { kind: "aggregate", text: `Group of related items (${request.id}).` },
    });
  }
}
