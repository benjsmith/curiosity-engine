#!/usr/bin/env python3
"""naming.py — shared naming + display-title utilities for the curiosity engine.

Used by:
  - sweep.py fix-source-stubs                    (citation-style source summaries)
  - local_ingest.py                              (source pages on ingest)
  - naming.py recommend-analysis                 (QUERY crystallise anti-crowding)
  - CURATE workers / reviewers                   (new page creation)

Hash-guarded by evolve_guard.sh. Deterministic, stdlib only.

Exposes:
  FRONTMATTER_TYPES   — valid `type:` frontmatter values
  TYPE_PREFIX         — short tag prefix for display titles (`[con]`, `[ent]`, ...)
  read_frontmatter    — parse YAML-ish frontmatter into dict + body
  citation_stem       — build `topic-author-year` or `topic-origin-year` slug
  source_display_title — build "Title - Author, Year" human-readable title
  parse_source_meta   — extract {topic, origin, year, author, full_title}
                        from a vault extraction file
  extract_topic       — pull clean topic from a raw vault stem
  url_to_origin       — map a URL to a short origin label
  normalize_ligatures — expand ﬁ/ﬂ/ﬀ… so FTS5 terms match ASCII prose
  repair_letter_spacing — rejoin pypdf's tracked-heading splits (QL ORA)

Filename/slug conventions:
  - Papers:    attention-vaswani-2017
  - Wikipedia: deep-learning-wikipedia-2026
  - Evidence:  evi-<topic>-<source>-<year>
  - Fact:      fact-<short-claim>
"""
# Defer annotation evaluation so PEP 604 unions (`str | None`) and
# built-in generics (`list[str]`) parse on Python 3.9 — setup.sh's
# floor.
from __future__ import annotations

import re
from pathlib import Path


SKIP_FILES = {"index.md", "log.md", "schema.md"}
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
CITATION_RE = re.compile(r"\(vault:([^)]+)\)")

# Typographic ligatures survive PDF text extraction — pypdf faithfully
# preserves the codepoints TeX emitted — and FTS5's `unicode61` tokenizer
# folds case but does not decompose them. So `speciﬁc` (U+FB01) and
# `specific` are two unrelated tokens, and a curator writing ordinary
# ASCII prose can never match the indexed text. The fi/fl/ff/ffi/ffl set
# covers most of the affected ML vocabulary: specific, efficient,
# different, final, workflow, coefficient.
#
# Deliberately a targeted table rather than `unicodedata.normalize("NFKC")`:
# NFKC also flattens superscripts and fractions, which silently corrupts
# scientific prose (`10²` becomes `102`, `½` becomes `1⁄2`).
LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",   # long-s + t
    "ﬆ": "st",
}
_LIGATURE_TABLE = str.maketrans(LIGATURES)


def normalize_ligatures(text: str) -> str:
    """Expand typographic ligatures to ASCII so FTS5 terms match.

    Applied on both sides of the search boundary: at extraction time
    (`local_ingest.py`), at index time (`vault_index.py`, so existing
    vaults heal on `--rebuild`), and to claim text before it becomes an
    FTS5 query (`score_diff.py`). Idempotent and safe on any string.
    """
    return text.translate(_LIGATURE_TABLE)


# pypdf's `extract_text()` inserts a space inside a word when the PDF renders
# it with tracking (letter-spacing) — the norm for display headings and
# small-caps runs in paper templates. `QLORA` extracts as `QL ORA` (34 times
# in one real extraction), `REACT` as `REAC T`. Since extractions are the
# FTS5-indexed citation target, the split token is not merely ugly: the term
# becomes unsearchable and `score_diff`'s claim-word probes miss it.
#
# Repaired by rejoining against the document's OWN vocabulary, which makes
# the check self-verifying with no dictionary and no second extraction pass:
# a paper that letter-spaces `QLORA` in a heading also writes `QLoRA` in
# body text 20 times, so the joined form is confirmed present before any
# edit. Measured over 565 real extractions: 6 documents touched, 21 repairs,
# zero false positives.
#
# Both halves must be wholly uppercase. A looser "mostly caps" rule admitted
# bibliography entries — `In ACL` / `In EMNLP` became `InACL` / `InEMNLP` —
# because `In` is title-case, not tracked text.
_TRACKED_PAIR_RE = re.compile(r"\b([A-Za-z]{1,6})[ ](?=([A-Za-z]{1,6})\b)")
_VOCAB_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
_MIN_JOIN_LEN = 4


def _unquote(value: str) -> str:
    """Strip one layer of matching surrounding quotes, repeatedly.

    Repeatedly, because a value may already carry nested quoting written
    by an earlier round-trip through a re-quoting writer; one pass would
    leave `"\\"39\\""` as `\\"39\\"`.
    """
    out = value.strip()
    while len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'":
        inner = out[1:-1]
        # Unescape the layer we just removed, so `\"39\"` becomes `"39"`.
        out = inner.replace('\\"', '"').replace("\\'", "'").strip()
    return out


