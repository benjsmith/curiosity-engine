#!/usr/bin/env python3
"""graph.py — kuzu-backed knowledge graph for the curiosity engine.

Maintains a property graph alongside the FTS5 index. Nodes are wiki pages
and vault sources; edges are wikilinks and vault citations.

Subcommands
-----------
    graph.py rebuild <wiki_dir>
        Drop and rebuild the entire graph from wiki pages on disk.
        Writes to .curator/graph.kuzu (single file, not git-tracked).

    graph.py shared-sources <wiki_dir> <page_a> <page_b>
        Vault sources cited by both pages.

    graph.py path <wiki_dir> <page_a> <page_b> [--max-hops N]
        Shortest wikilink path between two pages.

    graph.py neighbors <wiki_dir> <page> [--hops N]
        All pages within N hops (default 2).

    graph.py bridge-candidates <wiki_dir> [--limit N]
        Page pairs sharing vault sources but not linked. Replaces the
        O(n^2) connection_candidates in epoch_summary.py.

Requires: pip install kuzu
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import SKIP_FILES, WIKILINK_RE, CITATION_RE, read_frontmatter  # noqa: E402

try:
    import kuzu
except ImportError:
    kuzu = None


def _graph_path(wiki_dir: Path) -> str:
    return str(wiki_dir.parent / ".curator" / "graph.kuzu")


def _check_stale(wiki_dir: Path) -> bool:
    """Warn on stderr + print empty JSON if wiki is newer than the kuzu db."""
    kuzu_path = Path(_graph_path(wiki_dir))
    if not kuzu_path.exists():
        return False
    kuzu_mtime = kuzu_path.stat().st_mtime
    wiki_mtime = max((f.stat().st_mtime for f in wiki_dir.rglob("*.md")), default=0)
    if wiki_mtime > kuzu_mtime:
        print(f"graph stale (wiki newer than kuzu) — run: python3 scripts/graph.py rebuild {wiki_dir.name}",
              file=sys.stderr)
        print("[]")
        return True
    return False


def _connect(wiki_dir: Path):
    if kuzu is None:
        print(json.dumps({"error": "kuzu not installed (pip install kuzu)"}))
        sys.exit(1)
    path = _graph_path(wiki_dir)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(path)
    return kuzu.Connection(db)


def _init_schema(conn):
    for stmt in [
        "CREATE NODE TABLE IF NOT EXISTS WikiPage(path STRING, type STRING, title STRING, PRIMARY KEY (path))",
        "CREATE NODE TABLE IF NOT EXISTS VaultSource(path STRING, title STRING, PRIMARY KEY (path))",
        "CREATE REL TABLE IF NOT EXISTS WikiLink(FROM WikiPage TO WikiPage)",
        "CREATE REL TABLE IF NOT EXISTS Cites(FROM WikiPage TO VaultSource)",
    ]:
        conn.execute(stmt)


def rebuild(wiki_dir: Path):
    path = _graph_path(wiki_dir)
    p = Path(path)
    if p.exists():
        p.unlink()

    conn = _connect(wiki_dir)
    _init_schema(conn)

    pages = [f for f in wiki_dir.rglob("*.md")
             if f.name not in SKIP_FILES and "_suspect" not in f.parts]

    vault_sources = set()
    page_data = []

    for page in pages:
        text = page.read_text()
        fm, _ = read_frontmatter(text)
        rel = str(page.relative_to(wiki_dir))
        page_type = fm.get("type", "")
        title = fm.get("title", page.stem.replace("-", " ").title())

        links = set()
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip().lower().replace(" ", "-")
            links.add(target)

        citations = set()
        for m in CITATION_RE.finditer(text):
            vp = m.group(1).strip()
            citations.add(vp)
            vault_sources.add(vp)

        page_data.append((rel, page_type, title, links, citations))

    page_paths = {d[0] for d in page_data}
    page_stems = {}
    for d in page_data:
        stem = Path(d[0]).stem.lower()
        page_stems[stem] = d[0]

    for rel, page_type, title, _, _ in page_data:
        conn.execute(
            "CREATE (:WikiPage {path: $p, type: $t, title: $ti})",
            {"p": rel, "t": page_type, "ti": title}
        )

    for vp in vault_sources:
        conn.execute(
            "CREATE (:VaultSource {path: $p, title: $t})",
            {"p": vp, "t": vp}
        )

    for rel, _, _, links, citations in page_data:
        for target in links:
            target_path = page_stems.get(target)
            if target_path and target_path != rel:
                conn.execute(
                    "MATCH (a:WikiPage), (b:WikiPage) "
                    "WHERE a.path = $from AND b.path = $to "
                    "CREATE (a)-[:WikiLink]->(b)",
                    {"from": rel, "to": target_path}
                )
        for vp in citations:
            conn.execute(
                "MATCH (a:WikiPage), (b:VaultSource) "
                "WHERE a.path = $from AND b.path = $to "
                "CREATE (a)-[:Cites]->(b)",
                {"from": rel, "to": vp}
            )

    stats = {
        "pages": len(page_data),
        "vault_sources": len(vault_sources),
        "wikilinks": sum(len(d[3]) for d in page_data),
        "citations": sum(len(d[4]) for d in page_data),
    }
    print(json.dumps({"status": "rebuilt", **stats}))


def _query_to_json(conn, cypher, params=None):
    result = conn.execute(cypher, params or {})
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def cmd_shared_sources(wiki_dir: Path, page_a: str, page_b: str):
    conn = _connect(wiki_dir)
    rows = _query_to_json(conn,
        "MATCH (a:WikiPage)-[:Cites]->(v:VaultSource)<-[:Cites]-(b:WikiPage) "
        "WHERE a.path = $a AND b.path = $b "
        "RETURN DISTINCT v.path",
        {"a": page_a, "b": page_b}
    )
    print(json.dumps([r[0] for r in rows], indent=2))


def cmd_path(wiki_dir: Path, page_a: str, page_b: str, max_hops: int):
    conn = _connect(wiki_dir)
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


def cmd_neighbors(wiki_dir: Path, page: str, hops: int):
    conn = _connect(wiki_dir)
    hops = max(1, min(int(hops), 10))
    rows = _query_to_json(conn,
        f"MATCH (a:WikiPage)-[:WikiLink*1..{hops}]->(b:WikiPage) "
        "WHERE a.path = $p AND a.path <> b.path "
        "RETURN DISTINCT b.path, b.type",
        {"p": page}
    )
    print(json.dumps([{"path": r[0], "type": r[1]} for r in rows], indent=2))


def cmd_bridge_candidates(wiki_dir: Path, limit: int):
    conn = _connect(wiki_dir)
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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("rebuild").add_argument("wiki", default="wiki", nargs="?")

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

    bc = sub.add_parser("bridge-candidates")
    bc.add_argument("wiki", default="wiki", nargs="?")
    bc.add_argument("--limit", type=int, default=10)

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        sys.exit(1)

    wiki_dir = Path(args.wiki).resolve()

    if args.command != "rebuild" and _check_stale(wiki_dir):
        return

    if args.command == "rebuild":
        rebuild(wiki_dir)
    elif args.command == "shared-sources":
        cmd_shared_sources(wiki_dir, args.page_a, args.page_b)
    elif args.command == "path":
        cmd_path(wiki_dir, args.page_a, args.page_b, args.max_hops)
    elif args.command == "neighbors":
        cmd_neighbors(wiki_dir, args.page, args.hops)
    elif args.command == "bridge-candidates":
        cmd_bridge_candidates(wiki_dir, args.limit)


if __name__ == "__main__":
    main()
