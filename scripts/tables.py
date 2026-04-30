#!/usr/bin/env python3
"""tables.py — class-entity tables layer.

Adds a structured-data tier to the wiki. Each class-entity (an entity
page describing many instances: deals, patients, contracts, matters)
declares a table in its frontmatter; this module creates, manages, and
queries that table.

Design principles
-----------------
- The entity page is the single source of truth for schema. SQLite
  schema is derived; if they drift, we abort and the user reconciles.
- Row data lives in `.curator/tables.db` (not git-tracked). Every row
  carries provenance (`vault:...` or `log:...`) so the database is
  deterministically rebuildable from the git-tracked corpus.
- Schema evolution goes through the normal wiki ratchet: edit the
  entity page's frontmatter, score_diff + reviewer approve, then sync
  runs to ALTER the table. No DDL outside this path.
- Citations from evidence/analyses use `(table:<name>#id=<id>)` syntax.
  score_diff verifies the row still exists at commit time.
- Storage layers are distinct: vault.db (FTS5 index, unchanged),
  graph.kuzu (relationships, extended by graph.py), tables.db (rows,
  this module). Don't confuse them.

Subcommands
-----------
    tables.py sync <entity-path>
        Read the entity page's `table:` frontmatter; CREATE or ALTER
        the SQLite table to match. Additive changes only (new columns);
        rename/drop/enum-remove require `migrate` with explicit
        confirmation (Phase 4).

    tables.py insert <table> <json>
        Insert one row. JSON payload must include all non-nullable
        columns plus `_provenance` (`vault:...` or `log:...`).
        Validated against schema (types, enums, primary key).

    tables.py update <table> <id> <json>
        Update fields on an existing row. Primary key is the selector.
        Provenance updated to reflect the update event.

    tables.py query <table> [--where WHERE] [--args JSON] [--limit N]
        Parameterised read-only query. WHERE is a SQL fragment; --args
        is a JSON list of parameter values. Returns rows as JSON.
        Agents get SQL power without SQL-injection risk via parameter
        binding.

    tables.py schema <table>
        Print the current live schema of the table (from SQLite) and
        compare against the MD-declared schema. Flag drift.

    tables.py list
        List all tables with row counts, schema hashes, and sync status.

    tables.py rebuild <table>
        Drop and re-extract from vault + log. Used when the extraction
        recipe changes. Phase 3 feature; stub here.

    tables.py extracted-query <stem> [--where WHERE] [--args JSON] [--limit N]
        Query rows of a single extracted-table (`wiki/tables/tab-*.md`)
        from the `_extracted_tables` system table populated by
        `sweep.py promote-extracted-tables`. Returns rows as JSON with
        the table's headers unpacked as keys. WHERE applies to the
        system columns only (row_idx, source_stub, source_extraction,
        extraction_sha) — cell-value filters belong in the caller.

    tables.py extracted-list [--source-stub STUB]
        List all extracted tables with row counts and source citation
        info, optionally filtered to a single source-stub stem.

Never-installed-DDL path
------------------------
Agents can only reach schema changes through: (a) editing the entity
page's frontmatter, (b) that edit passing the wiki ratchet, (c)
`tables.py sync` applying the ALTER automatically. No `CREATE TABLE` or
`DROP COLUMN` is reachable from any other surface. This preserves the
"agent can't edit its own reward function" property we extended to all
skill scripts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple

# macOS system Python's sqlite3 is typically built without
# --enable-loadable-sqlite-extensions. pysqlite3 is a drop-in.
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "tables.py requires PyYAML: uv pip install pyyaml\n"
    )
    sys.exit(2)


DB_PATH = Path(".curator/tables.db")


# ---- schema parsing and normalisation ----

def _load_entity_schema(entity_path: Path) -> Optional[dict]:
    """Read the `table:` frontmatter block from an entity page. None if absent."""
    if not entity_path.exists():
        return None
    text = entity_path.read_text()
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    fm_text = text[4:end]
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        sys.stderr.write(f"tables.py: YAML error in {entity_path}: {e}\n")
        return None
    return fm.get("table")


def _schema_hash(schema: dict) -> str:
    """Canonicalised hash of schema — used for drift detection."""
    canon = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


_SQLITE_TYPE_MAP = {
    "text": "TEXT", "str": "TEXT", "string": "TEXT",
    "int": "INTEGER", "integer": "INTEGER",
    "real": "REAL", "float": "REAL", "number": "REAL",
    "bool": "INTEGER", "boolean": "INTEGER",
    "date": "TEXT", "datetime": "TEXT", "timestamp": "TEXT",
    "enum": "TEXT", "wikilink": "TEXT", "ref": "TEXT",
    "json": "TEXT", "blob": "BLOB",
}


def _sqlite_type(col_type: str) -> str:
    """Map frontmatter column types to SQLite storage types."""
    return _SQLITE_TYPE_MAP.get(col_type.lower(), "TEXT")


def _normalize_columns(schema: dict) -> List[dict]:
    """Normalize column declarations.

    Accepts:
        - {name: "id", type: "text", pk: true}
        - {name: "stage", type: "enum", values: [a, b, c]}
    Returns list of dicts with keys: name, type, pk, nullable, values.
    """
    cols = schema.get("columns", [])
    out = []
    for c in cols:
        if not isinstance(c, dict) or "name" not in c:
            continue
        out.append({
            "name": c["name"],
            "type": c.get("type", "text"),
            "pk": bool(c.get("pk", False)),
            "nullable": bool(c.get("nullable", True)),
            "values": c.get("values"),
            "default": c.get("default"),
            # _alias / alias points at a previous column name so sync can
            # apply a RENAME COLUMN instead of drop+add. Preserving either
            # spelling — YAML authors sometimes avoid leading-underscore keys.
            "_alias": c.get("_alias") or c.get("alias"),
        })
    return out


def _primary_key_col(columns: List[dict]) -> Optional[str]:
    for c in columns:
        if c["pk"]:
            return c["name"]
    return None


# ---- DB lifecycle ----

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _schema_meta (
            table_name TEXT PRIMARY KEY,
            schema_hash TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            entity_page TEXT NOT NULL,
            synced_at TEXT NOT NULL
        )
    """)
    # Audit log: tracks how many rows have changed since the last
    # citation-consistency audit, and when the audit last ran. Feeds
    # into epoch_summary's table_citation_risk metric so CURATE can
    # schedule audit waves adaptively.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _audit_log (
            table_name TEXT PRIMARY KEY,
            last_audit_at TEXT,
            row_changes_since_last INTEGER NOT NULL DEFAULT 0,
            audit_period_days INTEGER NOT NULL DEFAULT 30
        )
    """)
    return conn


def _bump_change_counter(conn, table_name: str) -> None:
    """Record a row change for audit-risk tracking."""
    conn.execute("""
        INSERT INTO _audit_log (table_name, row_changes_since_last, audit_period_days)
        VALUES (?, 1, 30)
        ON CONFLICT(table_name) DO UPDATE SET
            row_changes_since_last = row_changes_since_last + 1
    """, (table_name,))


_PROVENANCE_RE = re.compile(r"^(vault:|log:)\S+$")


def _validate_provenance(provenance: str) -> Tuple[bool, str]:
    if not isinstance(provenance, str):
        return False, "provenance must be a string"
    if not _PROVENANCE_RE.match(provenance):
        return False, "provenance must start with 'vault:' or 'log:' and contain no whitespace"
    return True, ""


def _validate_row(payload: dict, columns: List[dict]) -> Tuple[bool, str]:
    """Validate a payload against column schema. Returns (ok, error_message)."""
    col_by_name = {c["name"]: c for c in columns}
    # Required (non-nullable) columns must be present.
    for c in columns:
        if not c["nullable"] and c["name"] not in payload and c.get("default") is None:
            return False, f"missing required column '{c['name']}'"
    # Unknown keys (except the reserved _-prefixed fields) are errors.
    for key in payload:
        if key.startswith("_"):
            continue
        if key not in col_by_name:
            return False, f"unknown column '{key}' not in schema"
    # Enum value check.
    for key, value in payload.items():
        if key.startswith("_"):
            continue
        col = col_by_name.get(key)
        if col and col["type"].lower() == "enum":
            if col["values"] and value is not None and value not in col["values"]:
                return False, (f"column '{key}' value {value!r} not in enum "
                                f"{col['values']}")
    return True, ""


# ---- subcommands ----

def cmd_sync(entity_path: Path, confirm_human: bool = False) -> int:
    """Apply the entity page's `table:` schema to the SQLite table.

    Safe operations (additive) run automatically: create new table,
    add columns, extend enum values.

    Destructive operations (drop column, remove enum value where rows
    use it) require `--confirm-human`. Rationale: the wiki ratchet has
    already approved the intent (schema change is in the entity page,
    score_diff + reviewer approved it). `--confirm-human` confirms the
    actual destructive action one more time — the gap between "I
    approve the idea" and "I understand this will delete data" is
    where mistakes happen.

    Column renames use an `_alias: <old_name>` annotation. If the
    alias resolves to an existing live column and the new name doesn't
    exist, the column is renamed via ALTER TABLE RENAME COLUMN.
    """
    schema = _load_entity_schema(entity_path)
    if not schema:
        print(json.dumps({"error": f"no `table:` block in {entity_path}"}))
        return 1
    name = schema.get("name")
    if not name:
        print(json.dumps({"error": "schema missing `name` field"}))
        return 1
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        print(json.dumps({"error": f"invalid table name {name!r}"}))
        return 1
    columns = _normalize_columns(schema)
    if not columns:
        print(json.dumps({"error": "schema has no columns"}))
        return 1

    current_hash = _schema_hash(schema)
    conn = _connect()
    prev_row = conn.execute(
        "SELECT schema_json, schema_hash FROM _schema_meta WHERE table_name = ?",
        (name,)
    ).fetchone()
    if prev_row and prev_row[1] == current_hash:
        conn.close()
        print(json.dumps({"table": name, "status": "unchanged",
                          "hash": current_hash}))
        return 0

    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None

    actions = []
    if not table_exists:
        col_defs = []
        for c in columns:
            sqlite_type = _sqlite_type(c["type"])
            pk_frag = " PRIMARY KEY" if c["pk"] else ""
            nn_frag = "" if c["nullable"] else " NOT NULL"
            col_defs.append(f'"{c["name"]}" {sqlite_type}{pk_frag}{nn_frag}')
        col_defs.append('"_provenance" TEXT NOT NULL')
        col_defs.append('"_inserted_at" TEXT NOT NULL')
        col_defs.append('"_updated_at" TEXT')
        col_defs.append('"_schema_version" TEXT NOT NULL')
        ddl = f'CREATE TABLE "{name}" ({", ".join(col_defs)})'
        conn.execute(ddl)
        actions.append({"op": "create_table"})
    else:
        live_cols = {row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')}
        declared_names = {c["name"] for c in columns}
        # 1) Rename path — an alias pointing at an existing live column,
        #    where the new name isn't live yet.
        renamed = set()
        for c in columns:
            alias = c.get("_alias") or c.get("alias")
            if alias and alias in live_cols and c["name"] not in live_cols:
                conn.execute(
                    f'ALTER TABLE "{name}" RENAME COLUMN "{alias}" TO "{c["name"]}"'
                )
                live_cols.discard(alias)
                live_cols.add(c["name"])
                renamed.add(c["name"])
                actions.append({"op": "rename_column",
                                  "from": alias, "to": c["name"]})
        # 2) Add path — new columns not in the live table.
        added = []
        for c in columns:
            if c["name"] in live_cols:
                continue
            sqlite_type = _sqlite_type(c["type"])
            conn.execute(
                f'ALTER TABLE "{name}" ADD COLUMN "{c["name"]}" {sqlite_type}'
            )
            added.append(c["name"])
            actions.append({"op": "add_column", "name": c["name"]})
        # 3) Drop path — columns in the live table but not declared.
        #    Reserved columns (_provenance, _inserted_at, ...) are
        #    never dropped regardless of declaration.
        reserved = {"_provenance", "_inserted_at", "_updated_at", "_schema_version"}
        user_live_cols = live_cols - reserved
        to_drop = user_live_cols - declared_names
        if to_drop and not confirm_human:
            conn.close()
            print(json.dumps({
                "error": "destructive sync blocked",
                "table": name,
                "columns_to_drop": sorted(to_drop),
                "reason": ("columns present in live table but removed from "
                            "the entity-page schema. Rerun with "
                            "--confirm-human if you understand this will "
                            "drop the column and its data."),
            }))
            return 2
        for col in to_drop:
            conn.execute(f'ALTER TABLE "{name}" DROP COLUMN "{col}"')
            actions.append({"op": "drop_column", "name": col})

    # 4) Enum narrowing — check each declared enum against the previous
    #    schema's values. Removing a value used by existing rows
    #    requires --confirm-human.
    if prev_row:
        try:
            prev_schema = json.loads(prev_row[0])
        except json.JSONDecodeError:
            prev_schema = None
    else:
        prev_schema = None
    if prev_schema:
        prev_cols = {c["name"]: c for c in _normalize_columns(prev_schema)}
        for c in columns:
            if c["type"].lower() != "enum":
                continue
            prev_c = prev_cols.get(c["name"])
            if not prev_c or prev_c["type"].lower() != "enum":
                continue
            prev_vals = set(prev_c.get("values") or [])
            new_vals = set(c.get("values") or [])
            removed_vals = prev_vals - new_vals
            if not removed_vals:
                continue
            if not table_exists:
                continue
            placeholders = ",".join("?" * len(removed_vals))
            try:
                using = conn.execute(
                    f'SELECT COUNT(*) FROM "{name}" '
                    f'WHERE "{c["name"]}" IN ({placeholders})',
                    list(removed_vals)
                ).fetchone()[0]
            except sqlite3.Error:
                using = 0
            if using and not confirm_human:
                conn.close()
                print(json.dumps({
                    "error": "enum narrowing blocked",
                    "table": name,
                    "column": c["name"],
                    "removed_values": sorted(removed_vals),
                    "rows_using_removed_value": using,
                    "reason": ("enum values removed while rows still use "
                                "them. Rerun with --confirm-human to proceed, "
                                "or update those rows first."),
                }))
                return 2
            actions.append({"op": "narrow_enum", "column": c["name"],
                              "removed_values": sorted(removed_vals),
                              "rows_affected": using})

    conn.execute("""
        INSERT OR REPLACE INTO _schema_meta
            (table_name, schema_hash, schema_json, entity_page, synced_at)
        VALUES (?, ?, ?, ?, ?)
    """, (name, current_hash, json.dumps(schema), str(entity_path),
           datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    if not actions:
        status = "up_to_date"
    elif any(a["op"] == "create_table" for a in actions):
        status = "created"
    else:
        status = "altered"
    print(json.dumps({"table": name, "status": status, "hash": current_hash,
                       "columns": len(columns), "actions": actions}))
    return 0


def cmd_verify(name: Optional[str], wiki_dir: Path) -> int:
    """Check row provenance integrity.

    For every row in the scoped table(s), verify the `_provenance`
    points at something that exists:
      - `vault:<path>` → file must exist under `vault/`
      - `log:<id>` → accept (log entries are rarely deleted; future
        tightening could regex-check the log)

    Reports orphan rows (rows whose vault provenance no longer
    resolves). Does not delete — just reports. A retention-sweep or
    manual cleanup step is where deletion happens.
    """
    conn = _connect()
    tables = []
    if name:
        tables = [name]
    else:
        rows = conn.execute("SELECT table_name FROM _schema_meta").fetchall()
        tables = [r[0] for r in rows]
    report = []
    vault_dir = wiki_dir.parent / "vault"
    for t in tables:
        try:
            rows = conn.execute(
                f'SELECT "_provenance", '
                f'  (SELECT name FROM pragma_table_info("{t}") WHERE pk=1 LIMIT 1) '
                f'FROM "{t}"'
            ).fetchall()
        except sqlite3.Error:
            continue
        # Second column above is a scalar subquery — always the same per row.
        # Re-run a cleaner version for row_id resolution.
        try:
            pragma = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            pk = next((r[1] for r in pragma if r[5]), None)
            if not pk:
                continue
            prov_rows = conn.execute(
                f'SELECT "{pk}", "_provenance" FROM "{t}"'
            ).fetchall()
        except sqlite3.Error:
            continue
        orphans = []
        valid = 0
        for row_id, provenance in prov_rows:
            if not provenance:
                orphans.append({"row_id": row_id, "reason": "no provenance"})
                continue
            if provenance.startswith("vault:"):
                p = provenance.split(":", 1)[1]
                if not (vault_dir / p).exists():
                    orphans.append({"row_id": row_id,
                                      "provenance": provenance,
                                      "reason": "vault file missing"})
                else:
                    valid += 1
            elif provenance.startswith("log:"):
                # Log entries not individually verified here; accept.
                valid += 1
            else:
                orphans.append({"row_id": row_id,
                                  "provenance": provenance,
                                  "reason": "unrecognised provenance prefix"})
        report.append({
            "table": t,
            "total_rows": len(prov_rows),
            "valid": valid,
            "orphans": orphans,
        })
    conn.close()
    print(json.dumps({"wiki_dir": str(wiki_dir), "tables": report}, indent=2))
    return 0 if all(not r["orphans"] for r in report) else 1


def _load_table_schema(conn, name: str) -> Optional[Tuple[dict, str]]:
    """Load the synced schema for a table from _schema_meta. Returns (schema, hash) or None."""
    row = conn.execute(
        "SELECT schema_json, schema_hash FROM _schema_meta WHERE table_name = ?",
        (name,)
    ).fetchone()
    if not row:
        return None
    return json.loads(row[0]), row[1]


def cmd_insert(name: str, payload_json: str) -> int:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON payload: {e}"}))
        return 2
    if not isinstance(payload, dict):
        print(json.dumps({"error": "payload must be a JSON object"}))
        return 2
    provenance = payload.pop("_provenance", None)
    if not provenance:
        print(json.dumps({"error": "payload missing `_provenance`"}))
        return 2
    ok, msg = _validate_provenance(provenance)
    if not ok:
        print(json.dumps({"error": f"invalid provenance: {msg}"}))
        return 2

    conn = _connect()
    schema_info = _load_table_schema(conn, name)
    if not schema_info:
        conn.close()
        print(json.dumps({"error": f"table '{name}' not synced yet; "
                           f"run `tables.py sync <entity-path>` first"}))
        return 2
    schema, schema_version = schema_info
    columns = _normalize_columns(schema)
    ok, msg = _validate_row(payload, columns)
    if not ok:
        conn.close()
        print(json.dumps({"error": f"validation failed: {msg}"}))
        return 2

    pk = _primary_key_col(columns)
    if pk and pk not in payload:
        conn.close()
        print(json.dumps({"error": f"payload missing primary key '{pk}'"}))
        return 2

    col_names = list(payload.keys()) + ["_provenance", "_inserted_at", "_schema_version"]
    values = list(payload.values()) + [
        provenance,
        datetime.now(timezone.utc).isoformat(),
        schema_version,
    ]
    placeholders = ", ".join("?" * len(values))
    col_fragment = ", ".join(f'"{c}"' for c in col_names)
    sql = f'INSERT INTO "{name}" ({col_fragment}) VALUES ({placeholders})'
    try:
        conn.execute(sql, values)
    except sqlite3.IntegrityError as e:
        conn.close()
        print(json.dumps({"error": f"insert conflict: {e}"}))
        return 2
    _bump_change_counter(conn, name)
    conn.commit()
    row_id = payload.get(pk) if pk else None
    conn.close()
    print(json.dumps({"table": name, "status": "inserted", "id": row_id}))
    return 0


def cmd_update(name: str, row_id: str, payload_json: str) -> int:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON payload: {e}"}))
        return 2
    provenance = payload.pop("_provenance", None)
    if not provenance:
        print(json.dumps({"error": "update payload missing `_provenance` "
                           "(the evidence or log entry justifying the change)"}))
        return 2
    ok, msg = _validate_provenance(provenance)
    if not ok:
        print(json.dumps({"error": f"invalid provenance: {msg}"}))
        return 2

    conn = _connect()
    schema_info = _load_table_schema(conn, name)
    if not schema_info:
        conn.close()
        print(json.dumps({"error": f"table '{name}' not synced"}))
        return 2
    schema, schema_version = schema_info
    columns = _normalize_columns(schema)
    pk = _primary_key_col(columns)
    if not pk:
        conn.close()
        print(json.dumps({"error": f"table '{name}' has no primary key; "
                           "cannot update by id"}))
        return 2
    # Can't update primary key via this path.
    payload.pop(pk, None)
    ok, msg = _validate_row(payload, columns)
    if not ok:
        conn.close()
        print(json.dumps({"error": f"validation failed: {msg}"}))
        return 2

    if not payload:
        conn.close()
        print(json.dumps({"error": "payload had no updatable fields"}))
        return 2

    set_fragment = ", ".join(f'"{k}" = ?' for k in payload)
    set_fragment += ', "_provenance" = ?, "_updated_at" = ?'
    values = list(payload.values()) + [
        provenance,
        datetime.now(timezone.utc).isoformat(),
    ]
    sql = f'UPDATE "{name}" SET {set_fragment} WHERE "{pk}" = ?'
    cur = conn.execute(sql, values + [row_id])
    changed = cur.rowcount
    if changed:
        _bump_change_counter(conn, name)
    conn.commit()
    conn.close()
    print(json.dumps({"table": name, "status": "updated" if changed else "not_found",
                      "id": row_id, "rows_changed": changed}))
    return 0 if changed else 1


def cmd_query(name: str, where: Optional[str], args_json: Optional[str],
               limit: int) -> int:
    conn = _connect()
    schema_info = _load_table_schema(conn, name)
    if not schema_info:
        conn.close()
        print(json.dumps({"error": f"table '{name}' not synced"}))
        return 2
    try:
        args = json.loads(args_json) if args_json else []
    except json.JSONDecodeError as e:
        conn.close()
        print(json.dumps({"error": f"invalid --args JSON: {e}"}))
        return 2
    if not isinstance(args, list):
        conn.close()
        print(json.dumps({"error": "--args must be a JSON array"}))
        return 2
    sql = f'SELECT * FROM "{name}"'
    if where:
        # Strip obvious injection vectors. Semicolons and DDL/DML keywords
        # are blocked; the agent can still construct complex WHERE clauses
        # via parameter binding.
        forbidden = ("--", ";", "drop ", "delete ", "update ", "insert ",
                      "alter ", "attach ", "pragma ")
        low = where.lower()
        if any(f in low for f in forbidden):
            conn.close()
            print(json.dumps({"error": f"forbidden keyword in where clause"}))
            return 2
        sql += f" WHERE {where}"
    sql += f" LIMIT {int(limit)}"
    try:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
        col_names = [d[0] for d in cur.description]
    except sqlite3.Error as e:
        conn.close()
        print(json.dumps({"error": f"query error: {e}"}))
        return 2
    conn.close()
    results = [dict(zip(col_names, row)) for row in rows]
    print(json.dumps({"table": name, "row_count": len(results),
                      "results": results}, indent=2))
    return 0


def cmd_schema(name: str) -> int:
    conn = _connect()
    schema_info = _load_table_schema(conn, name)
    if not schema_info:
        conn.close()
        print(json.dumps({"error": f"table '{name}' not synced"}))
        return 2
    schema, schema_hash = schema_info
    live_cols = []
    for row in conn.execute(f'PRAGMA table_info("{name}")'):
        live_cols.append({"cid": row[0], "name": row[1], "type": row[2],
                           "notnull": row[3], "default": row[4], "pk": row[5]})
    count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    conn.close()
    declared_cols = {c["name"] for c in _normalize_columns(schema)}
    live_col_names = {c["name"] for c in live_cols
                       if not c["name"].startswith("_")}
    drift = sorted(declared_cols - live_col_names) + sorted(live_col_names - declared_cols)
    print(json.dumps({
        "table": name,
        "schema_hash": schema_hash,
        "row_count": count,
        "declared_columns": sorted(declared_cols),
        "live_columns": [c["name"] for c in live_cols],
        "drift": drift,
        "schema": schema,
    }, indent=2))
    return 0


def cmd_list() -> int:
    if not DB_PATH.exists():
        print(json.dumps({"tables": []}))
        return 0
    conn = _connect()
    rows = conn.execute(
        "SELECT table_name, schema_hash, synced_at, entity_page FROM _schema_meta "
        "ORDER BY table_name"
    ).fetchall()
    out = []
    for row in rows:
        name = row[0]
        count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        out.append({
            "name": name,
            "schema_hash": row[1],
            "synced_at": row[2],
            "entity_page": row[3],
            "row_count": count,
        })
    conn.close()
    print(json.dumps({"tables": out}, indent=2))
    return 0


_TABLE_CITATION_SCAN_RE = re.compile(
    r"\(table:([a-zA-Z_][a-zA-Z0-9_]*)#id=([^)]+)\)"
)


def cmd_audit(wiki_dir: Path, name: Optional[str]) -> int:
    """Verify (table:<name>#id=<id>) citations across the wiki.

    For each citation found in non-source wiki pages, check that the
    referenced row still exists. Report stale citations. Update the
    `_audit_log` for the audited tables so `table_citation_risk`
    resets.
    """
    if not wiki_dir.exists():
        print(json.dumps({"error": f"wiki_dir not found: {wiki_dir}"}))
        return 2
    conn = _connect()
    # Gather all (wiki_page, table, row_id) citations.
    citations = []
    for page in wiki_dir.rglob("*.md"):
        rel = str(page.relative_to(wiki_dir))
        if rel.startswith("sources/") or page.is_symlink():
            continue
        try:
            text = page.read_text()
        except OSError:
            continue
        for m in _TABLE_CITATION_SCAN_RE.finditer(text):
            t = m.group(1)
            rid = m.group(2)
            if name is None or t == name:
                citations.append({"page": rel, "table": t, "row_id": rid})

    # Load PK columns for each table once.
    pk_by_table = {}
    for cit in citations:
        t = cit["table"]
        if t in pk_by_table:
            continue
        schema_info = _load_table_schema(conn, t)
        if not schema_info:
            pk_by_table[t] = None
            continue
        schema, _ = schema_info
        cols = _normalize_columns(schema)
        pk_by_table[t] = _primary_key_col(cols)

    stale = []
    valid = 0
    for cit in citations:
        pk = pk_by_table.get(cit["table"])
        if pk is None:
            stale.append({**cit, "reason": "table not synced"})
            continue
        try:
            row = conn.execute(
                f'SELECT 1 FROM "{cit["table"]}" WHERE "{pk}" = ? LIMIT 1',
                (cit["row_id"],)
            ).fetchone()
        except sqlite3.Error as e:
            stale.append({**cit, "reason": f"sql error: {e}"})
            continue
        if row is None:
            stale.append({**cit, "reason": "row not found"})
        else:
            valid += 1

    # Reset audit counters for tables we actually audited.
    audited_tables = set(c["table"] for c in citations)
    if name:
        audited_tables.add(name)
    now = datetime.now(timezone.utc).isoformat()
    for t in audited_tables:
        conn.execute("""
            INSERT INTO _audit_log (table_name, last_audit_at, row_changes_since_last, audit_period_days)
            VALUES (?, ?, 0, 30)
            ON CONFLICT(table_name) DO UPDATE SET
                last_audit_at = excluded.last_audit_at,
                row_changes_since_last = 0
        """, (t, now))
    conn.commit()
    conn.close()

    print(json.dumps({
        "wiki_dir": str(wiki_dir),
        "table_filter": name,
        "citations_scanned": len(citations),
        "valid": valid,
        "stale_count": len(stale),
        "stale": stale,
        "audited_tables": sorted(audited_tables),
    }, indent=2))
    return 0 if not stale else 1


def cmd_risk() -> int:
    """Emit a per-table risk score for table_citation_risk telemetry."""
    if not DB_PATH.exists():
        print(json.dumps({"tables": []}))
        return 0
    conn = _connect()
    meta_rows = conn.execute("""
        SELECT m.table_name, m.last_audit_at, m.row_changes_since_last,
               m.audit_period_days
        FROM _audit_log m
    """).fetchall()
    report = []
    for table_name, last_audit, changes, period_days in meta_rows:
        try:
            total = conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
        except sqlite3.Error:
            total = 0
        churn = changes / max(1, total)
        if last_audit:
            delta = (datetime.now(timezone.utc)
                      - datetime.fromisoformat(last_audit)).total_seconds()
            days_since = delta / 86400.0
        else:
            days_since = float(period_days or 30)
        time_factor = min(1.0, days_since / max(1, period_days or 30))
        risk = min(1.0, churn * time_factor)
        report.append({
            "table": table_name,
            "total_rows": total,
            "row_changes_since_last_audit": changes,
            "churn_rate": round(churn, 3),
            "days_since_last_audit": round(days_since, 1),
            "audit_period_days": period_days,
            "risk": round(risk, 3),
        })
    conn.close()
    print(json.dumps({"tables": report}, indent=2))
    return 0


def cmd_rebuild(name: str) -> int:
    """Phase 4 feature — stub returns an informative message for now."""
    print(json.dumps({"error": "rebuild not implemented",
                      "hint": "full rebuild-from-provenance lands in Phase 4 "
                               "(governance + migrations). For now, insert "
                               "rows via `tables.py insert` or reconstruct "
                               "manually from the provenance record."}))
    return 2


# ---- extracted-table queries ----
#
# `_extracted_tables` is the long-format system table populated by
# `sweep.py promote-extracted-tables`. It holds verbatim cell-level
# transcriptions of tables found in source PDFs / spreadsheets / slide
# decks during ingest. Distinct from the class-tables mechanism above
# (`_schema_meta` + named per-entity tables); no schema declaration in
# any wiki page, so the standard `query` / `schema` / `list` commands
# don't apply. The two helpers below give agents and humans a JSON-
# returning query path for `[tab]`-page rows without dropping to raw
# SQLite.

# WHERE-clause keywords blocked across all SELECT-only paths
# (mirrors the guard in cmd_query). Kept module-level to share between
# class-table query and extracted-table query.
_FORBIDDEN_WHERE = ("--", ";", "drop ", "delete ", "update ", "insert ",
                     "alter ", "attach ", "pragma ")


def _ensure_extracted_table(conn) -> bool:
    """True if `_extracted_tables` exists in the connected DB."""
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='_extracted_tables'"
    ).fetchone()
    return row is not None


def cmd_extracted_query(table_stem: str, where: Optional[str],
                          args_json: Optional[str], limit: int,
                          include_flagged: bool = False,
                          wiki_dir: Optional[Path] = None) -> int:
    """Query rows of a single extracted table by stem.

    Returns rows as `{row_idx, source_stub, source_extraction, **cells}`
    where `**cells` is the headers→value mapping unpacked from the
    long-format storage. WHERE applies to the system columns
    (`row_idx`, `source_stub`, `source_extraction`, `extraction_sha`);
    filtering on extracted cell values is left to the caller (the
    headers vary per table, so SQL-side filtering would need a JOIN-
    on-JSON dance that's not worth the complexity).

    When `include_flagged` is False (default), pages whose [tab] file
    carries `verdict: suspect | wrong` (numeric-review flagged) are
    refused — callers get an empty results array with `flagged: true`.
    Synthesis workers default to clean rows; explicit
    `--include-flagged` opt-in is required to read potentially-bad
    transcriptions.
    """
    # Refuse flagged pages first — fast path before opening the DB.
    if not include_flagged and wiki_dir is not None:
        page_path = wiki_dir / "tables" / f"{table_stem}.md"
        if page_path.exists():
            try:
                fm_text = page_path.read_text()
            except OSError:
                fm_text = ""
            verdict_match = re.search(
                r"^verdict:\s*(\S+)\s*$", fm_text, re.MULTILINE
            )
            if verdict_match:
                v = verdict_match.group(1).strip().strip('"\'')
                if v in ("suspect", "wrong"):
                    print(json.dumps({
                        "table_stem": table_stem,
                        "row_count": 0,
                        "headers": [],
                        "results": [],
                        "flagged": True,
                        "verdict": v,
                        "hint": ("page is reviewer-flagged; pass "
                                 "--include-flagged to read anyway"),
                    }, indent=2))
                    return 0
    conn = _connect()
    if not _ensure_extracted_table(conn):
        conn.close()
        print(json.dumps({"error": "_extracted_tables not present",
                          "hint": "run `sweep.py promote-extracted-tables wiki` first"}))
        return 2
    try:
        args = json.loads(args_json) if args_json else []
    except json.JSONDecodeError as e:
        conn.close()
        print(json.dumps({"error": f"invalid --args JSON: {e}"}))
        return 2
    if not isinstance(args, list):
        conn.close()
        print(json.dumps({"error": "--args must be a JSON array"}))
        return 2
    sql = ("SELECT row_idx, source_stub, source_extraction, "
           "headers_json, cells_json, extraction_sha "
           "FROM _extracted_tables WHERE table_stem = ?")
    params: List = [table_stem]
    if where:
        low = where.lower()
        if any(f in low for f in _FORBIDDEN_WHERE):
            conn.close()
            print(json.dumps({"error": "forbidden keyword in where clause"}))
            return 2
        sql += f" AND ({where})"
        params.extend(args)
    sql += " ORDER BY row_idx LIMIT ?"
    params.append(int(limit))
    try:
        cur = conn.execute(sql, params)
        raw = cur.fetchall()
    except sqlite3.Error as e:
        conn.close()
        print(json.dumps({"error": f"query error: {e}"}))
        return 2
    conn.close()
    if not raw:
        print(json.dumps({"table_stem": table_stem, "row_count": 0,
                          "headers": [], "results": []}, indent=2))
        return 0
    # Headers are identical across rows of one table_stem (sweep writes
    # them once per row, but the value is the same). Pull from the
    # first row.
    headers = json.loads(raw[0][3])
    results = []
    for row_idx, source_stub, source_extraction, _hdr_json, cells_json, _sha in raw:
        cells = json.loads(cells_json)
        record = {
            "row_idx": row_idx,
            "source_stub": source_stub,
            "source_extraction": source_extraction,
        }
        for i, h in enumerate(headers):
            record[h] = cells[i] if i < len(cells) else ""
        results.append(record)
    print(json.dumps({"table_stem": table_stem, "row_count": len(results),
                      "headers": headers, "results": results}, indent=2))
    return 0


def _ensure_backup_table(conn) -> bool:
    """True if `_extracted_table_backups` exists in the connected DB."""
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='_extracted_table_backups'"
    ).fetchone()
    return row is not None


def cmd_list_backups(table_stem: Optional[str]) -> int:
    """List available row backups for extracted-table pages.

    Backups are written by `sweep.py apply-numeric-review` on
    `verdict: wrong` (auto-overwrite) before the rewrite. Each backup
    captures a full snapshot of the table's rows under a unique
    `backup_id`. Filter to a single `table_stem` to narrow the list
    when triaging a specific page.
    """
    if not DB_PATH.exists():
        print(json.dumps({"backups": []}))
        return 0
    conn = _connect()
    if not _ensure_backup_table(conn):
        conn.close()
        print(json.dumps({"backups": []}))
        return 0
    sql = ("SELECT backup_id, table_stem, source_stub, source_extraction, "
           "MAX(backup_at) AS backup_at, COUNT(*) AS row_count "
           "FROM _extracted_table_backups")
    params: List = []
    if table_stem:
        sql += " WHERE table_stem = ?"
        params.append(table_stem)
    sql += " GROUP BY backup_id, table_stem ORDER BY backup_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out = []
    for backup_id, t_stem, src_stub, src_ext, backup_at, row_count in rows:
        out.append({
            "backup_id": backup_id,
            "table_stem": t_stem,
            "source_stub": src_stub,
            "source_extraction": src_ext,
            "backup_at": backup_at,
            "row_count": row_count,
            "rewind_command": (
                f"tables.py restore-backup {t_stem} {backup_id}"
            ),
        })
    print(json.dumps({"backups": out}, indent=2))
    return 0


def cmd_restore_backup(table_stem: str, backup_id: str) -> int:
    """Restore an extracted-table's rows from a backup snapshot.

    Idempotent: copies backup rows into `_extracted_tables` (DELETE+
    INSERT under the unique constraint), rewrites the corresponding
    `wiki/tables/<table_stem>.md` page's GFM body from the restored
    rows, and clears the review-related fm fields (`verdict`,
    `flagged_cells`, `review_required`, `backup_id`,
    `numeric_review_done`). The backup itself is NOT deleted —
    multiple rewinds are safe; cleanup happens manually via
    `_extracted_table_backups` SQL when the curator is sure.

    Logs the rewind to `.curator/log.md` under
    `## numeric-review-rewinds-applied`.
    """
    import datetime as _dt
    if not DB_PATH.exists():
        print(json.dumps({"ok": False,
                          "error": ".curator/tables.db not found"}))
        return 1
    conn = _connect()
    if not _ensure_backup_table(conn):
        conn.close()
        print(json.dumps({"ok": False,
                          "error": "_extracted_table_backups table not present"}))
        return 1
    cur = conn.execute(
        "SELECT source_stub, source_extraction, headers_json, "
        "row_idx, cells_json, extraction_sha "
        "FROM _extracted_table_backups "
        "WHERE table_stem = ? AND backup_id = ? "
        "ORDER BY row_idx",
        (table_stem, backup_id),
    )
    backup_rows = cur.fetchall()
    if not backup_rows:
        conn.close()
        print(json.dumps({"ok": False,
                          "error": f"no backup found for "
                                   f"{table_stem}/{backup_id}"}))
        return 1
    source_stub = backup_rows[0][0]
    source_extraction = backup_rows[0][1]
    headers = json.loads(backup_rows[0][2])
    extraction_sha = backup_rows[0][5]
    rows = [json.loads(r[4]) for r in backup_rows]
    # Rewrite _extracted_tables for this stem.
    conn.execute("DELETE FROM _extracted_tables WHERE table_stem = ?",
                 (table_stem,))
    for ri, r in enumerate(rows, 1):
        cells = [(r[i] if i < len(headers) else "")
                 for i in range(len(headers))]
        conn.execute(
            "INSERT INTO _extracted_tables "
            "(table_stem, source_stub, source_extraction, headers_json, "
            " row_idx, cells_json, extraction_sha) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (table_stem, source_stub, source_extraction,
             json.dumps(headers), ri, json.dumps(cells), extraction_sha),
        )
    conn.commit()
    conn.close()
    # Rewrite the [tab] page if reachable.
    cwd_wiki = Path.cwd() / "wiki"
    page_path = None
    for candidate in (cwd_wiki / "tables" / f"{table_stem}.md",
                       Path("wiki") / "tables" / f"{table_stem}.md"):
        if candidate.exists():
            page_path = candidate
            break
    page_rewritten = False
    if page_path is not None:
        page_rewritten = _restore_page_body(page_path, headers, rows)
    # Log the rewind.
    log_path = (DB_PATH.parent.parent / "wiki" / ".." /
                ".curator" / "log.md").resolve()
    log_path = DB_PATH.parent / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    section = "## numeric-review-rewinds-applied"
    text = log_path.read_text() if log_path.exists() else ""
    if section not in text:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"\n{section}\n\n"
    entry = (f"\n- {ts} {table_stem} restored from {backup_id} "
             f"({len(rows)} rows). Page rewrite: "
             f"{'ok' if page_rewritten else 'page not found'}\n")
    if entry not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += entry
    log_path.write_text(text)
    print(json.dumps({
        "ok": True,
        "table_stem": table_stem,
        "backup_id": backup_id,
        "rows_restored": len(rows),
        "page_rewritten": page_rewritten,
        "page_path": str(page_path) if page_path else None,
    }))
    return 0


def _restore_page_body(page_path: Path, headers: list,
                         rows: list) -> bool:
    """Rewrite the GFM table on a [tab] page from restored rows.

    Drops `## Numeric review` block and review-related fm fields so
    the page returns to a pre-review state. Returns True if written.
    """
    text = page_path.read_text()
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    fm_block = text[3:end].strip()
    body = text[end + 4:]
    # Drop review-related fm keys.
    drop_keys = {"verdict", "flagged_cells_count", "review_required",
                 "backup_id", "numeric_review_done"}
    new_fm_lines = []
    skip_block = False
    for ln in fm_block.split("\n"):
        stripped = ln.lstrip()
        key = stripped.split(":", 1)[0] if ":" in stripped else ""
        if key in drop_keys:
            # Skip this line and any indented continuation lines.
            skip_block = True
            continue
        if skip_block and (ln.startswith(" ") or ln.startswith("\t")
                            or ln.startswith("-")):
            continue
        skip_block = False
        new_fm_lines.append(ln)
    new_fm = "\n".join(new_fm_lines)
    # Strip review block.
    review_re = __import__("re").compile(
        r"\n*## Numeric review.*?(?=\n## |\Z)", __import__("re").DOTALL
    )
    body = review_re.sub("", body).rstrip() + "\n"
    # Rewrite first GFM block in body.
    block_re = __import__("re").compile(
        r"(?:^|\n)([ \t]*\|[^\n]*\|[ \t]*\n"
        r"[ \t]*\|[ \t:\-|]+\|[ \t]*\n"
        r"(?:[ \t]*\|[^\n]*\|[ \t]*\n?)*)"
    )
    # Render fresh.
    try:
        is_snap = "is_snapshot: true" in new_fm.lower()
    except Exception:
        is_snap = False
    show_rows = rows[:10] if is_snap else rows
    norm_rows = [list(r) + [""] * (len(headers) - len(r)) if len(r) < len(headers) else list(r)
                 for r in show_rows]
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in norm_rows:
        out.append("| " + " | ".join(
            str(c).replace("|", "\\|").replace("\n", " ") for c in r[:len(headers)]
        ) + " |")
    new_block = "\n".join(out)
    m = block_re.search(body)
    if m:
        new_body = body[: m.start()] + "\n" + new_block + "\n" + body[m.end():]
    else:
        new_body = body + "\n" + new_block + "\n"
    page_path.write_text(f"---\n{new_fm}\n---{new_body}")
    return True


def cmd_extracted_list(source_stub: Optional[str]) -> int:
    """List all extracted tables (one entry per `table_stem`).

    Optional `--source-stub` filter narrows to tables extracted from a
    specific source. Returns row counts and source citation info so
    callers can decide which `extracted-query` to run.
    """
    if not DB_PATH.exists():
        print(json.dumps({"tables": []}))
        return 0
    conn = _connect()
    if not _ensure_extracted_table(conn):
        conn.close()
        print(json.dumps({"tables": []}))
        return 0
    sql = ("SELECT table_stem, source_stub, source_extraction, "
           "COUNT(*) AS row_count, MAX(headers_json) AS headers_json "
           "FROM _extracted_tables")
    params: List = []
    if source_stub:
        sql += " WHERE source_stub = ?"
        params.append(source_stub)
    sql += " GROUP BY table_stem ORDER BY table_stem"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out = []
    for table_stem, src_stub, src_ext, row_count, headers_json in rows:
        try:
            headers = json.loads(headers_json) if headers_json else []
        except json.JSONDecodeError:
            headers = []
        out.append({
            "table_stem": table_stem,
            "source_stub": src_stub,
            "source_extraction": src_ext,
            "row_count": row_count,
            "headers": headers,
        })
    print(json.dumps({"tables": out}, indent=2))
    return 0


# ---- CLI ----

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="sync schema from entity page")
    p_sync.add_argument("entity_path", type=Path)
    p_sync.add_argument("--confirm-human", action="store_true",
                          help="explicitly permit destructive ops (drop column, "
                               "narrow enum while rows use removed values)")

    p_insert = sub.add_parser("insert", help="insert a row")
    p_insert.add_argument("table")
    p_insert.add_argument("payload", help="JSON payload including _provenance")

    p_update = sub.add_parser("update", help="update a row by primary key")
    p_update.add_argument("table")
    p_update.add_argument("id")
    p_update.add_argument("payload", help="JSON payload including _provenance")

    p_query = sub.add_parser("query", help="query rows")
    p_query.add_argument("table")
    p_query.add_argument("--where", default=None,
                          help="SQL WHERE fragment (parameterised via --args)")
    p_query.add_argument("--args", default=None,
                          help="JSON array of parameter values for --where")
    p_query.add_argument("--limit", type=int, default=50)

    p_schema = sub.add_parser("schema", help="show live + declared schema")
    p_schema.add_argument("table")

    sub.add_parser("list", help="list all tables")

    p_audit = sub.add_parser("audit",
                               help="verify (table:X#id=Y) citations across wiki")
    p_audit.add_argument("wiki", type=Path)
    p_audit.add_argument("--table", default=None,
                          help="audit only this table (default: all)")

    sub.add_parser("risk", help="per-table citation-risk report")

    p_verify = sub.add_parser("verify", help="check row provenance integrity")
    p_verify.add_argument("wiki", type=Path)
    p_verify.add_argument("--table", default=None)

    p_rebuild = sub.add_parser("rebuild", help="rebuild table from vault+log (not yet)")
    p_rebuild.add_argument("table")

    p_eq = sub.add_parser("extracted-query",
                            help="query rows of an extracted table (tab-* page)")
    p_eq.add_argument("table_stem",
                       help="stem of the wiki/tables/tab-*.md page (without prefix)")
    p_eq.add_argument("--where", default=None,
                       help="SQL WHERE on system columns (row_idx, source_stub, "
                            "source_extraction, extraction_sha); cell-value "
                            "filters must be applied by the caller")
    p_eq.add_argument("--args", default=None,
                       help="JSON array of parameter values for --where")
    p_eq.add_argument("--limit", type=int, default=200)
    p_eq.add_argument("--include-flagged", action="store_true",
                       help="return rows even when the [tab] page has "
                            "verdict suspect|wrong (default: drop them so "
                            "callers don't accidentally cite reviewer-"
                            "flagged numbers)")
    p_eq.add_argument("--wiki", default="wiki",
                       help="wiki dir for the verdict lookup (default: wiki)")

    p_el = sub.add_parser("extracted-list",
                            help="list all extracted tables (tab-* pages)")
    p_el.add_argument("--source-stub", default=None,
                       help="filter to tables from a specific source-stub stem")

    p_lb = sub.add_parser("list-backups",
                            help="list available row backups for tab-* pages")
    p_lb.add_argument("--table-stem", default=None,
                       help="filter to one stem")

    p_rb = sub.add_parser("restore-backup",
                            help="restore a tab-* page's rows from a backup snapshot")
    p_rb.add_argument("table_stem",
                       help="stem of the wiki/tables/tab-*.md page")
    p_rb.add_argument("backup_id",
                       help="backup_id from list-backups (e.g. bk-7f3a2c)")

    args = ap.parse_args()
    if args.cmd == "sync":
        return cmd_sync(args.entity_path, confirm_human=args.confirm_human)
    if args.cmd == "insert":
        return cmd_insert(args.table, args.payload)
    if args.cmd == "update":
        return cmd_update(args.table, args.id, args.payload)
    if args.cmd == "query":
        return cmd_query(args.table, args.where, args.args, args.limit)
    if args.cmd == "schema":
        return cmd_schema(args.table)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "audit":
        return cmd_audit(args.wiki, args.table)
    if args.cmd == "risk":
        return cmd_risk()
    if args.cmd == "verify":
        return cmd_verify(args.table, args.wiki)
    if args.cmd == "rebuild":
        return cmd_rebuild(args.table)
    if args.cmd == "extracted-query":
        return cmd_extracted_query(args.table_stem, args.where, args.args,
                                     args.limit,
                                     include_flagged=args.include_flagged,
                                     wiki_dir=Path(args.wiki))
    if args.cmd == "extracted-list":
        return cmd_extracted_list(args.source_stub)
    if args.cmd == "list-backups":
        return cmd_list_backups(args.table_stem)
    if args.cmd == "restore-backup":
        return cmd_restore_backup(args.table_stem, args.backup_id)
    ap.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
