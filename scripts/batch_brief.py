#!/usr/bin/env python3
"""Compose per-page improvement briefs for one ITERATE batch.

Runs lint once, picks the N worst unvisited pages, and for each emits a
self-contained brief bundle: page text, worst lint dimension, a concrete
mechanical hint, best-match vault snippet, and a job_type tag for model routing.

Workers receive one brief and nothing else — no lint dumps, no index reads,
no vault searches. This is the single biggest lever against per-cycle tool
overhead and context bloat during autonomous loops.

Usage:
    python3 batch_brief.py                  # wiki/, default n=10
    python3 batch_brief.py <wiki_dir> --n N

Output: JSON array of brief objects on stdout.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from lint_scores import compute_all  # noqa: E402


def missing_wikilinks(text: str, all_titles: set, own_stem: str, limit: int = 5) -> list:
    """Hyphen-stemmed titles mentioned in text that aren't [[linked]].

    Excludes the page's own stem (self-links were the epoch-5 reward hack).
    Returns stems in the exact hyphen-case form that lint_scores.crossref_sparsity
    will credit when the worker inserts them.
    """
    text_lower = text.lower()
    missing = []
    for t in sorted(all_titles):
        if t == own_stem or len(t) <= 3 or t not in text_lower:
            continue
        if f"[[{t}]]" in text_lower or f"[[{t}|" in text_lower:
            continue
        missing.append(t)
        if len(missing) >= limit:
            break
    return missing


def visited_in_current_epoch(log_text: str) -> set:
    """Pages already touched since the last evolve-epoch header.

    Prevents a batch from retargeting the same page repeatedly within one epoch.
    """
    if not log_text:
        return set()
    tail = log_text.rsplit("## evolve-epoch", 1)[-1]
    return set(re.findall(r'iterate:\s*([^\s|]+)', tail))


def vault_snippet(db_path: Path, query: str) -> Optional[dict]:
    if not db_path.exists() or not query.strip():
        return None
    try:
        import sqlite3
        c = sqlite3.connect(str(db_path))
        row = c.execute(
            "SELECT path, title, snippet(sources, 2, '>>>', '<<<', '...', 20) "
            "FROM sources WHERE sources MATCH ? ORDER BY bm25(sources) LIMIT 1",
            (query,),
        ).fetchone()
        c.close()
        if row:
            return {"path": row[0], "title": row[1], "snippet": row[2]}
    except Exception:
        return None
    return None


def build_brief(wiki_dir: Path, result: dict, all_titles: set, db_path: Path) -> dict:
    page_path = wiki_dir / result["page"]
    text = page_path.read_text()
    scores = result["scores"]
    own_stem = page_path.stem.lower()

    active_dims = {k: v for k, v in scores.items()
                   if k != "composite" and v > 0}
    worst_dim = max(active_dims, key=active_dims.get) if active_dims else "crossref_sparsity"

    if worst_dim == "crossref_sparsity":
        missing = missing_wikilinks(text, all_titles, own_stem)
        if missing:
            hint = "add missing wikilinks in exact hyphen form: " + ", ".join(
                f"[[{m}]]" for m in missing
            )
        else:
            hint = "add at least one new [[hyphen-stem]] wikilink to a related page"
        job_type = "surgical"
    elif worst_dim == "orphan_rate":
        hint = (
            "this page has no inbound wikilinks. Find 2-3 related pages that "
            "should mention it and add [[" + own_stem + "]] to them instead of "
            "editing this page."
        )
        job_type = "surgical"
    elif worst_dim == "unsourced_density":
        hint = (
            "most prose lines lack a (vault:...) citation. Add citations to "
            "substantive claims using existing vault sources; do not invent sources."
        )
        job_type = "synthesis"
    elif worst_dim == "query_misses":
        hint = "broaden vault sourcing: this page has underperformed on past queries"
        job_type = "synthesis"
    elif worst_dim == "contradictions":
        hint = "resolve flagged contradictions by reconciling or scoping claims"
        job_type = "synthesis"
    elif worst_dim == "freshness_gap":
        hint = "replace or supplement stale citations with newer vault sources"
        job_type = "synthesis"
    else:
        hint = "tighten prose; add one sourced claim"
        job_type = "surgical"

    query = Path(result["page"]).stem.replace("-", " ").replace("_", " ")
    snippet = vault_snippet(db_path, query)

    return {
        "page": result["page"],
        "worst_dim": worst_dim,
        "scores": scores,
        "hint": hint,
        "job_type": job_type,
        "page_text": text,
        "vault_snippet": snippet,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wiki", nargs="?", default="wiki")
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()

    wiki_dir = Path(args.wiki).resolve()
    results = compute_all(wiki_dir)

    log_path = wiki_dir / "log.md"
    log_text = log_path.read_text() if log_path.exists() else ""
    visited = visited_in_current_epoch(log_text)

    non_source = [r for r in results if not r["page"].startswith("sources/")]
    unvisited = [r for r in non_source if r["page"] not in visited]
    pool = unvisited if unvisited else non_source
    targets = pool[: args.n]

    # Titles in the exact hyphen-stemmed form that lint_scores.crossref_sparsity
    # credits — matches what a worker must literally type for the lint to score it.
    all_titles = {Path(r["page"]).stem.lower() for r in results}
    db_path = wiki_dir.parent / "vault" / "vault.db"

    briefs = [build_brief(wiki_dir, r, all_titles, db_path) for r in targets]
    print(json.dumps(briefs, indent=2))


if __name__ == "__main__":
    main()
