#!/usr/bin/env python3
"""derived_cache.py — incremental materialisation of derived facts (U5).

Generalises the per-page score cache (`lint_scores.py`: keyed by
`text_hash + inbound`, global-busted by `titles_hash + vault_rowcount`)
into a reusable cache for *any* derived fact — aggregates, transitive
closures, cross-shard link proposals. The contract is the same one that
makes the score cache O(changed) instead of O(total): a derived value is
keyed by the fingerprints of its **dependency set**, and is served from
cache iff every dependency's fingerprint still matches. Change one
dependency, recompute one fact; everything else stays cached.

This is the Datalog materialised-view idea without Datalog: compute once
at write time, invalidate on source change. It's how "given enough token
budget" stops meaning "re-derive the world on every read" — spend the
budget where it compounds (identity resolution, shape validation,
cross-shard linking), never on re-deriving structure a read could cache.

Store: `.curator/.derived_cache.json` (best-effort atomic write,
silent-fail tolerant — exactly like `.score_cache.json`). A cache miss is
always safe: the caller recomputes.

CORRECTNESS RULE: the dependency set must be COMPLETE. A fact cached
against an incomplete dep set serves stale data. Start with
single-dependency facts where the dep is unambiguous (an aggregate over
one table → that table's fingerprint; a graph closure → the graph's
fingerprint). Add multi-dependency facts only once invalidation is proven.

Library API
-----------
    dep_hash(text)                      -> 16-char fingerprint of a string
    table_fingerprint(tables_db, name)  -> fingerprint of a class table
    graph_fingerprint(wiki_dir)         -> fingerprint of the kuzu graph
    get(wiki_dir, key, deps)            -> value | _MISS
    put(wiki_dir, key, deps, value)     -> None
    memoize(wiki_dir, key, deps, fn)    -> value (get-or-compute-and-put)
    clear(wiki_dir)                     -> None

Subcommands
-----------
    derived_cache.py stats [--wiki wiki]
    derived_cache.py clear [--wiki wiki]
    derived_cache.py cached-aggregate <table> "<SELECT ...>" [--wiki wiki]
        Demonstrated consumer: memoise a read-only aggregate over a class
        table, keyed on that table's fingerprint. Re-runs hit cache until a
        row changes. Shows `cache_hit: true|false`.

Hash-guarded by evolve_guard.sh.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Callable, Dict

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3


_CACHE_FILENAME = ".derived_cache.json"


class _Miss:
    """Sentinel for a cache miss (distinct from a cached value of None)."""
    __slots__ = ()
    def __repr__(self):  # noqa: E704
        return "<derived_cache MISS>"


_MISS = _Miss()


# ---- fingerprints ----

def dep_hash(text: str) -> str:
    """16-char SHA-256 of a string — same scheme as lint_scores._text_hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def table_fingerprint(tables_db: Path, name: str) -> str:
    """Cheap, reasonably-complete fingerprint of a class table's state.

    `(row_count, max _inserted_at, max _updated_at)`: inserts bump count +
    _inserted_at, updates bump _updated_at to now(), deletes change count.
    No full-table hash, so it stays O(1)-ish on a large table. Returns
    "absent" when the table/db is missing — distinct from any real state."""
    if not tables_db.exists():
        return "absent"
    try:
        conn = sqlite3.connect(str(tables_db), timeout=5)
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        return "absent"
    try:
        row = conn.execute(
            f'SELECT COUNT(*), COALESCE(MAX(_inserted_at), \'\'), '
            f'COALESCE(MAX(_updated_at), \'\') FROM "{name}"').fetchone()
    except sqlite3.Error:
        conn.close()
        return "absent"
    conn.close()
    return dep_hash(f"{row[0]}|{row[1]}|{row[2]}")


def graph_fingerprint(wiki_dir: Path) -> str:
    """Fingerprint of the kuzu graph: its mtime. graph.py rebuilds the whole
    db on any structural change and is idempotent, so the file mtime is a
    sound bust for any derived fact computed over graph structure."""
    p = wiki_dir.parent / ".curator" / "graph.kuzu"
    if not p.exists():
        return "absent"
    return dep_hash(str(p.stat().st_mtime))


