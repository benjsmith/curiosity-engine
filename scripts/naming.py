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
import re
from pathlib import Path


SKIP_FILES = {"index.md", "log.md", "schema.md"}
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
CITATION_RE = re.compile(r"\(vault:([^)]+)\)")

FRONTMATTER_TYPES = {"entity", "concept", "source", "analysis", "evidence", "fact"}

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
})

TYPE_PREFIX = {
    "concept": "[con]",
    "entity": "[ent]",
    "analysis": "[ana]",
    "source": "[src]",
    "evidence": "[evi]",
    "fact": "[fact]",
}


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
        line = lines[i].strip()
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
    text = vault_path.read_text()
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
                meta["author"] = author_str.split()[0]
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


def citation_stem(meta: dict) -> str:
    """Build a citation-style filename stem from parsed metadata.

    Papers with author:      vaswani-2017-attention
    Wikipedia (no author):   wikipedia-2026-deep-learning
    arXiv without author:    arxiv-2017-attention-is-all-you-need

    Author-year-topic ordering matches reference-list conventions: readers
    scan by author, then year, then disambiguate by topic. This also
    groups a single author's stubs alphabetically on disk, which is
    useful when the same author has many papers in the vault.
    """
    parts = []
    if meta.get("author"):
        parts.append(meta["author"].lower())
    elif meta.get("origin"):
        parts.append(meta["origin"].lower())
    if meta.get("year"):
        parts.append(meta["year"])
    if meta.get("topic"):
        parts.append(meta["topic"])
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
