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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from compress import compress, token_count, sourced_claims  # noqa: E402

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def lint_matchable_links(text: str) -> int:
    """Count wikilinks in lint-recognized form (hyphen-case, no spaces).

    lint_scores checks ``f"[[{stem}]]" in text_lower`` where stem is
    hyphen-case. ``[[deep-learning]]`` matches; ``[[Deep Learning]]`` becomes
    ``[[deep learning]]`` when lowered and does NOT match the stem
    ``deep-learning``. So only space-free targets register as recognized links.
    This fixes the Phase 1 finding where Title Case→hyphen conversions were
    invisible to score_diff's raw ``[[`` count.
    """
    count = 0
    for m in WIKILINK_RE.finditer(text):
        target = m.group(1).strip()
        if " " not in target:
            count += 1
    return count


def metrics(text: str, orphan_target: str = None) -> dict:
    comp = compress(text)
    m = {
        "tokens": token_count(comp),
        "claims": max(sourced_claims(text), 1),
        "wikilinks": lint_matchable_links(text),
    }
    if orphan_target:
        text_lower = text.lower()
        m["has_orphan_link"] = (
            f"[[{orphan_target}]]" in text_lower
            or f"[[{orphan_target}|" in text_lower
        )
    return m


def tpc(m: dict) -> float:
    return m["tokens"] / m["claims"]


def verdict(before: dict, after: dict, orphan_target: str = None) -> tuple:
    if after["claims"] < before["claims"]:
        return False, f"sourced_claims dropped ({before['claims']}->{after['claims']})"
    if after["tokens"] > before["tokens"] * 1.2:
        return False, f"token bloat ({before['tokens']}->{after['tokens']}, >20%)"

    tpc_progress = tpc(after) < tpc(before)
    link_progress = after["wikilinks"] > before["wikilinks"]
    claim_progress = after["claims"] > before["claims"]

    # Orphan-specific gate: if the edit adds the target orphan link, that
    # alone is sufficient progress — the whole point of the brief was to
    # create this inbound link. Hard floors (claims, bloat) still apply.
    orphan_link_added = (
        orphan_target
        and after.get("has_orphan_link")
        and not before.get("has_orphan_link")
    )
    if orphan_link_added:
        parts = [f"orphan link [[{orphan_target}]] added"]
        if tpc_progress:
            parts.append(f"tpc {tpc(before):.1f}->{tpc(after):.1f}")
        if link_progress:
            parts.append(f"wikilinks {before['wikilinks']}->{after['wikilinks']}")
        return True, ", ".join(parts)

    gates_passed = sum([tpc_progress, link_progress, claim_progress])
    required = 2 if before["wikilinks"] > 0 else 1
    if gates_passed < required:
        return False, (
            f"insufficient progress ({gates_passed}/{required} gates): "
            f"tpc {'down' if tpc_progress else 'flat'}, "
            f"wikilinks {'up' if link_progress else 'flat'}, "
            f"claims {'up' if claim_progress else 'flat'}"
        )
    parts = []
    if tpc_progress:
        parts.append(f"tpc {tpc(before):.1f}->{tpc(after):.1f}")
    if link_progress:
        parts.append(f"wikilinks {before['wikilinks']}->{after['wikilinks']}")
    if claim_progress:
        parts.append(f"claims {before['claims']}->{after['claims']}")
    return True, ", ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--new-file", default=None)
    ap.add_argument("--new-text-stdin", action="store_true")
    ap.add_argument("--orphan-target", default=None,
                    help="Orphan stem this edit aims to link. Activates orphan-specific gate.")
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

    ot = args.orphan_target
    before = metrics(old_text, orphan_target=ot)
    after = metrics(new_text, orphan_target=ot)
    accept, reason = verdict(before, after, orphan_target=ot)

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
