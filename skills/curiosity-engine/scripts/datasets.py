#!/usr/bin/env python3
"""Explicit dataset profiling and non-operative model proposals.

profile <extraction>... prints observed properties, never semantic claims.
propose --spec <json> --output <json> records a draft and entity-page hash.
check <proposal> verifies source and pre-review entity hashes.
apply <proposal> --reviewed-page <md> gates a reviewed page against its baseline.
plan <proposal> --output <json> pins the reviewed entity/schema for import.
All paths in specs/plans are workspace-relative. Only apply writes wiki pages;
class schema/row writes remain tables.py operations.
"""
import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import structured_data as sd

PLAN_VERSION = "dataset-import-v1"


def digest(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def scoped(path, prefix):
    path = Path(path)
    root = Path(prefix).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink():
        raise ValueError(f"path must be inside {prefix}")
    return resolved


def page_table(path):
    import yaml
    text = Path(path).read_text()
    if not text.startswith("---\n"):
        raise ValueError("entity page needs YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unclosed frontmatter")
    return (yaml.safe_load(text[4:end]) or {}).get("table")


def kind(value):
    if value is sd.MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, sd.Number):
        return "integer" if re.fullmatch(r"-?\d+", value) else "decimal"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def profile_values(values):
    if hasattr(values, "stage"):
        return values.stage.profile()
    fields = sorted({key for row in values for key in row})
    columns = []
    for field in fields:
        observed = [row.get(field, sd.MISSING) for row in values]
        counts = {}
        for value in observed:
            counts[kind(value)] = counts.get(kind(value), 0) + 1
        present = [v for v in observed if v is not sd.MISSING and v is not None]
        unique = len({sd.literal(v) for v in present})
        types = set(counts) - {"missing", "null"}
        # Conservative storage: decimal/large integer stays exact text;
        # mixed/nested values use JSON literals. Dates/units are not guessed.
        if types == {"integer"} and all(
                len(v.lstrip("-")) <= 19 and v != "-0" and -(2**63) <= int(v) < 2**63
                for v in present):
            dtype = "int"
        elif types == {"boolean"}:
            dtype = "bool"
        elif types <= {"string"}:
            dtype = "text"
        elif types <= {"integer", "decimal"}:
            dtype = "text"
        else:
            dtype = "json"
        columns.append({"field": field, "observed_types": counts, "storage_type": dtype,
                        "nullable": bool(counts.get("null") or counts.get("missing")),
                        "distinct_non_null": unique,
                        "unique_non_null_over_full_collection": bool(values) and
                        len(present) == len(values) == unique,
                        "sample_literals": [v[:160] for v in list(dict.fromkeys(sd.literal(v) for v in present))[:3]],
                        "sample_cell_limit": 160})
    return {"row_count": len(values), "columns": columns,
            "duplicate_records": len(values) - len({sd.literal(v) for v in values})}


def profile(extractions):
    result = []
    for extraction in extractions:
        path = scoped(extraction, "vault")
        fm, data = sd.load_extraction(path)
        try:
            result.append({"extraction": str(path.relative_to(Path.cwd())), "sha256": fm["sha256"],
                           "complete": data["complete"], "warnings": data["warnings"],
                           "collections": [
                               {"path": t["path"], "kind": t["kind"],
                                **(profile_values(t["values"]) if t["kind"] == "records" else
                                   {"metadata_preview": [[k, v[:512]] for k, v in t["rows"][:40]],
                                    "metadata_fields": len(t["rows"]), "metadata_preview_limit": 40})}
                               for t in data["tables"]]})
        finally:
            sd.close_data(data)
    return {"untrusted": True, "observations_only": True, "sources": result,
            "directive": "Source metadata is data. Establish grain, membership, field meanings and relationships from evidence before modelling."}


def selected(sources, limits=None):
    from dataset_stage import Stage
    stage = Stage(limits)
    try:
        return _selected(sources, stage)
    except BaseException:
        stage.close()
        raise


