#!/usr/bin/env python3
"""Minimal mechanical gate for wiki edits.

Hard floors only — the opus judge handles nuanced quality review.
These gates catch catastrophic regressions that no edit should cause:
  1. No citation loss: citations(after) >= citations(before)
  2. No extreme raw-token bloat: body_tokens(after) <= body_tokens(before) * 1.5
  3. New pages: floor depends on directory —
       facts/*:     >=1 citation, >=1 wikilink, >=30 words
       evidence/*:  >=1 citation, >=1 wikilink, >=50 words
       default:     >=2 citations, >=2 wikilinks, >=100 words
  4. Citation relevance (optional): new citations must match their source
     in FTS5. Catches spurious citations without a full reviewer pass.

Token counting ignores YAML frontmatter so the ceiling measures actual
prose growth.

Usage:
    echo "<new text>" | python3 score_diff.py <page.md> --new-text-stdin
    python3 score_diff.py <page.md> --new-text-stdin --vault-db vault/vault.db
    python3 score_diff.py <page.md> --new-text-file <path>          # alias: --new-file
    python3 score_diff.py <page.md> --new-page --new-text-stdin
    python3 score_diff.py <page.md> --new-text-stdin --dry-run

--vault-db enables citation verification: for each newly added (vault:...)
  citation, queries FTS5 to confirm the cited source contains words related
  to the claim. Rejects if any new citation is suspect. One FTS5 query per
  new citation — negligible overhead.
--dry-run returns the verdict without writing the file (for batch review).

Outputs one JSON line to stdout. Exit code always 0 on well-formed input.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import WIKILINK_RE, CITATION_RE, read_frontmatter  # noqa: E402

CITATION_RAW_RE = re.compile(r"\(vault:[^)]+\)")
# Table citation forms:
#   (table:<name>#id=<id>)       single-row citation
#   (table:<name>?query=<slug>)  pinned query citation (Phase 2)
TABLE_CITATION_RE = re.compile(r"\(table:([a-zA-Z_][a-zA-Z0-9_]*)(?:#id=([^)]+)|\?query=([^)]+))\)")

# FTS5 reserved tokens. If a claim word matches one of these (case-insensitive),
# the raw query would be parsed as an operator and blow up with a syntax error.
# Kept in sync with vault_search._sanitize_fts.
_FTS5_RESERVED = {"AND", "OR", "NOT", "NEAR"}

# Obsidian hides `%%…%%` as a comment block. LLMs occasionally emit `%%`
# while trying to escape `%` (LaTeX habit), which silently eats page prose
# between two such occurrences. We collapse to single `%` in body text,
# outside fenced code blocks.
_FENCED_CODE_RE = re.compile(r"(?ms)^```.*?^```")
_DOUBLE_PERCENT_RE = re.compile(r"%%+")


def _sanitize_fts(query: str) -> str:
    """Quote hyphenated tokens and FTS5 operators so raw syntax can't leak.

    Duplicated from vault_search so score_diff stays self-contained.
    """
    out = []
    for tok in re.findall(r'"[^"]*"|\S+', query):
        if tok.startswith('"'):
            out.append(tok)
        elif "-" in tok or tok.upper() in _FTS5_RESERVED or re.fullmatch(r"\w+:", tok):
            out.append('"' + tok.replace('"', "") + '"')
        else:
            out.append(tok)
    return " ".join(out)


def _collapse_double_percent(text: str) -> str:
    """Replace `%%` with `%` in body text outside fenced code blocks."""
    fm_end = 0
    if text.startswith("---\n"):
        m = re.search(r"\n---\n", text[4:])
        if m:
            fm_end = 4 + m.end()
    head, body = text[:fm_end], text[fm_end:]

    spans = [(m.start(), m.end()) for m in _FENCED_CODE_RE.finditer(body)]
    out = []
    cursor = 0
    for start, end in spans:
        out.append(_DOUBLE_PERCENT_RE.sub("%", body[cursor:start]))
        out.append(body[start:end])
        cursor = end
    out.append(_DOUBLE_PERCENT_RE.sub("%", body[cursor:]))
    return head + "".join(out)


def body_tokens(text: str) -> int:
    """Whitespace-split token count on body only (frontmatter excluded)."""
    _, body = read_frontmatter(text)
    return len(body.split())


def citation_count(text: str) -> int:
    """Count citations of any recognised form across the text.

    Counts both `(vault:...)` and `(table:name#id=X)` / `(table:name?query=X)`.
    Dropping a citation of either form should trip the citation-loss gate.
    """
    return len(CITATION_RAW_RE.findall(text)) + len(TABLE_CITATION_RE.findall(text))


def _table_citations(text: str) -> list:
    """Return table citations as (table, kind, value) tuples.

    kind = 'id' for row citations, 'query' for pinned-query citations.
    """
    out = []
    for m in TABLE_CITATION_RE.finditer(text):
        table_name = m.group(1)
        row_id = m.group(2)
        query = m.group(3)
        if row_id is not None:
            out.append((table_name, "id", row_id))
        elif query is not None:
            out.append((table_name, "query", query))
    return out


def verify_table_citations(old_text: str, new_text: str,
                              tables_db: Path) -> list:
    """For each newly-added table-row citation, verify the row exists.

    Only row-id citations are verified (Phase 1); pinned-query citations
    are verified in later phases when the query registry is populated.
    Returns a list of suspect citations (empty = all OK).
    """
    if not tables_db.exists():
        return []
    old_cits = set(_table_citations(old_text))
    new_cits = set(_table_citations(new_text))
    added = new_cits - old_cits
    if not added:
        return []
    suspects = []
    try:
        conn = sqlite3.connect(str(tables_db))
    except sqlite3.Error as e:
        return [{"citation": f"table:{t}#id={v}", "error": str(e)}
                 for (t, k, v) in added if k == "id"]
    # Quick existence check: load set of (table, row_id) for any referenced
    # table. Skip tables not present in the DB — they'd be reported as
    # missing, which is the right error.
    for (table_name, kind, value) in added:
        if kind != "id":
            continue
        try:
            # Primary key column must be named — extract from PRAGMA.
            pragma = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            pk_col = next((r[1] for r in pragma if r[5]), None)
            if pk_col is None:
                suspects.append({"citation": f"table:{table_name}#id={value}",
                                  "problem": f"table {table_name} not found or has no PK"})
                continue
            row = conn.execute(
                f'SELECT 1 FROM "{table_name}" WHERE "{pk_col}" = ? LIMIT 1',
                (value,)
            ).fetchone()
            if row is None:
                suspects.append({"citation": f"table:{table_name}#id={value}",
                                  "problem": "row not found"})
        except sqlite3.Error as e:
            suspects.append({"citation": f"table:{table_name}#id={value}",
                              "error": str(e)})
    conn.close()
    return suspects


def _citations_set(text: str) -> set:
    """Extract the set of vault paths cited in text."""
    return set(CITATION_RE.findall(text))


def _claim_words(line: str) -> str:
    """Extract significant content words from a citation line for FTS5 matching."""
    cleaned = CITATION_RAW_RE.sub("", line)
    cleaned = re.sub(r"\[\[[^\]]*\]\]", "", cleaned)
    words = re.findall(r"[a-zA-Z]{4,}", cleaned)
    stop = {"the", "and", "for", "are", "was", "were", "with", "from",
            "that", "this", "which", "have", "has", "been", "also",
            "more", "than", "about", "their", "other", "some"}
    return " ".join(w for w in words if w.lower() not in stop)


def verify_new_citations(old_text: str, new_text: str,
                          vault_db: Path) -> list:
    """Check that each newly added citation actually relates to the claim.

    For each (vault:path) in new_text but not in old_text, extracts the
    content words from the line containing the citation and queries FTS5
    for a match in that specific source. If no match, the citation is
    suspect — the source doesn't mention anything the claim talks about.

    Returns a list of suspect citations (empty = all OK).
    """
    if not vault_db.exists():
        return []

    old_citations = _citations_set(old_text)
    new_citations = _citations_set(new_text)
    added = new_citations - old_citations
    if not added:
        return []

    line_map = {}
    for line in new_text.split("\n"):
        for m in CITATION_RE.finditer(line):
            vp = m.group(1)
            if vp in added:
                line_map.setdefault(vp, line)

    suspects = []
    try:
        conn = sqlite3.connect(str(vault_db))
    except sqlite3.Error as e:
        # DB exists but can't be opened -> fail closed: every new citation is suspect.
        return [{"citation": vp, "claim_words": "<db-unavailable>", "error": str(e)}
                for vp in line_map]

    for vp, line in line_map.items():
        words = _claim_words(line)
        if not words:
            continue
        sanitized = _sanitize_fts(words)
        try:
            row = conn.execute(
                "SELECT count(*) FROM sources WHERE path = ? AND sources MATCH ?",
                (vp, sanitized)
            ).fetchone()
        except sqlite3.Error as e:
            # FTS5 error reaching here means our sanitizer missed something.
            # Don't reject the citation (the edit may be fine) — surface the
            # bug to stderr and treat this citation as unverified.
            print(f"score_diff: FTS5 error verifying {vp}: {e} "
                  f"(words={sanitized!r})", file=sys.stderr)
            continue
        if row[0] == 0:
            suspects.append({"citation": vp, "claim_words": sanitized})
    conn.close()
    return suspects


def matchable_links(text: str) -> int:
    """Count wikilinks in hyphen-case form (no spaces)."""
    return sum(1 for m in WIKILINK_RE.finditer(text)
               if " " not in m.group(1).strip())


def _bad_wikilink_targets(text: str) -> list:
    """Return wikilink target stems that contain a space or uppercase letter.

    These render as broken in Obsidian even when sweep considers them
    live (sweep normalises before matching; Obsidian does not). We gate
    on them to stop new instances from landing.
    """
    bad = []
    for m in WIKILINK_RE.finditer(text):
        inner = m.group(1).strip()
        target = inner.split("|", 1)[0]
        if " " in target or target != target.lower():
            bad.append(target)
    return bad


def metrics(text: str) -> dict:
    return {
        "tokens": body_tokens(text),
        "citations": citation_count(text),
        "wikilinks": matchable_links(text),
    }


def verdict(before: dict, after: dict) -> tuple:
    if after["citations"] < before["citations"]:
        return False, f"citation loss ({before['citations']}->{after['citations']})"
    if before["tokens"] > 0 and after["tokens"] > before["tokens"] * 1.5:
        return False, f"bloat ({before['tokens']}->{after['tokens']}, >50%)"
    return True, "pass"


# Curator annotations region marker — content after this header in a
# notes/ page is the curator's scratch zone (not subject to append-only).
_CURATOR_ANNOTATIONS_MARKER = "## curator-annotations"

# Lines/strings added by the curator that shouldn't count as modifications
# of user-authored content:
#   [[stem]] / [[stem|display]]  — wikilinks wrapping existing terms
#   (note:N<id>) / (todo:T<id>)  — mint-time markers
_WIKILINK_DISPLAY_RE = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
_MINT_MARKER_RE = re.compile(r"\s*\((?:note:N\d+|todo:T\d+)\)")


def _strip_curator_markers(text: str) -> str:
    """Normalise user-body text by removing wikilinks (keeping display
    label) and note/todo mint markers so append-only comparisons only
    see user-authored content.
    """
    def _wikilink_display(m):
        target = m.group(1) or ""
        display = m.group(2)
        return display if display is not None else target

    out = _WIKILINK_DISPLAY_RE.sub(_wikilink_display, text)
    out = _MINT_MARKER_RE.sub("", out)
    # Collapse runs of whitespace so cosmetic spacing doesn't trip the
    # comparison — a worker may insert a space around a wikilink.
    out = re.sub(r"[ \t]+", " ", out)
    return out


def _user_body(text: str) -> str:
    """Extract the user-authored region of a page body (everything
    before `## curator-annotations`).
    """
    _, body = read_frontmatter(text)
    if _CURATOR_ANNOTATIONS_MARKER in body:
        body = body.split(_CURATOR_ANNOTATIONS_MARKER, 1)[0]
    return body


def notes_append_only_verdict(old_text: str, new_text: str,
                                 page: Path) -> tuple:
    """For notes/ pages: every non-blank line from the old user-body
    (stripped of wikilinks + mint markers) must appear — in order — in
    the new user-body (same stripping). Wikilinks and mint markers can
    be added; user content is preserved. new.md and for-attention.md
    are exempt (curator drains them).

    Returns (ok, reason). ok=True when the invariant holds.
    """
    if page.name in ("new.md", "for-attention.md"):
        return True, "notes/ transient (drain-zone exempt)"

    def canon_lines(text: str) -> list:
        body = _user_body(text)
        return [_strip_curator_markers(ln).strip()
                for ln in body.split("\n")
                if _strip_curator_markers(ln).strip()]

    old_lines = canon_lines(old_text)
    new_lines = canon_lines(new_text)
    ni = 0
    for ol in old_lines:
        found = False
        while ni < len(new_lines):
            if new_lines[ni] == ol:
                found = True
                ni += 1
                break
            ni += 1
        if not found:
            snippet = ol[:60] + ("…" if len(ol) > 60 else "")
            return False, (f"notes/ append-only: user line missing or "
                              f"modified (expected to find {snippet!r})")
    return True, "notes/ append-only: preserved"


def _floors_for(page: Path) -> dict:
    """Minimum thresholds for a new page, tightened or relaxed by directory.

    `facts/` and `evidence/` pages are deliberately atomic: a single
    parameter or observation tied to one source. The default floors
    (>=2 citations, >=2 wikilinks, >=100 words) would kill a faithful
    fact like "Kaplan α_N ≈ 0.076 (Kaplan et al. 2020)" before it
    reached the reviewer. Relaxed floors per directory let those pages
    land while keeping the ratchet for denser analyses/concepts.

    `figures/` pages are captioned media: the body is an Obsidian
    transclusion + short caption, not prose. Wikilink/concept-linkage
    lives primarily in frontmatter (`relates_to`), so no wikilink
    floor. A citation is still required — the caption must name its
    source — and a minimal word floor catches empty or placeholder
    pages.
    """
    parts = set(page.parts)
    if "facts" in parts:
        return {"citations": 1, "wikilinks": 1, "words": 30}
    if "evidence" in parts:
        return {"citations": 1, "wikilinks": 1, "words": 50}
    if "figures" in parts:
        return {"citations": 1, "wikilinks": 0, "words": 10}
    if "tables" in parts:
        # Summary tables carry structured rows, not prose — frontmatter +
        # short framing sentence + the markdown table. Wikilinks typically
        # live inside table cells (entity refs, source stubs); citations
        # pin the data to its vault sources. Floor: >=1 citation, 0
        # wikilinks required (cells carry them naturally), >=10 words of
        # framing prose so we don't accept an empty table scaffold.
        return {"citations": 1, "wikilinks": 0, "words": 10}
    if "todos" in parts or "notes" in parts:
        # Notes and todo-list pages are user-authored raw input (notes/)
        # or curator-maintained priority buckets (todos/). Neither
        # warrants the citation/wikilink/words ratchet used for
        # concept/entity/analysis pages — they're staging areas, not
        # finished knowledge artefacts. Zero floors; additional rules
        # (append-only for notes/, todo-ID syntax) enforced separately.
        return {"citations": 0, "wikilinks": 0, "words": 0}
    return {"citations": 2, "wikilinks": 2, "words": 100}


def new_page_verdict(text: str, page: Path = None) -> tuple:
    m = metrics(text)
    words = body_tokens(text)
    floors = _floors_for(page) if page else {"citations": 2, "wikilinks": 2, "words": 100}
    if m["citations"] < floors["citations"]:
        return False, f"too few citations ({m['citations']}; need >={floors['citations']})"
    if m["wikilinks"] < floors["wikilinks"]:
        return False, f"too few wikilinks ({m['wikilinks']}; need >={floors['wikilinks']})"
    if words < floors["words"]:
        return False, f"too short ({words} words; need >={floors['words']})"
    return True, f"citations={m['citations']}, wikilinks={m['wikilinks']}, words={words}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    # --new-text-file is the canonical name (pairs naturally with
    # --new-text-stdin). --new-file kept as back-compat alias; the
    # orchestrator's natural intuition produced --new-text-file
    # repeatedly, costing a retry per page every wave.
    ap.add_argument("--new-text-file", "--new-file", dest="new_file",
                    default=None,
                    help="path to file containing new page text")
    ap.add_argument("--new-text-stdin", action="store_true")
    ap.add_argument("--new-page", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="Return verdict without writing the file.")
    ap.add_argument("--vault-db", default=None,
                    help="Path to vault.db for citation verification.")
    ap.add_argument("--tables-db", default=None,
                    help="Path to tables.db for (table:X#id=Y) citation verification.")
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

    # Silently collapse `%%` → `%` in body prose. Obsidian renders `%%…%%`
    # as a hidden comment; LLMs sometimes emit it while trying to escape a
    # percent sign. This is always wrong in wiki prose.
    new_text = _collapse_double_percent(new_text)

    if args.new_page:
        accept, reason = new_page_verdict(new_text, page)
        result = {
            "page": str(page), "accept": accept, "reason": reason,
            "after": metrics(new_text), "applied": False, "new_page": True,
        }
        if accept:
            bad = _bad_wikilink_targets(new_text)
            if bad:
                accept = False
                reason = (f"invalid wikilink targets (space or uppercase): "
                           f"{sorted(set(bad))[:3]} — use [[kebab-case|Display]]")
                result.update({"accept": False, "reason": reason,
                                "bad_wikilinks": sorted(set(bad))})
        if accept and args.tables_db:
            table_suspects = verify_table_citations("", new_text, Path(args.tables_db))
            if table_suspects:
                accept = False
                reason = ("suspect table citations: "
                           + ", ".join(s["citation"] for s in table_suspects))
                result.update({"accept": False, "reason": reason,
                                "table_suspects": table_suspects})
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

    # notes/ pages enforce append-only on user-authored content.
    # Curator writes can add wikilinks and mint markers; they cannot
    # delete or rewrite the user's prose. Exemption: new.md and
    # for-attention.md, which are curator-drained staging areas.
    if accept and "notes" in set(page.parts):
        ok, note_reason = notes_append_only_verdict(old_text, new_text, page)
        if not ok:
            accept = False
            reason = note_reason

    result = {
        "page": str(page), "accept": accept, "reason": reason,
        "before": before, "after": after, "applied": False,
    }

    if accept and args.vault_db:
        suspects = verify_new_citations(old_text, new_text, Path(args.vault_db))
        if suspects:
            accept = False
            reason = f"suspect citations: {', '.join(s['citation'] for s in suspects)}"
            result.update({"accept": False, "reason": reason, "suspects": suspects})

    if accept and args.tables_db:
        table_suspects = verify_table_citations(old_text, new_text, Path(args.tables_db))
        if table_suspects:
            accept = False
            reason = ("suspect table citations: "
                       + ", ".join(s["citation"] for s in table_suspects))
            result.update({"accept": False, "reason": reason,
                            "table_suspects": table_suspects})

    if accept:
        before_bad = set(_bad_wikilink_targets(old_text))
        after_bad = set(_bad_wikilink_targets(new_text))
        new_bad = after_bad - before_bad
        if new_bad:
            accept = False
            reason = (f"invalid wikilink targets added (space or uppercase): "
                       f"{sorted(new_bad)[:3]} — use [[kebab-case|Display]]")
            result.update({"accept": False, "reason": reason,
                            "bad_wikilinks": sorted(new_bad)})

    if accept and write:
        page.write_text(new_text)
        result["applied"] = True

    print(json.dumps(result))


if __name__ == "__main__":
    main()
