"""Derived FTS5 index over complete literal records, separate from vault previews."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import structured_data as sd

DB_PATH = Path(".curator/records.db")


def _connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS records USING fts5(
            extraction UNINDEXED, collection UNINDEXED, locator UNINDEXED,
            kind UNINDEXED, source_sha UNINDEXED, literal_json UNINDEXED, body,
            tokenize='unicode61');
        CREATE TABLE IF NOT EXISTS indexed_sources(
            extraction TEXT PRIMARY KEY, extraction_sha TEXT NOT NULL, source_sha TEXT NOT NULL,
            records INTEGER NOT NULL);
    """)
    return db


def searchable(value):
    if isinstance(value, str):
        return str(value)
    if isinstance(value, dict):
        return " ".join(str(k) + " " + searchable(v) for k, v in value.items())
    if isinstance(value, list):
        return " ".join(searchable(v) for v in value)
    return sd.literal(value)


def index_extraction(extraction, data=None, db_path=None):
    from naming import read_frontmatter
    extraction = Path(extraction).resolve()
    vault = Path("vault").resolve()
    if not extraction.is_relative_to(vault):
        raise ValueError("record index source must be inside vault")
    rel = str(extraction.relative_to(vault))
    fm, _ = read_frontmatter(extraction.read_text())
    sha = sd.file_hash(extraction)
    db = _connect(db_path or DB_PATH)
    owned = data is None
    try:
        prior = db.execute("SELECT extraction_sha FROM indexed_sources WHERE extraction=?", (rel,)).fetchone()
        if prior and prior[0] == sha:
            return {"status": "unchanged", "extraction": rel}
        if data is None:
            _, data = sd.load_extraction(extraction)
        if not data["supported"]:
            return {"status": "unsupported", "extraction": rel}
        count = 0
        with db:
            db.execute("DELETE FROM records WHERE extraction=?", (rel,))
            for table in data["tables"]:
                if table["kind"] == "records":
                    rows = zip(table["values"], table["locators"])
                else:
                    rows = (({field: sd.loads(value)}, field) for field, value in table["rows"])
                for values, locator in rows:
                    db.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (rel, table["path"], locator, table["kind"], fm["sha256"], sd.literal(values), searchable(values)))
                    count += 1
            if sd.file_hash(extraction) != sha:
                raise ValueError("extraction changed during record indexing")
            db.execute("INSERT OR REPLACE INTO indexed_sources VALUES (?, ?, ?, ?)", (rel, sha, fm["sha256"], count))
        return {"status": "indexed", "extraction": rel, "records": count}
    finally:
        if owned and data is not None:
            sd.close_data(data)
        db.close()


def rebuild():
    import os
    import tempfile
    from naming import read_frontmatter
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".record-index-", dir=DB_PATH.parent)
    os.close(fd)
    try:
        db = _connect(temp)
        db.close()
        results = []
        for path in sorted(Path("vault").rglob("*.extracted.md")):
            if any(p.startswith("_") for p in path.relative_to("vault").parts):
                continue
            fm, _ = read_frontmatter(path.read_text())
            if fm.get("structured_version") and fm.get("data_complete") == "true":
                results.append(index_extraction(path, db_path=temp))
        from dataset_recovery import publish_database
        publish_database(temp, DB_PATH)
        return {"status": "rebuilt", "sources": len(results),
                "records": sum(r.get("records", 0) for r in results)}
    finally:
        Path(temp).unlink(missing_ok=True)


def search(query, limit=10, extraction=None):
    from naming import read_frontmatter
    if not isinstance(query, str) or not query.strip() or len(query) > 512:
        raise ValueError("search query must contain 1–512 characters")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if not DB_PATH.exists():
        return {"results": [], "note": "run datasets.py index-records --rebuild"}
    # Every term is literal. No caller-provided FTS operators/column selectors.
    phrase = " AND ".join('"' + token.replace('"', '""') + '"' for token in query.split())
    sql = """SELECT extraction,collection,locator,kind,source_sha,literal_json,
             snippet(records,6,'[',']','…',24) FROM records WHERE records MATCH ?"""
    args = [phrase]
    if extraction:
        sql += " AND extraction=?"
        args.append(str(extraction).removeprefix("vault/"))
    sql += " ORDER BY bm25(records),extraction,collection,locator LIMIT ?"
    args.append(limit)
    checked, results, stale = {}, [], []
    with closing(sqlite3.connect(f"file:{DB_PATH.resolve()}?mode=ro", uri=True)) as db:
        for rel, collection, locator, kind, source_sha, literal_json, excerpt in db.execute(sql, args):
            if rel not in checked:
                path = Path("vault") / rel
                try:
                    fm, _ = read_frontmatter(path.read_text())
                    indexed = db.execute("SELECT extraction_sha FROM indexed_sources WHERE extraction=?", (rel,)).fetchone()[0]
                    original = path.parent / fm["kept_as"] if fm.get("kept_as") else Path(fm["source_path"])
                    checked[rel] = sd.file_hash(path) == indexed and sd.file_hash(original) == source_sha
                except (OSError, KeyError, TypeError):
                    checked[rel] = False
                if not checked[rel]:
                    stale.append(rel)
            if not checked[rel]:
                continue
            hit = {"extraction": "vault/" + rel, "collection": collection, "source_locator": locator,
                   "kind": kind, "excerpt": excerpt[:512], "literal_preview": literal_json[:1024],
                   "literal_truncated": len(literal_json) > 1024, "citation": f"(vault:{rel})"}
            table_db = Path(".curator/tables.db")
            if table_db.exists():
                try:
                    with closing(sqlite3.connect(f"file:{table_db.resolve()}?mode=ro", uri=True)) as tables:
                        row = tables.execute("""SELECT e.table_stem FROM _extracted_tables e
                            JOIN _structured_lineage l USING(table_stem,row_idx)
                            WHERE e.source_extraction=? AND l.collection_path=? AND l.source_locator=?
                            AND e.extraction_sha=? LIMIT 1""", (rel, collection, locator, source_sha)).fetchone()
                    if row:
                        hit["table_link"] = f"[[{row[0]}]]"
                except sqlite3.OperationalError:
                    pass
            results.append(hit)
    return {"untrusted": True, "results": results, "stale_sources": stale,
            "note": "Original literal records; use structured queries for numeric comparisons."}
