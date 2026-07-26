#!/usr/bin/env python3
"""graph.py — kuzu-backed knowledge graph for the curiosity engine.

Maintains a property graph alongside the FTS5 index. Nodes are wiki pages
and vault sources; edges are wikilinks and vault citations.

Subcommands
-----------
    graph.py rebuild <wiki_dir>
        Drop and rebuild the entire graph from wiki pages on disk.
        Writes to .curator/graph.kuzu (single file, not git-tracked).
        Also refreshes the wiki-page embedding index (when
        embedding_enabled) and builds the provisional edge tier
        (co-citation + embedding-neighbor, no LLM).

    graph.py retrieve <wiki_dir> "<query>" [--seeds N] [--limit K]
                      [--hops H] [--route auto|graph|blend]
                      [--vault-k N] [--no-provisional]
        First-class graph retrieval: semantic (or lexical-fallback) seed
        -> multi-hop BFS over the graph -> pages ranked by (distance asc,
        query-term overlap desc), with provenance. `--route auto`
        (default) sends global/sensemaking queries graph-only and blends
        vault-vector recall into everything else — the routing policy the
        CE-vs-RAG benchmark showed dominates fixed strategies.

    graph.py embed <wiki_dir> [--force]
        Build/refresh the wiki-page embedding index at .curator/wiki.db
        (chunked, content-hash incremental). Opt-in via
        embedding_enabled in .curator/config.json.

    graph.py link-candidates <wiki_dir> [--limit N]
        Ranked unlinked page pairs from the provisional tier — the
        candidate queue for the LINK proposer. Pairs rejected by a LINK
        classifier pass (.curator/link-rejects.json) are excluded.

    graph.py shared-sources <wiki_dir> <page_a> <page_b>
        Vault sources cited by both pages.

    graph.py path <wiki_dir> <page_a> <page_b> [--max-hops N]
        Shortest wikilink path between two pages.

    graph.py neighbors <wiki_dir> <page> [--hops N] [--direction out|in|both]
        All pages within N wikilink hops (default 2, outbound), each
        with distance/title/type. --direction both gives the undirected
        neighbourhood retrieval traverses.

    graph.py bridge-candidates <wiki_dir> [--limit N]
        Page pairs sharing vault sources but not linked. Replaces the
        O(n^2) connection_candidates in epoch_summary.py.

Two-tier graph: curated edges (WikiLink/Cites/Depicts/...) come from the
wiki markdown and are the ground truth. ProvisionalLink edges are cheap,
mechanically derived hints (co-citation, embedding-neighbor) that exist
only in kuzu — they are NEVER written into wiki markdown. Retrieval
traverses both tiers (typed edges cost 1 hop, provisional cost 2);
curation promotes a provisional edge by applying a real [[wikilink]]
(LINK pass), after which the next rebuild retires the provisional edge,
or prunes it via .curator/link-rejects.json.

Requires: pip install kuzu
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import SKIP_FILES, WIKILINK_RE, CITATION_RE, read_frontmatter  # noqa: E402
from embedder import load_embedder, predict_model_id  # noqa: E402

try:
    import kuzu
except ImportError:
    kuzu = None


def _graph_path(wiki_dir: Path) -> str:
    return str(wiki_dir.parent / ".curator" / "graph.kuzu")


def _wiki_newer_than_graph(wiki_dir: Path) -> bool:
    kuzu_path = Path(_graph_path(wiki_dir))
    if not kuzu_path.exists():
        return False
    kuzu_mtime = kuzu_path.stat().st_mtime
    wiki_mtime = max((f.stat().st_mtime for f in wiki_dir.rglob("*.md")), default=0)
    return wiki_mtime > kuzu_mtime


def _check_stale(wiki_dir: Path) -> bool:
    """Warn on stderr + print empty JSON if wiki is newer than the kuzu db.

    Gates the fixed list-shaped query verbs (neighbors/path/...). retrieve
    and link-candidates are NOT gated on this — they return an object, work
    degraded without a current graph, and flag `graph_stale` instead.
    """
    if _wiki_newer_than_graph(wiki_dir):
        print(f"graph stale (wiki newer than kuzu) — run: uv run python3 scripts/graph.py rebuild {wiki_dir.name}",
              file=sys.stderr)
        print("[]")
        return True
    return False


def _connect(wiki_dir: Path, read_only: bool = False):
    """read_only matters beyond safety: a read-write kuzu open bumps the
    DB file's mtime, which would mask wiki-newer-than-graph staleness for
    every subsequent command. Query verbs must pass read_only=True."""
    if kuzu is None:
        print(json.dumps({"error": "kuzu not installed (uv pip install kuzu, or rerun setup.sh)"}))
        sys.exit(1)
    path = _graph_path(wiki_dir)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        try:
            db = kuzu.Database(path, read_only=True)
        except TypeError:  # older kuzu without the kwarg
            db = kuzu.Database(path)
    else:
        db = kuzu.Database(path)
    return kuzu.Connection(db)


def _init_schema(conn):
    for stmt in [
        "CREATE NODE TABLE IF NOT EXISTS WikiPage(path STRING, type STRING, title STRING, PRIMARY KEY (path))",
        "CREATE NODE TABLE IF NOT EXISTS VaultSource(path STRING, title STRING, PRIMARY KEY (path))",
        # DataRow = one row in the class-tables layer. kuzu's node PK
        # syntax takes a single column; we synthesise a compound key
        # `key = table_name + ":" + row_id` to uniquely identify a row
        # while keeping table_name / row_id as queryable properties.
        "CREATE NODE TABLE IF NOT EXISTS DataRow(key STRING, table_name STRING, row_id STRING, PRIMARY KEY (key))",
        "CREATE REL TABLE IF NOT EXISTS WikiLink(FROM WikiPage TO WikiPage)",
        "CREATE REL TABLE IF NOT EXISTS Cites(FROM WikiPage TO VaultSource)",
        # DataRef = typed-data reference from a row to a wiki page. The
        # `column` property records which wikilink-typed column produced
        # the edge (customer_ref, owner, etc.), so cypher queries can
        # filter by relationship kind.
        "CREATE REL TABLE IF NOT EXISTS DataRef(FROM DataRow TO WikiPage, col_name STRING)",
        # Depicts = a figure page depicts / illustrates a subject page.
        # Populated from the figure's `relates_to:` frontmatter list.
        # Distinct from WikiLink because the relationship direction is
        # semantic (fig depicts subject) and doesn't depend on body
        # prose containing a [[wikilink]]. Queries like "what figures
        # illustrate this concept?" traverse Depicts in reverse.
        "CREATE REL TABLE IF NOT EXISTS Depicts(FROM WikiPage TO WikiPage)",
        # Note = an atomic user-authored note fragment. Rows carry an
        # id minted by sweep.py sync-notes and a content_hash for dedup.
        # Notes can appear on multiple wiki pages simultaneously (e.g.
        # once under notes/acme.md, once under notes/steerco-project-x.md)
        # so the Note → WikiPage relationship is many-to-many via
        # AppearsIn. The earliest-timestamped AppearsIn is the note's
        # origin; no separate origin edge needed.
        "CREATE NODE TABLE IF NOT EXISTS Note(id STRING, content_hash STRING, created STRING, PRIMARY KEY (id))",
        "CREATE REL TABLE IF NOT EXISTS AppearsIn(FROM Note TO WikiPage)",
        # ProvisionalLink = the second tier. Mechanically derived page-page
        # hints (origin: 'co-citation' | 'embedding'), kuzu-only — never
        # serialised back into wiki markdown. Kept as a SEPARATE rel table
        # (not a provisional flag on WikiLink) so every existing WikiLink
        # consumer — query_router cypher, viewer, path/neighbors,
        # vault_search --graph-expand — keeps curated-only semantics
        # without filtering. score: shared-source count for co-citation,
        # cosine similarity for embedding.
        "CREATE REL TABLE IF NOT EXISTS ProvisionalLink(FROM WikiPage TO WikiPage, origin STRING, score DOUBLE)",
    ]:
        conn.execute(stmt)


def _graph_meta_path(wiki_dir: Path) -> Path:
    return wiki_dir.parent / ".curator" / ".graph-meta.json"


def _graph_is_current(wiki_dir: Path) -> bool:
    """True iff the kuzu graph is at least as new as the latest wiki page.

    Short-circuit for `rebuild` calls from parallel CURATE sessions — at
    10 concurrent sessions each rebuilding at epoch end, most rebuilds
    are redundant and cost 2-10s each. Checking mtime first collapses
    them to <50ms.

    Beyond mtimes, the graph is NOT current when its schema version
    predates this script (upgraded skill: the ProvisionalLink tier and
    wiki embeddings don't exist yet), when embeddings are enabled but
    wiki.db is missing, or when link-rejects.json changed after the last
    rebuild (prunes must apply).
    """
    kuzu_path = Path(_graph_path(wiki_dir))
    if not kuzu_path.exists():
        return False
    meta_path = _graph_meta_path(wiki_dir)
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        meta = {}
    if meta.get("schema_version") != GRAPH_SCHEMA_VERSION:
        return False
    if _load_config(wiki_dir).get("embedding_enabled") \
            and not _wiki_db_path(wiki_dir).exists():
        return False
    kuzu_mtime = kuzu_path.stat().st_mtime
    rejects = _workspace(wiki_dir) / ".curator" / "link-rejects.json"
    if rejects.exists() and rejects.stat().st_mtime > kuzu_mtime:
        return False
    # A re-embed (model switch, explicit `embed`) newer than the graph
    # means the provisional embedding-neighbor edges derive from stale
    # vectors — rebuild them.
    wiki_db = _wiki_db_path(wiki_dir)
    if wiki_db.exists() and wiki_db.stat().st_mtime > kuzu_mtime:
        return False
    wiki_mtime = max(
        (f.stat().st_mtime for f in wiki_dir.rglob("*.md")
         if f.name not in SKIP_FILES and "_suspect" not in f.parts),
        default=0,
    )
    return wiki_mtime <= kuzu_mtime


# ── workspace config / shared helpers ─────────────────────────────────

# Bumped when rebuild's output surface changes (new tables/tiers); a
# mismatch defeats the mtime short-circuit so upgraded workspaces get
# the new surface on their next plain `rebuild` — no --force needed.
GRAPH_SCHEMA_VERSION = 2

# Tunable via a "provisional" object in .curator/config.json; these
# defaults match the corpus scale the CE-vs-RAG benchmark validated.
_PROV_DEFAULTS = {
    "enabled": True,
    "min_shared_sources": 2,     # co-citation: >= N shared vault sources
    "embedding_min_cosine": 0.60,  # embedding-neighbor floor
    "embedding_top_m": 5,        # max embedding neighbours per page
    "max_edges": 2000,           # hard cap per origin per rebuild
}


def _workspace(wiki_dir: Path) -> Path:
    return wiki_dir.parent


def _load_config(wiki_dir: Path) -> dict:
    cfg_path = _workspace(wiki_dir) / ".curator" / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _prov_config(wiki_dir: Path) -> dict:
    cfg = _load_config(wiki_dir).get("provisional", {})
    out = dict(_PROV_DEFAULTS)
    if isinstance(cfg, dict):
        out.update({k: cfg[k] for k in _PROV_DEFAULTS if k in cfg})
    return out


def _iter_wiki_pages(wiki_dir: Path) -> list:
    return [f for f in sorted(wiki_dir.rglob("*.md"))
            if f.name not in SKIP_FILES and "_suspect" not in f.parts]


def _load_link_rejects(wiki_dir: Path) -> set:
    """Pairs a LINK classifier voted invalid — never re-propose, never
    re-materialise as provisional edges. Canonical (min, max) tuples.
    File format: JSON array of {"a": <path>, "b": <path>, ...} objects
    (extra keys like reason/ts are ignored)."""
    path = _workspace(wiki_dir) / ".curator" / "link-rejects.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    pairs = set()
    for entry in data if isinstance(data, list) else []:
        if isinstance(entry, dict) and entry.get("a") and entry.get("b"):
            a, b = str(entry["a"]), str(entry["b"])
            pairs.add((min(a, b), max(a, b)))
    return pairs


# ── wiki-page embedding index (.curator/wiki.db) ──────────────────────

def _wiki_db_path(wiki_dir: Path) -> Path:
    return _workspace(wiki_dir) / ".curator" / "wiki.db"


def _sqlite3():
    # Mirror vault_index/vault_search: macOS stdlib sqlite3 often lacks
    # loadable-extension support, which sqlite-vec needs.
    try:
        import pysqlite3 as mod
    except ImportError:
        import sqlite3 as mod
    return mod


def _embedding_prereqs(wiki_dir: Path):
    """Cheap gate: (sqlite_vec module, config) or (None, reason). Does NOT
    load the embedding model — callers defer that until vectors are
    actually needed (rebuild runs hot; a no-op refresh must stay cheap)."""
    cfg = _load_config(wiki_dir)
    if not cfg.get("embedding_enabled"):
        return None, "embedding_enabled=false in .curator/config.json"
    try:
        import sqlite_vec
    except ImportError as e:
        return None, (f"sqlite-vec not installed ({e}) — "
                      "uv pip install fastembed sqlite-vec")
    return (sqlite_vec, cfg), ""


def _chunk_text(text: str, size: int = 900, overlap: int = 150) -> list:
    text = text[:12000]
    if len(text) <= size:
        return [text] if text.strip() else []
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + size])
        start += size - overlap
    return out


def _open_wiki_db(wiki_dir: Path, sqlite_vec_mod):
    sqlite3 = _sqlite3()
    path = _wiki_db_path(wiki_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.enable_load_extension(True)
    try:
        sqlite_vec_mod.load(conn)
    finally:
        conn.enable_load_extension(False)
    return conn


def _init_wiki_db(conn, dim: int):
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS wiki_chunks "
                 f"USING vec0(embedding float[{dim}])")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wiki_chunk_meta (
            vec_id INTEGER UNIQUE,
            path TEXT,
            chunk_idx INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wiki_page_meta (
            path TEXT PRIMARY KEY,
            sha256 TEXT,
            model TEXT,
            indexed_at TEXT
        )
    """)


