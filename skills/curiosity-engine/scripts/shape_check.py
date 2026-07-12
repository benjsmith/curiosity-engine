#!/usr/bin/env python3
"""shape_check.py — declared-shape validation for class-table rows (U3).

CE's `table:` frontmatter already declares columns, types, enums, and
nullability (parsed in tables.py). U3 extends that grammar with optional
*shape constraints* and enforces them mechanically — in CE's own
hash-guarded Python, no maplib/SHACL — so the emergent schema becomes
*validated* without becoming *universal*. Validation is local and
per-class: a page that declares no shape keys is unaffected.

Three optional column keys, all backward-compatible:

    columns:
      - name: ic50
        type: real
        units: nM                 # marks a MEASUREMENT column
        constraint: ">0"          # per-row numeric bound
        source_required: true     # value must trace to a vault source

Semantics (the research-scope quality gate — "every measurement row
carries units + a source page"):

- `units: <str>` makes the column a **measurement**. Every row must
  (a) carry a non-null value for it, and (b) be backed by a vault-tier
  provenance (`_provenance` starting `vault:`) — the "source page". A
  measurement sourced only from a `log:` entry (derived, not observed)
  is flagged.
- `source_required: true` gates provenance the same way but only when the
  column has a value — use it on non-measurement columns that must still
  trace to a document.
- `constraint: "<bound>"` checks each present numeric value. Supported:
  `>x`, `>=x`, `<x`, `<=x`, `==x`, `!=x`, and ranges `[lo,hi]` (inclusive)
  / `(lo,hi)` (exclusive); mixed brackets allowed.

The core (`parse_constraint`, `check_row`) is pure and importable, so
tables.py enforces shapes at insert time and score_diff.py enforces them
at the citation ratchet — one declaration, enforced wherever rows enter.

Subcommands
-----------
    shape_check.py check <entity_path> [--wiki wiki]
        Load the page's `table:` schema + its rows from .curator/tables.db
        (read-only) and report every shape violation. Exit 1 if any.

    shape_check.py check-constraint "<spec>" <value>
        Evaluate a single constraint against a value (debugging aid).

Hash-guarded by evolve_guard.sh.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3


# ---- constraint grammar ----

_OP_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)$")
_RANGE_RE = re.compile(
    r"^([\[\(])\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*([\]\)])$")


def parse_constraint(spec: str):
    """Parse a constraint spec into (label, predicate(float)->bool), or None
    if it doesn't parse. Deterministic; no eval."""
    if not isinstance(spec, str):
        return None
    spec = spec.strip()
    m = _OP_RE.match(spec)
    if m:
        op, num = m.group(1), float(m.group(2))
        ops = {
            ">": lambda v: v > num, ">=": lambda v: v >= num,
            "<": lambda v: v < num, "<=": lambda v: v <= num,
            "==": lambda v: v == num, "!=": lambda v: v != num,
        }
        return spec, ops[op]
    m = _RANGE_RE.match(spec)
    if m:
        lo_inc = m.group(1) == "["
        lo = float(m.group(2))
        hi = float(m.group(3))
        hi_inc = m.group(4) == "]"

        def pred(v, lo=lo, hi=hi, lo_inc=lo_inc, hi_inc=hi_inc):
            lo_ok = v >= lo if lo_inc else v > lo
            hi_ok = v <= hi if hi_inc else v < hi
            return lo_ok and hi_ok
        return spec, pred
    return None


# ---- row checking (pure) ----

def _is_vault_sourced(provenance) -> bool:
    return isinstance(provenance, str) and provenance.startswith("vault:")


def check_row(columns: List[dict], row: dict) -> List[str]:
    """Check one row (a payload or a DB row dict, including `_provenance`)
    against the shape constraints declared on `columns`. Returns a list of
    human-readable violation strings (empty = clean).

    `columns` are normalised column dicts (tables._normalize_columns), which
    carry the optional `units` / `constraint` / `source_required` keys."""
    violations: List[str] = []
    provenance = row.get("_provenance")
    for c in columns:
        name = c["name"]
        units = c.get("units")
        source_required = bool(c.get("source_required"))
        constraint = c.get("constraint")
        present = name in row and row[name] is not None
        value = row.get(name)

        if units:  # measurement column
            if not present:
                violations.append(
                    f"measurement column '{name}' (units={units}) has no value")
            elif not _is_vault_sourced(provenance):
                violations.append(
                    f"measurement column '{name}' requires a vault source; "
                    f"provenance is {provenance!r}")
        elif source_required and present and not _is_vault_sourced(provenance):
            violations.append(
                f"column '{name}' is source_required but provenance is "
                f"{provenance!r} (not vault-tier)")

        if constraint and present:
            parsed = parse_constraint(constraint)
            if parsed is None:
                violations.append(
                    f"column '{name}' has unparseable constraint {constraint!r}")
            else:
                _, pred = parsed
                try:
                    fv = float(value)
                except (TypeError, ValueError):
                    violations.append(
                        f"column '{name}' constraint {constraint} applied to "
                        f"non-numeric value {value!r}")
                else:
                    if not pred(fv):
                        violations.append(
                            f"column '{name}' value {value} violates "
                            f"constraint {constraint}")
    return violations


