/**
 * RemoteDataSource — cloud-style source (PLAN §15.5 / P4).
 *
 * Two modes:
 *  - wrap(inner, latencyMs): wraps any AtlasDataSource with simulated
 *    network latency + abort handling — used by the harness to prove
 *    local and cloud-style sources drive the same viewer API.
 *  - url(base): speaks the SceneRequest/SceneData contract over fetch
 *    (POST {base}/scene, GET {base}/item/:id, POST {base}/explain).
 *    The engine itself never fetches (AD-6); this class is host-side.
 */

import type {
  AtlasDataSource,
  Explanation,
  ExplanationRequest,
  KnowledgeItem,
  SceneData,
  SceneRequest,
} from "../core/types.ts";

export class RemoteDataSource implements AtlasDataSource {
  private constructor(
    private readonly impl: AtlasDataSource,
  ) {}

  static wrap(inner: AtlasDataSource, latencyMs: number): RemoteDataSource {
    const delayed = async <T>(fn: () => Promise<T>, signal?: AbortSignal): Promise<T> => {
      await new Promise<void>((resolve, reject) => {
        const t = setTimeout(resolve, latencyMs);
        signal?.addEventListener("abort", () => {
          clearTimeout(t);
          reject(new DOMException("aborted", "AbortError"));
        });
      });
      return fn();
    };
    return new RemoteDataSource({
      getScene: (req, signal) => delayed(() => inner.getScene(req, signal), signal),
      getItem: (id) => inner.getItem(id),
      getExplanation: (req) => inner.getExplanation(req),
    });
  }

  static url(base: string): RemoteDataSource {
    const json = async <T>(path: string, init?: RequestInit): Promise<T> => {
      const r = await fetch(`${base}${path}`, {
        headers: { "content-type": "application/json" },
        ...init,
      });
      if (!r.ok) throw new Error(`atlas remote: HTTP ${r.status} for ${path}`);
      return (await r.json()) as T;
    };
    return new RemoteDataSource({
      getScene: (req, signal) =>
        json<SceneData>("/scene", { method: "POST", body: JSON.stringify(req), signal }),
      getItem: (id) => json<KnowledgeItem | null>(`/item/${encodeURIComponent(id)}`),
      getExplanation: (req) =>
        json<Explanation>("/explain", { method: "POST", body: JSON.stringify(req) }),
    });
  }

  getScene(request: SceneRequest, signal?: AbortSignal): Promise<SceneData> {
    return this.impl.getScene(request, signal);
  }
  getItem(id: string): Promise<KnowledgeItem | null> {
    return this.impl.getItem(id);
  }
  getExplanation(request: ExplanationRequest): Promise<Explanation> {
    return this.impl.getExplanation(request);
  }
}
