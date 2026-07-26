#!/usr/bin/env python3
"""embedder.py — shared local embedding backend for every CE script.

One loader, two backends, auto-selected by what's installed:

- **fastembed** (ONNX runtime — no PyTorch, ~50 MB of deps). Preferred.
  Default model `BAAI/bge-small-en-v1.5`: 384-dim, stronger retrieval
  than MiniLM at the same size. bge-style models are asymmetric —
  queries embed with a retrieval instruction — so `embed_query` and
  `embed_passages` are distinct calls.
- **sentence-transformers** (PyTorch, ~2 GB of deps). Fallback for
  workspaces that already have it. Default model
  `sentence-transformers/all-MiniLM-L6-v2` (symmetric; query == passage).
  Loaded cache-first (`local_files_only=True`) so a warm cache never
  re-validates against the HF hub (or hangs offline); the network path
  only runs when the model genuinely isn't cached yet.

Both backends emit L2-normalised vectors as plain `list[float]`, ready
for `sqlite_vec.serialize_float32`, so the vec0 schema is identical —
only the vector *space* differs. `Embedder.model_id` labels that space:
plain model name for sentence-transformers (byte-compatible with indexes
built by pre-v0.6 CE), `fastembed:<model>` for fastembed. Consumers
store the label next to their vectors and re-embed when it changes.

Backend selection (`embedding_backend` in .curator/config.json, default
"auto"):
- "auto" — fastembed if importable, else sentence-transformers. A model
  name starting with `sentence-transformers/` pins the ST backend, so a
  pre-v0.6 workspace whose config (and vectors) are MiniLM/ST keeps its
  vector space even after fastembed gets installed.
- "fastembed" / "sentence-transformers" — force one; no silent fallback.

Local backends only, by design: embedding text never leaves the machine.

STABLE LIBRARY SURFACE (v0.8+). External tools that vendor CE (e.g.
switchyard) may import this module directly instead of shipping their
own local-embedding stack; the following are covered by CE's versioning
policy (breaking changes only on a major bump):

    load_embedder(config: dict) -> (Embedder | None, reason: str)
    predict_model_id(config: dict) -> str | None
    Embedder.embed_passages(texts) / .embed_query(text)
    Embedder.model_id / .model_name / .backend / .dim
    DEFAULT_FASTEMBED_MODEL / DEFAULT_ST_MODEL

`config` is any dict with the embedding_* keys — callers outside a CE
workspace pass their own (e.g. {"embedding_enabled": True}); nothing
here reads the filesystem.

A small CLI exists for diagnostics and one-off/non-Python callers
(per-call model load makes it unsuitable for hot loops — import the
module for those):

    embedder.py probe [--config PATH] [--model M] [--backend B]
    embedder.py embed-query "text" [...]
    embedder.py embed-passages [...]      # stdin: JSON array of strings
"""
from __future__ import annotations

import sys

__all__ = ["Embedder", "load_embedder", "predict_model_id",
           "DEFAULT_FASTEMBED_MODEL", "DEFAULT_ST_MODEL"]

DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_ST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder:
    """Uniform interface over the two local backends. All vectors are
    L2-normalised `list[float]`; `model_id` labels the vector space."""

    def __init__(self, backend: str, model_name: str, impl):
        self.backend = backend
        self.model_name = model_name
        self._impl = impl
        self._dim = None

    @property
    def model_id(self) -> str:
        if self.backend == "fastembed":
            return f"fastembed:{self.model_name}"
        return self.model_name

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_query("dimension probe"))
        return self._dim

    @staticmethod
    def _normed(vec) -> list:
        import numpy as np
        v = np.asarray(vec, dtype="float32")
        n = float(np.linalg.norm(v))
        # isfinite: a NaN norm is truthy, so a bare `if n` would divide
        # by NaN and propagate it into the stored vector.
        return (v / n if n and np.isfinite(n) else v).tolist()

    def embed_passages(self, texts: list) -> list:
        if self.backend == "fastembed":
            return [self._normed(v) for v in self._impl.embed(list(texts))]
        vecs = self._impl.encode(list(texts), normalize_embeddings=True,
                                 batch_size=32, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list:
        if self.backend == "fastembed":
            # bge/arctic-style query embedding carries the retrieval
            # instruction; symmetric models pass through unchanged.
            return self._normed(next(iter(self._impl.query_embed([text]))))
        return self._impl.encode(text, normalize_embeddings=True).tolist()


def _load_fastembed(model_name: str):
    from fastembed import TextEmbedding
    return Embedder("fastembed", model_name, TextEmbedding(model_name=model_name))


def _load_st(model_name: str):
    from sentence_transformers import SentenceTransformer
    try:
        impl = SentenceTransformer(model_name, local_files_only=True)
    except Exception:  # not cached yet — fetch for real
        impl = SentenceTransformer(model_name)
    return Embedder("sentence-transformers", model_name, impl)


def _select_backend(config: dict):
    backend = str(config.get("embedding_backend", "auto")).strip().lower()
    model_name = str(config.get("embedding_model") or "").strip()
    if backend == "auto" and model_name.startswith("sentence-transformers/"):
        # Pre-v0.6 configs name an ST model; keep their vector space.
        backend = "sentence-transformers"
    return backend, model_name


def predict_model_id(config: dict):
    """The model_id `load_embedder` would produce for this config given
    the currently-installed deps, WITHOUT loading any model. None when
    unpredictable (missing deps) — callers should treat None as 'match'
    and let load_embedder produce the real error."""
    backend, model_name = _select_backend(config)
    if backend in ("auto", "fastembed"):
        try:
            import fastembed  # noqa: F401
            return f"fastembed:{model_name or DEFAULT_FASTEMBED_MODEL}"
        except ImportError:
            if backend == "fastembed":
                return None
    return model_name or DEFAULT_ST_MODEL


def load_embedder(config: dict):
    """Return (Embedder, "") or (None, reason). Never raises.

    `config` is the parsed .curator/config.json dict; keys read:
    embedding_enabled, embedding_backend ("auto" default), embedding_model.
    """
    if not config.get("embedding_enabled"):
        return None, "embedding_enabled=false in .curator/config.json"
    backend, model_name = _select_backend(config)

    if backend in ("auto", "fastembed"):
        try:
            return _load_fastembed(model_name or DEFAULT_FASTEMBED_MODEL), ""
        except ImportError as e:
            if backend == "fastembed":
                return None, (f"embedding_backend=fastembed but fastembed not "
                              f"installed ({e}) — uv pip install fastembed")
        except Exception as e:
            msg = f"fastembed model load failed ({e})"
            if backend == "fastembed":
                return None, msg
            print(f"embedder: {msg}; trying sentence-transformers",
                  file=sys.stderr)

    try:
        return _load_st(model_name or DEFAULT_ST_MODEL), ""
    except ImportError as e:
        return None, (f"no embedding backend installed ({e}) — "
                      "uv pip install fastembed sqlite-vec")
    except Exception as e:
        return None, f"embedding model load failed ({e})"


def _cli() -> int:
    import argparse
    import json
    from pathlib import Path

    ap = argparse.ArgumentParser(
        description="Diagnostics / one-off access to the shared embedder. "
                    "Loads the model per call — import the module for hot loops.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "embed-query", "embed-passages"):
        p = sub.add_parser(name)
        if name == "embed-query":
            p.add_argument("text")
        p.add_argument("--config", default=".curator/config.json",
                       help="config JSON with embedding_* keys "
                            "(default: .curator/config.json if present)")
        p.add_argument("--model", default=None,
                       help="override embedding_model")
        p.add_argument("--backend", default=None,
                       help="override embedding_backend (auto|fastembed|"
                            "sentence-transformers)")
    args = ap.parse_args()

    cfg = {}
    cfg_path = Path(args.config)
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
        except (OSError, json.JSONDecodeError):
            cfg = {}
    # Invoking the CLI is itself the opt-in; a missing config or an
    # embedding_enabled=false workspace shouldn't make `probe` useless.
    cfg["embedding_enabled"] = True
    if args.model:
        cfg["embedding_model"] = args.model
    if args.backend:
        cfg["embedding_backend"] = args.backend

    emb, err = load_embedder(cfg)
    if emb is None:
        print(json.dumps({"error": err}))
        return 2
    if args.cmd == "probe":
        print(json.dumps({"backend": emb.backend, "model_id": emb.model_id,
                          "dim": emb.dim}))
    elif args.cmd == "embed-query":
        print(json.dumps({"model_id": emb.model_id,
                          "vector": emb.embed_query(args.text)}))
    else:  # embed-passages
        try:
            texts = json.loads(sys.stdin.read())
            assert isinstance(texts, list) \
                and all(isinstance(t, str) for t in texts)
        except Exception:
            print(json.dumps({"error": "stdin must be a JSON array of strings"}))
            return 2
        print(json.dumps({"model_id": emb.model_id,
                          "vectors": emb.embed_passages(texts)}))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