def embed_wiki(wiki_dir: Path, force: bool = False) -> dict:
    """Build/refresh the chunked wiki-page embedding index. Content-hash
    incremental: unchanged pages are skipped; removed pages are purged;
    a backend/model change (wiki_page_meta.model vs the active embedder's
    model_id) wipes and rebuilds. The embedding model is only loaded when
    there is actually something to (re-)embed, so a no-op refresh inside
    a hot rebuild stays cheap. Never raises — returns a status dict."""
    prereq, reason = _embedding_prereqs(wiki_dir)
    if prereq is None:
        return {"status": "skipped", "reason": reason}
    vec_mod, cfg = prereq
    try:
        conn = _open_wiki_db(wiki_dir, vec_mod)
    except Exception as e:
        return {"status": "skipped", "reason": f"cannot open wiki.db ({e})"}

    have_tables = bool(conn.execute(
        "SELECT name FROM sqlite_master WHERE name='wiki_page_meta'"
    ).fetchone())
    known = {}
    if have_tables and not force:
        known = dict(conn.execute("SELECT path, sha256 FROM wiki_page_meta"))

    seen, pending = set(), []   # pending: (rel, sha, [chunks])
    for page in _iter_wiki_pages(wiki_dir):
        text = page.read_text(encoding="utf-8", errors="replace")
        rel = str(page.relative_to(wiki_dir))
        seen.add(rel)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if known.get(rel) == sha:
            continue
        fm, _body = read_frontmatter(text)
        title = fm.get("title", page.stem.replace("-", " ").title())
        chunks = _chunk_text(f"{title}\n{text}")
        pending.append((rel, sha, chunks))
    removed = [p for p in known if p not in seen]

    stored_model = None
    if have_tables:
        row = conn.execute("SELECT model FROM wiki_page_meta LIMIT 1").fetchone()
        stored_model = row[0] if row else None

    predicted = predict_model_id(cfg)
    if have_tables and not force and not pending and not removed \
            and (stored_model is None or predicted is None
                 or stored_model == predicted):
        conn.close()
        return {"status": "embedded", "pages_indexed": len(known),
                "pages_refreshed": 0, "model": stored_model}

    emb, err = load_embedder(cfg)
    if emb is None:
        conn.close()
        return {"status": "skipped", "reason": err}

    if force or (stored_model and stored_model != emb.model_id):
        conn.execute("DROP TABLE IF EXISTS wiki_chunks")
        conn.execute("DROP TABLE IF EXISTS wiki_chunk_meta")
        conn.execute("DROP TABLE IF EXISTS wiki_page_meta")
        have_tables, known, removed = False, {}, []
        # Everything re-embeds under the new vector space.
        pending = []
        for page in _iter_wiki_pages(wiki_dir):
            text = page.read_text(encoding="utf-8", errors="replace")
            rel = str(page.relative_to(wiki_dir))
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            fm, _body = read_frontmatter(text)
            title = fm.get("title", page.stem.replace("-", " ").title())
            pending.append((rel, sha, _chunk_text(f"{title}\n{text}")))
    try:
        _init_wiki_db(conn, emb.dim)
    except Exception as e:
        conn.close()
        return {"status": "skipped", "reason": f"cannot init wiki.db ({e})"}

    # Purge removed/changed pages' rows before re-inserting.
    for rel in removed + [p[0] for p in pending]:
        for (vec_id,) in conn.execute(
                "SELECT vec_id FROM wiki_chunk_meta WHERE path=?", (rel,)):
            conn.execute("DELETE FROM wiki_chunks WHERE rowid=?", (vec_id,))
        conn.execute("DELETE FROM wiki_chunk_meta WHERE path=?", (rel,))
        conn.execute("DELETE FROM wiki_page_meta WHERE path=?", (rel,))

    texts = [c for _, _, chunks in pending for c in chunks]
    vecs = emb.embed_passages(texts) if texts else []
    now = datetime.now().isoformat()
    i = 0
    for rel, sha, chunks in pending:
        for idx in range(len(chunks)):
            cur = conn.execute(
                "INSERT INTO wiki_chunks(embedding) VALUES(?)",
                (vec_mod.serialize_float32(vecs[i]),))
            conn.execute(
                "INSERT INTO wiki_chunk_meta(vec_id, path, chunk_idx) "
                "VALUES(?,?,?)", (cur.lastrowid, rel, idx))
            i += 1
        # Meta row lands even for chunkless (empty) pages so they aren't
        # re-scanned every run.
        conn.execute(
            "INSERT OR REPLACE INTO wiki_page_meta"
            "(path, sha256, model, indexed_at) VALUES(?,?,?,?)",
            (rel, sha, emb.model_id, now))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM wiki_page_meta").fetchone()[0]
    conn.close()
    return {"status": "embedded", "pages_indexed": total,
            "pages_refreshed": len(pending), "model": emb.model_id}


