"""Git-tracked correction recipes and validated tables.db recovery.

Git is the history store. Recipes are ordinary files, referenced by accepted
pages; unreferenced recipes from an interrupted write are never applied.
"""
import hashlib
import json
from contextlib import contextmanager, closing
from pathlib import Path

import structured_data as sd


@contextmanager
def original_table(wiki, fm):
    import naming
    import sweep
    sources = fm.get("sources", [])
    if not isinstance(sources, list) or not sources:
        raise ValueError("table has no source extraction")
    vault = wiki.parent / "vault"
    extraction = (vault / sources[0]).resolve()
    if not extraction.is_relative_to(vault.resolve()):
        raise ValueError("unsafe source extraction")
    source_fm, body = naming.read_frontmatter(extraction.read_text())
    if source_fm.get("sha256") != fm.get("extraction_sha"):
        raise ValueError("table source hash changed")
    data = None
    try:
        if source_fm.get("structured_version"):
            _, data = sd.load_extraction(extraction)
            tables = data["tables"]
        else:
            tables = sweep._parse_gfm_tables_from_body(body)
        index = int(fm["table_index"]) - 1
        if index < 0 or index >= len(tables):
            raise ValueError("source table index not found")
        yield tables[index], sources[0], sd.file_hash(extraction)
    finally:
        if data is not None:
            sd.close_data(data)


