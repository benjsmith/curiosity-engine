#!/usr/bin/env python3
"""Minimal mechanical gate for wiki edits.

Hard floors only — the opus judge handles nuanced quality review.
These gates catch catastrophic regressions that no edit should cause:
  1. No citation loss: citations(after) >= citations(before)
  2. No extreme raw-token bloat: body_tokens(after) <= body_tokens(before) * 1.5,
     raised for stub expansion (placeholder -> normal page length) and for
     citation-backed growth (padding doesn't cite)
  3. New pages: floor depends on directory —
       facts/*:     >=1 citation, >=1 wikilink, >=30 words
                    (verbatim: true → >=15 words; origin bootstrap* → 0 wikilinks ok)
       evidence/*:  >=1 citation, >=1 wikilink, >=50 words
       figures/*:   >=1 citation, 0 wikilinks, >=10 words
       tables/*:    >=1 citation, 0 wikilinks, >=10 words
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
  citation, probes the claim line's most distinctive terms against the cited
  source in FTS5 and requires half of them to hit. Rejects if any new
  citation is suspect. A handful of single-term FTS5 queries per new
  citation — negligible overhead.
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
from naming import (  # noqa: E402
    WIKILINK_RE, CITATION_RE, read_frontmatter, normalize_ligatures,
)

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


def verify_table_shapes(old_text: str, new_text: str,
                         tables_db: Path) -> list:
    """For each newly-added table-row citation, verify the cited row passes
    the U3 shape constraints declared on its table (units / constraint /
    source_required). Parallels verify_table_citations: that checks the row
    *exists*; this checks it's shape-valid. Tables with no shape keys are
    skipped. Returns a list of suspects (empty = all OK)."""
    if not tables_db.exists():
        return []
    added = [(t, k, v) for (t, k, v) in
             (set(_table_citations(new_text)) - set(_table_citations(old_text)))
             if k == "id"]
    if not added:
        return []
    from shape_check import check_row, has_shape_constraints  # noqa: E402
    import tables as _tables  # noqa: E402
    try:
        conn = sqlite3.connect(str(tables_db), timeout=5)
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        return []
    suspects = []
    schema_cache: dict = {}
    for (name, _kind, value) in added:
        if name not in schema_cache:
            row = conn.execute(
                "SELECT schema_json FROM _schema_meta WHERE table_name = ?",
                (name,)).fetchone()
            try:
                schema_cache[name] = _tables._normalize_columns(
                    json.loads(row[0])) if row else []
            except (json.JSONDecodeError, TypeError):
                schema_cache[name] = []
        cols = schema_cache[name]
        if not cols or not has_shape_constraints(cols):
            continue
        pk = next((c["name"] for c in cols if c["pk"]), None)
        if not pk:
            continue
        try:
            cur = conn.execute(
                f'SELECT * FROM "{name}" WHERE "{pk}" = ? LIMIT 1', (value,))
            r = cur.fetchone()
            if r is None:
                continue  # existence is verify_table_citations' job
            violations = check_row(cols, dict(zip([d[0] for d in cur.description], r)))
            if violations:
                suspects.append({"citation": f"table:{name}#id={value}",
                                  "shape_violations": violations})
        except sqlite3.Error:
            continue
    conn.close()
    return suspects


def _citations_set(text: str) -> set:
    """Extract the set of vault paths cited in text."""
    return set(CITATION_RE.findall(text))


def _claim_words(line: str) -> str:
    """Extract significant content words from a citation line for FTS5 matching."""
    cleaned = CITATION_RAW_RE.sub("", line)
    cleaned = re.sub(r"\[\[[^\]]*\]\]", "", cleaned)
    # Ligature-normalise before word extraction: a curator who copied the
    # paper's own wording out of a PDF extraction may carry `speciﬁc`
    # (U+FB01), which `[a-zA-Z]{4,}` would split into `speci` + a dropped
    # `c` and then fail to match anything.
    words = re.findall(r"[a-zA-Z]{4,}", normalize_ligatures(cleaned))
    stop = {"the", "and", "for", "are", "was", "were", "with", "from",
            "that", "this", "which", "have", "has", "been", "also",
            "more", "than", "about", "their", "other", "some"}
    return " ".join(w for w in words if w.lower() not in stop)


# --- citation relevance ------------------------------------------------
# This check asks whether the cited source actually discusses the claim.
# It deliberately does NOT issue one bare multi-term FTS5 MATCH: FTS5 ANDs
# bare terms, so a compressed single-line paragraph — 40-90 content words
# under the default `write_other: ultra` — demands that every one of those
# words occur in that one document. Measured against a curated 25-source
# wiki, the AND form rejected 50% of citations an opus reviewer had
# already approved, and the workaround it taught workers (prepend a short
# lead line carrying the citation) is citation inflation the schema's
# "no filler" rule forbids.
#
# Instead: probe the claim's most DISTINCTIVE terms individually and
# require a coverage fraction. Distinctiveness carries the signal — in a
# topically homogeneous vault every paper contains "model" and "training",
# so coverage over ALL terms barely separates a real citation from a wrong
# one (47% of hard negatives passed at this threshold). Restricting the
# probe to low-document-frequency terms drops that to ~21% while accepting
# 100% of the genuine citations in the calibration set.
#
# Bias is toward accepting: a false accept is still caught by the batch
# reviewer downstream, while a false reject costs a full worker round-trip.
CITATION_COVERAGE_FLOOR = 0.5
CITATION_MAX_PROBE_TERMS = 12
# One surviving term is not evidence about a claim: a single miss reads as
# 0% coverage and rejects the citation on a coin flip. Observed on real
# pages whose lone probe was ordinary vocabulary ("becomes", "adjacent",
# "entry") that happened to be uncommon in a 25-document corpus — df
# filtering cannot tell rare-in-this-corpus from claim-specific. Below the
# floor the check fails open.
#
# Calibrated honestly: this is a pre-filter, not a precision instrument.
# It catches blatantly wrong citations — a claim whose distinctive terms
# are entirely absent from the cited source — and accepts a substantial
# share of subtler misattributions, which is what the opus batch reviewer
# is for. The bias is deliberate, because a false reject costs a whole
# worker round-trip and teaches workers to pad prose.
CITATION_MIN_PROBE_TERMS = 2
# A term in more than this fraction of the corpus is shared vocabulary,
# not claim-specific evidence. On a very small vault the ceiling collapses
# to 1 document and probe sets come out empty — which fails open (skip the
# check) rather than rejecting everything.
CITATION_MAX_DF_FRACTION = 0.5


def _term_in_doc(conn, path: str, term: str):
    """True if `term` FTS5-matches within one document. None on FTS5 error."""
    try:
        row = conn.execute(
            "SELECT count(*) FROM sources WHERE path = ? AND sources MATCH ?",
            (path, _sanitize_fts(term))
        ).fetchone()
        return row[0] > 0
    except sqlite3.Error:
        return None


def _probe_terms(conn, line: str, ndocs: int, df_cache: dict) -> list:
    """The claim line's most distinctive indexed terms, rarest first.

    Terms absent from the whole corpus (df == 0) are the curator's own
    analytical vocabulary — "interpretation", "propagates", a coined
    label. They can never match any source, so they carry no evidence
    about *this* source and are excluded from the denominator instead of
    counted as failures.
    """
    seen, terms = set(), []
    for w in _claim_words(line).split():
        k = w.lower()
        if k not in seen:
            seen.add(k)
            terms.append(w)

    df_ceiling = max(1, int(ndocs * CITATION_MAX_DF_FRACTION))
    scored = []
    for t in terms:
        k = t.lower()
        if k not in df_cache:
            try:
                row = conn.execute(
                    "SELECT count(*) FROM sources WHERE sources MATCH ?",
                    (_sanitize_fts(t),)
                ).fetchone()
                df_cache[k] = row[0]
            except sqlite3.Error:
                df_cache[k] = -1
        df = df_cache[k]
        if df <= 0 or df > df_ceiling:
            continue
        scored.append((df, t))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [t for _, t in scored[:CITATION_MAX_PROBE_TERMS]]


def verify_new_citations(old_text: str, new_text: str,
                          vault_db: Path) -> list:
    """Check that each newly added citation actually relates to the claim.

    For each (vault:path) in new_text but not in old_text, probes the most
    distinctive content words on the citation's line against that specific
    source in FTS5 and requires `CITATION_COVERAGE_FLOOR` of them to hit.
    Below the floor the citation is suspect — the source doesn't discuss
    what the claim talks about.

    A citation whose path isn't in the index at all is reported separately
    (`reason: "source-not-indexed"`): that's a broken citation path, not an
    unsupported claim, and the two need different repairs.

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

    try:
        ndocs = conn.execute("SELECT count(*) FROM sources").fetchone()[0]
    except sqlite3.Error as e:
        conn.close()
        return [{"citation": vp, "claim_words": "<index-unreadable>", "error": str(e)}
                for vp in line_map]

    df_cache: dict = {}
    for vp, line in line_map.items():
        try:
            indexed = conn.execute(
                "SELECT count(*) FROM sources WHERE path = ?", (vp,)
            ).fetchone()[0]
        except sqlite3.Error:
            indexed = 1  # can't tell — don't invent a failure
        if not indexed:
            # The cited file may well exist on disk; what it isn't is an
            # indexed FTS5 entry, so no claim against it can ever verify.
            # Say that plainly instead of blaming the prose.
            suspects.append({"citation": vp, "reason": "source-not-indexed",
                              "claim_words": "<source-not-indexed>"})
            continue

        probes = _probe_terms(conn, line, ndocs, df_cache)
        if len(probes) < CITATION_MIN_PROBE_TERMS:
            # Too little distinctive vocabulary to judge — fail open.
            continue
        results = [(t, _term_in_doc(conn, vp, t)) for t in probes]
        testable = [(t, hit) for t, hit in results if hit is not None]
        if len(testable) < CITATION_MIN_PROBE_TERMS:
            print(f"score_diff: FTS5 error verifying {vp} "
                  f"(probes={probes!r})", file=sys.stderr)
            continue
        hits = [t for t, hit in testable if hit]
        coverage = len(hits) / len(testable)
        if coverage < CITATION_COVERAGE_FLOOR:
            suspects.append({
                "citation": vp,
                "reason": "low-claim-coverage",
                "coverage": round(coverage, 2),
                "floor": CITATION_COVERAGE_FLOOR,
                "probed": [t for t, _ in testable],
                "missing": [t for t, hit in testable if not hit],
                "claim_words": " ".join(t for t, _ in testable),
            })
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


