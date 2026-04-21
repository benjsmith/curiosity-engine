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
    return conn


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

def cmd_sync(entity_path: Path) -> int:
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
    existing = conn.execute(
        "SELECT schema_hash FROM _schema_meta WHERE table_name = ?", (name,)
    ).fetchone()
    if existing and existing[0] == current_hash:
        conn.close()
        print(json.dumps({"table": name, "status": "unchanged",
                          "hash": current_hash}))
        return 0

    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None
    status = None
    if not table_exists:
        col_defs = []
        for c in columns:
            sqlite_type = _sqlite_type(c["type"])
            pk_frag = " PRIMARY KEY" if c["pk"] else ""
            nn_frag = "" if c["nullable"] else " NOT NULL"
            col_defs.append(f'"{c["name"]}" {sqlite_type}{pk_frag}{nn_frag}')
        # Provenance + bookkeeping.
        col_defs.append('"_provenance" TEXT NOT NULL')
        col_defs.append('"_inserted_at" TEXT NOT NULL')
        col_defs.append('"_updated_at" TEXT')
        col_defs.append('"_schema_version" TEXT NOT NULL')
        ddl = f'CREATE TABLE "{name}" ({", ".join(col_defs)})'
        conn.execute(ddl)
        status = "created"
    else:
        # ALTER path — additive only in Phase 1.
        existing_cols = {row[1] for row in conn.execute(
            f'PRAGMA table_info("{name}")'
        )}
        added = []
        for c in columns:
            if c["name"] in existing_cols:
                continue
            sqlite_type = _sqlite_type(c["type"])
            conn.execute(
                f'ALTER TABLE "{name}" ADD COLUMN "{c["name"]}" {sqlite_type}'
            )
            added.append(c["name"])
        status = "altered" if added else "up_to_date"

    conn.execute("""
        INSERT OR REPLACE INTO _schema_meta
            (table_name, schema_hash, schema_json, entity_page, synced_at)
        VALUES (?, ?, ?, ?, ?)
    """, (name, current_hash, json.dumps(schema), str(entity_path),
           datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    out = {"table": name, "status": status, "hash": current_hash,
           "columns": len(columns)}
    print(json.dumps(out))
    return 0


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


def cmd_rebuild(name: str) -> int:
    """Phase 3 feature — stub returns an informative message for now."""
    print(json.dumps({"error": "rebuild not implemented in Phase 1",
                      "hint": "coming in Phase 3 (conversational capture + audit)"}))
    return 2


# ---- CLI ----

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="sync schema from entity page")
    p_sync.add_argument("entity_path", type=Path)

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

    p_rebuild = sub.add_parser("rebuild", help="rebuild table from vault+log")
    p_rebuild.add_argument("table")

    args = ap.parse_args()
    if args.cmd == "sync":
        return cmd_sync(args.entity_path)
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
    if args.cmd == "rebuild":
        return cmd_rebuild(args.table)
    ap.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