def correction_manifest(page, fm, headers, rows, review):
    """Publish sparse differences against the complete original table."""
    page = Path(page).resolve()
    wiki = page.parent.parent
    changes = []
    with original_table(wiki, fm) as (table, source, extraction_hash):
        if headers != table["headers"] or len(rows) != len(table["rows"]):
            raise ValueError("reviewed table shape differs from source; cannot anchor corrections")
        base_hash = sd.table_hash(headers, table["rows"])
        for i, (before, after) in enumerate(zip(table["rows"], rows), 1):
            for col, (old, new) in enumerate(zip(before, after)):
                if old != new:
                    if not isinstance(new, str):
                        raise ValueError("corrected cells must be literal strings")
                    changes.append({"row_idx": i, "column_idx": col, "header": headers[col],
                        "before": old, "after": new,
                        "source_locator": table.get("locators", [])[i - 1] if "locators" in table else None})
    manifest = {"version": "table-correction-v1", "table_stem": page.stem,
        "source_extraction": source, "source_sha256": fm["extraction_sha"],
        "extraction_sha256": extraction_hash, "table_index": int(fm["table_index"]),
        "base_hash": base_hash, "result_hash": sd.table_hash(headers, rows),
        "row_count": len(rows), "headers": headers, "changes": changes, "review": review}
    raw = (json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(raw).hexdigest()
    target = wiki / "_data" / "corrections" / (sha + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != raw:
            raise ValueError("correction artifact collision")
    else:
        sd.write_once(target, raw)
    return str(target.relative_to(wiki))


def replay_correction(wiki, page, fm):
    """Apply only the recipe referenced by the currently accepted wiki page."""
    import sqlite3
    path = (wiki / fm["correction_manifest"]).resolve()
    if not path.is_relative_to((wiki / "_data/corrections").resolve()) or path.is_symlink():
        raise ValueError("unsafe correction manifest")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != path.stem:
        raise ValueError("correction manifest hash mismatch")
    recipe = json.loads(raw)
    if recipe.get("version") != "table-correction-v1" or recipe["table_stem"] != page.stem:
        raise ValueError("correction manifest identity mismatch")
    with original_table(wiki, fm) as (table, source, extraction_hash):
        if (recipe["source_extraction"] != source or recipe["source_sha256"] != fm["extraction_sha"]
                or recipe["extraction_sha256"] != extraction_hash
                or recipe["table_index"] != int(fm["table_index"])
                or recipe["headers"] != table["headers"]
                or recipe["base_hash"] != sd.table_hash(table["headers"], table["rows"])):
            raise ValueError("correction source/shape anchors changed")
        with closing(sqlite3.connect(wiki.parent / ".curator/tables.db")) as db, db:
            for change in recipe["changes"]:
                i, col = change["row_idx"], change["column_idx"]
                if type(i) is not int or type(col) is not int or not (1 <= i <= len(table["rows"])) or not (0 <= col < len(table["headers"])):
                    raise ValueError("invalid correction cell location")
                if change["header"] != table["headers"][col]:
                    raise ValueError("correction column anchor changed")
                if "locators" in table and change["source_locator"] != table["locators"][i - 1]:
                    raise ValueError("correction row locator changed")
                row = db.execute("SELECT cells_json FROM _extracted_tables WHERE table_stem=? AND row_idx=?", (page.stem, i)).fetchone()
                if row is None:
                    raise ValueError("missing row during correction replay")
                cells = json.loads(row[0])
                if cells[col] != change["before"] or not isinstance(change["after"], str):
                    raise ValueError("correction original cell changed")
                cells[col] = change["after"]
                db.execute("UPDATE _extracted_tables SET cells_json=? WHERE table_stem=? AND row_idx=?",
                           (json.dumps(cells), page.stem, i))
            rows = (json.loads(r[0]) for r in db.execute("SELECT cells_json FROM _extracted_tables WHERE table_stem=? ORDER BY row_idx", (page.stem,)))
            if sd.table_hash(table["headers"], rows) != recipe["result_hash"]:
                raise ValueError("correction result hash mismatch")
    return len(recipe["changes"])


def checkpoint_reviews(wiki):
    """Adopt existing accepted reviewed rows into Git-trackable replay recipes."""
    import naming
    import sweep
    count = 0
    for page in sorted((wiki / "tables").glob("tab-*.md")):
        text = page.read_text()
        fm, _ = naming.read_frontmatter(text)
        if not fm.get("numeric_review_done"):
            continue
        if fm.get("correction_manifest"):
            continue
        rows, headers = sweep._read_extracted_rows(wiki, page.stem)
        ref = correction_manifest(page, fm, headers, rows,
            {"operation": "checkpoint-existing-review", "reviewed_at": fm["numeric_review_done"],
             "page_sha256": hashlib.sha256(text.encode()).hexdigest()})
        if page.read_text() != text:
            raise ValueError("page changed during review checkpoint")
        page.write_text(naming.set_frontmatter_field(text, "correction_manifest", ref))
        count += 1
    return {"checkpointed": count, "next": "commit accepted pages and wiki/_data with normal wiki Git workflow"}


def _call(fn, *args, **kwargs):
    import io
    from contextlib import redirect_stdout
    stream = io.StringIO()
    with redirect_stdout(stream):
        rc = fn(*args, **kwargs)
    result = json.loads(stream.getvalue())
    if rc not in (None, 0):
        raise ValueError(result.get("error", str(result)))
    return result


def _class_state(path):
    import sqlite3
    if not path.exists():
        return {}
    out = {}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if name.startswith(("_", "sqlite_")):
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            cols = [row[1] for row in db.execute(f"PRAGMA table_info({quoted})") if not row[1].startswith("_")]
            fragment = ",".join('"' + c.replace('"', '""') + '"' for c in cols)
            sha, count = hashlib.sha256(), 0
            for row in db.execute(f"SELECT {fragment} FROM {quoted} ORDER BY {fragment}"):
                sha.update((repr(tuple(row)) + "\n").encode())
                count += 1
            out[name] = (cols, count, sha.hexdigest())
    return out


def publish_database(source_path, target_path):
    """Publish one SQLite database in a single backup transaction."""
    import sqlite3
    import time
    deadline = time.monotonic() + 30
    def progress(status, remaining, total):
        if status != sqlite3.SQLITE_DONE and time.monotonic() > deadline:
            raise TimeoutError("database publication timed out; stop other writers and retry")
    with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(target_path)) as target:
        source.backup(target, pages=256, progress=progress)


def recover(wiki, dry_run=False):
    """Build off to the side; refuse missing recipes, drift, or unmanifested rows."""
    import os
    import shutil
    import sqlite3
    import tempfile
    import naming
    import sweep
    import tables
    import record_index
    wiki = Path(wiki).resolve()
    root = wiki.parent
    live = root / ".curator/tables.db"
    before = _class_state(live)
    inputs = {p: sd.file_hash(p) for p in wiki.rglob("*")
              if p.is_file() and ".git" not in p.parts and p.suffix in (".md", ".json")}
    source_inputs = {}
    for path in (root / "vault").glob("*.extracted.md"):
        source_inputs[path] = sd.file_hash(path)
        fm, _ = naming.read_frontmatter(path.read_text())
        original = path.parent / fm["kept_as"] if fm.get("kept_as") else Path(fm.get("source_path", ""))
        if original.is_file():
            source_inputs[original] = sd.file_hash(original)
    reviewed = []
    for page in sorted((wiki / "tables").glob("tab-*.md")):
        fm, _ = naming.read_frontmatter(page.read_text())
        if fm.get("numeric_review_done") and not fm.get("correction_manifest"):
            raise ValueError(f"{page.name}: reviewed rows have no replay recipe; run checkpoint-reviews while the old database is available")
        if fm.get("correction_manifest"):
            reviewed.append((page.name, fm))
    live.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tables-recovery-", dir=live.parent) as tmp:
        stage = Path(tmp)
        shutil.copytree(wiki, stage / "wiki", ignore=shutil.ignore_patterns(".git"))
        def link_or_copy(src, dst):
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        shutil.copytree(root / "vault", stage / "vault", copy_function=link_or_copy,
                        ignore=shutil.ignore_patterns("*.db", "*.db-wal", "*.db-shm"))
        (stage / ".curator").mkdir()
        # Derived pages are disposable in this temporary copy. The live wiki
        # (including Git and accepted review annotations) is never rewritten.
        for page in (stage / "wiki/tables").glob("tab-*.md"):
            page.unlink()
        previous_cwd, previous_db, previous_records = Path.cwd(), tables.DB_PATH, record_index.DB_PATH
        try:
            os.chdir(stage)
            tables.DB_PATH = stage / ".curator/tables.db"
            record_index.DB_PATH = stage / ".curator/records.db"
            promoted = _call(sweep.cmd_promote_extracted_tables, Path("wiki"))
            for page in (wiki / "tables").glob("tab-*.md"):
                if not (stage / "wiki/tables" / page.name).exists():
                    raise ValueError(f"cannot reconstruct extracted table: {page.name}")
            corrections = sum(replay_correction(stage / "wiki", stage / "wiki/tables" / name, fm)
                              for name, fm in reviewed)
            for entity in sorted(Path("wiki/entities").rglob("*.md")):
                if tables._load_entity_schema(entity):
                    _call(tables.cmd_sync, entity)
            manifests = sorted(Path("wiki/_data/imports").glob("*.json"))
            imported = [_call(tables.cmd_import_dataset, p, recovery=True) for p in manifests]
            indexed = record_index.rebuild()
            if not tables.DB_PATH.exists():
                conn = tables._connect()
                conn.commit()
                conn.close()
            rebuilt = _class_state(tables.DB_PATH)
            for name, state in before.items():
                if rebuilt.get(name) != state:
                    raise ValueError(f"{name}: recovery would lose or alter existing class rows; missing/stale import manifests")
            with closing(sqlite3.connect(tables.DB_PATH)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("rebuilt database failed integrity check")
            if any(not p.exists() or sd.file_hash(p) != sha for p, sha in inputs.items()):
                raise ValueError("wiki replay inputs changed during recovery")
            if any(not p.exists() or sd.file_hash(p) != sha for p, sha in source_inputs.items()):
                raise ValueError("vault replay inputs changed during recovery")
            if _class_state(live) != before:
                raise ValueError("live class rows changed during recovery")
            index_status = {"status": "validated" if dry_run else "rebuilt", "records": indexed["records"]}
            if not dry_run:
                # SQLite's backup transaction publishes atomically, preserving
                # the live database inode and handling existing WAL sidecars.
                publish_database(tables.DB_PATH, live)
                try:
                    publish_database(record_index.DB_PATH, live.parent / "records.db")
                except (OSError, sqlite3.Error) as exc:
                    # Tables are already recovered. Report a retryable cache
                    # failure truthfully, rather than claiming nothing applied.
                    index_status = {"status": "error", "error": str(exc),
                                    "retry": "datasets.py index-records --rebuild"}
            return {"status": "validated" if dry_run else "recovered", "dry_run": dry_run,
                    "extracted_tables": promoted["created"], "corrections": corrections,
                    "import_manifests": len(imported), "class_tables": len(rebuilt),
                    "indexed_records": indexed["records"] if index_status["status"] != "error" else 0,
                    "record_index": index_status}
        finally:
            os.chdir(previous_cwd)
            tables.DB_PATH = previous_db
            record_index.DB_PATH = previous_records