def _page_vectors(wiki_dir: Path):
    """path -> unit-norm mean chunk vector, or None if the index is
    unavailable. Reads stored blobs only — no embedding model load.
    Uses numpy (a dep of both embedding backends, and this only runs on
    embedding-enabled workspaces)."""
    prereq, _reason = _embedding_prereqs(wiki_dir)
    if prereq is None or not _wiki_db_path(wiki_dir).exists():
        return None
    vec_mod, _cfg = prereq
    import numpy as np
    try:
        conn = _open_wiki_db(wiki_dir, vec_mod)
        rows = conn.execute(
            "SELECT m.path, c.embedding FROM wiki_chunk_meta m "
            "JOIN wiki_chunks c ON c.rowid = m.vec_id").fetchall()
        conn.close()
    except Exception:
        return None
    by_page = {}
    for rel, blob in rows:
        by_page.setdefault(rel, []).append(np.frombuffer(blob, dtype=np.float32))
    out = {}
    # errstate: a stored blob holding inf/huge components (corrupt or
    # mis-dtype bytes) overflows inside np.mean / np.linalg.norm's
    # sum-of-squares before any guard below can see it. The RuntimeWarnings
    # a full rebuild used to print originate here, not at the cosine matmul.
    with np.errstate(over="ignore", invalid="ignore"):
        for rel, vecs in by_page.items():
            v = np.mean(vecs, axis=0)
            n = float(np.linalg.norm(v))
            # isfinite AND an epsilon floor, not `> 0`:
            #  - an inf norm passes `> 0`, and inf/inf is NaN, so the page
            #    entered the cosine matrix as a NaN row and silently
            #    poisoned every similarity computed against it;
            #  - a NaN norm fails every comparison, so it was already
            #    dropped — isfinite just makes that explicit;
            #  - a near-zero norm (chunk vectors that nearly cancel: a
            #    semantically mixed page, or a stub) normalises to a
            #    finite unit vector, but its direction is amplified noise,
            #    which injects junk into the cosine ranking and can form
            #    spurious embedding edges.
            if np.isfinite(n) and n > 1e-6:
                out[rel] = v / n
    return out or None


# ── provisional edge tier (built during rebuild, no LLM) ──────────────

