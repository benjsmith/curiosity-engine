#!/usr/bin/env python3
"""Compute lint scores for all wiki pages.

Usage:
    python3 lint_scores.py                       # default wiki/ directory
    python3 lint_scores.py <wiki_dir>            # custom wiki path
    python3 lint_scores.py <wiki_dir> --top N    # only emit N worst pages
    python3 lint_scores.py <wiki_dir> --minimal  # only {page, composite}

Outputs JSON array sorted by composite score (worst first).
All scores are floats in [0, 1]. Higher = needs more work.

Four live dimensions, each weighted 0.25:
  - crossref_sparsity  (outbound link coverage)
  - orphan_rate        (inbound link coverage)
  - unsourced_density  (citation coverage on substantive lines)
  - vault_coverage_gap (unexplored relevant vault material)

Contradictions and query_misses were retired: the former's deterministic
negation-polarity check produced too many false positives; CURATE now
runs an explicit LLM-based semantic contradiction scan on concept/entity/
fact docs during the evaluate phase. Query-miss history was not reliably
populated. Both can return later if we get cleaner signals.

Flags exist because a full lint dump at 1000 pages is ~50k tokens, and the
CURATE batch phase only ever needs the top few. Pass `--top N --minimal`
to shrink the payload ~800x for target selection.
"""

import json
import re
import sys
from pathlib import Path

SKIP_FILES = {"index.md", "log.md", "schema.md"}


def wiki_pages_in(wiki_dir: Path):
    return [p for p in wiki_dir.rglob("*.md")
            if p.name not in SKIP_FILES and not p.name.startswith(".")]


def crossref_sparsity(text: str, all_titles: set, own_stem: str) -> float:
    """Fraction of linkable entity/concept mentions that aren't [[linked]].

    Self-references excluded. Uses word-boundary matching on hyphen-expanded
    stems to avoid false positives (e.g. stem "data" matching "metadata").
    Pages with zero mentions return 0.
    """
    if not all_titles:
        return 0.0
    text_lower = text.lower()
    mentioned = set()
    for t in all_titles:
        if t == own_stem or len(t) < 4:
            continue
        pattern = r"\b" + re.escape(t.replace("-", " ")) + r"\b"
        if re.search(pattern, text_lower):
            mentioned.add(t)
    if not mentioned:
        return 0.0
    linked = {t for t in mentioned
              if f"[[{t}]]" in text_lower or f"[[{t}|" in text_lower}
    return round(1.0 - (len(linked) / len(mentioned)), 2)


def orphan_rate(own_stem: str, inbound: dict) -> float:
    """Penalty for pages with few inbound wikilinks from elsewhere in the wiki.

    0 inbound -> 1.0 (orphan)
    1 inbound -> 0.66
    2 inbound -> 0.33
    3+ inbound -> 0.0
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
    """Fraction of substantive prose lines that carry no (vault:...) citation."""
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


def vault_coverage_gap(page_stem: str, text: str, vault_db_path) -> float:
    """Fraction of relevant vault material not cited on this page.

    Queries vault.db FTS (BM25) for the page's topic. Counts how many of
    the top vault hits aren't cited via (vault:...). Higher = more relevant
    source material this page hasn't incorporated yet.
    """
    if vault_db_path is None or not vault_db_path.exists():
        return 0.0
    query = page_stem.replace("-", " ").replace("_", " ")
    if len(query) < 3:
        return 0.0
    try:
        import sqlite3
        conn = sqlite3.connect(str(vault_db_path))
        rows = conn.execute(
            "SELECT path FROM sources WHERE sources MATCH ? "
            "ORDER BY bm25(sources) LIMIT 10",
            (query,),
        ).fetchall()
        conn.close()
    except Exception:
        return 0.0
    if not rows:
        return 0.0
    text_lower = text.lower()
    uncited = 0
    for (path,) in rows:
        if f"(vault:{path})" not in text_lower and path.lower() not in text_lower:
            uncited += 1
    return round(uncited / len(rows), 2)


def compute_all(wiki_dir: Path) -> list:
    """Score every page under wiki_dir. Sorted worst-first by composite.

    Two passes: (1) gather inbound-link counts, (2) score each page.

    Four dimensions, each 0.25:
    - crossref_sparsity  (outbound link quality)
    - orphan_rate        (inbound link coverage)
    - unsourced_density  (citation coverage)
    - vault_coverage_gap (unexplored vault material)
    """
    pages = wiki_pages_in(wiki_dir)
    pages_text = {p: p.read_text() for p in pages}
    titles = {p.stem.lower() for p in pages}
    vault_db_path = wiki_dir.parent / "vault" / "vault.db"

    inbound = {t: 0 for t in titles}
    for page, text in pages_text.items():
        own = page.stem.lower()
        text_lower = text.lower()
        for t in titles:
            if t == own:
                continue
            if f"[[{t}]]" in text_lower or f"[[{t}|" in text_lower:
                inbound[t] += 1

    results = []
    for page, text in pages_text.items():
        own = page.stem.lower()
        scores = {
            "crossref_sparsity": crossref_sparsity(text, titles, own),
            "orphan_rate": orphan_rate(own, inbound),
            "unsourced_density": unsourced_density(text),
            "vault_coverage_gap": vault_coverage_gap(own, text, vault_db_path),
        }
        scores["composite"] = round(
            0.25 * scores["crossref_sparsity"]
            + 0.25 * scores["orphan_rate"]
            + 0.25 * scores["unsourced_density"]
            + 0.25 * scores["vault_coverage_gap"],
            3,
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
