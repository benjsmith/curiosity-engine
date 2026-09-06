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
PREVIEW_VERSION = "json-markdown-v2"
DEFAULT_LIMITS = {"max_raw_bytes": 50 * 1024 * 1024, "max_depth": 64,
                  "max_fields": 512, "max_cell_bytes": 1024 * 1024,
                  "max_records": 100000, "max_cells": 2000000,
                  "max_stage_bytes": 512 * 1024 * 1024}
MISSING = object()


def write_once(path, raw):
    """Publish complete bytes exclusively; failed writes leave no final file."""
    import os
    import tempfile
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".structured-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Unlike replace(), link() refuses to overwrite an existing artifact.
        os.link(tmp, path)
    finally:
        os.unlink(tmp)


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
    result = {k: int(cfg.get(k, v)) for k, v in DEFAULT_LIMITS.items()}
    if "record_pointer" in cfg:
        result["record_pointer"] = cfg["record_pointer"]
        result["metadata_pointers"] = cfg.get("metadata_pointers", [])
    return result


def resolve_pointer(value, path):
    import re
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise StructuredError("invalid JSON pointer")
    for part in path.split("/")[1:] if path else []:
        if re.search(r"~(?![01])", part):
            raise StructuredError("invalid JSON pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise KeyError(part)
        except (KeyError, IndexError, ValueError):
            raise StructuredError(f"JSON pointer not found: {path}") from None
    return value


def selected_tables(value, limits):
    path = limits["record_pointer"]
    records = resolve_pointer(value, path)
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise StructuredError("record_pointer must select an array of objects")
    metadata = limits.get("metadata_pointers", [])
    if not isinstance(metadata, list) or any(not isinstance(p, str) for p in metadata):
        raise StructuredError("metadata_pointers must be a list of JSON pointers")
    paths = [path] + metadata
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if a == b or a == "" or b == "" or a.startswith(b + "/") or b.startswith(a + "/"):
                raise StructuredError("record and metadata pointers must not overlap")
    tables = []
    for p in metadata:
        item = resolve_pointer(value, p)
        flat = flatten(item, limits, p) if isinstance(item, dict) and item else {p: item}
        tables.append({"path": p, "kind": "metadata", "description": "Metadata " + p,
            "headers": ["field", "value"], "rows": [[k, literal(v)] for k, v in sorted(flat.items())],
            "locators": sorted(flat), "values": []})
    tables.append(_table(path, records, [pointer(path, i) for i in range(len(records))], limits))
    return tables


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
    if any(limits[k] <= 0 for k in DEFAULT_LIMITS):
        raise StructuredError("limits must be positive")
    if len(raw) > limits["max_raw_bytes"]:
        raise StructuredError("max_raw_bytes exceeded")
    try:
        tables, warnings = [], []
        if fmt == "jsonl":
            if "record_pointer" in limits:
                raise StructuredError("selectors are supported for JSON only")
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
            if "record_pointer" in limits:
                tables = selected_tables(value, limits)
            elif isinstance(value, list) and all(isinstance(r, dict) for r in value):
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


def markdown_literal(s):
    """Escape literal data without permitting Markdown or wrapper syntax."""
    return (html.escape(str(s), quote=False).replace("|", "&#124;")
            .replace("[", "&#91;").replace("]", "&#93;")
            .replace("`", "&#96;").replace("\\", "&#92;")
            .replace("*", "&#42;").replace("_", "&#95;").replace("~", "&#126;")
            .replace("\n", "&#10;").replace("\r", "&#13;"))


def gfm(headers, rows):
    cell = markdown_literal
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
    if hasattr(rows, "stage"):
        return rows.stage.summary()
    from decimal import Decimal, DecimalException
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
            try:
                info.update(min=str(min(map(Decimal, nums))), max=str(max(map(Decimal, nums))))
            except (DecimalException, ValueError, OverflowError):
                info["summary_warning"] = "numeric range exceeds summary limits; consult literal cells"
        else:
            values = list(dict.fromkeys(present))
            info.update(distinct_count=len(values), sample=[v[:160] for v in values[:3]])
        out.append(info)
    return out


def table_hash(headers, rows):
    """Hash the legacy JSON representation without materializing every row."""
    sha = hashlib.sha256()
    sha.update(("[" + json.dumps(headers, ensure_ascii=True) + ", [").encode())
    for i, row in enumerate(rows):
        if i:
            sha.update(b", ")
        sha.update(json.dumps(row, ensure_ascii=True).encode())
    sha.update(b"]]")
    return sha.hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def copy_once(source, target):
    import os
    import shutil
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".structured-", dir=Path(target).parent)
    try:
        with os.fdopen(fd, "wb") as out, Path(source).open("rb") as stream:
            shutil.copyfileobj(stream, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        os.link(tmp, target)
    finally:
        os.unlink(tmp)


def extract_path(path, fmt, cfg=None):
    """JSON is bounded in memory; JSONL is validated and staged one line at a time."""
    path = Path(path)
    limits = options(cfg)
    if any(limits[k] <= 0 for k in DEFAULT_LIMITS):
        raise StructuredError("limits must be positive")
    if path.stat().st_size > limits["max_raw_bytes"]:
        raise StructuredError("max_raw_bytes exceeded")
    if fmt != "jsonl":
        raw = path.read_bytes()
        data = extract(raw, fmt, cfg)
        data.update(_raw_bytes=raw, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        return data
    if "record_pointer" in limits:
        raise StructuredError("selectors are supported for JSON only")
    from dataset_stage import Stage
    from scrub_check import _scan_markers
    stage = Stage(limits)
    total, sha = 0, hashlib.sha256()
    try:
        with path.open("rb") as stream, (stage.path / "original.jsonl").open("wb") as original:
            line_no = 0
            while True:
                line = stream.readline(limits["max_raw_bytes"] + 1)
                if not line:
                    break
                line_no += 1
                total += len(line)
                if total > limits["max_raw_bytes"]:
                    raise StructuredError("max_raw_bytes exceeded")
                original.write(line)
                sha.update(line)
                if not line.strip():
                    continue
                try:
                    text = line.decode("utf-8-sig")
                    item = loads(text)
                    if not isinstance(item, dict):
                        raise StructuredError("JSONL record must be an object")
                    _check(item, limits)
                    scrub(item)
                    if _scan_markers(text, "full"):
                        raise StructuredError("scrub: injection markers")
                    stage.add(flatten(item, limits), {"locator": f"line:{line_no}"})
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise StructuredError(f"JSONL line {line_no}: {exc}") from exc
        stage.check_disk()
        return {"tables": [stage.table()], "warnings": [], "supported": True,
                "records": stage.count, "complete": True, "sha256": sha.hexdigest(), "bytes": total,
                "_stage": stage, "_raw_path": stage.path / "original.jsonl"}
    except BaseException:
        stage.close()
        raise


def close_data(data):
    if data.get("_stage") is not None:
        data["_stage"].close()


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
    data = extract_path(original, fm["structured_format"], cfg)
    if data["sha256"] != fm.get("sha256"):
        close_data(data)
        raise StructuredError("original hash changed; re-ingest as new evidence")
    if not data["supported"]:
        close_data(data)
        raise StructuredError("original no longer matches structured contract")
    return fm, data