def _build_provisional(conn, wiki_dir: Path, linked_pairs: set,
                       cites_map: dict, types: dict) -> dict:
    """Materialise ProvisionalLink edges from two mechanical signals:

    - co-citation: unlinked page pairs sharing >= min_shared_sources vault
      sources (the bridge-candidates signal, persisted as edges);
    - embedding-neighbor: unlinked pairs whose page vectors (mean of wiki.db
      chunk vectors) have cosine >= embedding_min_cosine, top_m per page.

    Pages of type 'source' are excluded from both signals (stubs are wired
    via the orphan-sources path, and near-duplicate papers would otherwise
    form noisy hub clusters). Pairs in link-rejects.json are pruned — this
    is how a LINK classifier's 'invalid' verdict permanently retires a
    provisional edge.
    """
    cfg = _prov_config(wiki_dir)
    empty = {"provisional_cocitation": 0, "provisional_embedding": 0}
    if not cfg["enabled"]:
        return {**empty, "provisional": "disabled"}
    rejects = _load_link_rejects(wiki_dir)
    max_edges = int(cfg["max_edges"])
    edges = {}   # canonical pair -> (origin, score)

    # co-citation
    by_source = {}
    for rel, cits in cites_map.items():
        if types.get(rel) == "source":
            continue
        for vp in cits:
            by_source.setdefault(vp, []).append(rel)
    pair_counts = {}
    for vp, rels in by_source.items():
        rels = sorted(set(rels))
        if len(rels) > 50:   # hub source: pairs are uninformative
            continue
        for i in range(len(rels)):
            for j in range(i + 1, len(rels)):
                pair = (rels[i], rels[j])
                pair_counts[pair] = pair_counts.get(pair, 0) + 1
    cocite = sorted(
        ((p, c) for p, c in pair_counts.items()
         if c >= int(cfg["min_shared_sources"])
         and p not in linked_pairs and p not in rejects),
        key=lambda x: (-x[1], x[0]))[:max_edges]
    for pair, c in cocite:
        edges[pair] = ("co-citation", float(c))

    # embedding-neighbor
    pv = _page_vectors(wiki_dir)
    if pv:
        import numpy as np
        paths = sorted(p for p in pv if types.get(p) != "source")
        if len(paths) >= 2:
            # _page_vectors guarantees finite unit rows, so this cannot
            # overflow — deliberately left unguarded so that if it ever
            # warns again, the warning is real signal.
            m = np.stack([pv[p] for p in paths])
            sims = m @ m.T
            top_m = int(cfg["embedding_top_m"])
            floor = float(cfg["embedding_min_cosine"])
            emb_pairs = {}
            for i, p in enumerate(paths):
                for j in np.argsort(-sims[i])[:top_m + 1]:
                    if j == i:
                        continue
                    s = float(sims[i][j])
                    if s < floor:
                        break
                    q = paths[j]
                    pair = (min(p, q), max(p, q))
                    if pair in linked_pairs or pair in rejects or pair in edges:
                        continue
                    emb_pairs[pair] = max(emb_pairs.get(pair, 0.0), s)
            for pair, s in sorted(emb_pairs.items(),
                                  key=lambda kv: -kv[1])[:max_edges]:
                edges[pair] = ("embedding", round(s, 4))

    for (a, b), (origin, score) in edges.items():
        conn.execute(
            "MATCH (x:WikiPage), (y:WikiPage) "
            "WHERE x.path = $a AND y.path = $b "
            "CREATE (x)-[:ProvisionalLink {origin: $o, score: $s}]->(y)",
            {"a": a, "b": b, "o": origin, "s": float(score)})
    return {
        "provisional_cocitation": sum(1 for o, _ in edges.values() if o == "co-citation"),
        "provisional_embedding": sum(1 for o, _ in edges.values() if o == "embedding"),
    }


