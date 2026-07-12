#!/usr/bin/env python3
"""restyle.py — wiki-wide style rewrite wave.

Restyle is a CURATE-shaped wave that enumerates every page (not just
the worst-scoring ones, as repair-mode does) and rewrites each in a
target style: succinct readable prose (`prose-v1`), lite-compression
(`caveman-lite-v1` — historical name retained for backward compat),
or ultra-compression (`caveman-ultra-v1` — likewise historical).
Bidirectional — hydrate compressed pages to prose, or compress prose
back, on demand.

Resumability + idempotency come from a frontmatter marker `style:
<target-id>` on each page. The wave skips pages whose style already
matches the target, so re-runs pick up exactly where the previous run
left off — including across days, sessions, and rate-limit pauses.

The script does not dispatch workers itself (that's the orchestrator's
job, via the Agent tool, following SKILL.md § RESTYLE). It provides the
mechanical primitives the orchestrator needs:

  plan         enumerate + filter + cost estimate
  mark         set the `style:` frontmatter key on a single page
  progress     count pages by style state
  score-check  ratchet wrapper that runs score_diff with --bloat-mult 2.0

Stdlib only. Hash-guarded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Valid restyle targets. The `-v1` suffix is the marker version: bumping
# to `-v2` lets a future schema change re-process every page without an
# `--force` flag at the operator level.
VALID_TARGETS = ("prose-v1", "caveman-lite-v1", "caveman-ultra-v1")

# Per-target bloat-cap override. Hydration legitimately expands the body;
# compression shrinks it (1.5× cap is irrelevant). 2.0× covers ultra→
# prose expansion (~1.55× typical) with headroom; the reviewer catches
# anything that actually drifts into bloat.
BLOAT_CAP = {
    "prose-v1":          2.0,
    "caveman-lite-v1":   1.5,
    "caveman-ultra-v1":  1.5,
}

# Rough per-page input/output token estimate for the cost prediction.
# Used only for the plan-stage estimate; actual cost is bounded by the
# wave wallclock budget.
AVG_PAGE_TOKENS_IN = 1500
AVG_PAGE_TOKENS_OUT = 1500
# Sonnet 4.6 list rates (USD/M tokens) as of 2026-05. Off by ~10% across
# vendors; serves as a planning-floor estimate, not a billing surface.
COST_PER_M_IN = 3.0
COST_PER_M_OUT = 15.0


# ---------------------------------------------------------------------------
# Frontmatter helpers (lightweight — only top-level scalar keys needed)
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block_with_fences, body). If no frontmatter,
    return ('', original_text)."""
    if not text.startswith("---"):
        return "", text
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return "", text
    # Include trailing newline after the closing fence so body starts cleanly.
    rest = text[end + 4:]
    if rest.startswith("\n"):
        rest = rest[1:]
    return text[: end + 4], rest


def _fm_value(fm_block: str, key: str) -> str | None:
    if not fm_block:
        return None
    for line in fm_block.splitlines():
        s = line.strip()
        if s.startswith(f"{key}:"):
            v = s.split(":", 1)[1].strip()
            if v.startswith('"') and v.endswith('"') and len(v) >= 2:
                v = v[1:-1]
            return v
    return None


def _set_fm_value(fm_block: str, key: str, value: str) -> str:
    """Insert-or-update `key: value` in a frontmatter block. Preserves
    line order. Quotes the value if it contains characters that would
    confuse strict YAML parsers."""
    needs_quote = bool(re.search(r'[\[\]:#&*!|>%@`]', value))
    line_value = f'"{value}"' if needs_quote else value
    new_line = f"{key}: {line_value}"

    lines = fm_block.splitlines()
    out_lines: list[str] = []
    found = False
    for line in lines:
        s = line.lstrip()
        if s.startswith(f"{key}:"):
            indent = line[: len(line) - len(s)]
            out_lines.append(f"{indent}{new_line}")
            found = True
        else:
            out_lines.append(line)
    if not found:
        # Insert before the closing fence.
        # Walk from end to find the second `---` line (closing fence).
        insert_at = None
        for i in range(len(out_lines) - 1, -1, -1):
            if out_lines[i].strip() == "---":
                insert_at = i
                break
        if insert_at is None:
            # Shouldn't happen for well-formed FM, but be defensive.
            out_lines.append(new_line)
        else:
            out_lines.insert(insert_at, new_line)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Wiki enumeration
