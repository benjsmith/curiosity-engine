#!/usr/bin/env python3
"""BM25-ranked full-text search over vault sources.

Usage:
    python3 vault_search.py "query"              # top 10 results as JSON
    python3 vault_search.py "query" --limit 5    # top 5
    python3 vault_search.py "query" --text       # include full extracted text
    python3 vault_search.py --count              # total indexed documents
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path("vault/vault.db")


def search(query: str, limit: int, include_text: bool):
    if not DB.exists():
        print("[]")
        return

    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA journal_mode=WAL")

    if include_text:
        rows = conn.execute(
            "SELECT path, title, source_path, date, body, bm25(sources) as rank "
            "FROM sources WHERE sources MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        results = [{"path": r[0], "title": r[1], "source_path": r[2],
                    "date": r[3], "text": r[4], "rank": r[5]} for r in rows]
    else:
        rows = conn.execute(
            "SELECT path, title, source_path, date, "
            "snippet(sources, 2, '>>>', '<<<', '...', 40) as snippet, "
            "bm25(sources) as rank "
            "FROM sources WHERE sources MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        results = [{"path": r[0], "title": r[1], "source_path": r[2],
                    "date": r[3], "snippet": r[4], "rank": r[5]} for r in rows]

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
                    help="FTS5 query string")
    ap.add_argument("--limit", type=int, default=10,
                    help="max results (default: 10)")
    ap.add_argument("--text", action="store_true",
                    help="include full extracted body instead of a snippet")
    ap.add_argument("--count", action="store_true",
                    help="print total indexed document count and exit")
    args = ap.parse_args()

    if args.count:
        count()
        return

    if not args.query:
        ap.print_usage()
        sys.exit(1)

    search(args.query, args.limit, args.text)


if __name__ == "__main__":
    main()