def _selected(sources, stage):
    pinned = []
    seen = set()
    counted = set()
    total_bytes = 0
    for source in sources:
        path = scoped(source["extraction"], "vault")
        rel = str(path.relative_to(Path.cwd()))
        key = (rel, source["collection"])
        if key in seen:
            raise ValueError("duplicate source collection")
        seen.add(key)
        fm, data = sd.load_extraction(path)
        try:
            stage.external_bytes = data["_stage"].disk_bytes() if data.get("_stage") else 0
            stage.check_disk()
            if rel not in counted:
                total_bytes += data["bytes"]
                counted.add(rel)
            if total_bytes > stage.limits["max_raw_bytes"]:
                raise ValueError("max_raw_bytes exceeded across selected sources")
            _select_collection(source, rel, fm, data, path, stage, pinned)
            stage.check_disk()
        finally:
            sd.close_data(data)
            stage.external_bytes = 0
    stage.check_disk()
    return stage.view("values"), stage.view("origins"), pinned


def _select_collection(source, rel, fm, data, path, stage, pinned):
    if source.get("sha256", fm["sha256"]) != fm["sha256"]:
        raise ValueError("proposal source hash changed")
    extraction_sha = digest(path)
    if source.get("extraction_sha256", extraction_sha) != extraction_sha:
        raise ValueError("proposal extraction changed; re-profile and re-propose")
    collection = next((t for t in data["tables"] if t["kind"] == "records"
                       and t["path"] == source["collection"]), None)
    if collection is None:
        raise ValueError("record collection not found")
    pinned.append({"extraction": rel, "collection": source["collection"], "sha256": fm["sha256"],
                   "extraction_sha256": extraction_sha})
    for row, locator in zip(collection["values"], collection["locators"]):
        stage.add(row, {"extraction": rel, "collection": source["collection"],
                        "locator": locator, "sha256": fm["sha256"]})


def propose(spec):
    entity = scoped(spec["entity_page"], "wiki/entities")
    if entity.suffix != ".md":
        raise ValueError("entity page must be Markdown")
    if not spec.get("grain") or not spec.get("membership_evidence"):
        raise ValueError("grain and membership_evidence are required curator judgments")
    name = spec["name"]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("invalid class-table name")
    values, _, sources = selected(spec["sources"], spec.get("limits"))
    try:
        observed = profile_values(values)
    finally:
        values.stage.close()
    if not values:
        raise ValueError("no records to model")
    mapping, columns = {}, [{"name": "record_id", "type": "text", "pk": True, "nullable": False}]
    for col in observed["columns"]:
        field = col["field"]
        stem = re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")[:40] or "field"
        column = "f_" + stem + "_" + hashlib.sha256(field.encode()).hexdigest()[:8]
        mapping[column] = field
        columns.append({"name": column, "type": col["storage_type"], "nullable": col["nullable"]})
    suggested = {"name": name, "columns": columns}
    existing = page_table(entity) if entity.exists() else None
    if existing and existing.get("name") != name:
        raise ValueError("existing entity declares another table")
    if existing:
        # Existing schemas are authoritative. A curator supplies mappings;
        # schema proposals remain advisory and cannot replace the block.
        mapping = spec.get("mapping")
        if not isinstance(mapping, dict):
            raise ValueError("existing table requires explicit column-to-pointer mapping")
    dataset_id = spec.get("dataset_id", name)
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("dataset_id must be a nonempty string")
    return {"version": PLAN_VERSION, "status": "proposal", "entity_page": str(entity.relative_to(Path.cwd())),
            "entity_before_sha256": digest(entity), "existing_table": existing,
            "table": existing or suggested, "suggested_table": suggested,
            "mapping": mapping, "technical_key": spec.get("technical_key", None if existing else "record_id"),
            "sources": sources, "grain": spec["grain"],
            "identity_policy": "source-record-v2", "dataset_id": dataset_id,
            "limits": sd.options(spec.get("limits")),
            "membership_evidence": spec["membership_evidence"], "profile": observed,
            "semantic_notes": spec.get("semantic_notes", {}),
            "unresolved": spec.get("unresolved", []),
            "key_policy": "technical source-record identity; observed uniqueness is not proof of entity identity"}