def rebuild(wiki_dir: Path, force: bool = False):
    if not force and _graph_is_current(wiki_dir):
        print(json.dumps({"status": "up-to-date",
                          "note": "kuzu graph newer than all wiki pages; skipped rebuild"}))
        return
    path = _graph_path(wiki_dir)
    p = Path(path)
    if p.exists():
        p.unlink()

    conn = _connect(wiki_dir)
    _init_schema(conn)

    pages = _iter_wiki_pages(wiki_dir)

    vault_sources = set()
    page_data = []
    note_occurrences = {}   # note_id -> (content_hash, created, {page_rels})

    import hashlib as _hashlib
    _NOTE_MARKER_RE = re.compile(r"\(note:N(\d+)\)")
    _CREATED_TAG_RE = re.compile(r"created:\s*(\d{4}-\d{2}-\d{2})")

    for page in pages:
        text = page.read_text()
        fm, _ = read_frontmatter(text)
        rel = str(page.relative_to(wiki_dir))
        page_type = fm.get("type", "")
        title = fm.get("title", page.stem.replace("-", " ").title())

        # Note occurrences — each `(note:N<id>)` marker contributes an
        # AppearsIn edge from the Note node to this page. Content hash
        # is computed from the first line/block containing the marker
        # so two verbatim appearances of the same note resolve to the
        # same hash for dedup.
        for line in text.split("\n"):
            for m in _NOTE_MARKER_RE.finditer(line):
                nid = f"N{m.group(1)}"
                entry = note_occurrences.setdefault(nid, (None, None, set()))
                ch, created, pages_set = entry
                pages_set.add(rel)
                if ch is None:
                    # First time seeing this note — compute its hash and
                    # pull the created-date if present on the same line.
                    normalised = line.strip().lower()
                    normalised = _NOTE_MARKER_RE.sub("", normalised).strip()
                    ch = _hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]
                    cm = _CREATED_TAG_RE.search(line)
                    created = cm.group(1) if cm else ""
                note_occurrences[nid] = (ch, created, pages_set)

        links = set()
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip().lower().replace(" ", "-")
            links.add(target)

        citations = set()
        for m in CITATION_RE.finditer(text):
            vp = m.group(1).strip()
            citations.add(vp)
            vault_sources.add(vp)

        # relates_to on figure pages becomes Depicts edges. Entries may
        # be full wiki-relative paths (concepts/foo.md) or bare stems
        # (foo) — resolve both at rebuild time against page_stems.
        depicts = set()
        if page_type == "figure":
            rel_to = fm.get("relates_to", [])
            if isinstance(rel_to, str):
                rel_to = [rel_to]
            for target in rel_to:
                t = str(target).strip()
                if not t:
                    continue
                depicts.add(t)

        page_data.append((rel, page_type, title, links, citations, depicts))

    page_paths = {d[0] for d in page_data}
    page_stems = {}
    for d in page_data:
        stem = Path(d[0]).stem.lower()
        page_stems[stem] = d[0]

    for rel, page_type, title, _, _, _ in page_data:
        conn.execute(
            "CREATE (:WikiPage {path: $p, type: $t, title: $ti})",
            {"p": rel, "t": page_type, "ti": title}
        )

    for vp in vault_sources:
        conn.execute(
            "CREATE (:VaultSource {path: $p, title: $t})",
            {"p": vp, "t": vp}
        )

    depicts_edges = 0
    linked_pairs = set()   # canonical (min, max) pairs with any curated edge
    for rel, _, _, links, citations, depicts in page_data:
        for target in links:
            target_path = page_stems.get(target)
            if target_path and target_path != rel:
                conn.execute(
                    "MATCH (a:WikiPage), (b:WikiPage) "
                    "WHERE a.path = $from AND b.path = $to "
                    "CREATE (a)-[:WikiLink]->(b)",
                    {"from": rel, "to": target_path}
                )
                linked_pairs.add((min(rel, target_path), max(rel, target_path)))
        for vp in citations:
            conn.execute(
                "MATCH (a:WikiPage), (b:VaultSource) "
                "WHERE a.path = $from AND b.path = $to "
                "CREATE (a)-[:Cites]->(b)",
                {"from": rel, "to": vp}
            )
        for target in depicts:
            target_path = None
            if target in page_paths:
                target_path = target
            else:
                # Try stem match (case-insensitive, strip directory).
                stem = Path(target).stem.lower()
                target_path = page_stems.get(stem)
            if target_path and target_path != rel:
                conn.execute(
                    "MATCH (a:WikiPage), (b:WikiPage) "
                    "WHERE a.path = $from AND b.path = $to "
                    "CREATE (a)-[:Depicts]->(b)",
                    {"from": rel, "to": target_path}
                )
                depicts_edges += 1
                linked_pairs.add((min(rel, target_path), max(rel, target_path)))

    # Populate DataRow nodes + DataRef edges from tables.db if present.
    # Wikilink-typed columns become typed edges from the row to the
    # wiki page it references. Empty/unresolvable refs are skipped.
    tables_db = wiki_dir.parent / ".curator" / "tables.db"
    data_rows = 0
    data_refs = 0
    if tables_db.exists():
        try:
            import sqlite3 as _sqlite3
        except ImportError:
            _sqlite3 = None
        if _sqlite3 is not None:
            try:
                tconn = _sqlite3.connect(str(tables_db))
                tconn.execute("PRAGMA journal_mode=WAL")
                meta = tconn.execute(
                    "SELECT table_name, schema_json FROM _schema_meta"
                ).fetchall()
                for table_name, schema_json in meta:
                    try:
                        schema = json.loads(schema_json)
                    except json.JSONDecodeError:
                        continue
                    cols = schema.get("columns", [])
                    pk = next((c["name"] for c in cols
                                if isinstance(c, dict) and c.get("pk")), None)
                    wikilink_cols = [c["name"] for c in cols
                                       if isinstance(c, dict)
                                       and c.get("type", "").lower() in ("wikilink", "ref")]
                    if not pk:
                        continue
                    select_cols = ", ".join(f'"{c}"' for c in [pk] + wikilink_cols)
                    try:
                        rows = tconn.execute(
                            f'SELECT {select_cols} FROM "{table_name}"'
                        ).fetchall()
                    except _sqlite3.Error:
                        continue
                    for row in rows:
                        row_id = str(row[0])
                        key = f"{table_name}:{row_id}"
                        conn.execute(
                            "CREATE (:DataRow {key: $k, table_name: $t, row_id: $i})",
                            {"k": key, "t": table_name, "i": row_id}
                        )
                        data_rows += 1
                        for i, col_name in enumerate(wikilink_cols, start=1):
                            target_stem = row[i]
                            if not target_stem:
                                continue
                            target_path = page_stems.get(str(target_stem).lower())
                            if not target_path:
                                continue
                            conn.execute(
                                "MATCH (a:DataRow), (b:WikiPage) "
                                "WHERE a.key = $k AND b.path = $p "
                                "CREATE (a)-[:DataRef {col_name: $c}]->(b)",
                                {"k": key, "p": target_path, "c": col_name}
                            )
                            data_refs += 1
                tconn.close()
            except _sqlite3.Error:
                pass

    # Note nodes + AppearsIn edges. Runs after WikiPage nodes are
    # created so AppearsIn can resolve its WikiPage endpoint.
    appears_in_edges = 0
    for nid, (content_hash, created, pages_set) in note_occurrences.items():
        conn.execute(
            "CREATE (:Note {id: $i, content_hash: $h, created: $c})",
            {"i": nid, "h": content_hash or "", "c": created or ""}
        )
        for target_rel in pages_set:
            if target_rel not in page_paths:
                continue
            conn.execute(
                "MATCH (a:Note), (b:WikiPage) "
                "WHERE a.id = $nid AND b.path = $p "
                "CREATE (a)-[:AppearsIn]->(b)",
                {"nid": nid, "p": target_rel}
            )
            appears_in_edges += 1

    # Second tier: refresh wiki embeddings (opt-in, soft-skip), then build
    # provisional edges. Runs inside rebuild so INGEST/SWEEP/LINK flows that
    # already call `graph.py rebuild` get the warm graph for free. Neither
    # step may take down the rebuild — the typed graph is already complete.
    emb_status = embed_wiki(wiki_dir)
    types = {d[0]: d[1] for d in page_data}
    cites_map = {d[0]: d[4] for d in page_data}
    try:
        prov_stats = _build_provisional(conn, wiki_dir, linked_pairs,
                                        cites_map, types)
    except Exception as e:
        prov_stats = {"provisional_cocitation": 0, "provisional_embedding": 0,
                      "provisional_error": str(e)}

    _graph_meta_path(wiki_dir).write_text(json.dumps({
        "schema_version": GRAPH_SCHEMA_VERSION,
        "rebuilt_at": datetime.now().isoformat(),
    }))

    stats = {
        "pages": len(page_data),
        "vault_sources": len(vault_sources),
        "wikilinks": sum(len(d[3]) for d in page_data),
        "citations": sum(len(d[4]) for d in page_data),
        "data_rows": data_rows,
        "data_refs": data_refs,
        "depicts_edges": depicts_edges,
        "notes": len(note_occurrences),
        "appears_in_edges": appears_in_edges,
        "wiki_embeddings": emb_status.get("status"),
        **prov_stats,
    }
    print(json.dumps({"status": "rebuilt", **stats}))


def _query_to_json(conn, cypher, params=None):
    result = conn.execute(cypher, params or {})
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def cmd_shared_sources(wiki_dir: Path, page_a: str, page_b: str):
    conn = _connect(wiki_dir, read_only=True)
    rows = _query_to_json(conn,
        "MATCH (a:WikiPage)-[:Cites]->(v:VaultSource)<-[:Cites]-(b:WikiPage) "
        "WHERE a.path = $a AND b.path = $b "
        "RETURN DISTINCT v.path",
        {"a": page_a, "b": page_b}
    )
    print(json.dumps([r[0] for r in rows], indent=2))


def cmd_path(wiki_dir: Path, page_a: str, page_b: str, max_hops: int):
    conn = _connect(wiki_dir, read_only=True)
    max_hops = max(1, min(int(max_hops), 20))
    rows = _query_to_json(conn,
        f"MATCH (a:WikiPage)-[e:WikiLink* SHORTEST 1..{max_hops}]->(b:WikiPage) "
        "WHERE a.path = $a AND b.path = $b "
        "RETURN a.path, b.path, length(e)",
        {"a": page_a, "b": page_b}
    )
    if rows:
        print(json.dumps({"from": rows[0][0], "to": rows[0][1],
                           "hops": rows[0][2]}))
    else:
        print(json.dumps({"result": "no path found", "max_hops": max_hops}))


def cmd_neighbors(wiki_dir: Path, page: str, hops: int, direction: str = "out"):
    """Pages within N wikilink hops, each with distance/title/type.

    --direction out (default, the pre-v0.8 semantics) follows outbound
    [[wikilinks]]; `in` follows backlinks; `both` is the undirected view
    retrieval uses. BFS in Python over the WikiLink edge list — kuzu's
    variable-length MATCH can't emit per-node distance or mix directions.
    """
    conn = _connect(wiki_dir, read_only=True)
    hops = max(1, min(int(hops), 10))
    meta = {r[0]: {"type": r[1], "title": r[2]} for r in _query_to_json(
        conn, "MATCH (p:WikiPage) RETURN p.path, p.type, p.title")}
    if page not in meta:
        print(f"graph neighbors: no page {page!r} in graph", file=sys.stderr)
        print("[]")
        return
    out_adj, in_adj = {}, {}
    for a, b in _query_to_json(
            conn, "MATCH (a:WikiPage)-[:WikiLink]->(b:WikiPage) "
                  "RETURN a.path, b.path"):
        out_adj.setdefault(a, set()).add(b)
        in_adj.setdefault(b, set()).add(a)
    from collections import deque
    dist = {page: 0}
    q = deque([page])
    while q:
        cur = q.popleft()
        d = dist[cur]
        if d >= hops:
            continue
        nbrs = set()
        if direction in ("out", "both"):
            nbrs |= out_adj.get(cur, set())
        if direction in ("in", "both"):
            nbrs |= in_adj.get(cur, set())
        for nb in nbrs:
            if nb not in dist:
                dist[nb] = d + 1
                q.append(nb)
    items = sorted(((p, d) for p, d in dist.items() if p != page),
                   key=lambda kv: (kv[1], kv[0]))
    print(json.dumps([
        {"path": p, "type": meta.get(p, {}).get("type", ""),
         "title": meta.get(p, {}).get("title", ""), "distance": d}
        for p, d in items
    ], indent=2))


