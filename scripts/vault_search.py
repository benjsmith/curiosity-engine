#!/usr/bin/env python3
"""Ranked search over vault sources — FTS5, semantic, or hybrid.

Usage:
    python3 vault_search.py "query"                       # FTS5, top 10
    python3 vault_search.py "query" --limit 5             # FTS5, top 5
    python3 vault_search.py "query" --text                # include full body
    python3 vault_search.py --count                       # total doc count
    python3 vault_search.py "query" --mode semantic       # semantic only
    python3 vault_search.py "query" --mode hybrid         # FTS5 + semantic (RRF)

Modes:
    fts5      BM25 keyword. Default. Sharp for exact/stem matches, weak
              for paraphrased queries. No dep beyond stdlib sqlite3.
    semantic  MiniLM (or configured model) cosine similarity. Requires
              embedding_enabled=true in config + the embedding index
              (built by vault_index at ingest time). Catches paraphrases.
    hybrid    Run both; merge via Reciprocal Rank Fusion (RRF). Best of
              both in most cases. Falls back to fts5 if embeddings
              aren't available.
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DB = Path("vault/vault.db")
CONFIG_PATH = Path(".curator/config.json")
DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_FTS5_RESERVED = {"AND", "OR", "NOT", "NEAR"}


def _sanitize_fts(query: str) -> str:
    """Quote hyphenated tokens and FTS5 operators so raw syntax can't leak."""
    out = []
    for tok in re.findall(r'"[^"]*"|\S+', query):
        if tok.startswith('"'):
            out.append(tok)
        elif "-" in tok or tok.upper() in _FTS5_RESERVED or re.fullmatch(r"\w+:", tok):
            out.append('"' + tok.replace('"', "") + '"')
        else:
            out.append(tok)
    return " ".join(out)


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _load_embedder_for_search():
    """Return (model, sqlite_vec) if embeddings usable, else (None, None).

    Unlike vault_index, we soft-fail on missing deps — a user might have
    disabled embeddings after indexing, or be searching a FTS5-only vault
    with --mode hybrid. Falling back to FTS5 is always safe.
    """
    cfg = _load_config()
    if not cfg.get("embedding_enabled"):
        return None, None
    try:
        from sentence_transformers import SentenceTransformer
        import sqlite_vec
    except ImportError:
        sys.stderr.write(
            "vault_search: embedding_enabled=true but sentence-transformers "
            "or sqlite-vec missing; falling back to FTS5.\n"
        )
        return None, None
    model_name = cfg.get("embedding_model", DEFAULT_EMBED_MODEL)
    return SentenceTransformer(model_name), sqlite_vec


def _fts5_search(conn, query: str, limit: int, include_text: bool) -> list:
    query = _sanitize_fts(query)
    if include_text:
        rows = conn.execute(
            "SELECT path, title, source_path, date, body, bm25(sources) as rank "
            "FROM sources WHERE sources MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [{"path": r[0], "title": r[1], "source_path": r[2],
                 "date": r[3], "text": r[4], "rank": round(r[5], 4)} for r in rows]
    rows = conn.execute(
        "SELECT path, title, source_path, date, "
        "snippet(sources, 2, '>>>', '<<<', '...', 40) as snippet, "
        "bm25(sources) as rank "
        "FROM sources WHERE sources MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    ).fetchall()
    return [{"path": r[0], "title": r[1], "source_path": r[2],
             "date": r[3], "snippet": r[4], "rank": round(r[5], 4)} for r in rows]


def _semantic_search(conn, model, sqlite_vec_mod, query: str,
                      limit: int, include_text: bool) -> list:
    sqlite_vec_mod.load(conn)
    qvec = model.encode(query, normalize_embeddings=True).tolist()
    qbytes = sqlite_vec_mod.serialize_float32(qvec)
    rows = conn.execute("""
        SELECT em.path, s.title, s.source_path, s.date, s.body, se.distance
        FROM source_embeddings se
        JOIN embedding_meta em ON em.vec_id = se.rowid
        JOIN sources s ON s.path = em.path
        WHERE se.embedding MATCH ? AND k = ?
        ORDER BY se.distance
    """, (qbytes, limit)).fetchall()
    results = []
    for r in rows:
        entry = {"path": r[0], "title": r[1], "source_path": r[2],
                 "date": r[3], "distance": round(r[5], 4)}
        if include_text:
            entry["text"] = r[4]
        else:
            # Cheap snippet: first substantive line.
            body = r[4] or ""
            first = next(
                (ln for ln in body.split("\n")
                 if ln.strip() and not ln.startswith("#")
                 and not ln.startswith("<!--") and not ln.startswith("---")),
                ""
            )
            entry["snippet"] = first[:400]
        results.append(entry)
    return results


def _rrf_merge(fts_results: list, sem_results: list, k: int = 60,
                limit: int = 10) -> list:
    """Reciprocal Rank Fusion on two ranked lists keyed by `path`."""
    scores = {}
    for i, r in enumerate(fts_results):
        s = scores.setdefault(r["path"], {"score": 0.0, "entry": r})
        s["score"] += 1.0 / (k + i + 1)
    for i, r in enumerate(sem_results):
        if r["path"] in scores:
            scores[r["path"]]["score"] += 1.0 / (k + i + 1)
        else:
            scores[r["path"]] = {"score": 1.0 / (k + i + 1), "entry": r}
    merged = sorted(scores.values(), key=lambda x: -x["score"])
    return [m["entry"] for m in merged[:limit]]


def search(query: str, limit: int, include_text: bool, mode: str = "fts5"):
    if not DB.exists():
        print("[]")
        return
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA journal_mode=WAL")

    if mode in ("semantic", "hybrid"):
        model, vec_mod = _load_embedder_for_search()
        if model is None:
            mode = "fts5"  # soft fallback

    if mode == "fts5":
        results = _fts5_search(conn, query, limit, include_text)
    elif mode == "semantic":
        results = _semantic_search(conn, model, vec_mod, query, limit, include_text)
    else:  # hybrid
        fts = _fts5_search(conn, query, limit * 2, include_text)
        sem = _semantic_search(conn, model, vec_mod, query, limit * 2, include_text)
        results = _rrf_merge(fts, sem, limit=limit)

    conn.close()
    print(json.dumps(results, indent=2))


def count():
    if not DB.exists():
        print("0")
        return
    conn = sqlite3.connect(str(DB))
    print(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None,
                    help="query string (FTS5 for fts5/hybrid modes, "
                         "natural-language for semantic)")
    ap.add_argument("--limit", type=int, default=10,
                    help="max results (default: 10)")
    ap.add_argument("--text", action="store_true",
                    help="include full extracted body instead of a snippet")
    ap.add_argument("--count", action="store_true",
                    help="print total indexed document count and exit")
    ap.add_argument("--mode", choices=["fts5", "semantic", "hybrid"],
                    default="fts5",
                    help="retrieval mode (default: fts5). semantic/hybrid "
                         "require embedding_enabled=true and the "
                         "semantic index (built by vault_index).")
    args = ap.parse_args()

    if args.count:
        count()
        return

    if not args.query:
        ap.print_usage()
        sys.exit(1)

    search(args.query, args.limit, args.text, args.mode)


if __name__ == "__main__":
    main()
