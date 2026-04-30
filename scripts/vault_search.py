#!/usr/bin/env python3
"""Ranked search over vault sources — FTS5, semantic, or hybrid, optionally
graph-expanded.

Usage:
    python3 vault_search.py "query"                       # FTS5, top 10
    python3 vault_search.py "query" --limit 5             # FTS5, top 5
    python3 vault_search.py "query" --text                # include full body
    python3 vault_search.py --count                       # total doc count
    python3 vault_search.py "query" --mode semantic       # semantic only
    python3 vault_search.py "query" --mode hybrid         # FTS5 + semantic (RRF)
    python3 vault_search.py "query" --graph-expand        # any mode + 1-hop kuzu

Modes:
    fts5      BM25 keyword. Default. Sharp for exact/stem matches, weak
              for paraphrased queries. No dep beyond stdlib sqlite3.
    semantic  MiniLM (or configured model) cosine similarity. Requires
              embedding_enabled=true in config + the embedding index
              (built by vault_index at ingest time). Catches paraphrases.
    hybrid    Run both; merge via Reciprocal Rank Fusion (RRF). Best of
              both in most cases. Falls back to fts5 if embeddings
              aren't available.

--graph-expand augments any mode with a third RRF stream: take the top
seeds from FTS5/semantic, walk one hop through the kuzu wiki layer
(sources cited by wiki pages that also cite the seeds), and merge those
neighbours into the result set ranked by shared-page count. Surfaces
sources that don't keyword-match the query but share thematic context
with the strongest matches. Soft-falls-back to no expansion if kuzu
isn't installed or the graph DB hasn't been built yet.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# See vault_index.py — stdlib sqlite3 on macOS often lacks load_extension,
# which sqlite-vec needs. pysqlite3-binary is a drop-in with it enabled.
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

DB = Path("vault/vault.db")
GRAPH_DB = Path(".curator/graph.kuzu")
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


def _fts5_query(conn, fts_expr: str, limit: int, include_text: bool) -> list:
    """Run one FTS5 query, return rows as dicts. Internal helper."""
    if include_text:
        rows = conn.execute(
            "SELECT path, title, source_path, date, body, bm25(sources) as rank "
            "FROM sources WHERE sources MATCH ? ORDER BY rank LIMIT ?",
            (fts_expr, limit),
        ).fetchall()
        return [{"path": r[0], "title": r[1], "source_path": r[2],
                 "date": r[3], "text": r[4], "rank": round(r[5], 4)} for r in rows]
    rows = conn.execute(
        "SELECT path, title, source_path, date, "
        "snippet(sources, 2, '>>>', '<<<', '...', 40) as snippet, "
        "bm25(sources) as rank "
        "FROM sources WHERE sources MATCH ? ORDER BY rank LIMIT ?",
        (fts_expr, limit),
    ).fetchall()
    return [{"path": r[0], "title": r[1], "source_path": r[2],
             "date": r[3], "snippet": r[4], "rank": round(r[5], 4)} for r in rows]


def _tokenize(raw_query: str) -> list:
    """Extract quoted phrases and bare words from a query."""
    return re.findall(r'"[^"]*"|\S+', raw_query)


def _sanitize_token(tok: str) -> str:
    """Per-token version of _sanitize_fts so we can compose OR/AND ourselves."""
    if tok.startswith('"'):
        return tok
    if "-" in tok or tok.upper() in _FTS5_RESERVED or re.fullmatch(r"\w+:", tok):
        return '"' + tok.replace('"', "") + '"'
    return tok


def _fts5_search(conn, query: str, limit: int, include_text: bool) -> list:
    """FTS5 search with automatic OR fallback on zero-hit AND queries.

    FTS5's implicit AND on space-separated tokens is strict — a query like
    'investment committee riftlabs due diligence memo' returns zero hits
    when no single document contains every token, even if every token is
    present in the corpus. Users read that as "nothing found" when the
    right answer is "close match, loosen your query".

    Strategy: run the AND query first. If it returns rows, done. If empty
    AND the query has multiple tokens, retry with OR between them and
    mark every result with `downgraded: true` so the caller knows the
    relaxation happened.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []
    sanitized = [_sanitize_token(t) for t in tokens]
    and_expr = " ".join(sanitized)
    results = _fts5_query(conn, and_expr, limit, include_text)
    if results or len(sanitized) < 2:
        return results
    or_expr = " OR ".join(sanitized)
    results = _fts5_query(conn, or_expr, limit, include_text)
    for r in results:
        r["downgraded"] = True
    return results


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


def _rrf_merge(*streams, k: int = 60, limit: int = 10) -> list:
    """Reciprocal Rank Fusion across N ranked lists, all keyed by `path`.

    First-stream entry wins on path collisions, so callers should pass
    streams in order of richest fields first (typically fts → sem →
    graph) — the FTS5 entry has the actual matched-snippet, the semantic
    entry has a generic first-line, and the graph entry has only a
    similarity-by-context signal. Keeping the FTS entry when a path
    appears in multiple streams gives the user the most informative row.
    """
    scores = {}
    for stream in streams:
        for i, r in enumerate(stream):
            s = scores.setdefault(r["path"], {"score": 0.0, "entry": r})
            s["score"] += 1.0 / (k + i + 1)
    merged = sorted(scores.values(), key=lambda x: -x["score"])
    return [m["entry"] for m in merged[:limit]]


