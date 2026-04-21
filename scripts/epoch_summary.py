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
from datetime import datetime, timezone
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


_LOG_TAIL_BYTES = 64 * 1024  # 64 KB — ~20 recent epoch blocks typical

_CLUSTER_SCOPE_DEFAULT = 500


def _cluster_scope_threshold(wiki_dir: Path) -> int:
    cfg_path = wiki_dir.parent / ".curator" / "config.json"
    if not cfg_path.exists():
        return _CLUSTER_SCOPE_DEFAULT
    try:
        cfg = json.loads(cfg_path.read_text())
        return int(cfg.get("cluster_scope_threshold", _CLUSTER_SCOPE_DEFAULT))
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return _CLUSTER_SCOPE_DEFAULT


def _tail_bytes(path: Path, max_bytes: int = _LOG_TAIL_BYTES) -> str:
    """Return the last max_bytes of `path` aligned to a line boundary.

    The curator log is append-only and grows unbounded as epochs accrue
    — at 155 KB it's already the single biggest chunk of context the
    orchestrator ingests per plan phase. Capping here keeps warmup
    bounded as the wiki matures. Nothing we need (saturation check,
    recent-entry list) looks further back than the last few epochs.
    """
    size = path.stat().st_size
    if size <= max_bytes:
        return path.read_text()
    with path.open("rb") as f:
        f.seek(size - max_bytes)
        chunk = f.read()
    text = chunk.decode("utf-8", errors="replace")
    # Drop leading partial line so mid-word cutoff doesn't confuse parsers
    nl = text.find("\n")
    return text[nl + 1:] if nl >= 0 else text


def recent_log_entries(wiki_dir: Path, last_n: int = 5) -> list:
    """Extract last N log section headers with key metrics."""
    log_path = wiki_dir.parent / ".curator" / "log.md"
    if not log_path.exists():
        return []
    text = _tail_bytes(log_path)
    entries = re.findall(r'^## (.+)$', text, re.MULTILINE)
    return entries[-last_n:] if entries else []


def saturation_check(wiki_dir: Path, threshold: float = 0.005,
                      consecutive: int = 2) -> dict:
    """Parse recent wave logs and detect editorial saturation.

    Prefers `rate_per_accept_existing` (rate computed only over edits to
    pages that existed pre-wave) over `rate_per_accept` (rate over all
    accepts including new pages). Reason: a pure create-mode wave that
    adds N new pages mechanically has near-zero editorial rate because
    the new pages didn't exist to improve — but the overall rate metric
    would flag them as saturated. That misfires the saturation pivot
    INTO a wave type we're already in. The existing-edits rate filters
    out that noise. Pure create waves where no existing edits happened
    write "n/a" and are skipped from the saturation signal entirely.

    Falls back to `rate_per_accept` when `rate_per_accept_existing` is
    absent (older logs written before this field existed).
    """
    log_path = wiki_dir.parent / ".curator" / "log.md"
    if not log_path.exists():
        return {"saturated": False, "epochs_checked": 0, "rates": []}

    text = _tail_bytes(log_path)

    # Prefer rate_per_accept_existing; numbers only, so "n/a" waves are
    # naturally excluded.
    rates = [float(m) for m in re.findall(
        r"^rate_per_accept_existing:\s*([-\d.]+)", text, re.MULTILINE)]
    source_field = "rate_per_accept_existing"
    if not rates:
        rates = [float(m) for m in re.findall(
            r"^rate_per_accept:\s*([-\d.]+)", text, re.MULTILINE)]
        source_field = "rate_per_accept"

    if len(rates) < consecutive:
        return {"saturated": False, "epochs_checked": len(rates),
                "rates": rates, "source_field": source_field}

    recent = rates[-consecutive:]
    saturated = all(r < threshold for r in recent)
    return {
        "saturated": saturated,
        "epochs_checked": len(rates),
        "consecutive_low": sum(1 for r in reversed(rates) if r < threshold),
        "threshold": threshold,
        "required_consecutive": consecutive,
        "recent_rates": recent,
        "source_field": source_field,
        "action": "pivot_to_exploration" if saturated else "continue_editorial",
    }


def orphan_dominance(results: list) -> dict:
    """Fraction of non-source composite debt coming from orphan_rate.

    Used by Phase 1 mode-pick: if this ratio exceeds
    `orphan_dominance_threshold` (default 0.6), fire wire mode. Source
    stubs are definitionally orphans until wired into concepts/entities,
    so including them over-weights the orphan signal and suppresses
    wire-mode from firing when it should. This computation excludes
    sources/ — making the signal the orchestrator reads consistent with
    SKILL.md's prose spec ("across non-source pages").

    Composite = 0.25 × (crossref + orphan + unsourced + vault_coverage),
    so orphan's contribution to composite is 0.25 × orphan. Summed
    across non-source pages, orphan_sum / composite_sum is the share of
    the wiki's total composite debt caused by inbound-link starvation.
    """
    non_source = [r for r in results if not r["page"].startswith("sources/")]
    if not non_source:
        return {"ratio": 0.0, "orphan_sum": 0.0, "composite_sum": 0.0,
                "non_source_pages": 0}
    orphan_sum = sum(r["scores"]["orphan_rate"] * 0.25 for r in non_source)
    composite_sum = sum(r["scores"]["composite"] for r in non_source)
    ratio = orphan_sum / composite_sum if composite_sum > 0 else 0.0
    return {
        "ratio": round(ratio, 3),
        "orphan_sum": round(orphan_sum, 3),
        "composite_sum": round(composite_sum, 3),
        "non_source_pages": len(non_source),
    }