# Below this many body tokens a page is a placeholder, not prose, and a
# multiplicative ceiling is the wrong instrument: the skill creates its own
# stubs (`sweep.py fix-source-stubs`, demand promotions, bootstrap), so a
# 1.5× cap blocks the first real curation pass on every stub it ever makes.
# Measured on a curated wiki, source stubs sit at 31-62 body tokens while
# finished concept/entity pages sit at 160-230 — i.e. the work the stub
# exists to receive is a 3-4× expansion by construction.
STUB_TOKEN_CEILING = 120
# What a stub is allowed to become in one pass: a shade above the p75 of
# finished concept/entity pages, so it can reach normal length but not
# balloon into an analysis (those start around 375 tokens).
STUB_TARGET_TOKENS = 240
# Hard ceiling on any computed allowance. 4.0 is what curate waves were
# already passing by hand via --bloat-mult when fighting this.
MAX_EFFECTIVE_BLOAT_MULT = 4.0


def _bloat_ceiling(before: dict, after: dict, bloat_mult: float) -> tuple:
    """Body-token ceiling for this edit, plus labels for allowances applied.

    Two relaxations, both of which only ever raise the ceiling:

    - **stub expansion** — a placeholder page may reach normal page length.
    - **citation-backed growth** — the cap exists to catch padding, and
      padding does not cite. An edit that triples the body while going
      1 -> 9 citations is nine sources' worth of grounded prose, not the
      failure mode being guarded against.
    """
    ceiling = before["tokens"] * bloat_mult
    allowances = []

    if before["tokens"] < STUB_TOKEN_CEILING and STUB_TARGET_TOKENS > ceiling:
        ceiling = float(STUB_TARGET_TOKENS)
        allowances.append("stub-expansion")

    cite_before, cite_after = before["citations"], after["citations"]
    if cite_after > cite_before:
        growth = (cite_after / cite_before if cite_before
                  else MAX_EFFECTIVE_BLOAT_MULT)
        effective = min(bloat_mult * growth, MAX_EFFECTIVE_BLOAT_MULT)
        if before["tokens"] * effective > ceiling:
            ceiling = before["tokens"] * effective
            allowances.append(f"citation-backed({cite_before}->{cite_after})")

    return ceiling, allowances