# ---------------------------------------------------------------------------


SKIP_DIR_PREFIXES = (
    "_assets",   # gitignored figure assets
    "_suspect",  # quarantined sources
    ".",         # any dotfile dir
)


def _enumerate_pages(wiki: Path, types: list[str] | None) -> list[Path]:
    """List all .md pages under wiki/, optionally restricted to a set of
    type subdirectories (e.g. ['analyses', 'evidence'])."""
    if not wiki.is_dir():
        return []
    if types:
        out: list[Path] = []
        for t in types:
            d = wiki / t
            if d.is_dir():
                out.extend(p for p in d.rglob("*.md") if _eligible(p))
        return sorted(out)
    return sorted(p for p in wiki.rglob("*.md") if _eligible(p))


def _eligible(page: Path) -> bool:
    # Skip hub/index pages and anything under _assets / _suspect / dotdirs.
    if any(part.startswith(SKIP_DIR_PREFIXES) for part in page.parts):
        return False
    if page.name in {"index.md", "notes.md", "todos.md"}:
        return False
    return True


def _current_style(page: Path) -> str | None:
    try:
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, _ = _split_frontmatter(text)
    return _fm_value(fm, "style")


def _body_token_estimate(page: Path) -> int:
    """Cheap word-count proxy on the body; used to refine the per-page
    cost estimate beyond the AVG_PAGE_TOKENS_IN constant."""
    try:
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return AVG_PAGE_TOKENS_IN
    _, body = _split_frontmatter(text)
    return max(50, len(body.split()))


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_plan(args):
    wiki = Path(args.wiki).expanduser().resolve()
    target = args.target
    if target not in VALID_TARGETS:
        print(f"invalid --target {target!r}; pick one of {VALID_TARGETS}",
              file=sys.stderr)
        sys.exit(2)
    types = [t.strip() for t in args.types.split(",")] if args.types else None
    pages = _enumerate_pages(wiki, types)

    candidates: list[dict] = []
    already_styled = 0
    other_styled = 0
    for p in pages:
        cs = _current_style(p)
        if cs == target:
            already_styled += 1
            continue
        if cs is not None and cs in VALID_TARGETS and cs != target:
            other_styled += 1
        rel = str(p.relative_to(wiki))
        candidates.append({
            "path": rel,
            "current_style": cs,
            "tokens_estimate": _body_token_estimate(p),
        })

    if args.limit and args.limit > 0:
        candidates = candidates[: args.limit]

    # Cost estimate. Multiplier of 1 for compression (output shorter
    # than input) and 1.5 for prose hydration. Spot-audit at 1-in-5
    # adds reviewer cost (rough: reviewer is opus, ~5× sonnet rate;
    # 20% of pages reviewed → effective +20% × 5 = +1× on output side).
    is_hydration = target == "prose-v1"
    out_mult = 1.5 if is_hydration else 0.65
    spot_audit_factor = 1.0 + (0.20 * 5.0)  # 1-in-5 reviewer pass at ~5× rate

    in_tok = sum(c["tokens_estimate"] for c in candidates)
    out_tok = int(in_tok * out_mult)
    cost_low = ((in_tok / 1_000_000) * COST_PER_M_IN
                + (out_tok / 1_000_000) * COST_PER_M_OUT)
    cost_high = cost_low * spot_audit_factor
    # Pad ±25% for vendor pricing variance and prompt overhead.
    cost_low *= 0.75
    cost_high *= 1.25

    summary = {
        "target": target,
        "wiki": str(wiki),
        "types_filter": types,
        "pages_total": len(pages),
        "pages_already_target": already_styled,
        "pages_in_other_style": other_styled,
        "pages_to_restyle": len(candidates),
        "estimated_cost_usd_low": round(cost_low, 2),
        "estimated_cost_usd_high": round(cost_high, 2),
        "bloat_cap": BLOAT_CAP[target],
        "spot_audit_rate": "1-in-5",
        "candidates": [c["path"] for c in candidates],
    }
    print(json.dumps(summary, indent=2))


