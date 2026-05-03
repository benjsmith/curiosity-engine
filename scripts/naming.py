#!/usr/bin/env python3
"""naming.py — shared naming + display-title utilities for the curiosity engine.

Used by:
  - sweep.py fix-source-stubs                    (citation-style stub creation)
  - local_ingest.py                              (source stubs on ingest)
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
    # `tables_present` are written by local_ingest.py; `extracted_from`
    # / `table_index` / `row_count` / `db_table` / `is_snapshot` /
    # `extraction_sha` annotate the `[tab]` wiki pages produced by
    # `sweep.py promote-extracted-tables`.
    "has_tables", "tables_extracted", "tables_present",
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
            fm[key] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        elif (v.startswith('"') and v.endswith('"')) or \
             (v.startswith("'") and v.endswith("'")):
            fm[key] = v[1:-1]
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