def repair_letter_spacing(text: str) -> str:
    """Rejoin intra-word spaces pypdf introduced on letter-spaced text.

    Conservative by construction: a join happens only when both halves are
    uppercase, the joined form is at least `_MIN_JOIN_LEN` characters, the
    document already uses that joined form elsewhere, and the two halves are
    not both independently attested as words (which would make the join
    ambiguous). Anything failing a test is left exactly as-is.

    Applied to pypdf output at extraction time (`local_ingest.py`) and, for
    pypdf-derived extractions, at index time (`vault_index.py`) so vaults
    predating this heal on `--rebuild` without rewriting append-only files.
    """
    vocab = {t.casefold() for t in _VOCAB_TOKEN_RE.findall(text)}
    accepted = []

    for m in _TRACKED_PAIR_RE.finditer(text):
        a, b = m.group(1), m.group(2)
        if not (a.isupper() and b.isupper()):
            continue
        joined = a + b
        if len(joined) < _MIN_JOIN_LEN:
            continue
        if joined.casefold() not in vocab:
            continue
        if a.casefold() in vocab and b.casefold() in vocab:
            continue
        start, end = m.start(), m.end() + len(b)
        # The lookahead lets candidates overlap, which is deliberate: with a
        # consuming pattern a REJECTED candidate (`4-BIT QL`) swallows the
        # text and hides the real one (`QL ORA`) behind it, which silently
        # left 13 of 34 occurrences unrepaired. Accepted joins must still not
        # overlap each other.
        if accepted and start < accepted[-1][1]:
            continue
        accepted.append((start, end, joined))

    if not accepted:
        return text
    out, cursor = [], 0
    for start, end, joined in accepted:
        out.append(text[cursor:start])
        out.append(joined)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


FRONTMATTER_TYPES = {"entity", "concept", "source", "analysis", "evidence",
                      "fact", "summary-table", "extracted-table", "figure",
                      "note", "todo-list", "project"}

