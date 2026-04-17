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
    # Author/metadata (used by parse_source_meta)
    "author", "from", "date", "subject",
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
    (``sources: [a.md, b.md]`` → Python list), bare scalars. Keys outside
    ``ALLOWED_FM_KEYS`` are silently dropped.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:]
    fm = {}
    for line in fm_block.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        if key not in ALLOWED_FM_KEYS:
            continue
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            fm[key] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        elif (v.startswith('"') and v.endswith('"')) or \
             (v.startswith("'") and v.endswith("'")):
            fm[key] = v[1:-1]
        else:
            fm[key] = v
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
                if not p.isdigit() and p not in ("en", "wikipedia", "org", "wiki", "www", "http", "https"):
                    topic = "-".join(parts[i:])
                    break
            else:
                return stem
    topic = re.sub(r"(?:-[0-9a-f]{2})+", "-", topic)
    topic = re.sub(r"-{2,}", "-", topic).strip("-")
    return topic


_TITLE_STOP = {"a", "an", "the", "of", "in", "for", "and", "is", "are", "on", "to", "with"}


def _topic_from_title(title: str) -> str:
    words = [w for w in title.split() if w.lower() not in _TITLE_STOP]
    return "-".join(w.lower() for w in words[:3])


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
    fm, body = read_frontmatter(text)
    meta = {"topic": "", "origin": "", "year": "", "author": "", "full_title": ""}

    source_url = fm.get("source_url", "")
    if source_url:
        meta["origin"] = url_to_origin(source_url)
        raw_stem = vault_path.stem.replace(".extracted", "")
        meta["topic"] = extract_topic(raw_stem)
        fetched = fm.get("fetched_at", "")
        if fetched:
            meta["year"] = fetched[:4]
        meta["full_title"] = meta["topic"].replace("-", " ").title()
        return meta

    fm_title = fm.get("title", "")
    if isinstance(fm_title, list):
        fm_title = fm_title[0] if fm_title else ""
    fm_author = fm.get("author") or fm.get("from", "")
    fm_date = fm.get("date") or fm.get("created", "")
    fm_subject = fm.get("subject", "")

    if fm_title or fm_subject:
        meta["full_title"] = fm_title or fm_subject
        meta["topic"] = _topic_from_title(meta["full_title"]) or vault_path.stem.replace(".extracted", "")
        if fm_author:
            meta["author"] = fm_author.split()[0] if isinstance(fm_author, str) else str(fm_author)
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
        meta["topic"] = vault_path.stem.replace(".extracted", "")

    return meta


def citation_stem(meta: dict) -> str:
    """Build a citation-style filename stem from parsed metadata.

    Papers with author: attention-vaswani-2017
    Wikipedia (no author): deep-learning-wikipedia-2026
    """
    parts = [meta["topic"]]
    if meta.get("author"):
        parts.append(meta["author"].lower())
    if meta.get("year"):
        parts.append(meta["year"])
    if not meta.get("author") and meta.get("origin"):
        parts.append(meta["origin"].lower())
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
