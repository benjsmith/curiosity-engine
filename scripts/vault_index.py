#!/usr/bin/env python3
"""Index extracted text into the vault's FTS5 search database.

Usage:
    python3 vault_index.py <extracted_file.md> "<title>"
    python3 vault_index.py --init              # create empty DB
    python3 vault_index.py --rebuild           # reindex all .extracted.md files
    python3 vault_index.py --count             # print document count
    python3 vault_index.py --hash <file>       # print SHA-256 of file
    python3 vault_index.py --reembed           # recompute embeddings with current model

Dedup: inserts are keyed on `path`. Re-indexing the same path replaces
the old row rather than creating a duplicate.

Optional semantic layer: if `.curator/config.json` has
`embedding_enabled: true`, every indexed source also gets a vector
embedding via `sentence-transformers` + `sqlite-vec` stored alongside
the FTS5 row. Default model is `all-MiniLM-L6-v2` (384-dim, ~80MB).
Requires `uv pip install sentence-transformers sqlite-vec`.
"""

import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime

# macOS system Python's sqlite3 is often compiled without
# --enable-loadable-sqlite-extensions, so conn.load_extension is missing —
# which breaks sqlite-vec. pysqlite3-binary is a drop-in with extensions
# enabled. Prefer it when available; fall back to stdlib for workspaces
# that don't have embeddings turned on (and therefore never need to load
# an extension).
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

DB = Path("vault/vault.db")
CONFIG_PATH = Path(".curator/config.json")
DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _embedding_enabled() -> bool:
    return bool(_load_config().get("embedding_enabled"))


def _load_embedder():
    """Return (model, sqlite_vec, model_name, dim). Fails fast if deps missing.

    Embeddings are opt-in (`embedding_enabled=true` in config), so a missing
    dep means the user opted in without running the install. Hard-fail with
    a clear message beats silent skip — the user won't notice missing
    vectors until their hybrid search mysteriously returns FTS5 only.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import sqlite_vec
    except ImportError as e:
        sys.stderr.write(
            "vault_index: embedding_enabled=true but "
            f"sentence-transformers/sqlite-vec not installed ({e}).\n"
            "  Install: uv pip install sentence-transformers sqlite-vec\n"
            "  Or set embedding_enabled=false in .curator/config.json\n"
        )
        sys.exit(2)
    cfg = _load_config()
    model_name = cfg.get("embedding_model", DEFAULT_EMBED_MODEL)
    model = SentenceTransformer(model_name)
    dim = model.get_sentence_embedding_dimension()
    return model, sqlite_vec, model_name, dim


def _init_embed_tables(conn, sqlite_vec_mod, dim: int) -> None:
    sqlite_vec_mod.load(conn)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS source_embeddings "
        f"USING vec0(embedding float[{dim}])"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embedding_meta (
            path TEXT PRIMARY KEY,
            vec_id INTEGER UNIQUE,
            model TEXT,
            indexed_at TEXT
        )
    """)


def _embed_and_upsert(conn, sqlite_vec_mod, model, model_name: str,
                       path_rel: str, text: str) -> None:
    """Embed `text` and insert/update the row keyed by `path_rel`.

    MiniLM has 512-token context (~2000 chars). An 8k-char cap gives
    headroom with graceful internal truncation and works for longer-
    context alternatives (nomic-embed at 8192 tokens) without
    re-architecting. Normalize to unit length so cosine == dot product
    and sqlite-vec's built-in L2 distance is equivalent to cosine.
    """
    vec = model.encode(text[:8000], normalize_embeddings=True).tolist()
    vec_bytes = sqlite_vec_mod.serialize_float32(vec)
    existing = conn.execute(
        "SELECT vec_id FROM embedding_meta WHERE path=?", (path_rel,)
    ).fetchone()
    now = datetime.now().isoformat()
    if existing:
        conn.execute("UPDATE source_embeddings SET embedding=? WHERE rowid=?",
                      (vec_bytes, existing[0]))
        conn.execute(
            "UPDATE embedding_meta SET model=?, indexed_at=? WHERE path=?",
            (model_name, now, path_rel)
        )
    else:
        cur = conn.execute(
            "INSERT INTO source_embeddings(embedding) VALUES(?)", (vec_bytes,)
        )
        conn.execute(
            "INSERT INTO embedding_meta(path, vec_id, model, indexed_at) "
            "VALUES(?,?,?,?)",
            (path_rel, cur.lastrowid, model_name, now)
        )


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