# Allowlist of frontmatter keys the curator actually reads. Unknown keys are
# dropped by read_frontmatter so an adversarial source cannot smuggle
# arbitrary keyed data into downstream callers via frontmatter.
ALLOWED_FM_KEYS = frozenset({
    # Wiki page schema
    "title", "type", "created", "updated", "sources",
    # Source-stub / ingest provenance
    "source_path", "source_url", "source_type", "ingested_at", "fetched_at",
    "sha256", "vault_sha256", "bytes", "kept_as", "extraction",
    "max_extract_bytes", "untrusted",
    # `source_in_place` marks an extraction whose original was never copied
    # into vault/. local_ingest.py has always written it, but it was missing
    # here — so read_frontmatter silently dropped it and no consumer using
    # the parser could see it. That is why scan.py raw-parses the key by
    # line prefix instead. Not a smuggling risk: extraction frontmatter is
    # authored by local_ingest, never by the source (whose content lands in
    # the body, inside the FETCHED CONTENT markers).
    "source_in_place",
    # Author/metadata (used by parse_source_meta). `authors` (plural) is
    # the standard arXiv/paper form; `author` (singular) is used by blog/
    # email-style sources. Both are allowed.
    "author", "authors", "from", "date", "subject",
    # Class-table schema (on entity pages) and summary-table metadata
    # (on wiki/tables/ pages). `table` is a nested mapping parsed via
    # PyYAML in tables.py; the other keys annotate summary tables that
    # pin a query or describe their source.
    "table", "source_query", "source_table",
    # Figure metadata (on wiki/figures/ pages). `asset` names the binary
    # in assets/figures/; `origin` is extracted|created; `source_page`
    # pairs with `source_path` for PDF regeneration; `source_analysis`
    # points at the analysis that produced a created figure;
    # `extraction_method` records how the asset was produced so
    # figures.py regen can reproduce it deterministically; `page_region`
    # is a human hint when a page-render asset hosts multiple figures;
    # `relates_to` is the reverse-index of pages the figure depicts.
    "asset", "origin", "source_page", "source_analysis",
    "extraction_method", "page_region", "relates_to",
    # Figure-extraction completion flag (on vault extraction stubs).
    # ISO timestamp set by `figures.py mark-extracted` when the
    # multimodal figure-extraction pass has run against the source —
    # regardless of whether any figures were produced. Absence means
    # pending work; presence suppresses re-extraction.
    "figures_extracted",
    # Table-extraction signals (on vault extractions, on
    # `wiki/tables/tab-*.md` extracted-table pages, and on the source
    # stubs they link to). `has_tables` / `tables_extracted` /
    # `tables_present` are written by local_ingest.py; `tables_filtered`
    # records blocks pdfplumber returned that failed the quality filter,
    # which is what explains `has_tables: true` alongside
    # `tables_extracted: 0`; `extracted_from` / `table_index` /
    # `row_count` / `db_table` / `is_snapshot` / `extraction_sha`
    # annotate the `[tab]` wiki pages produced by
    # `sweep.py promote-extracted-tables`.
    "has_tables", "tables_extracted", "tables_present", "tables_filtered",
    "structured_version", "structured_format", "structured_options", "structured_settings",
    "structured_preview_version",
    "correction_manifest",
    "data_complete", "preview_truncated", "records_accepted", "records_rejected",
    "collection_path", "collection_kind", "record_encoding", "table_content_sha",
    # `cid_glyphs` counts `(cid:NN)` artifacts — glyphs the PDF's font
    # carried no Unicode mapping for. Non-zero means part of the text is
    # structurally present but unreadable, which is why the source is
    # escalated to the multimodal reader.
    "cid_glyphs",
    "extracted_from", "table_index", "row_count",
    "db_table", "is_snapshot", "extraction_sha",
    # Multimodal-upgrade flag (vault extractions): pypdf failed sanity
    # OR the doc has math/tables the text extractor mangled.
    "multimodal_recommended", "has_math", "sanity_note",
    "extraction_method", "extraction_quality",
    # Multimodal-table-extract pass annotations (vault extractions).
    # `multimodal_extracted` is the ISO timestamp set by
    # `sweep.py mark-multimodal-extracted` after the Sonnet
    # `scientific_table_extractor` pass writes recovered tables back
    # into the body. `parsing_issues` and `extraction_notes` are
    # per-table self-flags emitted by the worker (lists of strings).
    "multimodal_extracted", "parsing_issues", "extraction_notes",
    # Numeric-review pass annotations (on `wiki/tables/tab-*.md`
    # pages produced from multimodal-extracted sources).
    # `numeric_review_done` is the reviewer-pass timestamp;
    # `verdict` is one of {ok, suspect, wrong}; `flagged_cells_count`
    # is a cheap queryable signal (the cell-level detail lives in the
    # `## Numeric review` body block where it's both human-readable
    # and grep-able); `review_required` flips to true when the
    # reviewer needs human follow-up; `backup_id` is the rewind
    # handle for `wrong`-verdict auto-overwrites; `source_pages`
    # lists the 1-indexed PDF pages the table came from for
    # spot-checking.
    "numeric_review_done", "verdict", "flagged_cells_count",
    "review_required", "backup_id", "source_pages",
    # Identifier-normalisation flag (on `wiki/tables/tab-*.md` pages).
    # `normalise_columns` is a list of `"column:type"` strings (e.g.
    # `["Compound:chemicals", "Gene:genes"]`) set by
    # `sweep.py promote-extracted-tables` from a deterministic header
    # heuristic; curators may edit to override. The string form
    # roundtrips through the simple bracket-list parser.
    "normalise_columns",
    # Multi-project model. `projects` is a list of project-name slugs the
    # page belongs to (derived by classify-projects from the citation
    # graph; user-overridable). `description` is the human-supplied
    # one-liner on a project home page (`wiki/projects/<name>.md`).
    # `ingest_kind` is set by archive-imports to "archival" so the
    # default-mode activity score can filter them out (current ingests
    # are unset / "current"). See docs/multi-project.md.
    "projects", "description", "ingest_kind",
    # Entity identity (U1, on `wiki/entities/` pages). `iri` is the
    # workspace-stable minted identifier (`ce:<class>:<workspace>:<slug>`)
    # recorded by `identifier_cache.py mint-entity`; it is the join key
    # `curiosity-merge` reconciles on, replacing slug matching. `same_as`
    # is a bracket-list of `authority:id` pairs (e.g.
    # `[pubchem:CID2244, wikidata:Q18253]`) holding external canonical
    # ids — owl:sameAs-style links that never gate identity. `entity_class`
    # names the resolution domain (chemical|gene|protein|person|org|
    # concept|...) and selects which authority resolver, if any, applies.
    # `aliases` is a bracket-list of curated synonyms/codenames for the
    # page's subject; the entity-resolution gate (entity_gate.py) resolves
    # query mentions through it, so a name listed here answers as the page's
    # entity while unlisted look-alikes abstain. All four roundtrip through
    # read_frontmatter's bracket-list parser.
    "iri", "same_as", "entity_class", "aliases",
    # Bootstrap densify + caption harvest (v0.9.2). `origin` distinguishes
    # caption-text figures, bootstrap facts (0 wikilink floor until the
    # links pack), and bulk harvest. `verbatim: true` relaxes the fact
    # word floor to 15 for near-quote exam/caption claims.
    "origin", "verbatim",
})

TYPE_PREFIX = {
    "concept": "[con]",
    "entity": "[ent]",
    "analysis": "[ana]",
    "source": "[src]",
    "evidence": "[evi]",
    "fact": "[fact]",
    "summary-table": "[tbl]",
    "extracted-table": "[tab]",
    "figure": "[fig]",
    "note": "[note]",
    "todo-list": "[todo]",
    "project": "[proj]",
}

# Filename stem prefixes for the page types that live in dedicated
# subdirectories (`wiki/tables/`, `wiki/figures/`). Helps Obsidian
# quick-switcher group them and disambiguates stems across the wiki.
# `tab-` for extracted-table pages keeps them lexicographically
# adjacent to `tbl-` summary tables but visually distinct.
STEM_PREFIX = {
    "summary-table": "tbl-",
    "extracted-table": "tab-",
    "figure": "fig-",
}