def cmd_bridge_candidates(wiki_dir: Path, limit: int):
    conn = _connect(wiki_dir, read_only=True)
    limit = max(1, min(int(limit), 100))
    rows = _query_to_json(conn,
        "MATCH (a:WikiPage)-[:Cites]->(v:VaultSource)<-[:Cites]-(b:WikiPage) "
        "WHERE a.path < b.path "
        "AND NOT EXISTS { MATCH (a)-[:WikiLink]->(b) } "
        "AND NOT EXISTS { MATCH (b)-[:WikiLink]->(a) } "
        "AND a.type <> 'source' AND b.type <> 'source' "
        "WITH a.path AS page_a, b.path AS page_b, count(v) AS shared "
        "ORDER BY shared DESC "
        f"LIMIT {limit} "
        "RETURN page_a, page_b, shared"
    )
    print(json.dumps([{"page_a": r[0], "page_b": r[1], "shared_sources": r[2]}
                       for r in rows], indent=2))


# ── retrieve: seed → BFS traversal → ranked pages (the benchmark's A1) ─

_WORD = re.compile(r"\w{3,}")

# Global/sensemaking cue list for --route auto. The benchmark showed the
# winning policy is graph-only on global questions (vault chunks dilute
# comprehensiveness: H 0.80 vs A1 0.91 win-rate) and graph+vault blend on
# factoid/multi-hop (vault recall is best-in-class there). Conservative on
# purpose: an unmatched global query degrades gracefully to blend, which
# is still strong everywhere. Force with --route graph|blend.
_ROUTE_GLOBAL_RE = re.compile(
    r"\b(overview|overall|big.picture|landscape|themes?|main "
    r"(ideas|topics|findings|takeaways|threads)|"
    r"summari[sz]e|synthesi[sz]e|state of|"
    r"across (the|this|all|my|our)\b|"
    r"what (do|does) (we|i|the wiki|this) know|"
    r"fit together|open questions|research directions)",
    re.IGNORECASE,
)


def classify_route(query: str):
    m = _ROUTE_GLOBAL_RE.search(query)
    if m:
        return "graph", m.group(0)
    return "blend", None


def _try_connect(wiki_dir: Path):
    """Soft variant of _connect: None instead of exit when kuzu or the
    graph DB is unavailable, so retrieve can degrade to seeds-only.
    Read-only — see _connect."""
    if kuzu is None or not Path(_graph_path(wiki_dir)).exists():
        return None
    try:
        try:
            db = kuzu.Database(_graph_path(wiki_dir), read_only=True)
        except TypeError:
            db = kuzu.Database(_graph_path(wiki_dir))
        return kuzu.Connection(db)
    except Exception:
        return None


def _semantic_seed_pages(wiki_dir: Path, query: str, n: int):
    """Top-n wiki pages by chunk-embedding cosine, or None if the wiki
    index is unavailable (embeddings off, deps missing, index not built,
    or built under a different backend/model)."""
    prereq, _reason = _embedding_prereqs(wiki_dir)
    if prereq is None or not _wiki_db_path(wiki_dir).exists():
        return None
    vec_mod, cfg = prereq
    try:
        conn = _open_wiki_db(wiki_dir, vec_mod)
    except Exception:
        return None
    try:
        row = conn.execute("SELECT model FROM wiki_page_meta LIMIT 1").fetchone()
        emb, err = load_embedder(cfg)
        if emb is None:
            print(f"graph retrieve: {err}; falling back to lexical.",
                  file=sys.stderr)
            conn.close()
            return None
        if row and row[0] != emb.model_id:
            print(f"graph retrieve: wiki.db built with {row[0]}, active "
                  f"embedder is {emb.model_id} — run: graph.py embed <wiki>",
                  file=sys.stderr)
            conn.close()
            return None
        qbytes = vec_mod.serialize_float32(emb.embed_query(query))
        rows = conn.execute(
            "SELECT m.path FROM wiki_chunks c "
            "JOIN wiki_chunk_meta m ON m.vec_id = c.rowid "
            "WHERE c.embedding MATCH ? AND k = ? ORDER BY c.distance",
            (qbytes, max(n * 10, 20))).fetchall()
    except Exception as e:
        print(f"graph retrieve: semantic seed failed ({e}); "
              "falling back to lexical.", file=sys.stderr)
        conn.close()
        return None
    conn.close()
    seeds = []
    for (rel,) in rows:
        if rel not in seeds:
            seeds.append(rel)
        if len(seeds) >= n:
            break
    return seeds


def _lexical_seed_pages(wiki_dir: Path, query: str, n: int) -> list:
    """Fallback seed: term-overlap scoring over title/path (x3) + body."""
    qterms = {t.casefold() for t in _WORD.findall(query)}
    if not qterms:
        return []
    scored = []
    for page in _iter_wiki_pages(wiki_dir):
        text = page.read_text(encoding="utf-8", errors="replace")
        fm, body = read_frontmatter(text)
        rel = str(page.relative_to(wiki_dir))
        title = str(fm.get("title", page.stem.replace("-", " ")))
        hay_title = (title + " " + rel).casefold()
        hay_body = body.casefold()
        score = sum(3 for t in qterms if t in hay_title) \
            + sum(min(hay_body.count(t), 5) for t in qterms)
        if score:
            scored.append((-score, rel))
    return [rel for _s, rel in sorted(scored)[:n]]


def _load_adjacency(conn, include_provisional: bool):
    """Undirected weighted adjacency over the page graph: curated edges
    (WikiLink, Depicts) cost 1, ProvisionalLink costs 2 — the two-tier
    weighting (typed=1, provisional lower). Returns (adj, meta)."""
    meta = {r[0]: {"type": r[1], "title": r[2]} for r in _query_to_json(
        conn, "MATCH (p:WikiPage) RETURN p.path, p.type, p.title")}
    adj = {}

    def add(a, b, w, prov):
        adj.setdefault(a, []).append((b, w, prov))
        adj.setdefault(b, []).append((a, w, prov))

    tables = [("WikiLink", 1, False), ("Depicts", 1, False)]
    if include_provisional:
        tables.append(("ProvisionalLink", 2, True))
    for table, w, prov in tables:
        try:
            rows = _query_to_json(
                conn, f"MATCH (a:WikiPage)-[:{table}]->(b:WikiPage) "
                      "RETURN a.path, b.path")
        except Exception:
            if prov:
                # Graph built by a pre-v0.6 skill — no ProvisionalLink
                # table yet. Curated tiers still work; next rebuild adds it.
                continue
            raise
        for a, b in rows:
            add(a, b, w, prov)
    return adj, meta


