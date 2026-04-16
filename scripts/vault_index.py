#!/usr/bin/env python3
"""Index extracted text into the vault's FTS5 search database.

Usage:
    python3 vault_index.py <extracted_file.md> "<title>"
    python3 vault_index.py --init              # create empty DB
    python3 vault_index.py --rebuild           # reindex all .extracted.md files
    python3 vault_index.py --count             # print document count
    python3 vault_index.py --hash <file>       # print SHA-256 of file

Dedup: inserts are keyed on `path`. Re-indexing the same path replaces
the old row rather than creating a duplicate.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

DB = Path("vault/vault.db")


def init_db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS sources USING fts5(
            path, title, body, date, source_path,
            tokenize='porter unicode61'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS source_meta (
            path TEXT PRIMARY KEY,
            sha256 TEXT,
            indexed_at TEXT
        )
    """)
    c.commit()
    c.close()


def file_sha256(filepath: Path) -> str:
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


def _find_original(extracted: Path) -> str:
    stem = extracted.name.replace(".extracted.md", "")
    originals = [f for f in extracted.parent.glob(f"{stem}.*")
                 if ".extracted." not in f.name]
    if originals:
        try:
            return str(originals[0].relative_to(Path("vault")))
        except ValueError:
            return str(originals[0])
    return ""


def index_file(path_str, title):
    init_db()
    p = Path(path_str)
    if not p.exists():
        print(json.dumps({"error": f"{p} not found"}))
        sys.exit(1)
    text = p.read_text()
    rel = str(p.relative_to(Path("vault"))) if str(p).startswith("vault") else str(p)
    sha = file_sha256(p)
    src = _find_original(p)

    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA journal_mode=WAL")

    existing = c.execute(
        "SELECT sha256 FROM source_meta WHERE path = ?", (rel,)
    ).fetchone()
    if existing and existing[0] == sha:
        c.close()
        print(json.dumps({"path": rel, "status": "unchanged", "sha256": sha[:12]}))
        return

    if existing:
        c.execute("DELETE FROM sources WHERE path = ?", (rel,))
        c.execute("DELETE FROM source_meta WHERE path = ?", (rel,))

    c.execute(
        "INSERT INTO sources(path, title, body, date, source_path) VALUES(?,?,?,?,?)",
        (rel, title, text, datetime.now().strftime("%Y-%m-%d"), src)
    )
    c.execute(
        "INSERT OR REPLACE INTO source_meta(path, sha256, indexed_at) VALUES(?,?,?)",
        (rel, sha, datetime.now().isoformat())
    )
    c.commit()
    c.close()
    status = "updated" if existing else "indexed"
    print(json.dumps({"path": rel, "status": status, "sha256": sha[:12],
                       "chars": len(text)}))


def rebuild():
    DB.unlink(missing_ok=True)
    init_db()
    vault = Path("vault")
    count = 0
    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA journal_mode=WAL")
    for f in sorted(vault.rglob("*.extracted.md")):
        text = f.read_text()
        rel = str(f.relative_to(vault))
        sha = file_sha256(f)
        src = _find_original(f)
        c.execute(
            "INSERT INTO sources(path, title, body, date, source_path) VALUES(?,?,?,?,?)",
            (rel, f.name.replace(".extracted.md", ""), text, "", src)
        )
        c.execute(
            "INSERT OR REPLACE INTO source_meta(path, sha256, indexed_at) VALUES(?,?,?)",
            (rel, sha, datetime.now().isoformat())
        )
        count += 1
    c.commit()
    c.close()
    print(json.dumps({"status": "rebuilt", "documents": count}))


def count():
    if not DB.exists():
        print("0")
        return
    c = sqlite3.connect(str(DB))
    n = c.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    c.close()
    print(n)


def hash_file(path_str):
    p = Path(path_str)
    if not p.exists():
        print(json.dumps({"error": f"{p} not found"}))
        sys.exit(1)
    print(json.dumps({"path": str(p), "sha256": file_sha256(p)}))


if __name__ == "__main__":
    if "--init" in sys.argv:
        init_db()
        print(json.dumps({"status": "initialized"}))
    elif "--rebuild" in sys.argv:
        rebuild()
    elif "--count" in sys.argv:
        count()
    elif "--hash" in sys.argv:
        idx = sys.argv.index("--hash")
        if idx + 1 < len(sys.argv):
            hash_file(sys.argv[idx + 1])
        else:
            print(json.dumps({"error": "usage: vault_index.py --hash <file>"}))
            sys.exit(1)
    elif len(sys.argv) >= 3:
        index_file(sys.argv[1], sys.argv[2])
    else:
        print("Usage: vault_index.py <file.extracted.md> <title>")
        print("       vault_index.py --init | --rebuild | --count | --hash <file>")
        sys.exit(1)