def _slugify(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (topic or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def prefixed_stem(page_type: str, topic: str) -> str:
    """Build a filename stem with the type's prefix, if any.

    `summary-table` → `tbl-<slug>`; `figure` → `fig-<slug>`; other
    types pass through the raw slug unchanged. Idempotent — never
    double-prefixes when the topic already starts with the prefix.
    """
    slug = _slugify(topic)
    prefix = STEM_PREFIX.get(page_type, "")
    if prefix and slug.startswith(prefix):
        return slug
    return f"{prefix}{slug}" if prefix else slug


def read_frontmatter(text: str) -> tuple:
    """Parse leading ``---\\n...\\n---\\n`` frontmatter. Returns (dict, body).

    Handles: quoted values (``title: "Some: Title"``), bracket lists
    (``sources: [a.md, b.md]`` → list), multi-line YAML lists
    (``authors:\\n  - Alice\\n  - Bob`` → list), and bare scalars. Keys
    outside ``ALLOWED_FM_KEYS`` are silently dropped.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:]
    fm = {}
    lines = fm_block.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        # Lines indented under a parent (e.g. column rows inside a
        # `table:` block) are NOT top-level keys. Without this guard a
        # column like `      type: string` was getting parsed as the
        # page's `type` and overwriting the real value.
        if raw and raw[0] in (" ", "\t"):
            i += 1
            continue
        line = raw.strip()
        if not line or ":" not in line:
            i += 1
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        v = v.strip()
        if key not in ALLOWED_FM_KEYS:
            i += 1
            continue
        if not v:
            # Multi-line YAML list: `key:` with empty value, followed by
            # indented `- item` lines. Collect contiguous item lines.
            items = []
            j = i + 1
            while j < len(lines):
                raw = lines[j]
                if not raw or not (raw.startswith(" ") or raw.startswith("\t")):
                    break
                stripped = raw.strip()
                if stripped.startswith("-"):
                    items.append(stripped[1:].strip())
                    j += 1
                    continue
                break
            if items:
                fm[key] = items
                i = j
                continue
        if v.startswith("[") and v.endswith("]"):
            # Strip quotes from list items exactly as the scalar branch
            # below does. Keeping them made the parse asymmetric — a scalar
            # `"x"` read back as `x` while a list `["x"]` read back as
            # `'"x"'` — so any writer that re-quotes on output (see
            # `sweep._assemble_page`) deepened the quoting on every
            # round-trip: `["x"]` -> `["\"x\""]` -> `["\"\\\"x\\\"\""]`.
            # It also silently broke consumers doing
            # `name.endswith(".extracted.md")` against a quoted item.
            fm[key] = [_unquote(x.strip()) for x in v[1:-1].split(",")
                       if x.strip()]
        elif (v.startswith('"') and v.endswith('"')) or \
             (v.startswith("'") and v.endswith("'")):
            fm[key] = v[1:-1]
            if key in ("title", "collection_path") and v.startswith('"'):
                try:
                    import json
                    fm[key] = json.loads(v)
                except ValueError:
                    pass  # Legacy YAML scalars may use non-JSON escapes.
        else:
            fm[key] = v
        i += 1
    return fm, body


def set_frontmatter_field(text: str, key: str, value_str: str | None) -> str:
    """Replace, insert, or remove a single-line frontmatter field.

    Removes any prior single-line OR multi-line YAML form of the key
    so a field that started as ``foo:\\n  - a\\n  - b`` is rewritten
    as ``foo: <value_str>`` cleanly.

    Pass ``value_str=None`` to delete the field entirely.

    Returns text unchanged when the input has no frontmatter
    (no leading ``---\\n`` block) or when the closing ``\\n---\\n``
    cannot be found. Caller is responsible for formatting the value
    string (e.g. ``"[a, b]"`` for an inline list, ``'"quoted"'`` for
    a string literal).
    """
    if not text.startswith("---\n"):
        return text
    fm_end = text.find("\n---\n", 4)
    if fm_end == -1:
        return text
    fm_block = text[:fm_end]
    body = text[fm_end:]

    new_line = f"{key}: {value_str}" if value_str is not None else None
    lines = fm_block.split("\n")
    out: list = []
    i = 0
    handled = False
    while i < len(lines):
        line = lines[i]
        if not handled and re.match(rf"^{re.escape(key)}\s*:", line):
            if new_line is not None:
                out.append(new_line)
            handled = True
            i += 1
            # Drop any indented continuation lines (multi-line YAML list).
            while i < len(lines) and lines[i].startswith((" ", "\t")):
                i += 1
            continue
        out.append(line)
        i += 1

    if not handled and new_line is not None:
        insert_at = len(out)
        while insert_at > 0 and out[insert_at - 1].strip() == "":
            insert_at -= 1
        out.insert(insert_at, new_line)

    return "\n".join(out) + body


def url_to_origin(url: str) -> str:
    """Map a source URL to a short human-readable origin label."""
    url_lower = url.lower()
    for domain, label in (
        ("wikipedia.org", "Wikipedia"), ("arxiv.org", "arXiv"),
        ("nature.com", "Nature"), ("sciencedirect.com", "ScienceDirect"),
        ("cell.com", "Cell"), ("springer.com", "Springer"),
        ("ieee.org", "IEEE"), ("acm.org", "ACM"), ("github.com", "GitHub"),
        ("nytimes.com", "NYTimes"), ("bbc.co", "BBC"),
    ):
        if domain in url_lower:
            return label
    m = re.search(r"//(?:www\.)?([^/]+)", url)
    if m:
        parts = m.group(1).split(".")
        return parts[-2].capitalize() if len(parts) >= 2 else parts[0].capitalize()
    return "Web"


def extract_topic(stem: str) -> str:
    """Pull the topic name from a source stub stem.

    Handles URL-derived stems (``20260411-...-wiki-game-theory``) and plain
    stems. Strips leaked hex-encoded characters generically (any sequence of
    2-char hex fragments separated by hyphens, e.g. ``e2-80-93``).
    """
    m = re.search(r"-wiki-(.+)$", stem)
    if m:
        topic = m.group(1)
    else:
        m = re.search(r"-org-(.+)$", stem)
        if m:
            topic = m.group(1)
        else:
            parts = stem.split("-")
            for i, p in enumerate(parts):
                if not p.isdigit() and p not in ("en", "wikipedia", "org", "wiki", "www", "http", "https", "local"):
                    topic = "-".join(parts[i:])
                    break
            else:
                return stem
    # Strip sequences of UTF-8 hex-byte noise like `-e2-80-93`. Require at
    # least 2 consecutive `-XX` chunks so we don't shred valid words that
    # happen to start with hex-looking pairs (`-ad-versarial`, `-ac-ting`,
    # `-ef-ficient`).
    topic = re.sub(r"(?:-[0-9a-f]{2}){2,}", "-", topic)
    topic = re.sub(r"-{2,}", "-", topic).strip("-")
    return topic


_TITLE_STOP = {"a", "an", "the", "of", "in", "for", "and", "is", "are", "on", "to", "with"}

# Section headings we refuse to derive a topic from. If the body's first
# non-empty heading matches one of these, we keep scanning for a more
# specific heading rather than producing a generic stem like `abstract-1`.
_GENERIC_HEADINGS = {
    "abstract", "introduction", "overview", "summary", "conclusion",
    "references", "contents", "background", "discussion", "methodology",
    "results", "experiment", "experiments", "bibliography", "related work",
    "acknowledgements", "appendix", "notes",
}


def _topic_from_title(title: str) -> str:
    # Normalise unicode dashes to plain hyphen before splitting. Em-dash
    # and en-dash as standalone separators otherwise survive as bare
    # "—" / "–" tokens, producing garbled stems like `sources/case-brief-—.md`.
    # Also filter any token that becomes pure punctuation after
    # normalisation — stop-word filtering alone doesn't catch these.
    t = title.replace("\u2014", "-").replace("\u2013", "-").replace("\u2012", "-")
    words = [w for w in t.split() if w.lower() not in _TITLE_STOP]
    words = [w for w in words if any(c.isalnum() for c in w)]
    return "-".join(w.lower() for w in words[:3])


_SURNAME_STRIP = "().,;:[]<>\"'"


def _surname(name: str) -> str:
    """Extract surname from a full-name string for citation stems.

    Handles:
      - `Last, First [Title]` — surname is the token ending in a comma
        (works for `Kingma, Diederik P.` and `Alice Smith, PhD`).
      - `First Last` — surname is the last token.
      - Parenthesized qualifiers like `NVIDIA (130+ researchers)` — parens
        and their contents are stripped first, leaving `NVIDIA`.
    Returns empty string for empty input.
    """
    name = (name or "").strip()
    if not name:
        return ""
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if not name:
        return ""
    parts = name.split()
    for p in parts:
        if p.endswith(","):
            return p.rstrip(",").strip(_SURNAME_STRIP)
    return parts[-1].strip(_SURNAME_STRIP)


def _inner_fm_from_fetched(body: str) -> dict:
    """Parse the inner frontmatter wrapped inside a FETCHED CONTENT block.

    `local_ingest.py` writes each source as::

        ---
        source_path: ...
        ingested_at: ...
        ---

        <!-- BEGIN FETCHED CONTENT -->
        ---
        title: ...
        source_url: ...
        date: ...
        ---

        # body

    The outer frontmatter is provenance metadata from the ingester; the
    inner frontmatter is the source's own metadata. parse_source_meta
    needs the inner block to recover title / source_url / date. Returns
    {} if no marker or no inner fm is found.
    """
    i = body.find("<!-- BEGIN FETCHED CONTENT")
    if i == -1:
        return {}
    fm_start = body.find("---\n", i)
    if fm_start == -1:
        return {}
    fm_end = body.find("\n---", fm_start + 4)
    if fm_end == -1:
        return {}
    inner_text = "---\n" + body[fm_start + 4:fm_end] + "\n---\n"
    fm, _ = read_frontmatter(inner_text)
    return fm


def parse_source_meta(vault_path: Path) -> dict:
    """Extract metadata from a vault extraction for citation-style naming.

    Returns dict with keys: topic, origin, year, author, full_title.

    Fallback chain (first match wins):
      1. Frontmatter ``source_url`` → origin from domain, topic from stem.
      2. Frontmatter ``title`` / ``author`` / ``date`` / ``subject`` / ``from``
         → handles webclips, memos, emails, book chapters, simple notes.
      3. First ``# Heading (Author, Year)`` in body → academic papers.
      4. First ``# Heading`` in body (no parenthetical) → simple notes.
      5. Filename stem → last resort.
    """
    # Tolerate non-UTF-8 content — source stubs occasionally point at a
    # binary (e.g. a PDF directly in vault/raw/ without a corresponding
    # .extracted.md). read_text() defaults to strict UTF-8 and would
    # raise UnicodeDecodeError; `errors='replace'` gives us a best-effort
    # decode so the frontmatter-first fallback chain can still run. For a
    # binary file the chain will fall through to the filename-stem branch.
    text = vault_path.read_text(errors="replace")
    outer_fm, body = read_frontmatter(text)
    # local_ingest wraps the source's own frontmatter inside a FETCHED
    # CONTENT block. Merge it in so title/source_url/date surface — inner
    # values override the outer provenance frontmatter where both exist.
    inner_fm = _inner_fm_from_fetched(body)
    fm = {**outer_fm, **inner_fm}
    meta = {"topic": "", "origin": "", "year": "", "author": "", "full_title": ""}

    # Prefer the original filename (recorded in outer fm `source_path`) for
    # topic derivation — it's cleaner than the timestamped vault stem.
    source_path_fm = outer_fm.get("source_path", "")
    if source_path_fm:
        raw_stem = Path(source_path_fm).stem
    else:
        raw_stem = vault_path.stem.replace(".extracted", "")

    source_url = fm.get("source_url", "")
    if source_url:
        meta["origin"] = url_to_origin(source_url)
        meta["topic"] = extract_topic(raw_stem)
        fetched = fm.get("fetched_at", "") or fm.get("date", "")
        ym = re.search(r"\b(19|20)\d{2}\b", str(fetched))
        if ym:
            meta["year"] = ym.group(0)
        # First author from either singular `author` or plural `authors` list.
        authors = fm.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]
        first = fm.get("author") or (authors[0] if authors else "")
        meta["author"] = _surname(first if isinstance(first, str) else "")
        inner_title = fm.get("title", "")
        if isinstance(inner_title, list):
            inner_title = inner_title[0] if inner_title else ""
        meta["full_title"] = inner_title or meta["topic"].replace("-", " ").title()
        return meta

    fm_title = fm.get("title", "")
    if isinstance(fm_title, list):
        fm_title = fm_title[0] if fm_title else ""
    authors = fm.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    fm_author = fm.get("author") or fm.get("from", "") or (authors[0] if authors else "")
    fm_date = fm.get("date") or fm.get("created", "")
    fm_subject = fm.get("subject", "")

    if fm_title or fm_subject:
        meta["full_title"] = fm_title or fm_subject
        meta["topic"] = _topic_from_title(meta["full_title"]) or vault_path.stem.replace(".extracted", "")
        if fm_author:
            meta["author"] = _surname(fm_author if isinstance(fm_author, str) else str(fm_author))
        ym = re.search(r"\b(19|20)\d{2}\b", str(fm_date))
        if ym:
            meta["year"] = ym.group(0)
        return meta

    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("#"):
            continue
        header = line.lstrip("#").strip()
        header = re.sub(r"^title\s*[:\u2014\-]\s*", "", header, flags=re.IGNORECASE)
        # Generic section headings (abstract, introduction, references, ...)
        # don't describe the document — keep scanning for a real title.
        if header.lower() in _GENERIC_HEADINGS:
            continue
        m = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", header)
        if m:
            title_part = m.group(1).strip()
            paren = m.group(2).strip()
            meta["full_title"] = title_part
            ym = re.search(r"\b(19|20)\d{2}\b", paren)
            if ym:
                meta["year"] = ym.group(0)
            author_str = re.sub(r"\b(19|20)\d{2}\b", "", paren)
            author_str = author_str.replace("et al.", "").replace(",", "").strip()
            if author_str:
                first_word = author_str.split()[0]
                # Only accept candidates that look like a real name:
                # start with a letter, allow internal apostrophes/periods/
                # hyphens. Rejects junk like "-26" that's left over when a
                # year range like "(2025-26)" has the year stripped out.
                if re.match(r"^[A-Za-z][A-Za-z.'-]*$", first_word):
                    meta["author"] = first_word
            meta["topic"] = _topic_from_title(title_part)
        else:
            meta["full_title"] = header
            meta["topic"] = _topic_from_title(header)
        break

    if not meta["topic"]:
        # Last resort: use the original filename stem (preferred) or vault
        # stem. Drop common extensions.
        fallback_stem = source_path_fm or vault_path.stem.replace(".extracted", "")
        meta["topic"] = Path(fallback_stem).stem if "/" in fallback_stem else fallback_stem

    return meta


def _sanitize_stem_part(s: str) -> str:
    """Lowercase a stem fragment, squash non-alphanumeric runs to single
    hyphens, and strip leading/trailing hyphens. Protects against
    filename-unsafe characters leaking into citation stems — e.g. a
    title with an em-dash, a value like `-26` that slipped past the
    metadata validators, or a parenthesized citation body.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower())
    return s.strip("-")


def citation_stem(meta: dict) -> str:
    """Build a citation-style filename stem from parsed metadata.

    Papers with author:      vaswani-2017-attention
    Wikipedia (no author):   wikipedia-2026-deep-learning
    arXiv without author:    arxiv-2017-attention-is-all-you-need

    Author-year-topic ordering matches reference-list conventions: readers
    scan by author, then year, then disambiguate by topic. This also
    groups a single author's stubs alphabetically on disk, which is
    useful when the same author has many papers in the vault.

    Each part is sanitized individually so a junk fragment (non-letter
    leading char, em-dash, parenthetical) can't produce a stem that
    starts with a hyphen or contains filename-unsafe characters. Empty
    parts after sanitization are dropped.
    """
    parts = []
    if meta.get("author"):
        p = _sanitize_stem_part(meta["author"])
        if p:
            parts.append(p)
    elif meta.get("origin"):
        p = _sanitize_stem_part(meta["origin"])
        if p:
            parts.append(p)
    if meta.get("year"):
        p = _sanitize_stem_part(meta["year"])
        if p:
            parts.append(p)
    if meta.get("topic"):
        p = _sanitize_stem_part(meta["topic"])
        if p:
            parts.append(p)
    return "-".join(parts)


def source_display_title(meta: dict) -> str:
    """Build rich display title for a source stub.

    "Deep Learning - Wikipedia, 2026" or "Attention Is All You Need - Vaswani, 2017"
    """
    title = meta.get("full_title") or meta["topic"].replace("-", " ").title()
    suffix_parts = []
    if meta.get("author"):
        suffix_parts.append(meta["author"])
    elif meta.get("origin"):
        suffix_parts.append(meta["origin"])
    if meta.get("year"):
        suffix_parts.append(meta["year"])
    if suffix_parts:
        return f"{title} \u2014 {', '.join(suffix_parts)}"
    return title


# ---------------------------------------------------------------------------
# QUERY crystallise anti-crowding (v1.8)
# ---------------------------------------------------------------------------
# Lexical title/head similarity over analyses/*.md so QUERY write-back can
# prefer update / minor linking analysis / new rather than minting near-twins.
# Stdlib only; no embeddings required (optional embedder path not wired —
# Jaccard is enough for deterministic recommendation).

_ANALYSIS_STOP = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "vs", "versus", "across", "between", "from", "into", "via", "as", "by",
    "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "these", "those", "how", "what", "why", "when", "where", "which",
    "analysis", "synthesis", "comparison", "overview", "review", "notes",
})
_WORD_RE = re.compile(r"[a-z0-9]+", re.I)