def _traverse(adj: dict, seeds: list, budget: int):
    """Multi-source Dijkstra (weights are 1 or 2, budget = typed-hop
    budget, so one provisional hop consumes two). Tracks which seed
    reached each page and whether any provisional edge was used."""
    import heapq
    dist, via, prov_used = {}, {}, {}
    heap = []
    for s in seeds:
        dist[s], via[s], prov_used[s] = 0, s, False
        heapq.heappush(heap, (0, s, s, False))
    while heap:
        d, node, origin, prov = heapq.heappop(heap)
        if d > dist.get(node, budget + 1):
            continue
        for nb, w, is_prov in adj.get(node, ()):
            nd = d + w
            np_ = prov or is_prov
            if nd > budget:
                continue
            if nd < dist.get(nb, budget + 1):
                dist[nb], via[nb], prov_used[nb] = nd, origin, np_
                heapq.heappush(heap, (nd, nb, origin, np_))
            elif nd == dist.get(nb) and prov_used.get(nb) and not np_:
                # Equal-cost curated path beats a provisional one — the
                # tier label should reflect the strongest evidence.
                via[nb], prov_used[nb] = origin, False
                heapq.heappush(heap, (nd, nb, origin, False))
    return dist, via, prov_used


def cmd_retrieve(wiki_dir: Path, query: str, seeds_n: int, limit: int,
                 hops: int, route: str, include_provisional: bool,
                 vault_k: int, stale: bool = False):
    hops = max(1, min(int(hops), 6))
    if route == "auto":
        decided, cue = classify_route(query)
    else:
        decided, cue = route, "forced"

    # Entity-resolution abstention gate (v0.8.3). Runs BEFORE any seeding:
    # a query naming an entity that resolves against neither the curated
    # identity layer nor the raw corpus gets no retrieval context at all —
    # lexical/embedding proximity would otherwise seed a similarly-named
    # entity's pages, and the model then answers with the wrong entity's
    # facts (false-bridging). Deterministic; no LLM call, no network.
    import entity_gate
    gate = entity_gate.gate_query(wiki_dir, query)
    if gate["action"] == "abstain":
        out = {"query": query, "route": decided, "route_cue": cue,
               "entity_gate": gate, "abstain": True,
               "seeds": [], "pages": [], "vault": [],
               "note": "entity gate abstained — no retrieval context "
                       "returned; answer that the entity is not in this "
                       "workspace (see entity_gate.directive)"}
        if stale:
            out["graph_stale"] = True
        print(json.dumps(out, indent=2))
        return

    # Pure-uncurated (Option C): every mention is vault/wiki-body-only.
    # Hard-filter context to material that names the mention verbatim so
    # lexical proximity cannot seed a similarly-named curated entity.
    # Force blend so vault-only names keep their only evidence channel.
    pure_unc = entity_gate.pure_uncurated(gate)
    unc_phrases = entity_gate.mention_phrases(gate) if pure_unc else []
    if pure_unc and decided == "graph":
        decided, cue = "blend", f"{cue}+uncurated-verbatim"

    # One embedder load serves both the seeds and blend's breadth extras.
    sem = _semantic_seed_pages(wiki_dir, query, seeds_n + 4)
    if sem is not None:
        seeds, sem_extras, seed_mode = sem[:seeds_n], sem[seeds_n:], "semantic"
    else:
        seeds = _lexical_seed_pages(wiki_dir, query, seeds_n)
        sem_extras, seed_mode = [], "lexical"

    # A resolved mention's own page is the strongest possible seed —
    # guarantee it leads the seed list (curated context preferred for the
    # resolved entity; proximity seeds only augment).
    resolved = [m["page"] for m in gate["mentions"]
                if m.get("status") == "resolved" and m.get("page")]
    for rel in reversed(resolved):
        if rel in seeds:
            seeds = [rel] + [s for s in seeds if s != rel]
        elif (wiki_dir / rel).is_file():
            seeds.insert(0, rel)

    if pure_unc and unc_phrases:
        seeds = [s for s in seeds
                 if entity_gate.wiki_page_has_mention(wiki_dir, s, unc_phrases)]
        sem_extras = [s for s in sem_extras
                      if entity_gate.wiki_page_has_mention(
                          wiki_dir, s, unc_phrases)]

    out = {"query": query, "route": decided, "route_cue": cue,
           "seed_mode": seed_mode, "seeds": seeds}
    if gate["mentions"]:
        out["entity_gate"] = gate
    if stale:
        out["graph_stale"] = True
        print(f"graph retrieve: wiki newer than kuzu — results may lag; "
              f"run: graph.py rebuild {wiki_dir.name}", file=sys.stderr)
    if not seeds:
        out["pages"] = []
        if pure_unc and unc_phrases:
            # Vault-only uncurated name: no wiki seed is expected. Still
            # surface verbatim vault hits so the agent can answer from
            # raw material without a proximity wiki page.
            workspace = _workspace(wiki_dir)
            out["vault"] = entity_gate.vault_hits_for_mentions(
                workspace, unc_phrases, limit=vault_k)
            out["verbatim_filter"] = True
            out["note"] = ("entity gate: pure-uncurated — no wiki page names "
                           "the mention verbatim; vault hits restricted to "
                           "sources that do")
            print(json.dumps(out, indent=2))
            return
        out["note"] = "no seed pages matched — is the wiki empty?"
        print(json.dumps(out, indent=2))
        return

    conn = _try_connect(wiki_dir)
    qterms = {t.casefold() for t in _WORD.findall(query)}
    meta = {}
    if conn is None:
        pages = list(seeds)
        out["graph"] = ("unavailable (kuzu not installed or graph not "
                        "built — run graph.py rebuild); returning seeds only")
        dist, via, prov_used = {s: 0 for s in seeds}, {}, {}
    else:
        adj, meta = _load_adjacency(conn, include_provisional)
        dist, via, prov_used = _traverse(adj, seeds, hops)

        def overlap(rel):
            title = (meta.get(rel) or {}).get("title") or ""
            return sum(1 for t in qterms if t in (title + " " + rel).casefold())

        # The A1 ranking: graph distance asc, query-term overlap desc.
        ranked = sorted((r for r in dist if r not in seeds),
                        key=lambda r: (dist[r], -overlap(r), r))
        pages = seeds + ranked

    if decided == "blend":
        # H-arm breadth, mirroring the benchmarked hybrid exactly: extra
        # pure-semantic pages appended after the graph-ranked list, then
        # capped together. On a dense neighbourhood they fall below the
        # cap (as in the bench); they surface exactly when the graph
        # around the seeds is sparse — which is when they're needed.
        pages += [p for p in sem_extras if p not in pages]

    def _page_entry(rel):
        m = meta.get(rel)
        if m is None:
            fp = wiki_dir / rel
            title, ptype = rel, ""
            if fp.is_file():
                fm, _b = read_frontmatter(
                    fp.read_text(encoding="utf-8", errors="replace"))
                title = str(fm.get("title", Path(rel).stem.replace("-", " ")))
                ptype = str(fm.get("type", ""))
            m = {"title": title, "type": ptype}
        d = dist.get(rel)
        return {
            "page": rel, "title": m.get("title", ""), "type": m.get("type", ""),
            "distance": d,
            "overlap": sum(1 for t in qterms
                           if t in ((m.get("title") or "") + " " + rel).casefold()),
            "via": via.get(rel),
            "tier": ("seed" if rel in seeds else
                     "provisional" if prov_used.get(rel) else
                     "typed" if d is not None else "semantic"),
        }

    out["pages"] = [_page_entry(r) for r in pages[:limit]]

    if decided == "blend":
        # Vault-vector recall stream (the hybrid arm's second half). The
        # benchmark's H used ~65% curated context + vault fill; callers
        # assembling context should weight wiki pages over vault chunks
        # accordingly.
        workspace = _workspace(wiki_dir)
        vault = []
        if (workspace / "vault" / "vault.db").exists():
            import vault_search
            clean = re.sub(r"[^\w\s]", " ", query).strip() or query
            cwd = os.getcwd()
            try:
                os.chdir(workspace)
                vault = vault_search.search_results(
                    clean, limit=vault_k, mode="hybrid")
            except Exception as e:
                out["vault_note"] = f"vault search failed: {e}"
            finally:
                os.chdir(cwd)
        else:
            out["vault_note"] = "no vault/vault.db — graph-only result"
        out["vault"] = vault

    if pure_unc and unc_phrases:
        # Drop any residual non-verbatim wiki/vault hits (graph neighbours
        # of a seed that itself passed, hybrid vault proximity, etc.).
        workspace = _workspace(wiki_dir)
        out["pages"] = [
            p for p in out["pages"]
            if entity_gate.wiki_page_has_mention(
                wiki_dir, p["page"], unc_phrases)]
        out["seeds"] = [
            s for s in out.get("seeds") or []
            if entity_gate.wiki_page_has_mention(wiki_dir, s, unc_phrases)]
        vault = [
            v for v in (out.get("vault") or [])
            if entity_gate.vault_record_has_mention(
                workspace, v, unc_phrases)]
        if not vault:
            vault = entity_gate.vault_hits_for_mentions(
                workspace, unc_phrases, limit=vault_k)
        out["vault"] = vault
        out["verbatim_filter"] = True
        out["note"] = (
            "entity gate: pure-uncurated — context restricted to material "
            "that names the mention(s) verbatim; do not answer from a "
            "similarly-named curated entity")

    print(json.dumps(out, indent=2))