def _graph_search(seed_paths: list, limit: int, sqlite_conn) -> list:
    """Expand a set of vault-source seeds by 1 hop through the kuzu wiki
    layer. Returns OTHER vault sources cited by wiki pages that also cite
    any seed, ranked by number of shared citing pages.

    Soft-fails (returns []) if kuzu isn't installed or the graph DB
    hasn't been built — the calling mode degrades to non-expanded.
    """
    if not seed_paths or not GRAPH_DB.exists():
        return []
    try:
        import kuzu
    except ImportError:
        sys.stderr.write(
            "vault_search: --graph-expand requested but kuzu not installed; "
            "skipping graph stream.\n"
        )
        return []
    db = kuzu.Database(str(GRAPH_DB))
    conn = kuzu.Connection(db)
    cypher = (
        "MATCH (s:VaultSource)<-[:Cites]-(p:WikiPage)-[:Cites]->(other:VaultSource) "
        "WHERE s.path IN $seeds AND NOT other.path IN $seeds "
        "WITH other.path AS path, count(DISTINCT p) AS shared "
        "ORDER BY shared DESC "
        f"LIMIT {limit} "
        "RETURN path, shared"
    )
    try:
        result = conn.execute(cypher, {"seeds": list(seed_paths)})
    except Exception as e:
        sys.stderr.write(f"vault_search: graph query failed ({e}); skipping.\n")
        return []
    expanded = []
    while result.has_next():
        row = result.get_next()
        expanded.append({"path": row[0], "shared_pages": int(row[1])})
    if not expanded:
        return []

    # Hydrate from the FTS5 sources table for title / snippet. Sources
    # found via graph have no inherent match-snippet; use the first
    # substantive line of the body, mirroring _semantic_search's choice.
    placeholders = ",".join("?" * len(expanded))
    paths_only = [e["path"] for e in expanded]
    rows = sqlite_conn.execute(
        f"SELECT path, title, source_path, date, body "
        f"FROM sources WHERE path IN ({placeholders})",
        paths_only,
    ).fetchall()
    by_path = {r[0]: r for r in rows}
    out = []
    # Preserve the order produced by kuzu so RRF ranks remain meaningful.
    for e in expanded:
        r = by_path.get(e["path"])
        if not r:
            continue
        body = r[4] or ""
        first = next(
            (ln for ln in body.split("\n")
             if ln.strip() and not ln.startswith("#")
             and not ln.startswith("<!--") and not ln.startswith("---")),
            "",
        )
        out.append({
            "path": r[0],
            "title": r[1],
            "source_path": r[2],
            "date": r[3],
            "snippet": first[:400],
            "shared_pages": e["shared_pages"],
            "via_graph": True,
        })
    return out


def search(query: str, limit: int, include_text: bool, mode: str = "fts5",
           graph_expand: bool = False):
    if not DB.exists():
        print("[]")
        return
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA journal_mode=WAL")

    if mode in ("semantic", "hybrid"):
        model, vec_mod = _load_embedder_for_search()
        if model is None:
            mode = "fts5"  # soft fallback

    # Compute primary stream(s).
    if mode == "fts5":
        fts = _fts5_search(conn, query, limit * 2, include_text)
        sem = []
    elif mode == "semantic":
        fts = []
        sem = _semantic_search(conn, model, vec_mod, query, limit * 2, include_text)
    else:  # hybrid
        fts = _fts5_search(conn, query, limit * 2, include_text)
        sem = _semantic_search(conn, model, vec_mod, query, limit * 2, include_text)

    if graph_expand:
        # Seed the graph stream with the strongest hits from primary
        # streams. Use top-K (= limit) so we expand around clear matches
        # rather than long-tail noise.
        primary = _rrf_merge(fts, sem, limit=limit) if (fts and sem) \
                  else (fts or sem)[:limit]
        seeds = [r["path"] for r in primary]
        graph = _graph_search(seeds, limit * 2, conn)
        results = _rrf_merge(fts, sem, graph, limit=limit)
    elif fts and sem:
        results = _rrf_merge(fts, sem, limit=limit)
    else:
        results = (fts or sem)[:limit]

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
    ap.add_argument("--graph-expand", action="store_true",
                    help="augment the primary stream(s) with a 1-hop kuzu "
                         "expansion: sources cited by wiki pages that also "
                         "cite the strongest matches. Useful for synthesis "
                         "queries where the right source is one wikilink "
                         "away from the keyword/semantic match. Soft-fails "
                         "to no expansion if kuzu / .curator/graph.kuzu "
                         "is unavailable.")
    args = ap.parse_args()

    if args.count:
        count()
        return

    if not args.query:
        ap.print_usage()
        sys.exit(1)

    search(args.query, args.limit, args.text, args.mode, args.graph_expand)


if __name__ == "__main__":
    main()
