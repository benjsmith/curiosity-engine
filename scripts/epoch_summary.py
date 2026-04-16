#!/usr/bin/env python3
"""Gather wiki-wide metrics for the CURATE plan phase.

Produces a compact JSON summary that the reviewer reads to create an
epoch plan. Includes: aggregate scores, dimension distributions, frontier
analysis (uncited vault material), cross-cluster edge density, connection
candidates, and recent log history.

Usage:
    python3 epoch_summary.py wiki              # default
    python3 epoch_summary.py wiki --last-n 5   # last N log entries

Output: JSON object on stdout.
"""
import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lint_scores import compute_all, wiki_pages_in  # noqa: E402
from naming import WIKILINK_RE, CITATION_RE  # noqa: E402


def dimension_distribution(results: list) -> dict:
    """Per-dimension: mean, pages > 0.5, pages at 0.0."""
    dims = ["crossref_sparsity", "orphan_rate", "unsourced_density",
            "vault_coverage_gap"]
    dist = {}
    for d in dims:
        vals = [r["scores"][d] for r in results]
        dist[d] = {
            "mean": round(sum(vals) / len(vals), 3) if vals else 0,
            "high_count": sum(1 for v in vals if v > 0.5),
            "zero_count": sum(1 for v in vals if v == 0.0),
        }
    return dist


LIVE_DIMS = {"crossref_sparsity", "orphan_rate", "unsourced_density",
             "vault_coverage_gap"}


def worst_live_dim(scores: dict) -> str:
    """Highest-value live dimension."""
    live = {k: v for k, v in scores.items() if k in LIVE_DIMS and v > 0}
    return max(live, key=live.get) if live else "crossref_sparsity"


def worst_dimension_per_page(results: list) -> dict:
    """Count how many pages have each dimension as their worst."""
    counts = Counter()
    for r in results:
        if r["page"].startswith("sources/"):
            continue
        wdim = worst_live_dim(r["scores"])
        counts[wdim] += 1
    return dict(counts)


def cluster_analysis(wiki_dir: Path, pages_text: dict) -> dict:
    """Analyze cross-cluster connectivity.

    Clusters are subdirectories (concepts/, entities/, analyses/).
    Cross-cluster edges are wikilinks between pages in different clusters.
    """
    titles_to_cluster = {}
    for p in pages_text:
        rel = str(p.relative_to(wiki_dir))
        cluster = rel.split("/")[0] if "/" in rel else "root"
        titles_to_cluster[p.stem.lower()] = cluster

    intra_cluster = 0
    cross_cluster = 0
    cross_edges = []

    for page, text in pages_text.items():
        own_stem = page.stem.lower()
        own_cluster = titles_to_cluster.get(own_stem, "unknown")
        if own_cluster == "sources":
            continue
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip().lower().replace(" ", "-")
            target_cluster = titles_to_cluster.get(target, None)
            if target_cluster is None or target_cluster == "sources":
                continue
            if target_cluster == own_cluster:
                intra_cluster += 1
            else:
                cross_cluster += 1
                cross_edges.append(f"{own_cluster}/{own_stem} -> {target_cluster}/{target}")

    total = intra_cluster + cross_cluster
    return {
        "intra_cluster_edges": intra_cluster,
        "cross_cluster_edges": cross_cluster,
        "cross_cluster_ratio": round(cross_cluster / max(total, 1), 3),
        "sample_cross_edges": cross_edges[:10],
    }


def vault_frontier(wiki_dir: Path, pages_text: dict, limit: int = 10):
    """Find vault sources with the most uncited material across the wiki.

    These are exploration targets: the vault has knowledge the wiki hasn't
    synthesized yet.
    """
    vault_db_path = wiki_dir.parent / "vault" / "vault.db"
    if not vault_db_path.exists():
        return [], 0, 0

    # Gather vault paths cited in non-source pages only.
    # Source stubs always cite their own extraction — that doesn't count
    # as the wiki having synthesized the knowledge.
    cited = set()
    for p, text in pages_text.items():
        rel = str(p.relative_to(wiki_dir))
        if rel.startswith("sources/"):
            continue
        for m in CITATION_RE.finditer(text):
            cited.add(m.group(1).strip())

    try:
        conn = sqlite3.connect(str(vault_db_path))
        rows = conn.execute("SELECT path, title FROM sources").fetchall()
        conn.close()
    except sqlite3.Error:
        return [], 0, 0

    uncited = []
    for path, title in rows:
        if path not in cited and path.lower() not in cited:
            uncited.append({"path": path, "title": title or path})

    return uncited[:limit], len(rows), len(uncited)


def page_type_counts(pages_text: dict, wiki_dir: Path) -> dict:
    """Count pages by subdirectory."""
    counts = Counter()
    for p in pages_text:
        rel = str(p.relative_to(wiki_dir))
        cluster = rel.split("/")[0] if "/" in rel else "root"
        counts[cluster] += 1
    return dict(counts)