def lexical_tokens(text: str) -> set:
    """Lowercased alphanumeric tokens with a small English/analysis stop-set."""
    if not text:
        return set()
    return {t for t in _WORD_RE.findall(text.casefold())
            if len(t) > 1 and t not in _ANALYSIS_STOP}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def page_title_and_head(path: Path, head_chars: int = 480) -> tuple:
    """Return (display_title, first_prose_paragraph) for a wiki page."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path.stem.replace("-", " "), ""
    fm, body = read_frontmatter(text)
    title = str(fm.get("title") or path.stem.replace("-", " "))
    # Strip [xx] type prefix for similarity (keep human words).
    title_plain = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
    head = ""
    for para in re.split(r"\n\s*\n", body or ""):
        line = para.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        # Drop leading list markers for tokenisation cleanliness.
        head = re.sub(r"^[-*]\s+", "", line, flags=re.M)
        break
    if len(head) > head_chars:
        head = head[:head_chars]
    return title_plain, head


def score_analysis_similarity(proposed_title: str, proposed_head: str,
                              candidate_title: str, candidate_head: str) -> dict:
    """Title-weighted Jaccard of proposed vs one existing analysis."""
    pt, ph = lexical_tokens(proposed_title), lexical_tokens(proposed_head)
    ct, ch = lexical_tokens(candidate_title), lexical_tokens(candidate_head)
    title_j = jaccard(pt, ct)
    head_j = jaccard(ph | pt, ch | ct)  # title tokens reinforce head
    # Title agreement dominates near-twin detection; head catches paraphrase.
    score = 0.65 * title_j + 0.35 * head_j
    return {
        "score": round(score, 4),
        "title_jaccard": round(title_j, 4),
        "head_jaccard": round(head_j, 4),
    }


def recommend_analysis_write(
    wiki_dir: Path,
    proposed_title: str,
    proposed_head: str = "",
    *,
    update_threshold: float = 0.55,
    link_threshold: float = 0.35,
) -> dict:
    """Recommend update | link | new for a proposed QUERY crystallise write.

    Scans `wiki/analyses/*.md` with lexical title/head Jaccard only.
    - **update**: most of the answer already lives on a near-twin page
      (score >= update_threshold) — edit that page instead of minting another.
    - **link**: related but not the same synthesis (link_threshold <= score
      < update_threshold) — prefer a short linking analysis that wikilinks
      existing pages and states the conjunction/delta.
    - **new**: genuinely new content (best score < link_threshold).
    """
    analyses = sorted((wiki_dir / "analyses").glob("*.md")) \
        if (wiki_dir / "analyses").is_dir() else []
    ranked = []
    for path in analyses:
        title, head = page_title_and_head(path)
        sim = score_analysis_similarity(
            proposed_title, proposed_head or "", title, head)
        ranked.append({
            "page": f"analyses/{path.name}",
            "title": title,
            **sim,
        })
    ranked.sort(key=lambda r: (-r["score"], r["page"]))
    best = ranked[0] if ranked else None
    best_score = best["score"] if best else 0.0
    if best and best_score >= update_threshold:
        action = "update"
        rationale = (
            f"near-twin of {best['page']} (score={best_score}); "
            "prefer updating that analysis or its linked content pages"
        )
    elif best and best_score >= link_threshold:
        action = "link"
        rationale = (
            f"related to {best['page']} (score={best_score}); "
            "prefer a short linking analysis (wikilinks + conjunction/delta) "
            "over a full near-twin"
        )
    else:
        action = "new"
        rationale = (
            "no close existing analysis; full new analysis is appropriate"
            if analyses else
            "no analyses/ pages yet; full new analysis is appropriate"
        )
    return {
        "action": action,
        "proposed_title": proposed_title,
        "best": best,
        "candidates": ranked[:8],
        "thresholds": {
            "update": update_threshold,
            "link": link_threshold,
        },
        "rationale": rationale,
    }


def build_source_summary(meta: dict, body: str, extraction_name: str,
                         *, max_claim_chars: int = 900) -> str:
    """Succinct factual summary for a wiki/sources page from a vault extract.

    Carries who/when/subject + key claim lines and a single `(vault:...)`
    citation. Never embeds raw URLs (those stay in vault frontmatter).
    Strips untrusted-marker wrappers so wiki bodies stay agent-authored.
    """
    # Prefer content inside FETCHED markers when present; else whole body.
    fetched = ""
    m = re.search(
        r"<!--\s*BEGIN FETCHED CONTENT\s*-->(.*?)<!--\s*END FETCHED CONTENT\s*-->",
        body or "",
        flags=re.I | re.S,
    )
    fetched = (m.group(1) if m else body) or ""
    # Drop HTML comments leftover and heading-only noise.
    lines = []
    for raw in fetched.splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--"):
            continue
        if line.startswith("#"):
            # Keep ##-level section titles as soft claim anchors only when
            # short; skip giant ALL-CAPS banners.
            heading = line.lstrip("#").strip()
            if heading and len(heading) < 120:
                lines.append(heading)
            continue
        if line.startswith("|") or line.startswith("---"):
            continue
        lines.append(line)

    author = (meta.get("author") or "").strip()
    year = (meta.get("year") or "").strip()
    origin = (meta.get("origin") or "").strip()
    full_title = (meta.get("full_title") or meta.get("topic") or "").strip()
    subject = full_title or (meta.get("topic") or "untitled").replace("-", " ")

    who_bits = []
    if author:
        who_bits.append(author)
    elif origin:
        who_bits.append(origin)
    when = year or ""

    lead_parts = [subject]
    attr = ", ".join(p for p in (who_bits[0] if who_bits else None, when) if p)
    if attr:
        lead_parts.append(f"({attr})")
    lead = " ".join(lead_parts).strip() + "."

    # Key claims: first few substantive prose lines, capped.
    claims = []
    budget = max_claim_chars
    for line in lines:
        # Skip lines that are just the title repeated.
        if full_title and line.casefold() == full_title.casefold():
            continue
        if len(line) < 40:
            continue
        # Avoid dumping bibliographic noise.
        if re.match(r"^(doi|arxiv|https?:)", line, flags=re.I):
            continue
        snippet = line
        if len(snippet) > 280:
            snippet = snippet[:277].rstrip() + "..."
        claims.append(snippet)
        budget -= len(snippet)
        if len(claims) >= 4 or budget <= 0:
            break

    parts = [lead]
    if who_bits or when:
        meta_line = "Who/when: " + ", ".join(
            p for p in [(" / ".join(who_bits) if who_bits else ""), when] if p
        ) + "."
        parts.append(meta_line)
    parts.append(f"Subject: {subject}.")
    if claims:
        parts.append("Key claims:")
        for c in claims:
            parts.append(f"- {c}")
    else:
        parts.append(
            "Key claims: extraction has little recoverable prose; "
            "open the vault source for detail."
        )
    parts.append(f"(vault:{extraction_name})")
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(
        description="naming.py utilities (citation stems + QUERY anti-crowding)")
    sub = ap.add_subparsers(dest="command")

    ra = sub.add_parser(
        "recommend-analysis",
        help="score a proposed analysis title/head against analyses/*.md "
             "and recommend update | link | new")
    ra.add_argument("wiki", nargs="?", default="wiki")
    ra.add_argument("--title", required=True,
                    help="proposed analysis title (with or without [anl] prefix)")
    ra.add_argument("--head", default="",
                    help="optional first-paragraph / answer head for similarity")
    ra.add_argument("--update-threshold", type=float, default=0.55)
    ra.add_argument("--link-threshold", type=float, default=0.35)

    args = ap.parse_args()
    if args.command != "recommend-analysis":
        ap.print_help()
        sys.exit(1)
    wiki_dir = Path(args.wiki).resolve()
    if not wiki_dir.is_dir():
        print(json.dumps({"error": f"wiki dir not found: {wiki_dir}"}))
        sys.exit(1)
    out = recommend_analysis_write(
        wiki_dir, args.title, args.head,
        update_threshold=args.update_threshold,
        link_threshold=args.link_threshold,
    )
    print(json.dumps(out, indent=2))

