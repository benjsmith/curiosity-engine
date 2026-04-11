#!/usr/bin/env python3
"""Apply one worker-proposed edit and score it with compression-progress rules.

This is the deterministic apply-and-grade gate in the ITERATE batch. Workers
return a diff spec; the main session feeds each spec through this script. On
accept, the file is written to disk so the batch commit picks it up. On reject,
the file is untouched.

Acceptance rules (mirror SKILL.md ITERATE spec):
  1. sourced_claims(after) >= sourced_claims(before)        — no citation loss
  2. compressed_tokens(after) <= compressed_tokens(before) * 1.2  — no bloat
  3. tpc decreased OR wikilink count increased              — real progress

Usage:
    echo "<new text>" | python3 score_diff.py <page.md> --new-text-stdin
    python3 score_diff.py <page.md> --new-file <candidate.md>

Outputs one JSON verdict line to stdout. Exit code is always 0 on well-formed
input; parse `accept` and `applied` fields to branch.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from compress import compress, token_count, sourced_claims  # noqa: E402


def metrics(text: str) -> dict:
    comp = compress(text)
    return {
        "tokens": token_count(comp),
        "claims": max(sourced_claims(text), 1),
        "wikilinks": text.count("[["),
    }


def tpc(m: dict) -> float:
    return m["tokens"] / m["claims"]


def verdict(before: dict, after: dict) -> tuple:
    if after["claims"] < before["claims"]:
        return False, f"sourced_claims dropped ({before['claims']}->{after['claims']})"
    if after["tokens"] > before["tokens"] * 1.2:
        return False, f"token bloat ({before['tokens']}->{after['tokens']}, >20%)"
    tpc_progress = tpc(after) < tpc(before)
    link_progress = after["wikilinks"] > before["wikilinks"]
    if not (tpc_progress or link_progress):
        return False, "no compression progress: tpc flat and no new wikilink"
    if tpc_progress:
        return True, f"tpc {tpc(before):.1f}->{tpc(after):.1f}"
    return True, f"wikilinks {before['wikilinks']}->{after['wikilinks']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--new-file", default=None)
    ap.add_argument("--new-text-stdin", action="store_true")
    args = ap.parse_args()

    page = Path(args.page)
    if not page.exists():
        print(json.dumps({"error": f"page not found: {page}", "applied": False}))
        return

    old_text = page.read_text()
    if args.new_file:
        new_text = Path(args.new_file).read_text()
    elif args.new_text_stdin:
        new_text = sys.stdin.read()
    else:
        print(json.dumps({"error": "need --new-file or --new-text-stdin", "applied": False}))
        return

    before = metrics(old_text)
    after = metrics(new_text)
    accept, reason = verdict(before, after)

    result = {
        "page": str(page),
        "accept": accept,
        "reason": reason,
        "before": before,
        "after": after,
        "applied": False,
    }
    if accept:
        page.write_text(new_text)
        result["applied"] = True

    print(json.dumps(result))


if __name__ == "__main__":
    main()