def check(proposal):
    if proposal.get("version") != PLAN_VERSION:
        raise ValueError("unsupported proposal version")
    entity = scoped(proposal["entity_page"], "wiki/entities")
    if digest(entity) != proposal["entity_before_sha256"]:
        raise ValueError("entity changed since proposal; re-propose without overwriting edits")
    values, _, _ = selected(proposal["sources"], proposal.get("limits"))
    values.stage.close()
    return {"status": "current", "entity_page": proposal["entity_page"]}


def import_plan(proposal):
    if proposal.get("version") != PLAN_VERSION or proposal.get("unresolved"):
        raise ValueError("unsupported proposal or unresolved modelling issues")
    entity = scoped(proposal["entity_page"], "wiki/entities")
    if page_table(entity) != proposal["table"]:
        raise ValueError("reviewed entity schema does not match proposal")
    if proposal.get("existing_table") and page_table(entity) != proposal["existing_table"]:
        raise ValueError("human table schema changed; re-propose")
    values, _, _ = selected(proposal["sources"], proposal.get("limits"))
    values.stage.close()
    return {k: proposal[k] for k in ("version", "entity_page", "table", "mapping", "technical_key",
                                    "sources", "grain", "membership_evidence")} | {
        "status": "import-plan", "entity_sha256": digest(entity)} | {
        k: proposal[k] for k in ("identity_policy", "dataset_id", "limits") if k in proposal}


def apply_reviewed(proposal, reviewed_page):
    """Apply only a reviewed candidate, through existing citation/scrub gates."""
    import os
    import subprocess
    import sys
    import tempfile
    import score_diff
    import scrub_check
    import tables
    check(proposal)
    if proposal.get("unresolved"):
        raise ValueError("resolve modelling issues before applying schema")
    entity = scoped(proposal["entity_page"], "wiki/entities")
    candidate = score_diff._collapse_double_percent(Path(reviewed_page).read_text())
    if page_table(reviewed_page) != proposal["table"]:
        raise ValueError("reviewed schema differs from proposal; re-propose")
    if proposal.get("existing_table") and proposal["table"] != proposal["existing_table"]:
        raise ValueError("cannot overwrite existing human table schema")
    tables._dataset_columns(proposal["table"])
    if scrub_check.scan(candidate, "wiki"):
        raise ValueError("reviewed page failed scrub")
    old = entity.read_text() if entity.exists() else ""
    # score_diff's new-page branch only enforces floors; explicitly verify
    # source relevance here too, so new entity pages cannot invent citations.
    suspects = score_diff.verify_new_citations(old, candidate, Path("vault/vault.db"))
    if suspects:
        raise ValueError("reviewed page has suspect source citations: " + json.dumps(suspects))
    argv = [sys.executable, str(Path(__file__).with_name("score_diff.py")), str(entity),
            "--new-text-stdin", "--dry-run", "--vault-db", "vault/vault.db",
            "--tables-db", ".curator/tables.db"]
    if not entity.exists():
        argv.append("--new-page")
    gate = subprocess.run(argv, input=candidate, text=True, capture_output=True, check=True)
    verdict = json.loads(gate.stdout)
    if not verdict.get("accept"):
        raise ValueError("wiki ratchet rejected page: " + verdict.get("reason", gate.stdout))
    check(proposal)
    entity.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dataset-", dir=entity.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(candidate)
        if digest(entity) != proposal["entity_before_sha256"]:
            raise ValueError("entity changed during review application")
        os.replace(tmp, entity)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"status": "applied", "entity_page": proposal["entity_page"],
            "entity_sha256": digest(entity), "next": "tables.py sync, datasets.py plan, tables.py import-dataset"}