def table_citation_risk(wiki_dir: Path) -> dict:
    """Per-table citation staleness risk, read from .curator/tables.db.

    Mirrors the logic of `tables.py risk` in-process so the orchestrator
    gets this signal in the same JSON pass as everything else. Empty
    dict when tables.db doesn't exist (no class tables yet).
    """
    db_path = wiki_dir.parent / ".curator" / "tables.db"
    if not db_path.exists():
        return {}
    try:
        import sqlite3 as _s3
    except ImportError:
        return {}
    try:
        conn = _s3.connect(str(db_path))
    except _s3.Error:
        return {}
    try:
        audit_rows = conn.execute("""
            SELECT table_name, last_audit_at, row_changes_since_last,
                   audit_period_days FROM _audit_log
        """).fetchall()
    except _s3.Error:
        conn.close()
        return {}
    out = {}
    for name, last_audit, changes, period in audit_rows:
        try:
            total = conn.execute(
                f'SELECT COUNT(*) FROM "{name}"'
            ).fetchone()[0]
        except _s3.Error:
            total = 0
        churn = changes / max(1, total)
        if last_audit:
            try:
                delta = (datetime.now(timezone.utc)
                          - datetime.fromisoformat(last_audit)).total_seconds()
                days_since = delta / 86400.0
            except ValueError:
                days_since = float(period or 30)
        else:
            days_since = float(period or 30)
        time_factor = min(1.0, days_since / max(1, period or 30))
        risk = min(1.0, churn * time_factor)
        out[name] = {
            "total_rows": total,
            "changes_since_audit": changes,
            "days_since_audit": round(days_since, 1),
            "risk": round(risk, 3),
        }
    conn.close()
    return out


def _format_frontier(vf) -> dict:
    uncited, total, uncited_count = vf
    return {
        "uncited_sources": uncited,
        "total_vault_entries": total,
        "uncited_count": uncited_count,
        "utilization": round(1 - uncited_count / max(total, 1), 3),
    }


def wave_scope(wiki_dir: Path, worst_pages: list, threshold: int) -> dict:
    """Cluster-scope for large wikis: seed page + 2-hop wikilink neighborhood.

    When non_source_pages >= threshold (default 500), pick the worst-scoring
    non-source page as the seed and expand to all pages within 2 wikilink
    hops (either direction — inbound and outbound). This gives the
    orchestrator a locally coherent slice to work on per wave, keeping
    plan + execute cost bounded as the wiki grows.

    Returns None below threshold so smaller wikis skip scoping entirely.
    Returns {"seed": path, "pages": [...], "size": N} when active.
    """
    if len(worst_pages) < threshold or not worst_pages:
        return None
    seed = worst_pages[0]["page"]

    graph_path = wiki_dir.parent / ".curator" / "graph.kuzu"
    if not graph_path.exists():
        return {"seed": seed, "pages": [seed], "size": 1}
    try:
        import kuzu
    except ImportError:
        return {"seed": seed, "pages": [seed], "size": 1}
    try:
        db = kuzu.Database(str(graph_path))
        conn = kuzu.Connection(db)
        # 1-hop neighborhood, both directions.
        r1 = conn.execute(
            "MATCH (a:WikiPage)-[:WikiLink]->(b:WikiPage) "
            "WHERE a.path = $p OR b.path = $p "
            "RETURN DISTINCT a.path, b.path",
            {"p": seed}
        )
        hop1 = {seed}
        while r1.has_next():
            a, b = r1.get_next()
            hop1.add(a)
            hop1.add(b)
        # 2-hop: edges touching any hop-1 node.
        r2 = conn.execute(
            "MATCH (a:WikiPage)-[:WikiLink]->(b:WikiPage) "
            "WHERE a.path IN $ps OR b.path IN $ps "
            "RETURN DISTINCT a.path, b.path",
            {"ps": list(hop1)}
        )
        scope = set(hop1)
        while r2.has_next():
            a, b = r2.get_next()
            scope.add(a)
            scope.add(b)
        return {"seed": seed, "pages": sorted(scope), "size": len(scope)}
    except Exception:
        return {"seed": seed, "pages": [seed], "size": 1}


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

    # Cluster-scope activates above threshold; below, wave_scope is null
    # and the orchestrator plans globally as before.
    scope_threshold = _cluster_scope_threshold(wiki_dir)

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
        "orphan_dominance": orphan_dominance(results),
        "table_citation_risk": table_citation_risk(wiki_dir),
        "wave_scope": wave_scope(wiki_dir, non_source, scope_threshold),
        "recent_log": recent_log_entries(wiki_dir, args.last_n),
    }

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
