#!/usr/bin/env python3
"""query_router.py — first-class deterministic query surface (U2).

Promotes the two engines CE already maintains as curator scratch into a
user/agent-facing query verb:

- `.curator/tables.db`  — SQLite class-entity rows (structured questions:
  aggregations, filters, joins). Cheap, exact, unbounded re-reads.
- `.curator/graph.kuzu` — kuzu property graph (structural questions: paths,
  neighbours, shared sources, bridges).

The point is the read/write inversion: structured + structural questions hit
an engine instead of spending tokens re-reading prose. Only *synthesis*
questions ("what do I know about X?") stay on the LLM + vault_search path —
and for those this script returns a routing directive, it does not answer
them.

The split is deliberately honest. This script EXECUTES explicit structured
queries (`sql` / `cypher`). It does NOT translate natural language into
SQL/Cypher — that needs a model. `classify` inspects a natural-language
question, returns a route plus the queryable surface, and lets the
orchestrator either ask the LLM to emit a concrete `sql`/`cypher` query or
fall through to synthesis.

Both engines are opened READ-ONLY here. Reads can never mutate curator
state, regardless of what query text arrives:
- SQLite via the `file:...?mode=ro` URI (engine-level write refusal).
- kuzu via `read_only=True` where the installed version supports it.
- Plus a statement allowlist (SQL: SELECT/WITH only, single statement;
  Cypher: no CREATE/SET/DELETE/MERGE/DROP/COPY/ALTER/INSTALL/LOAD).

Subcommands
-----------
    query_router.py sql "SELECT ... FROM <table> ..." [--limit N]
        Execute a read-only SQL query over the class tables. Refuses
        anything that isn't a single SELECT/WITH statement.

    query_router.py cypher "MATCH ... RETURN ..." [--wiki wiki]
        Execute a read-only Cypher query over the wiki graph.

    query_router.py introspect [--wiki wiki]
        Dump the queryable surface: class tables + columns + row counts,
        and the graph node/edge catalogue. This is what an agent reads to
        write a concrete sql/cypher query.

    query_router.py classify "<natural language question>"
        Heuristic route (structured | structural | synthesis) + the
        surface. Advisory: defaults to synthesis on low confidence so a
        misclassified question fails safe to the LLM path.

Hash-guarded by evolve_guard.sh.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Match tables.py: macOS system sqlite3 is often built without loadable
# extensions; pysqlite3 is a drop-in. Either works for read-only SELECTs.
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

try:
    import kuzu
except ImportError:
    kuzu = None


TABLES_DB = Path(".curator/tables.db")


# ---- SQL (tables.db), read-only ----

_SQL_ALLOWED_FIRST = ("select", "with")


def _strip_sql_comments(q: str) -> str:
    """Drop -- line comments and /* */ block comments before validation so
    they cannot smuggle a second statement past the single-statement check."""
    q = re.sub(r"/\*.*?\*/", " ", q, flags=re.S)
    q = re.sub(r"--[^\n]*", " ", q)
    return q.strip()


def validate_readonly_sql(query: str) -> Tuple[bool, str]:
    """Allow exactly one SELECT/WITH statement. Returns (ok, reason)."""
    stripped = _strip_sql_comments(query)
    if not stripped:
        return False, "empty query"
    # A single trailing semicolon is fine; an interior one means multiple
    # statements, which we refuse (even read-only, keep the surface narrow).
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        return False, "multiple statements are not allowed"
    first = body.split(None, 1)[0].lower()
    if first not in _SQL_ALLOWED_FIRST:
        return False, f"only SELECT/WITH queries are allowed (got '{first}')"
    return True, ""


def _ro_sqlite_conn():
    """Open tables.db read-only at the engine level via PRAGMA query_only.

    We deliberately do NOT use the `mode=ro` URI: that hangs/fails on a
    WAL-mode database whose -shm needs write access to map (the common
    state for a live curator db). A normal connection has the file access
    WAL needs; `query_only=ON` then makes the engine refuse every write
    (SQLITE_READONLY), which is the guarantee we actually want. The
    statement allowlist (single SELECT/WITH only) means a routed query
    can't issue the `PRAGMA query_only=OFF` that would lift it. busy_timeout
    keeps a contended db from blocking the call indefinitely.

    Callers guard `TABLES_DB.exists()` first, so this never creates the db.
    """
    conn = sqlite3.connect(str(TABLES_DB), timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=ON")
    return conn


def cmd_sql(query: str, limit: int) -> int:
    if not TABLES_DB.exists():
        print(json.dumps({"error": f"no class tables yet ({TABLES_DB} missing)"}))
        return 2
    ok, reason = validate_readonly_sql(query)
    if not ok:
        print(json.dumps({"error": f"rejected: {reason}"}))
        return 2
    try:
        conn = _ro_sqlite_conn()
    except sqlite3.Error as e:
        print(json.dumps({"error": f"cannot open tables.db read-only: {e}"}))
        return 2
    try:
        cur = conn.execute(query)
        rows = cur.fetchmany(limit + 1)
        col_names = [d[0] for d in cur.description] if cur.description else []
    except sqlite3.Error as e:
        conn.close()
        print(json.dumps({"error": f"query error: {e}"}))
        return 2
    conn.close()
    truncated = len(rows) > limit
    rows = rows[:limit]
    results = [dict(zip(col_names, r)) for r in rows]
    print(json.dumps({
        "engine": "sql",
        "columns": col_names,
        "row_count": len(results),
        "truncated": truncated,
        "results": results,
    }, indent=2, default=str))
    return 0


# ---- Cypher (graph.kuzu), read-only ----

_CYPHER_WRITE_RE = re.compile(
    r"\b(create|set|delete|detach|merge|drop|copy|alter|install|load)\b",
    re.IGNORECASE,
)


def validate_readonly_cypher(query: str) -> Tuple[bool, str]:
    if not query.strip():
        return False, "empty query"
    m = _CYPHER_WRITE_RE.search(query)
    if m:
        return False, f"write/DDL clause '{m.group(1)}' is not allowed"
    return True, ""


def _graph_path(wiki_dir: Path) -> str:
    return str(wiki_dir.parent / ".curator" / "graph.kuzu")


def _ro_kuzu_conn(wiki_dir: Path):
    path = _graph_path(wiki_dir)
    if not Path(path).exists():
        return None
    try:
        db = kuzu.Database(path, read_only=True)
    except TypeError:
        # Older kuzu without the read_only kwarg — the validate step + the
        # absence of any write path here keep it read-only in practice.
        db = kuzu.Database(path)
    return kuzu.Connection(db)


def _kuzu_rows(conn, cypher: str, params=None):
    result = conn.execute(cypher, params or {})
    cols = result.get_column_names()
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return cols, rows


def cmd_cypher(query: str, wiki_dir: Path) -> int:
    # Validate first: the read-only guarantee should hold deterministically
    # whether or not kuzu happens to be installed on this machine.
    ok, reason = validate_readonly_cypher(query)
    if not ok:
        print(json.dumps({"error": f"rejected: {reason}"}))
        return 2
    if kuzu is None:
        print(json.dumps({"error": "kuzu not installed (uv pip install kuzu)"}))
        return 2
    conn = _ro_kuzu_conn(wiki_dir)
    if conn is None:
        print(json.dumps({"error": f"no graph yet ({_graph_path(wiki_dir)} missing) "
                          "— run graph.py rebuild"}))
        return 2
    try:
        cols, rows = _kuzu_rows(conn, query)
    except Exception as e:  # kuzu raises a family of RuntimeErrors
        print(json.dumps({"error": f"query error: {e}"}))
        return 2
    results = [dict(zip(cols, r)) for r in rows]
    print(json.dumps({
        "engine": "cypher",
        "columns": cols,
        "row_count": len(results),
        "results": results,
    }, indent=2, default=str))
    return 0


# ---- Introspection: the queryable surface ----

def table_surface() -> list:
    """Class tables + their columns + row counts, read-only. The structured
    half of the query surface; also reused by epoch_summary aggregates."""
    if not TABLES_DB.exists():
        return []
    try:
        conn = _ro_sqlite_conn()
    except sqlite3.Error:
        return []
    out = []
    try:
        names = [r[0] for r in conn.execute(
            "SELECT table_name FROM _schema_meta ORDER BY table_name")]
        for name in names:
            cols = []
            for row in conn.execute(f'PRAGMA table_info("{name}")'):
                cname = row[1]
                cols.append({
                    "name": cname,
                    "type": row[2],
                    "pk": bool(row[5]),
                    "reserved": cname.startswith("_"),
                })
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            out.append({"name": name, "row_count": count, "columns": cols})
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def graph_surface(wiki_dir: Path) -> dict:
    """Graph node/edge catalogue. Falls back to the static schema (from
    graph.py's _init_schema) when kuzu can't introspect."""
    static = {
        "nodes": ["WikiPage", "VaultSource", "DataRow", "Note"],
        "edges": ["WikiLink", "Cites", "DataRef", "Depicts", "AppearsIn",
                  "ProvisionalLink"],
    }
    if kuzu is None:
        return {**static, "source": "static (kuzu not installed)"}
    conn = _ro_kuzu_conn(wiki_dir)
    if conn is None:
        return {**static, "source": "static (graph not built)"}
    try:
        _, rows = _kuzu_rows(conn, "CALL show_tables() RETURN name, type")
        nodes = sorted(r[0] for r in rows if str(r[1]).upper() == "NODE")
        edges = sorted(r[0] for r in rows if str(r[1]).upper() in ("REL", "RELATIONSHIP"))
        if nodes or edges:
            return {"nodes": nodes, "edges": edges, "source": "live"}
    except Exception:
        pass
    return {**static, "source": "static (introspection unavailable)"}


def cmd_introspect(wiki_dir: Path) -> int:
    print(json.dumps({
        "tables": table_surface(),
        "graph": graph_surface(wiki_dir),
        "note": "Write a concrete query with `query_router.py sql ...` "
                "(SELECT/WITH over the tables above) or `cypher ...` "
                "(over the graph nodes/edges).",
    }, indent=2, default=str))
    return 0


# ---- Heuristic routing (advisory) ----

_STRUCTURED_RE = re.compile(
    r"\b(how many|count|number of|total|sum|average|avg|mean|median|"
    r"per |group(ed)? by|greater than|less than|more than|fewer than|"
    r"top \d+|highest|lowest|list all|rows where|filter|aggregate)\b",
    re.IGNORECASE,
)
_STRUCTURAL_RE = re.compile(
    r"\b(connected|connection|path between|how (are|is) .* (related|linked|connected)|"
    r"neighbou?rs?|links? to|cites|citing|shared sources?|bridge|"
    r"shortest path|related to|depends on)\b",
    re.IGNORECASE,
)


def classify_question(q: str) -> dict:
    structured = bool(_STRUCTURED_RE.search(q))
    structural = bool(_STRUCTURAL_RE.search(q))
    # Structural cues win ties — "how many pages connect to X" is best served
    # by the graph; pure counting without relation words goes to SQL.
    if structural:
        route, confidence = "structural", "high" if not structured else "medium"
    elif structured:
        route, confidence = "structured", "high"
    else:
        route, confidence = "synthesis", "default"
    return {"route": route, "confidence": confidence,
            "signals": {"structured": structured, "structural": structural}}


def cmd_classify(query: str, wiki_dir: Path) -> int:
    decision = classify_question(query)
    out = {"query": query, **decision}
    if decision["route"] == "structured":
        out["surface"] = {"tables": table_surface()}
        out["next"] = ("Ask the LLM to emit a SELECT/WITH over these tables, "
                       "then run `query_router.py sql ...`.")
    elif decision["route"] == "structural":
        out["surface"] = {"graph": graph_surface(wiki_dir)}
        out["next"] = ("Ask the LLM to emit a MATCH ... RETURN over these "
                       "nodes/edges, then run `query_router.py cypher ...`.")
    else:
        out["next"] = ("No structured/structural signal — answer via "
                       "vault_search + synthesis (the existing LLM path).")
        # Entity-resolution abstention gate (v0.8.3): a synthesis question
        # naming an entity that resolves against neither the curated
        # identity layer nor the raw corpus must abstain — never answer
        # from a similarly-named entity's context (false-bridging).
        # Deterministic; no LLM call, no network.
        import entity_gate
        gate = entity_gate.gate_query(wiki_dir, query)
        if gate["mentions"]:
            out["entity_gate"] = gate
            if gate["action"] == "abstain":
                out["next"] = ("ABSTAIN — " + gate["directive"]
                               + " (Per-mention verdicts in entity_gate.)")
            elif gate["action"] == "partial":
                out["next"] += (" Entity gate: abstain for "
                                + ", ".join(gate["abstained_mentions"])
                                + " — see entity_gate.")
    print(json.dumps(out, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sql = sub.add_parser("sql", help="read-only SELECT/WITH over tables.db")
    p_sql.add_argument("query")
    p_sql.add_argument("--limit", type=int, default=100)

    p_cy = sub.add_parser("cypher", help="read-only Cypher over graph.kuzu")
    p_cy.add_argument("query")
    p_cy.add_argument("--wiki", default="wiki")

    p_in = sub.add_parser("introspect", help="dump the queryable surface")
    p_in.add_argument("--wiki", default="wiki")

    p_cl = sub.add_parser("classify", help="heuristic route for a NL question")
    p_cl.add_argument("query")
    p_cl.add_argument("--wiki", default="wiki")

    args = ap.parse_args()
    if args.cmd == "sql":
        return cmd_sql(args.query, args.limit)
    if args.cmd == "cypher":
        return cmd_cypher(args.query, Path(args.wiki).resolve())
    if args.cmd == "introspect":
        return cmd_introspect(Path(args.wiki).resolve())
    if args.cmd == "classify":
        return cmd_classify(args.query, Path(args.wiki).resolve())
    ap.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
