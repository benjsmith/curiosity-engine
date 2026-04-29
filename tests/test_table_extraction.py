#!/usr/bin/env python3
"""test_table_extraction.py — multi-extractor comparison harness.

Runs each backend in tests/extractors/ over each fixture in
tests/fixtures/tables/ and prints a pass/partial/fail comparison grid
plus a JSON summary. The harness is a *measurement* — it does not gate.

Usage:
    uv run python3 tests/test_table_extraction.py
    uv run python3 tests/test_table_extraction.py --json     # summary JSON only
    uv run python3 tests/test_table_extraction.py --markdown # write RESULTS.md

Backends are imported lazily; missing optional deps degrade to
`unavailable` for that backend without aborting the run.

Assertion vocabulary (per *.expected.yaml):
    must_contain: [str, ...]               substring matches against body
    must_not_contain: [str, ...]           negative substring matches
    precision_check.min_pipe_rows: N       count `\\n|` in body >= N
    precision_check.min_word_count: N      body word count >= N
    expected_method: <str>                 frontmatter method (baseline only)

Verdict per (fixture, backend):
    pass    — all must_contain hits, all must_not_contain misses, all
              precision_checks pass.
    partial — ≥1 must_contain hits AND no failures other than missing
              must_contain entries OR precision shortfalls.
    fail    — must_not_contain matched, OR backend errored / not_extracted,
              OR zero must_contain hits.
    n/a     — backend doesn't claim to support this fixture's kind.
    unavailable — backend missing required deps.
"""
import argparse
import importlib
import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "tables"
EXTRACTORS = HERE / "extractors"
sys.path.insert(0, str(HERE))


def _load_yaml(path: Path) -> dict:
    """Minimal YAML loader, prefers PyYAML, falls back to a safe subset."""
    try:
        import yaml
        return yaml.safe_load(path.read_text())
    except ImportError:
        return _fallback_yaml(path.read_text())


def _fallback_yaml(text: str) -> dict:
    """Just enough to parse our expected.yaml shape if PyYAML is absent."""
    out = {}
    stack = [(out, -1)]
    cur_list_key = None
    cur_list = None
    for raw_line in text.split("\n"):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()
        if line.startswith("- "):
            val = line[2:].strip().strip('"').strip("'")
            if cur_list is not None:
                cur_list.append(val)
            continue
        cur_list = None
        while stack and stack[-1][1] >= indent:
            stack.pop()
        parent = stack[-1][0]
        if ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v == "":
                child = {}
                parent[k] = child
                stack.append((child, indent))
            elif v.startswith("[") and v.endswith("]"):
                inner = v[1:-1].strip()
                parent[k] = [s.strip().strip('"').strip("'")
                             for s in inner.split(",") if s.strip()]
            else:
                if v.replace(".", "", 1).replace("-", "", 1).isdigit():
                    parent[k] = float(v) if "." in v else int(v)
                elif v in ("true", "false"):
                    parent[k] = (v == "true")
                else:
                    parent[k] = v
            if v == "":
                # Next line(s) may be a list under this key
                cur_list_key = k
                cur_list = []
                parent[k] = cur_list
    return out


def _load_backends() -> list:
    backends = []
    for f in sorted(EXTRACTORS.glob("*.py")):
        if f.name.startswith("_"):
            continue
        modname = f"extractors.{f.stem}"
        spec = importlib.util.spec_from_file_location(modname, f)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"!! failed to load backend {f.stem}: {e}", file=sys.stderr)
            continue
        backends.append(mod)
    return backends


def _evaluate(spec: dict, body: str) -> dict:
    """Return verdict + structured failure list."""
    misses = {"must_contain": [], "must_not_contain": [],
              "precision_check": []}
    for s in spec.get("must_contain", []) or []:
        if s not in body:
            misses["must_contain"].append(s)
    for s in spec.get("must_not_contain", []) or []:
        if s in body:
            misses["must_not_contain"].append(s)
    pc = spec.get("precision_check") or {}
    if "min_pipe_rows" in pc:
        n = body.count("\n|")
        if n < pc["min_pipe_rows"]:
            misses["precision_check"].append(
                f"min_pipe_rows: got {n}, want >= {pc['min_pipe_rows']}"
            )
    if "min_word_count" in pc:
        n = len(body.split())
        if n < pc["min_word_count"]:
            misses["precision_check"].append(
                f"min_word_count: got {n}, want >= {pc['min_word_count']}"
            )

    must_total = len(spec.get("must_contain") or [])
    must_misses = len(misses["must_contain"])
    must_hits = must_total - must_misses
    any_misses = any(misses[k] for k in misses)

    if not any_misses:
        verdict = "pass"
    elif misses["must_not_contain"] or must_total == 0 or must_hits == 0:
        verdict = "fail"
    else:
        verdict = "partial"
    return {
        "verdict": verdict,
        "must_hits": must_hits,
        "must_total": must_total,
        "misses": misses,
    }


def _supports(backend, kind: str) -> bool:
    return kind in getattr(backend, "SUPPORTS", set())


def _short(verdict: str, ev: dict) -> str:
    if verdict == "pass":
        return "PASS"
    if verdict == "partial":
        return f"PART({ev['must_hits']}/{ev['must_total']})"
    if verdict == "fail":
        return "FAIL"
    if verdict == "n/a":
        return "n/a"
    if verdict == "unavailable":
        return "UNAV"
    return verdict