def cmd_mark(args):
    page = Path(args.page).resolve()
    if not page.is_file():
        print(f"page not found: {page}", file=sys.stderr)
        sys.exit(1)
    if args.style not in VALID_TARGETS:
        print(f"invalid --style {args.style!r}; pick one of {VALID_TARGETS}",
              file=sys.stderr)
        sys.exit(2)
    text = page.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    if not fm:
        print(f"page has no frontmatter: {page}", file=sys.stderr)
        sys.exit(1)
    fm_new = _set_fm_value(fm, "style", args.style)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fm_new = _set_fm_value(fm_new, "updated", today)
    page.write_text(fm_new + "\n" + body, encoding="utf-8")
    print(json.dumps({
        "path": str(page),
        "style": args.style,
        "updated": today,
    }))


def cmd_progress(args):
    wiki = Path(args.wiki).expanduser().resolve()
    pages = _enumerate_pages(wiki, None)
    counts: dict[str, int] = {"unstyled": 0}
    for t in VALID_TARGETS:
        counts[t] = 0
    other: dict[str, int] = {}
    for p in pages:
        cs = _current_style(p)
        if cs is None:
            counts["unstyled"] += 1
        elif cs in VALID_TARGETS:
            counts[cs] += 1
        else:
            other[cs] = other.get(cs, 0) + 1
    out = {
        "wiki": str(wiki),
        "pages_total": len(pages),
        "by_style": counts,
    }
    if other:
        out["unrecognised_styles"] = other
    print(json.dumps(out, indent=2))


def cmd_score_check(args):
    """Thin wrapper around score_diff.py that injects the target-
    specific bloat cap. The orchestrator pipes the worker's rewrite to
    stdin and reads the JSON verdict back, identical to a plain
    score_diff call — just with the relaxed cap baked in."""
    target = args.target
    if target not in VALID_TARGETS:
        print(json.dumps({"applied": False,
                          "error": f"invalid --target {target!r}"}))
        sys.exit(2)
    cap = BLOAT_CAP[target]
    cmd = [
        sys.executable, str(SCRIPT_DIR / "score_diff.py"), args.page,
        "--new-text-stdin", "--dry-run", "--bloat-mult", str(cap),
    ]
    if args.vault_db:
        cmd += ["--vault-db", args.vault_db]
    if args.tables_db:
        cmd += ["--tables-db", args.tables_db]
    # Pass through stdin transparently.
    r = subprocess.run(cmd, stdin=sys.stdin, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(prog="restyle.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("plan", help="enumerate + filter + cost estimate")
    s.add_argument("wiki")
    s.add_argument("--target", required=True,
                   choices=list(VALID_TARGETS))
    s.add_argument("--types", default=None,
                   help="comma-separated subdir names to restrict to "
                        "(default: all 8 wiki types)")
    s.add_argument("--limit", type=int, default=0,
                   help="cap the candidate list to N pages (0 = no cap)")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("mark", help="set the `style:` frontmatter key")
    s.add_argument("page")
    s.add_argument("--style", required=True, choices=list(VALID_TARGETS))
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser("progress", help="count pages by style state")
    s.add_argument("wiki")
    s.set_defaults(fn=cmd_progress)

    s = sub.add_parser("score-check",
                       help="score_diff wrapper with target-specific bloat cap")
    s.add_argument("page")
    s.add_argument("--target", required=True, choices=list(VALID_TARGETS))
    s.add_argument("--vault-db", default=None)
    s.add_argument("--tables-db", default=None)
    s.set_defaults(fn=cmd_score_check)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