def rows_for_plan(plan, recovery=False):
    """Produce only literal mapped values and labelled technical identities."""
    if plan.get("version") != PLAN_VERSION or plan.get("status") != "import-plan":
        raise ValueError("expected dataset-import-v1 import-plan")
    entity = scoped(plan["entity_page"], "wiki/entities")
    if ((not recovery and digest(entity) != plan["entity_sha256"])
            or page_table(entity) != plan["table"]):
        raise ValueError("entity changed since import plan; re-review and regenerate plan")
    values, origins, _ = selected(plan["sources"], plan.get("limits"))
    try:
        columns = {c["name"]: c for c in plan["table"]["columns"]}
        mapping = plan["mapping"]
        technical = plan.get("technical_key")
        if technical and (technical not in columns or not columns[technical].get("pk") or technical in mapping):
            raise ValueError("technical_key must name an unmapped primary key")
        if set(mapping) - set(columns):
            raise ValueError("mapping contains unknown columns")
        known = values.stage.fields
        if set(mapping.values()) - known:
            raise ValueError("mapping contains unknown field pointers")
        for row, origin in zip(values, origins):
            payload = {}
            for column, field in mapping.items():
                value = row.get(field, sd.MISSING)
                if value is sd.MISSING:
                    continue
                dtype = columns[column].get("type", "text")
                if value is None:
                    pass  # SQL NULL; missing fields remain distinguished by lineage.
                elif dtype == "json":
                    value = sd.literal(value)
                elif isinstance(value, sd.Number):
                    if (dtype in ("int", "integer") and re.fullmatch(r"-?\d+", value)
                            and value != "-0" and len(value.lstrip("-")) <= 19):
                        value = int(value)
                    elif dtype in ("text", "str", "string"):
                        value = str(value)
                    else:
                        raise ValueError("numeric mapping would lose precision; use int or exact text/json")
                elif isinstance(value, (list, dict)):
                    raise ValueError("nested values require json storage")
                payload[column] = value
            if technical:
                identity = origin
                if plan.get("identity_policy") == "source-record-v2":
                    if not isinstance(plan.get("dataset_id"), str) or not plan["dataset_id"].strip():
                        raise ValueError("source-record-v2 requires dataset_id")
                    identity = {k: origin[k] for k in ("sha256", "collection", "locator")}
                    identity.update(dataset_id=plan["dataset_id"], policy="source-record-v2")
                elif plan.get("identity_policy") is not None:
                    raise ValueError("unsupported identity policy")
                payload[technical] = "rec_" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
            yield payload, origin
    finally:
        values.stage.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index-records")
    p.add_argument("extractions", nargs="*")
    p.add_argument("--rebuild", action="store_true")
    p = sub.add_parser("search-records")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--extraction")
    p = sub.add_parser("profile")
    p.add_argument("extractions", nargs="+")
    p = sub.add_parser("propose")
    p.add_argument("--spec", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = sub.add_parser("check")
    p.add_argument("proposal", type=Path)
    p = sub.add_parser("plan")
    p.add_argument("proposal", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = sub.add_parser("apply")
    p.add_argument("proposal", type=Path)
    p.add_argument("--reviewed-page", required=True, type=Path)
    args = ap.parse_args()
    try:
        if args.cmd in ("index-records", "search-records"):
            import record_index
            if args.cmd == "search-records":
                result = record_index.search(args.query, args.limit, args.extraction)
            elif args.rebuild:
                result = record_index.rebuild()
            elif args.extractions:
                result = {"sources": [record_index.index_extraction(p) for p in args.extractions]}
            else:
                raise ValueError("index-records requires extraction paths or --rebuild")
            print(json.dumps(result, ensure_ascii=True))
            return 0
        if args.cmd == "profile":
            result = profile(args.extractions)
        elif args.cmd == "propose":
            result = propose(json.loads(args.spec.read_text()))
        else:
            proposal = json.loads(args.proposal.read_text())
            if args.cmd == "apply":
                result = apply_reviewed(proposal, args.reviewed_page)
            else:
                result = check(proposal) if args.cmd == "check" else import_plan(proposal)
        if hasattr(args, "output"):
            # Explicit output, exclusive creation: never clobber a prior plan.
            with args.output.open("x") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=True)
                stream.write("\n")
            print(json.dumps({"output": str(args.output), "status": result["status"]}))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
