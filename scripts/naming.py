#!/usr/bin/env python3
"""naming.py — shared naming + display-title utilities for the curiosity engine.

Used by:
  - sweep.py rename-sources / fix-display-names  (batch hygiene passes)
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


FRONTMATTER_TYPES = {"entity", "concept", "source", "analysis", "evidence", "fact"}

TYPE_PREFIX = {
    "concept": "[con]",
    "entity": "[ent]",
    "analysis": "[ana]",
    "source": "[src]",
    "evidence": "[evi]",
    "fact": "[fact]",
}


def read_frontmatter(text: str) -> tuple:
    """Parse leading `---\\n...\\n---\\n` frontmatter. Returns (dict, body)."""
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
        fm[k.strip()] = v.strip()
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
    """Pull the topic name from a source stub stem like '20260411-...-wiki-game-theory'.

    Strips URL-encoded characters (``27s`` for apostrophe, ``e2-80-93`` for
    em-dash, ``28``/``29`` for parens) that leak through from Wikipedia URLs.
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
    topic = re.sub(r"-?27s-?", "s-", topic)
    topic = re.sub(r"-?e2-80-9[0-9]-?", "-", topic)
    topic = re.sub(r"-?2[89]-?", "-", topic)
    topic = re.sub(r"-{2,}", "-", topic).strip("-")
    return topic


def parse_source_meta(vault_path: Path) -> dict:
    """Extract metadata from a vault extraction for citation-style naming.

    Returns dict with keys: topic, origin, year, author, full_title.
    Works for structured frontmatter (web fetches with source_url) and
    plain markdown (papers with ``# Title (Author et al., Year)`` headers).
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
    else:
        for line in body.split("\n"):
            line = line.strip()
            if not line.startswith("#"):
                continue
            header = line.lstrip("#").strip()
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
                stop = {"a", "an", "the", "of", "in", "for", "and", "is", "are", "on", "to", "with"}
                words = [w for w in title_part.split() if w.lower() not in stop]
                meta["topic"] = "-".join(w.lower() for w in words[:3])
            else:
                meta["full_title"] = header
                meta["topic"] = vault_path.stem.replace(".extracted", "")
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