def verdict(before: dict, after: dict, bloat_mult: float = 1.5) -> tuple:
    """Mechanical gate. `bloat_mult` overrides the default 1.5× ceiling
    on body-token growth — restyle waves pass 2.0× because prose
    hydration of compressed pages legitimately expands the body
    (typically ~1.5–1.65×) without adding new content. Citation
    floor is unconditional; the multiplier only relaxes the bloat
    side of the gate. `_bloat_ceiling` may raise the ceiling further for
    stub expansion and citation-backed growth."""
    if after["citations"] < before["citations"]:
        return False, f"citation loss ({before['citations']}->{after['citations']})"
    if before["tokens"] > 0:
        ceiling, allowances = _bloat_ceiling(before, after, bloat_mult)
        if after["tokens"] > ceiling:
            detail = f", allowed {'+'.join(allowances)}" if allowances else ""
            return False, (f"bloat ({before['tokens']}->{after['tokens']}, "
                            f"ceiling {int(ceiling)}{detail})")
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


def _floors_for(page: Path, text=None) -> dict:
    """Minimum thresholds for a new page, tightened or relaxed by directory.

    `facts/` and `evidence/` pages are deliberately atomic: a single
    parameter or observation tied to one source. The default floors
    (>=2 citations, >=2 wikilinks, >=100 words) would kill a faithful
    fact like "Kaplan α_N ≈ 0.076 (Kaplan et al. 2020)" before it
    reached the reviewer. Relaxed floors per directory let those pages
    land while keeping the ratchet for denser analyses/concepts.

    `verbatim: true` facts (caption-grade / near-quote claims) use a
    15-word floor so short exam atoms pass. Bootstrap-origin facts
    (`origin: bootstrap*`) may ship with 0 wikilinks — the bootstrap
    links pack densifies them later.

    `figures/` pages are captioned media: the body is an Obsidian
    transclusion + short caption, not prose. Wikilink/concept-linkage
    lives primarily in frontmatter (`relates_to`), so no wikilink
    floor. A citation is still required — the caption must name its
    source — and a minimal word floor catches empty or placeholder
    pages. `origin: caption-text` needs no binary asset.
    """
    parts = set(page.parts)
    fm = {}
    if text:
        try:
            fm, _ = read_frontmatter(text)
        except Exception:
            fm = {}
    origin = str(fm.get("origin") or "").strip().lower()
    verbatim = fm.get("verbatim")
    is_verbatim = verbatim is True or str(verbatim).lower() in ("true", "1", "yes")
    is_bootstrap = origin.startswith("bootstrap")

    if "facts" in parts:
        # Verbatim/bootstrap: short exam or caption atoms (floor 15; short
        # captions still clear when the body includes a framing clause).
        words = 15 if (is_verbatim or is_bootstrap) else 30
        wikilinks = 0 if is_bootstrap else 1
        return {"citations": 1, "wikilinks": wikilinks, "words": words}
    if "evidence" in parts:
        return {"citations": 1, "wikilinks": 1, "words": 50}
    if "figures" in parts:
        return {"citations": 1, "wikilinks": 0, "words": 10}
    if "tables" in parts:
        # Summary tables, extracted grids, and caption-only table pages.
        # Floor: >=1 citation, 0 wikilinks required, >=10 words framing.
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
    floors = (_floors_for(page, text) if page
              else {"citations": 2, "wikilinks": 2, "words": 100})
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
    ap.add_argument("--bloat-mult", type=float, default=1.5,
                    help="Override the body-token bloat cap. Default 1.5; "
                         "restyle waves pass 2.0 because prose hydration of "
                         "compressed pages legitimately expands ~1.5-1.65×.")
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
        if accept and args.tables_db:
            shape_suspects = verify_table_shapes("", new_text, Path(args.tables_db))
            if shape_suspects:
                accept = False
                reason = ("shape-violating table citations: "
                           + ", ".join(s["citation"] for s in shape_suspects))
                result.update({"accept": False, "reason": reason,
                                "shape_suspects": shape_suspects})
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
    accept, reason = verdict(before, after, bloat_mult=args.bloat_mult)

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
            # Name the two failure kinds separately in the headline reason.
            # They need opposite repairs — rewrite the claim vs fix the path
            # — and a curator that reads only `reason` shouldn't be sent to
            # rewrite prose that was never the problem.
            unindexed = [s["citation"] for s in suspects
                         if s.get("reason") == "source-not-indexed"]
            unsupported = [s["citation"] for s in suspects
                           if s.get("reason") != "source-not-indexed"]
            parts = []
            if unsupported:
                parts.append(f"suspect citations: {', '.join(unsupported)}")
            if unindexed:
                parts.append("citation paths not in the vault index "
                              f"(fix the path or re-index): {', '.join(unindexed)}")
            reason = "; ".join(parts)
            result.update({"accept": False, "reason": reason, "suspects": suspects})

    if accept and args.tables_db:
        table_suspects = verify_table_citations(old_text, new_text, Path(args.tables_db))
        if table_suspects:
            accept = False
            reason = ("suspect table citations: "
                       + ", ".join(s["citation"] for s in table_suspects))
            result.update({"accept": False, "reason": reason,
                            "table_suspects": table_suspects})

    if accept and args.tables_db:
        shape_suspects = verify_table_shapes(old_text, new_text, Path(args.tables_db))
        if shape_suspects:
            accept = False
            reason = ("shape-violating table citations: "
                       + ", ".join(s["citation"] for s in shape_suspects))
            result.update({"accept": False, "reason": reason,
                            "shape_suspects": shape_suspects})

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