def index_file_result(path_str, title):
    """Index one file, returning a result dict. No stdout side effects, no exits.

    Library-safe wrapper so local_ingest.py (and other callers) can chain to
    indexing without subprocess overhead or output munging. Returns
    {status: "error"|"unchanged"|"indexed"|"updated", ...details} with
    status="error" on missing file instead of sys.exit.
    """
    init_db()
    p = Path(path_str)
    if not p.exists():
        return {"path": path_str, "status": "error", "error": "not found"}
    text = p.read_text()
    rel = str(p.relative_to(Path("vault"))) if str(p).startswith("vault") else str(p)
    sha = file_sha256(p)
    src = _find_original(p)

    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA journal_mode=WAL")

    embedder = None
    if _embedding_enabled():
        model, vec_mod, model_name, dim = _load_embedder()
        _init_embed_tables(c, vec_mod, dim)
        embedder = (model, vec_mod, model_name)

    existing = c.execute(
        "SELECT sha256 FROM source_meta WHERE path = ?", (rel,)
    ).fetchone()
    if existing and existing[0] == sha:
        c.close()
        return {"path": rel, "status": "unchanged", "sha256": sha[:12]}

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
    if embedder is not None:
        _embed_and_upsert(c, embedder[1], embedder[0], embedder[2], rel, text)
    c.commit()
    c.close()
    status = "updated" if existing else "indexed"
    out = {"path": rel, "status": status, "sha256": sha[:12], "chars": len(text)}
    if embedder is not None:
        out["embedded"] = True
    return out


def index_file(path_str, title):
    """CLI wrapper: calls index_file_result, prints the result, exits on error."""
    out = index_file_result(path_str, title)
    if out.get("status") == "error":
        print(json.dumps({"error": out.get("error", "unknown")}))
        sys.exit(1)
    print(json.dumps(out))


def rebuild():
    DB.unlink(missing_ok=True)
    init_db()
    vault = Path("vault")
    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA journal_mode=WAL")

    embedder = None
    if _embedding_enabled():
        model, vec_mod, model_name, dim = _load_embedder()
        _init_embed_tables(c, vec_mod, dim)
        embedder = (model, vec_mod, model_name)

    # First pass: FTS5 insert + collect texts for batched embedding.
    pending_paths = []
    pending_texts = []
    files = sorted(vault.rglob("*.extracted.md"))
    for f in files:
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
        if embedder is not None:
            pending_paths.append(rel)
            pending_texts.append(text[:8000])

    # Second pass: batched embedding. model.encode handles batches natively
    # much faster than one-at-a-time.
    if embedder is not None and pending_texts:
        model, vec_mod, model_name = embedder
        vecs = model.encode(
            pending_texts, normalize_embeddings=True,
            batch_size=32, show_progress_bar=False
        ).tolist()
        now = datetime.now().isoformat()
        for rel, vec in zip(pending_paths, vecs):
            vec_bytes = vec_mod.serialize_float32(vec)
            cur = c.execute(
                "INSERT INTO source_embeddings(embedding) VALUES(?)", (vec_bytes,)
            )
            c.execute(
                "INSERT INTO embedding_meta(path, vec_id, model, indexed_at) "
                "VALUES(?,?,?,?)",
                (rel, cur.lastrowid, model_name, now)
            )

    c.commit()
    c.close()
    out = {"status": "rebuilt", "documents": len(files)}
    if embedder is not None:
        out["embedded"] = len(pending_paths)
    print(json.dumps(out))


def reembed():
    """Recompute embeddings for every existing row with the current model.

    Use after swapping `embedding_model` in config.json — the FTS5 index
    stays; only vectors are regenerated. Drops the existing
    source_embeddings table (dimensions may differ across models) and
    rebuilds from sources.body.
    """
    if not _embedding_enabled():
        print(json.dumps({
            "error": "embedding_enabled=false in .curator/config.json",
            "hint": "set embedding_enabled=true before running --reembed",
        }))
        sys.exit(2)
    if not DB.exists():
        print(json.dumps({"error": "vault.db missing; run --rebuild first"}))
        sys.exit(2)
    model, vec_mod, model_name, dim = _load_embedder()
    c = sqlite3.connect(str(DB))
    c.execute("PRAGMA journal_mode=WAL")
    vec_mod.load(c)
    # Dimensions may differ; wipe and recreate both tables.
    c.execute("DROP TABLE IF EXISTS source_embeddings")
    c.execute("DELETE FROM embedding_meta")
    _init_embed_tables(c, vec_mod, dim)

    rows = c.execute("SELECT path, body FROM sources").fetchall()
    paths = [r[0] for r in rows]
    texts = [(r[1] or "")[:8000] for r in rows]
    vecs = model.encode(
        texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False
    ).tolist()
    now = datetime.now().isoformat()
    for rel, vec in zip(paths, vecs):
        vec_bytes = vec_mod.serialize_float32(vec)
        cur = c.execute(
            "INSERT INTO source_embeddings(embedding) VALUES(?)", (vec_bytes,)
        )
        c.execute(
            "INSERT INTO embedding_meta(path, vec_id, model, indexed_at) "
            "VALUES(?,?,?,?)",
            (rel, cur.lastrowid, model_name, now)
        )
    c.commit()
    c.close()
    print(json.dumps({
        "status": "reembedded", "documents": len(paths),
        "model": model_name, "dim": dim,
    }))


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
    elif "--reembed" in sys.argv:
        reembed()
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
        print("       vault_index.py --init | --rebuild | --reembed | --count | --hash <file>")
        sys.exit(1)
