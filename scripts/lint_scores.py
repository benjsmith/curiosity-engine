#!/usr/bin/env python3
"""Compute lint scores for all wiki pages.

Usage:
    python3 lint_scores.py                       # default wiki/ directory
    python3 lint_scores.py <wiki_dir>            # custom wiki path
    python3 lint_scores.py <wiki_dir> --top N    # only emit N worst pages
    python3 lint_scores.py <wiki_dir> --minimal  # only {page, composite}

Outputs JSON array sorted by composite score (worst first).
All scores are floats in [0, 1]. Higher = needs more work.

Flags exist because a full lint dump at 1000 pages is ~50k tokens, and the
ITERATE batch phase only ever needs the top few. Pass `--top N --minimal`
to shrink the payload ~800x for batch_brief consumers.
"""

import json
import re
import sys
from pathlib import Path

SKIP_FILES = {"index.md", "log.md", "schema.md"}


def wiki_pages_in(wiki_dir: Path):
    return [p for p in wiki_dir.rglob("*.md") if p.name not in SKIP_FILES]


def crossref_sparsity(text: str, all_titles: set) -> float:
    """Fraction of linkable entity/concept mentions that aren't [[linked]].

    A mention is linkable if a wiki page exists with that title.
    Score 0 = everything linked. Score 1 = nothing linked.
    """
    if not all_titles:
        return 0.0

    text_lower = text.lower()
    # Only consider titles longer than 3 chars to avoid false positives
    mentioned = {t for t in all_titles if t in text_lower and len(t) > 3}
    if not mentioned:
        return 0.0

    linked = set()
    for t in mentioned:
        # Check for [[title]] or [[title|display text]] patterns
        if f"[[{t}]]" in text_lower or f"[[{t}|" in text_lower or f"[[{t}" in text_lower:
            linked.add(t)

    return round(1.0 - (len(linked) / len(mentioned)), 2)


def query_misses(page_stem: str, log_text: str) -> float:
    """Fraction of queries involving this page that needed vault fallback.

    Parsed from log.md. Returns 0.5 (unknown) if no queries found.
    """
    if not log_text:
        return 0.5

    # Find query log blocks
    blocks = re.findall(
        r'## \[.*?\] query \|.*?\n(.*?)(?=\n## |\Z)',
        log_text, re.DOTALL
    )

    relevant = [b for b in blocks if page_stem.lower() in b.lower()]
    if not relevant:
        return 0.5  # no queries touched this page — unknown

    fallbacks = sum(1 for b in relevant if "vault fallback" in b.lower())
    return round(fallbacks / len(relevant), 2)


def contradictions(text: str) -> float:
    """Fraction of claims with contradicting evidence.

    Stub for v1: returns 0.0. Full implementation would:
    1. Extract each sourced claim
    2. Search vault for same topic
    3. Use LLM to judge contradiction
    """
    return 0.0


def freshness_gap(text: str) -> float:
    """Fraction of cited sources that are stale when newer ones exist.

    Stub for v1: returns 0.0. Full implementation would:
    1. Parse source dates from vault metadata
    2. Search vault for newer sources on same topics
    3. Score = stale_sources / total_sources
    """
    return 0.0


def compute_all(wiki_dir: Path) -> list:
    """Score every page under wiki_dir. Sorted worst-first by composite."""
    pages = wiki_pages_in(wiki_dir)
    titles = {p.stem.lower() for p in pages}
    log_path = wiki_dir / "log.md"
    log_text = log_path.read_text() if log_path.exists() else ""

    results = []
    for page in pages:
        text = page.read_text()
        scores = {
            "contradictions": contradictions(text),
            "freshness_gap": freshness_gap(text),
            "crossref_sparsity": crossref_sparsity(text, titles),
            "query_misses": query_misses(page.stem, log_text),
        }
        scores["composite"] = round(
            0.35 * scores["contradictions"]
            + 0.25 * scores["freshness_gap"]
            + 0.20 * scores["crossref_sparsity"]
            + 0.20 * scores["query_misses"],
            3
        )
        results.append({
            "page": str(page.relative_to(wiki_dir)),
            "scores": scores,
        })

    results.sort(key=lambda x: x["scores"]["composite"], reverse=True)
    return results


def main():
    args = [a for a in sys.argv[1:]]
    top_n = None
    minimal = False
    positional = []
    i = 0
    while i < len(args):
        if args[i] == "--top":
            top_n = int(args[i + 1])
            i += 2
        elif args[i] == "--minimal":
            minimal = True
            i += 1
        else:
            positional.append(args[i])
            i += 1

    wiki_dir = Path(positional[0]) if positional else Path("wiki")
    results = compute_all(wiki_dir)

    if top_n is not None:
        results = results[:top_n]
    if minimal:
        results = [{"page": r["page"], "composite": r["scores"]["composite"]} for r in results]

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