def run() -> dict:
    fixtures = []
    for spec_path in sorted(FIXTURES.glob("*.expected.yaml")):
        spec = _load_yaml(spec_path)
        fname = spec["fixture"]
        fixtures.append({
            "name": fname,
            "kind": spec.get("kind", Path(fname).suffix.lstrip(".")),
            "spec": spec,
            "path": FIXTURES / fname,
        })

    backends = _load_backends()
    grid = {}
    summary = {"backends": [], "fixtures": [f["name"] for f in fixtures], "cells": {}}

    for b in backends:
        avail, avail_msg = b.available()
        summary["backends"].append({
            "name": b.NAME,
            "available": avail,
            "error": avail_msg if not avail else None,
        })

    for f in fixtures:
        grid[f["name"]] = {}
        for b in backends:
            cell = grid[f["name"]][b.NAME] = {}
            avail, avail_msg = b.available()
            if not avail:
                cell["verdict"] = "unavailable"
                cell["short"] = _short("unavailable", {})
                cell["error"] = avail_msg
                continue
            if not _supports(b, f["kind"]):
                cell["verdict"] = "n/a"
                cell["short"] = "n/a"
                continue
            try:
                result = b.extract(f["path"])
            except Exception as e:
                cell["verdict"] = "fail"
                cell["short"] = "FAIL"
                cell["error"] = f"{type(e).__name__}: {e}"
                continue
            if not result.get("available", True):
                cell["verdict"] = "unavailable"
                cell["short"] = "UNAV"
                cell["error"] = result.get("error")
                continue
            body = result.get("body", "") or ""
            err = result.get("error")
            if not body and err:
                cell["verdict"] = "fail"
                cell["short"] = "FAIL"
                cell["error"] = err
                continue
            ev = _evaluate(f["spec"], body)
            cell["verdict"] = ev["verdict"]
            cell["short"] = _short(ev["verdict"], ev)
            cell["misses"] = ev["misses"]
            cell["must_hits"] = ev["must_hits"]
            cell["must_total"] = ev["must_total"]
            cell["body_words"] = len(body.split())
            cell["body_pipe_rows"] = body.count("\n|")
            if result.get("extra"):
                cell["extra"] = result["extra"]
            if err:
                cell["note"] = err

    summary["cells"] = grid
    return summary


def render_grid(summary: dict) -> str:
    backend_names = [b["name"] for b in summary["backends"]]
    rows = []
    header = ["fixture"] + backend_names
    rows.append(header)
    for fx in summary["fixtures"]:
        row = [fx]
        for bn in backend_names:
            cell = summary["cells"].get(fx, {}).get(bn, {"short": "?"})
            row.append(cell.get("short", "?"))
        rows.append(row)
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join(fmt.format(*r) for r in rows)


def render_markdown(summary: dict) -> str:
    backend_names = [b["name"] for b in summary["backends"]]
    out = []
    out.append("# Table-extraction comparison results")
    out.append("")
    out.append("Per-fixture verdicts across the four backend adapters under "
               "`tests/extractors/`. Verdict legend: PASS = every assertion "
               "satisfied; PART(h/t) = `must_contain` short by t-h items but "
               "no other failures; FAIL = a `must_not_contain` matched, the "
               "backend errored, or zero `must_contain` hits; n/a = backend "
               "doesn't claim to support this fixture kind; UNAV = backend "
               "missing optional deps.")
    out.append("")
    avail_lines = []
    for b in summary["backends"]:
        if b["available"]:
            avail_lines.append(f"- `{b['name']}` — available")
        else:
            avail_lines.append(f"- `{b['name']}` — UNAVAILABLE: {b['error']}")
    out.extend(avail_lines)
    out.append("")
    out.append("## Comparison grid")
    out.append("")
    header = "| fixture | " + " | ".join(f"`{n}`" for n in backend_names) + " |"
    sep = "|---|" + "|".join(["---"] * len(backend_names)) + "|"
    out.append(header)
    out.append(sep)
    for fx in summary["fixtures"]:
        row = [fx]
        for bn in backend_names:
            cell = summary["cells"].get(fx, {}).get(bn, {})
            row.append(cell.get("short", "?"))
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    out.append("## Per-cell detail")
    out.append("")
    for fx in summary["fixtures"]:
        out.append(f"### `{fx}`")
        out.append("")
        for bn in backend_names:
            cell = summary["cells"].get(fx, {}).get(bn, {})
            v = cell.get("verdict", "?")
            line = f"- **{bn}** — {cell.get('short', '?')}"
            if v in ("partial", "fail") and cell.get("misses"):
                missing = []
                m = cell["misses"]
                if m.get("must_contain"):
                    missing.append("missing strings: "
                                   + ", ".join(repr(s) for s in m["must_contain"]))
                if m.get("must_not_contain"):
                    missing.append("forbidden strings present: "
                                   + ", ".join(repr(s) for s in m["must_not_contain"]))
                if m.get("precision_check"):
                    missing.extend(m["precision_check"])
                if missing:
                    line += " — " + "; ".join(missing)
            if cell.get("note"):
                line += f" (note: {cell['note']})"
            if cell.get("error") and v in ("fail", "unavailable"):
                line += f" (error: {cell['error']})"
            if cell.get("extra"):
                line += f" [{cell['extra']}]"
            out.append(line)
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable summary JSON only")
    ap.add_argument("--markdown", action="store_true",
                    help="write tests/fixtures/tables/RESULTS.md")
    args = ap.parse_args()

    summary = run()

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return 0

    print(render_grid(summary))
    print()
    cell_counts = {}
    for fx in summary["fixtures"]:
        for bn, cell in summary["cells"].get(fx, {}).items():
            v = cell.get("verdict", "?")
            cell_counts[v] = cell_counts.get(v, 0) + 1
    print("verdict counts:", json.dumps(cell_counts))
    print()
    for b in summary["backends"]:
        if not b["available"]:
            print(f"hint: install missing deps for {b['name']} — {b['error']}")

    if args.markdown:
        out = FIXTURES / "RESULTS.md"
        md = render_markdown(summary)
        out.write_text(md)
        print(f"wrote {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
