"""Lossless JSON record extraction. Markdown is a preview, never row storage.

Original bytes + version/options are the replay artifact. Field names are
RFC 6901 pointers (relative to a record); every cell is a JSON literal, except
MISSING. Numeric lexemes stay strings of a distinct type, never binary floats.
"""
import hashlib
import html
import io
import json
from pathlib import Path

VERSION = "json-records-v1"
DEFAULT_LIMITS = {"max_raw_bytes": 50 * 1024 * 1024, "max_depth": 64,
                  "max_fields": 512, "max_cell_bytes": 1024 * 1024,
                  "max_records": 100000, "max_cells": 2000000}
MISSING = object()


class Number(str):
    """An original JSON numeric lexeme, including exponent and trailing zeros."""


class StructuredError(ValueError):
    pass


def literal(value):
    if value is MISSING:
        return "⟨missing⟩"
    if isinstance(value, Number):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=True) + ":" + literal(v)
                              for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(literal(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def pointer(parent, key):
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise StructuredError("duplicate object key")
        result[key] = value
    return result


def _constant(value):
    raise StructuredError("non-finite JSON number")


def loads(text):
    return json.loads(text, parse_int=Number, parse_float=Number,
                      object_pairs_hook=_pairs, parse_constant=_constant)


def options(cfg=None):
    cfg = cfg or {}
    return {k: int(cfg.get(k, v)) for k, v in DEFAULT_LIMITS.items()}


def fingerprint(cfg=None):
    return hashlib.sha256(json.dumps(options(cfg), sort_keys=True).encode()).hexdigest()


def _check(value, limits, depth=0):
    if depth > limits["max_depth"]:
        raise StructuredError("max_depth exceeded")
    if isinstance(value, dict):
        if len(value) > limits["max_fields"]:
            raise StructuredError("max_fields exceeded")
        for k, v in value.items():
            _check(k, limits, depth + 1)
            _check(v, limits, depth + 1)
    elif isinstance(value, list):
        if len(value) > limits["max_records"]:
            raise StructuredError("max_records exceeded")
        for v in value:
            _check(v, limits, depth + 1)
    elif len(literal(value).encode()) > limits["max_cell_bytes"]:
        raise StructuredError("max_cell_bytes exceeded")


def flatten(record, limits, parent=""):
    out = {}
    for key, value in record.items():
        path = pointer(parent, key)
        if isinstance(value, dict) and value:
            out.update(flatten(value, limits, path))
        else:
            if len(literal(value).encode()) > limits["max_cell_bytes"]:
                raise StructuredError("max_cell_bytes exceeded")
            out[path] = value
        if len(out) > limits["max_fields"]:
            raise StructuredError("max_fields exceeded")
    return out


def _table(path, records, locators, limits, kind="records"):
    flat = [flatten(r, limits) for r in records]
    headers = sorted({k for r in flat for k in r})
    if len(headers) > limits["max_fields"]:
        raise StructuredError("max_fields exceeded across records")
    if len(records) * max(1, len(headers)) > limits["max_cells"]:
        raise StructuredError("max_cells exceeded; split wide/sparse collections")
    # Empty objects still count as records; a synthetic technical column
    # exposes their source locations without inventing a domain attribute.
    rows = [[literal(r.get(k, MISSING)) for k in headers] for r in flat]
    return {"path": path, "kind": kind, "description": path or "root",
            "headers": headers or ["@source_locator"],
            "rows": rows if headers else [[json.dumps(p)] for p in locators],
            "locators": locators, "values": flat}


def scrub(value):
    from scrub_check import _scan_markers
    if isinstance(value, dict):
        for k, v in value.items():
            scrub(k)
            scrub(v)
    elif isinstance(value, list):
        for v in value:
            scrub(v)
    elif isinstance(value, str):
        hits = _scan_markers(value, "full")
        if hits:
            raise StructuredError("scrub: " + ", ".join(hits))


def extract(raw, fmt, cfg=None):
    limits = options(cfg)
    if any(n <= 0 for n in limits.values()):
        raise StructuredError("limits must be positive")
    if len(raw) > limits["max_raw_bytes"]:
        raise StructuredError("max_raw_bytes exceeded")
    try:
        tables, warnings = [], []
        if fmt == "jsonl":
            records, locators = [], []
            for line_no, line in enumerate(io.BytesIO(raw), 1):
                if not line.strip():
                    continue
                try:
                    record = loads(line.decode("utf-8-sig"))
                    if not isinstance(record, dict):
                        raise StructuredError("JSONL record must be an object")
                    _check(record, limits)
                    scrub(record)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise StructuredError(f"JSONL line {line_no}: {exc}") from exc
                records.append(record)
                locators.append(f"line:{line_no}")
                if len(records) > limits["max_records"]:
                    raise StructuredError("max_records exceeded")
            tables = [_table("", records, locators, limits)]
        else:
            value = loads(raw.decode("utf-8-sig"))
            _check(value, limits)
            scrub(value)
            if isinstance(value, list) and all(isinstance(r, dict) for r in value):
                tables = [_table("", value, [f"/{i}" for i in range(len(value))], limits)]
            elif isinstance(value, dict):
                # Only immediate object-record arrays are collections. No
                # recursive envelope guessing; all other fields are metadata.
                arrays = {k: v for k, v in value.items()
                          if isinstance(v, list) and v
                          and all(isinstance(r, dict) for r in v)}
                metadata = {k: v for k, v in value.items() if k not in arrays}
                if metadata:
                    flat = flatten(metadata, limits)
                    tables.append({"path": "", "kind": "metadata",
                                   "description": "File metadata", "headers": ["field", "value"],
                                   "rows": [[k, literal(v)] for k, v in sorted(flat.items())],
                                   "locators": sorted(flat), "values": []})
                for key in sorted(arrays):
                    path = pointer("", key)
                    tables.append(_table(path, arrays[key],
                                         [pointer(path, i) for i in range(len(arrays[key]))], limits))
                if not value:
                    warnings.append("empty object; no fields or records")
            else:
                return {"tables": [], "warnings": ["unsupported root shape; UTF-8 fallback"],
                        "supported": False, "records": 0, "complete": False}
        count = sum(len(t["rows"]) for t in tables)
        if count > limits["max_records"]:
            raise StructuredError("max_records exceeded across collections")
        if sum(len(t["rows"]) * len(t["headers"]) for t in tables) > limits["max_cells"]:
            raise StructuredError("max_cells exceeded across collections")
        return {"tables": tables, "warnings": warnings, "supported": True,
                "records": sum(len(t["rows"]) for t in tables if t["kind"] == "records"),
                "complete": True}
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StructuredError(str(exc)) from exc


def gfm(headers, rows):
    def cell(s):
        # Escape source HTML, links, fetched markers, pipes, and line breaks.
        return (html.escape(str(s), quote=False).replace("|", "&#124;")
                .replace("[", "&#91;").replace("]", "&#93;")
                .replace("`", "&#96;").replace("\n", "&#10;").replace("\r", "&#13;"))
    return "\n".join(["| " + " | ".join(map(cell, headers)) + " |",
                      "|" + "|".join("---" for _ in headers) + "|"] +
                     ["| " + " | ".join(map(cell, r)) + " |" for r in rows])


def preview(data, cap):
    blocks, used, truncated = [], 0, False
    for i, table in enumerate(data["tables"], 1):
        # Headers and individual rows are indivisible. Full rows always
        # remain accessible through the original, even if no row fits.
        block = f"## Collection {i} ({table['kind']})\n\n" + gfm(table["headers"], [])
        block_bytes = len(block.encode()) + (2 if blocks else 0)
        if used + block_bytes > cap:
            truncated = True
            break
        used += block_bytes
        lines = [block]
        for row in table["rows"]:
            line = gfm(table["headers"], [row]).splitlines()[-1]
            size = len(line.encode()) + 1
            if used + size > cap:
                truncated = True
                break
            used += size
            lines.append(line)
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    return text, truncated


def column_summary(headers, rows):
    from decimal import Decimal
    out = []
    for i, name in enumerate(headers):
        col = [r[i] for r in rows]
        present = [v for v in col if v not in ("null", "⟨missing⟩")]
        nums = []
        for v in present:
            try:
                nums.append(loads(v))
            except ValueError:
                nums.append(v)
        numeric = bool(nums) and all(isinstance(v, Number) for v in nums)
        info = {"name": name, "non_null": len(present), "total": len(col),
                "dtype": "numeric" if numeric else "json-literal"}
        if numeric:
            info.update(min=str(min(map(Decimal, nums))), max=str(max(map(Decimal, nums))))
        else:
            values = list(dict.fromkeys(present))
            info.update(distinct_count=len(values), sample=[v[:160] for v in values[:3]])
        out.append(info)
    return out


def load_extraction(path):
    """Replay only declared structured extractions, verifying original bytes."""
    from naming import read_frontmatter
    path = Path(path).resolve()
    fm, _ = read_frontmatter(path.read_text())
    if fm.get("structured_version") != VERSION:
        raise StructuredError("unsupported structured_version; re-ingest original")
    kept = fm.get("kept_as")
    original = path.parent / kept if kept else Path(fm.get("source_path", ""))
    if kept and (Path(kept).name != kept or original.is_symlink()):
        raise StructuredError("unsafe kept_as")
    if not original.is_file() or original.is_symlink():
        raise StructuredError("original unavailable; retain source for replay")
    cfg = json.loads(fm["structured_options"])
    if original.stat().st_size > options(cfg)["max_raw_bytes"]:
        raise StructuredError("max_raw_bytes exceeded")
    raw = original.read_bytes()
    if hashlib.sha256(raw).hexdigest() != fm.get("sha256"):
        raise StructuredError("original hash changed; re-ingest as new evidence")
    data = extract(raw, fm["structured_format"], cfg)
    if not data["supported"]:
        raise StructuredError("original no longer matches structured contract")
    return fm, data