def cmd_link_candidates(wiki_dir: Path, limit: int, stale: bool = False):
    """The LINK proposer's candidate queue: provisional edges ranked with
    co-citation (evidentiary) ahead of embedding (associative)."""
    conn = _try_connect(wiki_dir)
    if conn is None:
        print(json.dumps({"error": "graph unavailable (kuzu not installed "
                          "or graph not built — run graph.py rebuild)"}))
        sys.exit(1)
    limit = max(1, min(int(limit), 200))
    rejects = _load_link_rejects(wiki_dir)
    try:
        rows = _query_to_json(conn,
            "MATCH (a:WikiPage)-[e:ProvisionalLink]->(b:WikiPage) "
            "RETURN a.path, b.path, e.origin, e.score")
    except Exception:
        # Graph built by a pre-v0.6 skill — no ProvisionalLink table.
        print(json.dumps({"error": "graph has no provisional tier yet — "
                          "run graph.py rebuild"}))
        sys.exit(1)
    cands = [
        {"page_a": r[0], "page_b": r[1], "origin": r[2], "score": r[3]}
        for r in rows
        if (min(r[0], r[1]), max(r[0], r[1])) not in rejects
    ]
    cands.sort(key=lambda c: (c["origin"] != "co-citation", -c["score"],
                              c["page_a"], c["page_b"]))
    out = {
        "count": min(len(cands), limit),
        "total_provisional": len(cands),
        "candidates": cands[:limit],
        "note": ("empty — run graph.py rebuild to materialise the "
                 "provisional tier") if not cands else
                ("promote by applying a real [[wikilink]] (LINK pass); "
                 "record classifier rejects in .curator/link-rejects.json"),
    }
    if stale:
        out["graph_stale"] = True
    print(json.dumps(out, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")

    rb = sub.add_parser("rebuild")
    rb.add_argument("wiki", default="wiki", nargs="?")
    rb.add_argument("--force", action="store_true",
                    help="rebuild even if the graph is already current")

    ss = sub.add_parser("shared-sources")
    ss.add_argument("wiki")
    ss.add_argument("page_a")
    ss.add_argument("page_b")

    pa = sub.add_parser("path")
    pa.add_argument("wiki")
    pa.add_argument("page_a")
    pa.add_argument("page_b")
    pa.add_argument("--max-hops", type=int, default=10)

    nb = sub.add_parser("neighbors")
    nb.add_argument("wiki")
    nb.add_argument("page")
    nb.add_argument("--hops", type=int, default=2)
    nb.add_argument("--direction", choices=["out", "in", "both"],
                    default="out",
                    help="follow outbound wikilinks (default), backlinks "
                         "(in), or both (the undirected view retrieval uses)")

    bc = sub.add_parser("bridge-candidates")
    bc.add_argument("wiki", default="wiki", nargs="?")
    bc.add_argument("--limit", type=int, default=10)

    rt = sub.add_parser("retrieve")
    rt.add_argument("wiki")
    rt.add_argument("query")
    rt.add_argument("--seeds", type=int, default=2,
                    help="number of seed pages (default 2)")
    rt.add_argument("--limit", type=int, default=6,
                    help="max pages returned (default 6)")
    rt.add_argument("--hops", type=int, default=2,
                    help="typed-hop budget for BFS expansion (default 2; "
                         "a provisional edge costs 2)")
    rt.add_argument("--route", choices=["auto", "graph", "blend"],
                    default="auto",
                    help="auto (default): global/sensemaking queries go "
                         "graph-only, everything else blends vault recall")
    rt.add_argument("--vault-k", type=int, default=3,
                    help="vault hits in blend mode (default 3)")
    rt.add_argument("--no-provisional", action="store_true",
                    help="traverse curated edges only")

    em = sub.add_parser("embed")
    em.add_argument("wiki", default="wiki", nargs="?")
    em.add_argument("--force", action="store_true",
                    help="wipe and re-embed every page")

    lc = sub.add_parser("link-candidates")
    lc.add_argument("wiki", default="wiki", nargs="?")
    lc.add_argument("--limit", type=int, default=40)

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        sys.exit(1)

    wiki_dir = Path(args.wiki).resolve()

    # retrieve/link-candidates return objects and degrade gracefully, so
    # they flag staleness instead of being hard-gated like the fixed
    # list-shaped query verbs.
    if args.command not in ("rebuild", "embed", "retrieve", "link-candidates") \
            and _check_stale(wiki_dir):
        return
    stale = _wiki_newer_than_graph(wiki_dir)

    if args.command == "rebuild":
        rebuild(wiki_dir, force=args.force)
    elif args.command == "shared-sources":
        cmd_shared_sources(wiki_dir, args.page_a, args.page_b)
    elif args.command == "path":
        cmd_path(wiki_dir, args.page_a, args.page_b, args.max_hops)
    elif args.command == "neighbors":
        cmd_neighbors(wiki_dir, args.page, args.hops, args.direction)
    elif args.command == "bridge-candidates":
        cmd_bridge_candidates(wiki_dir, args.limit)
    elif args.command == "retrieve":
        cmd_retrieve(wiki_dir, args.query, args.seeds, args.limit, args.hops,
                     args.route, not args.no_provisional, args.vault_k,
                     stale=stale)
    elif args.command == "embed":
        print(json.dumps(embed_wiki(wiki_dir, force=args.force)))
    elif args.command == "link-candidates":
        cmd_link_candidates(wiki_dir, args.limit, stale=stale)


if __name__ == "__main__":
    main()
