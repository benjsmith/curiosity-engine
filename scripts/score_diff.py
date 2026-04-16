#!/usr/bin/env python3
"""Minimal mechanical gate for wiki edits.

Hard floors only — the opus judge handles nuanced quality review.
These gates catch catastrophic regressions that no edit should cause:
  1. No citation loss: citations(after) >= citations(before)
  2. No extreme raw-token bloat: body_tokens(after) <= body_tokens(before) * 1.5
  3. New pages: >=2 citations, >=2 wikilinks, >=100 body words

Token counting ignores YAML frontmatter so the ceiling measures actual
prose growth.

Usage:
    echo "<new text>" | python3 score_diff.py <page.md> --new-text-stdin
    python3 score_diff.py <page.md> --new-file <candidate.md>
    python3 score_diff.py <page.md> --new-page --new-text-stdin
    python3 score_diff.py <page.md> --new-text-stdin --dry-run

--dry-run returns the verdict without writing the file (for batch review).

Outputs one JSON line to stdout. Exit code always 0 on well-formed input.
"""
import argparse
import json
import re
import sys
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
CITATION_RE = re.compile(r"\(vault:[^)]+\)")


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def body_tokens(text: str) -> int:
    """Whitespace-split token count on body only (frontmatter excluded)."""
    return len(_strip_frontmatter(text).split())


def citation_count(text: str) -> int:
    """Count individual (vault:...) citations across the entire text."""
    return len(CITATION_RE.findall(text))


def matchable_links(text: str) -> int:
    """Count wikilinks in hyphen-case form (no spaces)."""
    return sum(1 for m in WIKILINK_RE.finditer(text)
               if " " not in m.group(1).strip())


def metrics(text: str) -> dict:
    return {
        "tokens": body_tokens(text),
        "citations": max(citation_count(text), 1),
        "wikilinks": matchable_links(text),
    }


def verdict(before: dict, after: dict) -> tuple:
    if after["citations"] < before["citations"]:
        return False, f"citation loss ({before['citations']}->{after['citations']})"
    if before["tokens"] > 0 and after["tokens"] > before["tokens"] * 1.5:
        return False, f"bloat ({before['tokens']}->{after['tokens']}, >50%)"
    return True, "pass"


def new_page_verdict(text: str) -> tuple:
    m = metrics(text)
    words = body_tokens(text)
    if m["citations"] < 2:
        return False, f"too few citations ({m['citations']}; need >=2)"
    if m["wikilinks"] < 2:
        return False, f"too few wikilinks ({m['wikilinks']}; need >=2)"
    if words < 100:
        return False, f"too short ({words} words; need >=100)"
    return True, f"citations={m['citations']}, wikilinks={m['wikilinks']}, words={words}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--new-file", default=None)
    ap.add_argument("--new-text-stdin", action="store_true")
    ap.add_argument("--new-page", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="Return verdict without writing the file.")
    args = ap.parse_args()

    page = Path(args.page)
    write = not args.dry_run

    if args.new_file:
        new_text = Path(args.new_file).read_text()
    elif args.new_text_stdin:
        new_text = sys.stdin.read()
    else:
        print(json.dumps({"error": "need --new-file or --new-text-stdin", "applied": False}))
        return

    if args.new_page:
        accept, reason = new_page_verdict(new_text)
        result = {
            "page": str(page), "accept": accept, "reason": reason,
            "after": metrics(new_text), "applied": False, "new_page": True,
        }
        if accept and write:
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(new_text)
            result["applied"] = True
        print(json.dumps(result))
        return

    if not page.exists():
        print(json.dumps({"error": f"page not found: {page}", "applied": False}))
        return

    old_text = page.read_text()
    before = metrics(old_text)
    after = metrics(new_text)
    accept, reason = verdict(before, after)

    result = {
        "page": str(page), "accept": accept, "reason": reason,
        "before": before, "after": after, "applied": False,
    }
    if accept and write:
        page.write_text(new_text)
        result["applied"] = True

    print(json.dumps(result))


if __name__ == "__main__":
    main()