def has_shape_constraints(columns: List[dict]) -> bool:
    """True if any column declares a shape key — lets callers skip the row
    scan entirely for the common (no-shapes) case."""
    return any(c.get("units") or c.get("source_required") or c.get("constraint")
               for c in columns)


# ---- entity-level check (CLI) ----

def _connect_ro(db_path: Path):
    """Read-only connection via query_only (mode=ro hangs on WAL dbs)."""
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.execute("PRAGMA query_only=ON")
    return conn


def check_entity(entity_path: Path, wiki_dir: Path) -> dict:
    """Load an entity page's schema + its rows and report violations."""
    # Deferred import: tables.py imports check_row from this module, so a
    # top-level import here would be a cycle. At call time it's resolved.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tables  # noqa: E402

    schema = tables._load_entity_schema(entity_path)
    if not schema:
        return {"error": f"no `table:` block in {entity_path}"}
    name = schema.get("name")
    columns = tables._normalize_columns(schema)
    if not has_shape_constraints(columns):
        return {"table": name, "shape_columns": [], "checked_rows": 0,
                "violations": [], "note": "no shape constraints declared"}

    db_path = wiki_dir.parent / ".curator" / "tables.db"
    if not db_path.exists():
        return {"table": name, "checked_rows": 0, "violations": [],
                "note": "no tables.db yet"}
    conn = _connect_ro(db_path)
    pk = next((c["name"] for c in columns if c["pk"]), None)
    out_violations = []
    checked = 0
    try:
        cur = conn.execute(f'SELECT * FROM "{name}"')
        col_names = [d[0] for d in cur.description]
        for raw in cur.fetchall():
            row = dict(zip(col_names, raw))
            checked += 1
            v = check_row(columns, row)
            if v:
                rid = row.get(pk) if pk else None
                out_violations.append({"row_id": rid, "violations": v})
    except sqlite3.Error as e:
        conn.close()
        return {"table": name, "error": f"query error: {e}"}
    conn.close()
    shape_cols = [c["name"] for c in columns
                  if c.get("units") or c.get("source_required") or c.get("constraint")]
    return {
        "table": name,
        "shape_columns": shape_cols,
        "checked_rows": checked,
        "violation_count": len(out_violations),
        "violations": out_violations,
    }


def cmd_check(entity_path: Path, wiki_dir: Path) -> int:
    report = check_entity(entity_path, wiki_dir)
    print(json.dumps(report, indent=2, default=str))
    if report.get("error"):
        return 2
    return 1 if report.get("violations") else 0


def cmd_check_constraint(spec: str, value: str) -> int:
    parsed = parse_constraint(spec)
    if parsed is None:
        print(json.dumps({"error": f"unparseable constraint {spec!r}"}))
        return 2
    label, pred = parsed
    try:
        fv = float(value)
    except ValueError:
        print(json.dumps({"error": f"non-numeric value {value!r}"}))
        return 2
    ok = pred(fv)
    print(json.dumps({"constraint": label, "value": fv, "satisfied": ok}))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ck = sub.add_parser("check", help="check an entity page's rows against its shapes")
    p_ck.add_argument("entity_path", type=Path)
    p_ck.add_argument("--wiki", default="wiki")

    p_cc = sub.add_parser("check-constraint", help="evaluate one constraint (debug)")
    p_cc.add_argument("spec")
    p_cc.add_argument("value")

    args = ap.parse_args()
    if args.cmd == "check":
        return cmd_check(args.entity_path, Path(args.wiki).resolve())
    if args.cmd == "check-constraint":
        return cmd_check_constraint(args.spec, args.value)
    ap.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