def recent_log_entries(wiki_dir: Path, last_n: int = 5) -> list:
    """Extract last N log section headers with key metrics."""
    log_path = wiki_dir.parent / ".curator" / "log.md"
    if not log_path.exists():
        return []
    text = log_path.read_text()
    entries = re.findall(r'^## (.+)$', text, re.MULTILINE)
    return entries[-last_n:] if entries else []


def saturation_check(wiki_dir: Path, threshold: float = 0.001,
                      consecutive: int = 3) -> dict:
    """Parse recent epoch logs and detect editorial saturation.

    Reads rate_per_accept from the last N curate-epoch blocks in
    .curator/log.md. Returns a structured signal the orchestrator can
    branch on without judgment calls.
    """
    log_path = wiki_dir.parent / ".curator" / "log.md"
    if not log_path.exists():
        return {"saturated": False, "epochs_checked": 0, "rates": []}

    text = log_path.read_text()
    rates = [float(m) for m in re.findall(
        r"^rate_per_accept:\s*([\d.]+)", text, re.MULTILINE)]

    if len(rates) < consecutive:
        return {"saturated": False, "epochs_checked": len(rates), "rates": rates}

    recent = rates[-consecutive:]
    saturated = all(r < threshold for r in recent)
    return {
        "saturated": saturated,
        "epochs_checked": len(rates),
        "consecutive_low": sum(1 for r in reversed(rates) if r < threshold),
        "threshold": threshold,
        "required_consecutive": consecutive,
        "recent_rates": recent,
        "action": "pivot_to_exploration" if saturated else "continue_editorial",
    }


def _format_frontier(vf) -> dict:
    uncited, total, uncited_count = vf
    return {
        "uncited_sources": uncited,
        "total_vault_entries": total,
        "uncited_count": uncited_count,
        "utilization": round(1 - uncited_count / max(total, 1), 3),
    }


def connection_candidates(wiki_dir: Path, limit: int = 5) -> list:
    """Bridge candidates via the kuzu graph.

    Returns [] if the graph file is missing (rebuild hasn't been run yet)
    or kuzu isn't importable. The orchestrator is expected to rebuild the
    graph before calling epoch_summary.
    """
    graph_path = wiki_dir.parent / ".curator" / "graph.kuzu"
    if not graph_path.exists():
        return []
    try:
        import kuzu
    except ImportError:
        return []
    try:
        db = kuzu.Database(str(graph_path))
        conn = kuzu.Connection(db)
        limit = max(1, min(int(limit), 100))
        result = conn.execute(
            "MATCH (a:WikiPage)-[:Cites]->(v:VaultSource)<-[:Cites]-(b:WikiPage) "
            "WHERE a.path < b.path "
            "AND NOT EXISTS { MATCH (a)-[:WikiLink]->(b) } "
            "AND NOT EXISTS { MATCH (b)-[:WikiLink]->(a) } "
            "AND a.type <> 'source' AND b.type <> 'source' "
            "WITH a.path AS page_a, b.path AS page_b, count(v) AS shared "
            "ORDER BY shared DESC "
            f"LIMIT {limit} "
            "RETURN page_a, page_b, shared"
        )
        candidates = []
        while result.has_next():
            r = result.get_next()
            candidates.append({"page_a": r[0], "page_b": r[1], "shared_sources": r[2]})
        return candidates
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wiki", nargs="?", default="wiki")
    ap.add_argument("--last-n", type=int, default=5)
    args = ap.parse_args()

    wiki_dir = Path(args.wiki).resolve()

    # Single pass over wiki pages: read each file exactly once and share
    # the dict across cluster_analysis / vault_frontier / page_type_counts.
    pages_text = {p: p.read_text() for p in wiki_pages_in(wiki_dir)}

    results = compute_all(wiki_dir)

    non_source = [r for r in results if not r["page"].startswith("sources/")]
    composites = [r["scores"]["composite"] for r in non_source]

    summary = {
        "page_counts": page_type_counts(pages_text, wiki_dir),
        "non_source_pages": len(non_source),
        "avg_composite": round(sum(composites) / max(len(composites), 1), 4),
        "worst_5": [
            {"page": r["page"], "composite": r["scores"]["composite"],
             "worst_dim": max(
                 {k: v for k, v in r["scores"].items() if k != "composite"},
                 key=lambda k: r["scores"][k]
             )}
            for r in non_source[:5]
        ],
        "dimension_distribution": dimension_distribution(non_source),
        "worst_dimension_counts": worst_dimension_per_page(results),
        "cluster_analysis": cluster_analysis(wiki_dir, pages_text),
        "vault_frontier": _format_frontier(vault_frontier(wiki_dir, pages_text)),
        "connection_candidates": connection_candidates(wiki_dir),
        "saturation": saturation_check(wiki_dir),
        "recent_log": recent_log_entries(wiki_dir, args.last_n),
    }

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
