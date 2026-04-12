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


def crossref_sparsity(text: str, all_titles: set, own_stem: str) -> float:
    """Fraction of linkable entity/concept mentions that aren't [[linked]].

    A mention is linkable if a wiki page exists with that title. Self-references
    are excluded so a page cannot satisfy the metric by linking to itself —
    that reward-hack was observed in the epoch-5 run of 2026-04-11.

    Score 0 = every linkable mention is linked. Score 1 = nothing linked.
    Pages with zero mentions of any other wiki title return 0 (informationless
    for this dimension; orphan_rate picks up the under-connection signal).
    """
    if not all_titles:
        return 0.0

    text_lower = text.lower()
    mentioned = {t for t in all_titles
                 if t != own_stem and len(t) > 3 and t in text_lower}
    if not mentioned:
        return 0.0

    linked = {t for t in mentioned
              if f"[[{t}]]" in text_lower or f"[[{t}|" in text_lower}
    return round(1.0 - (len(linked) / len(mentioned)), 2)


def orphan_rate(own_stem: str, inbound: dict) -> float:
    """Penalty for pages with few inbound wikilinks from elsewhere in the wiki.

    0 inbound → 1.0 (orphan)
    1 inbound → 0.66
    2 inbound → 0.33
    3+ inbound → 0.0
    """
    n = inbound.get(own_stem, 0)
    if n == 0:
        return 1.0
    if n == 1:
        return 0.66
    if n == 2:
        return 0.33
    return 0.0


def unsourced_density(text: str) -> float:
    """Fraction of substantive prose lines that carry no (vault:...) citation.

    Skips YAML frontmatter, headings, empty lines, and lines under 5 words.
    Gives the ratchet a second real signal independent of wikilink counting.
    """
    substantive = 0
    unsourced = 0
    in_frontmatter = False
    frontmatter_seen = 0
    for line in text.split("\n"):
        s = line.strip()
        if s == "---":
            frontmatter_seen += 1
            in_frontmatter = frontmatter_seen == 1
            continue
        if in_frontmatter or not s or s.startswith("#"):
            continue
        if len(s.split()) < 5:
            continue
        substantive += 1
        if "(vault:" not in s:
            unsourced += 1
    if substantive == 0:
        return 0.0
    return round(unsourced / substantive, 2)


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
    """Score every page under wiki_dir. Sorted worst-first by composite.

    Two passes: gather inbound-link counts across the whole wiki, then score
    each page. orphan_rate and crossref_sparsity are live signals;
    contradictions and freshness_gap remain stubs that return 0.
    """
    pages = wiki_pages_in(wiki_dir)
    pages_text = {p: p.read_text() for p in pages}
    titles = {p.stem.lower() for p in pages}
    log_path = wiki_dir / "log.md"
    log_text = log_path.read_text() if log_path.exists() else ""

    # Pass 1: global inbound-link count per title (excluding self-links)
    inbound = {t: 0 for t in titles}
    for page, text in pages_text.items():
        own = page.stem.lower()
        text_lower = text.lower()
        for t in titles:
            if t == own:
                continue
            if f"[[{t}]]" in text_lower or f"[[{t}|" in text_lower:
                inbound[t] += 1

    # Pass 2: per-page scoring
    results = []
    for page, text in pages_text.items():
        own = page.stem.lower()
        scores = {
            "contradictions": contradictions(text),
            "freshness_gap": freshness_gap(text),
            "crossref_sparsity": crossref_sparsity(text, titles, own),
            "query_misses": query_misses(page.stem, log_text),
            "orphan_rate": orphan_rate(own, inbound),
            "unsourced_density": unsourced_density(text),
        }
        scores["composite"] = round(
            0.35 * scores["crossref_sparsity"]
            + 0.35 * scores["orphan_rate"]
            + 0.30 * scores["unsourced_density"],
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
