#!/usr/bin/env python3
"""procedure_maturity.py — rank wiki/procedures for CURATE deepen/repair spend.

Deterministic, stdlib-only. Scans procedure pages on disk and emits a
maturity score + spend_priority (higher = spend deepen time sooner).

Does NOT edit the wiki. CURATE should call this before deepening
procedure-shaped demand (see SKILL.md). Also exposed as
`sweep.py procedure-candidates`.

Signals (all from frontmatter + body):
  - steps_attested (false/missing → immature)
  - numbered / step-like list items carrying (vault:...) citations
  - wikilinks to executions/ or stems starting exec-
  - people/entity wikilinks (heuristic: entities/ path or non-exec stem)
  - status token (draft / needs-tightening → immature; in-use / stable → mature)
  - body length + citation count floors
  - chronology_risk: many ISO dates + sync/handoff language without steps
    (rank higher spend so chronology moves to executions — never auto-edit)

Usage:
  procedure_maturity.py <wiki> [--json|--pretty] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from naming import (  # noqa: E402
    CITATION_RE,
    WIKILINK_RE,
    read_frontmatter,
)

_ISO_DATE_RE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b")
_HANDOFF_RE = re.compile(
    r"(?i)\b(sync|handoff|hand[- ]?off|let'?s\s+sync|follow[- ]?up|"
    r"catch\s+up|ping\s+me|circle\s+back|email\s+thread|"
    r"messy|scattered)\b"
)
# Numbered SOP lines only (1. / 1) / Step N). Bare bullets are not steps —
# Biocure chatter often uses "- Document what changed" without attestation.
_NUMBERED_STEP_RE = re.compile(
    r"(?m)^\s*(?:\d+[.)]\s+|[-*]\s+Step\s+\d+[:.\s])"
)
_IMMATURE_STATUS = frozenset({
    "needs-tightening", "draft", "stub", "wip", "partial", "unknown", "",
})
_MATURE_STATUS = frozenset({
    "in-use", "stable", "active", "approved", "published",
})


def _truthy_attested(raw) -> bool:
    if raw is True:
        return True
    if raw is False or raw is None:
        return False
    s = str(raw).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no", "", "partial", "none"):
        return False
    return False


def _attested_step_count(body: str) -> int:
    """Count step-like lines that also carry a (vault:...) citation."""
    count = 0
    for line in (body or "").splitlines():
        if not _NUMBERED_STEP_RE.match(line):
            continue
        if CITATION_RE.search(line):
            count += 1
    return count


def _wikilink_targets(body: str) -> list[str]:
    out = []
    for m in WIKILINK_RE.finditer(body or ""):
        inner = m.group(1).strip()
        target = inner.split("|", 1)[0].strip().lower().replace(" ", "-")
        if target:
            out.append(target)
    return out


def _execution_link_count(targets: list[str]) -> int:
    n = 0
    for t in targets:
        if t.startswith("executions/") or "/executions/" in t:
            n += 1
        elif t.startswith("exec-") or "/exec-" in t:
            n += 1
        elif re.search(r"(^|/)exec-[a-z0-9-]+$", t):
            n += 1
    return n


def _people_link_count(targets: list[str]) -> int:
    """Count likely people/entity links (not procedures/executions/concepts)."""
    skip_prefixes = (
        "exec-", "executions/", "procedures/", "concepts/", "sources/",
        "evidence/", "facts/", "analyses/", "tables/", "figures/",
        "notes/", "todos/", "projects/",
    )
    n = 0
    for t in targets:
        if any(t.startswith(p) or f"/{p}" in t for p in skip_prefixes):
            continue
        # entities/… or bare person-ish stems (hyphenated names)
        if t.startswith("entities/") or t.count("-") >= 1:
            n += 1
    return n


def _chronology_risk(body: str, attested_steps: int) -> bool:
    """Many ISO dates + sync/handoff language, without attested steps."""
    dates = len(_ISO_DATE_RE.findall(body or ""))
    handoffs = len(_HANDOFF_RE.findall(body or ""))
    if attested_steps >= 2:
        return False
    return dates >= 2 and handoffs >= 2


def _body_words(body: str) -> int:
    return len(re.findall(r"\b\w+\b", body or ""))


def score_procedure(path: Path, text: str | None = None) -> dict:
    """Compute maturity + spend_priority for one procedure page."""
    raw = text if text is not None else path.read_text(encoding="utf-8", errors="replace")
    fm, body = read_frontmatter(raw)
    status = str(fm.get("status") or "").strip().lower()
    steps_attested = _truthy_attested(fm.get("steps_attested"))
    # "partial" in FM is not fully attested
    if str(fm.get("steps_attested") or "").strip().lower() == "partial":
        steps_attested = False

    attested_steps = _attested_step_count(body)
    targets = _wikilink_targets(body)
    exec_links = _execution_link_count(targets)
    people_links = _people_link_count(targets)
    citations = len(CITATION_RE.findall(body or ""))
    words = _body_words(body)
    chrono = _chronology_risk(body, attested_steps)

    reasons: list[str] = []
    maturity = 0.0

    if steps_attested and attested_steps >= 1:
        maturity += 0.35
        reasons.append(f"steps_attested with {attested_steps} cited step(s)")
    elif steps_attested and attested_steps == 0:
        maturity += 0.10
        reasons.append("steps_attested true but no cited step lines found")
    else:
        reasons.append("steps_attested false/missing — immature SOP")

    # Up to 0.25 from attested step count (3+ steps ≈ full credit)
    step_frac = min(attested_steps, 3) / 3.0
    maturity += 0.25 * step_frac
    if attested_steps:
        reasons.append(f"attested_step_count={attested_steps}")

    if exec_links:
        maturity += min(0.15, 0.05 * exec_links)
        reasons.append(f"execution_links={exec_links}")
    else:
        reasons.append("no execution links")

    if people_links:
        maturity += min(0.10, 0.03 * people_links)
        reasons.append(f"people_links={people_links}")

    if status in _MATURE_STATUS:
        maturity += 0.10
        reasons.append(f"status={status} (mature)")
    elif status in _IMMATURE_STATUS:
        reasons.append(f"status={status or 'missing'} (immature)")
    else:
        maturity += 0.05
        reasons.append(f"status={status}")

    if citations >= 1 and words >= 80:
        maturity += 0.05
    elif citations < 1:
        reasons.append("below citation floor")
    if words < 40:
        maturity = max(0.0, maturity - 0.10)
        reasons.append("very short body")

    if chrono:
        maturity = max(0.0, maturity - 0.20)
        reasons.append("chronology_risk: dated handoff chatter on hub")

    maturity = max(0.0, min(1.0, round(maturity, 4)))

    # Higher spend when immature, especially chronology dumps and unattested steps.
    spend = 1.0 - maturity
    if not steps_attested:
        spend = min(1.0, spend + 0.10)
    if chrono:
        spend = min(1.0, spend + 0.15)
    if status in ("needs-tightening", "draft", "stub", "wip"):
        spend = min(1.0, spend + 0.05)
    spend = max(0.0, min(1.0, round(spend, 4)))

    stem = path.stem
    try:
        rel = str(path)
    except Exception:
        rel = path.name

    return {
        "path": rel,
        "stem": stem,
        "maturity_score": maturity,
        "spend_priority": spend,
        "steps_attested": steps_attested,
        "attested_step_count": attested_steps,
        "execution_links": exec_links,
        "people_links": people_links,
        "status": status or None,
        "chronology_risk": chrono,
        "citations": citations,
        "words": words,
        "reasons": reasons,
    }


def rank_procedures(wiki_dir: Path, limit: int | None = None) -> dict:
    """Scan wiki/procedures/*.md and rank by spend_priority desc."""
    wiki_dir = wiki_dir.resolve()
    proc_dir = wiki_dir / "procedures"
    rows = []
    if proc_dir.is_dir():
        for path in sorted(proc_dir.glob("*.md")):
            if path.name.lower() in ("index.md", "log.md", "schema.md"):
                continue
            row = score_procedure(path)
            try:
                row["path"] = str(path.relative_to(wiki_dir))
            except ValueError:
                row["path"] = f"procedures/{path.name}"
            rows.append(row)

    rows.sort(key=lambda r: (-r["spend_priority"], r["maturity_score"], r["stem"]))
    ranked = [r["stem"] for r in rows]
    if limit is not None and limit >= 0:
        rows = rows[:limit]
        ranked = ranked[:limit]
    return {
        "wiki": str(wiki_dir),
        "procedures": rows,
        "ranked_for_spend": ranked,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Rank procedure pages for CURATE deepen/repair spend")
    ap.add_argument("wiki", help="path to wiki/ directory")
    ap.add_argument("--json", action="store_true", default=True,
                    help="emit JSON (default)")
    ap.add_argument("--pretty", action="store_true",
                    help="pretty-print JSON")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap procedures returned (CURATE slot budget)")
    args = ap.parse_args(argv)

    wiki = Path(args.wiki)
    if not wiki.is_dir():
        print(json.dumps({"error": f"wiki dir not found: {wiki}"}))
        return 1
    # Accept either .../wiki or a workspace root containing wiki/
    if (wiki / "procedures").is_dir():
        wiki_dir = wiki
    elif (wiki / "wiki" / "procedures").is_dir() or (wiki / "wiki").is_dir():
        wiki_dir = wiki / "wiki"
    else:
        wiki_dir = wiki

    out = rank_procedures(wiki_dir, limit=args.limit)
    indent = 2 if args.pretty else None
    print(json.dumps(out, indent=indent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