# ---- store ----

def _cache_path(wiki_dir: Path) -> Path:
    return wiki_dir.parent / ".curator" / _CACHE_FILENAME


def _load(wiki_dir: Path) -> dict:
    path = _cache_path(wiki_dir)
    if not path.exists():
        return {"entries": {}}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"entries": {}}


def _store(wiki_dir: Path, data: dict) -> None:
    path = _cache_path(wiki_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data))
        tmp.replace(path)  # atomic on POSIX
    except OSError:
        pass


# ---- API ----

def get(wiki_dir: Path, key: str, deps: Dict[str, str]):
    """Return the cached value for `key` iff its stored dependency
    fingerprints exactly match `deps` (same keys, same values). Else _MISS.
    A stored value of None is returned as None, not _MISS."""
    entry = _load(wiki_dir)["entries"].get(key)
    if entry is None:
        return _MISS
    if entry.get("deps") != deps:
        return _MISS
    return entry.get("value")


def put(wiki_dir: Path, key: str, deps: Dict[str, str], value) -> None:
    data = _load(wiki_dir)
    data["entries"][key] = {"deps": dict(deps), "value": value}
    _store(wiki_dir, data)


def memoize(wiki_dir: Path, key: str, deps: Dict[str, str],
            compute: Callable[[], object]):
    """Get-or-(compute-and-put). Returns (value, cache_hit)."""
    cached = get(wiki_dir, key, deps)
    if cached is not _MISS:
        return cached, True
    value = compute()
    put(wiki_dir, key, deps, value)
    return value, False


def clear(wiki_dir: Path) -> None:
    _store(wiki_dir, {"entries": {}})


# ---- CLI ----

def cmd_stats(wiki_dir: Path) -> int:
    data = _load(wiki_dir)
    entries = data["entries"]
    print(json.dumps({
        "cache_path": str(_cache_path(wiki_dir)),
        "entry_count": len(entries),
        "keys": sorted(entries.keys()),
    }, indent=2))
    return 0


def cmd_clear(wiki_dir: Path) -> int:
    clear(wiki_dir)
    print(json.dumps({"cleared": True}))
    return 0


def cmd_cached_aggregate(wiki_dir: Path, table: str, query: str) -> int:
    """Demonstrated consumer: memoise a read-only aggregate over a class
    table, keyed on the table's fingerprint. Invalidates on row churn."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import query_router as qr  # noqa: E402

    tables_db = wiki_dir.parent / ".curator" / "tables.db"
    ok, reason = qr.validate_readonly_sql(query)
    if not ok:
        print(json.dumps({"error": f"rejected: {reason}"}))
        return 2
    deps = {f"table:{table}": table_fingerprint(tables_db, table)}
    key = f"aggregate:{table}:{dep_hash(query)}"

    def compute():
        if not tables_db.exists():
            return {"error": "no tables.db"}
        conn = qr._ro_sqlite_conn()
        try:
            cur = conn.execute(query)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
        return {"columns": cols, "results": rows}

    value, hit = memoize(wiki_dir, key, deps, compute)
    print(json.dumps({"cache_hit": hit, "table_fingerprint": deps[f"table:{table}"],
                      "value": value}, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_st = sub.add_parser("stats", help="cache entry count + keys")
    p_st.add_argument("--wiki", default="wiki")

    p_cl = sub.add_parser("clear", help="wipe the derived cache")
    p_cl.add_argument("--wiki", default="wiki")

    p_ca = sub.add_parser("cached-aggregate",
                          help="memoised read-only aggregate over a class table")
    p_ca.add_argument("table")
    p_ca.add_argument("query")
    p_ca.add_argument("--wiki", default="wiki")

    args = ap.parse_args()
    if args.cmd == "stats":
        return cmd_stats(Path(args.wiki).resolve())
    if args.cmd == "clear":
        return cmd_clear(Path(args.wiki).resolve())
    if args.cmd == "cached-aggregate":
        return cmd_cached_aggregate(Path(args.wiki).resolve(), args.table, args.query)
    ap.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
