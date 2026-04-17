#!/usr/bin/env python3
"""sweep.py - mechanical hygiene operations for the curiosity-engine wiki.

Distinct from CURATE's semantic ratchet: SWEEP operates across the whole
wiki at once, deterministically, in seconds. It catches the kinds of issues
that CURATE's compression-progress ratchet cannot see (or would burn huge
numbers of slow cycles on): dead wikilinks, duplicate slugs, orphan pages,
missing wiki/sources stubs, index.md drift, frontmatter invalid.

Subcommands
-----------
    sweep.py scan [wiki_dir]
        Read-only report. Emits one JSON object covering every hygiene
        dimension. Main session reads the report and decides what to fix.

    sweep.py fix-source-stubs [wiki_dir]
        Deterministic backfill: for every file in `vault/` without a
        corresponding stub in `wiki/sources/`, create one from the vault
        file's extracted-text frontmatter and a short auto-summary.
        Idempotent. Prints JSON summary of what was created.

    sweep.py fix-index [wiki_dir]
        Rewrite `.curator/index.md` so it matches the pages on disk.
        Preserves any top-of-file prose (before the first list item).
        Prints JSON summary of drift resolved.

    sweep.py fix-percent-escapes [wiki_dir]
        Collapse `%%` → `%` in wiki page bodies outside fenced code blocks.
        Obsidian renders `%%…%%` as a hidden comment; LLMs occasionally
        emit it (LaTeX escape habit) which silently eats page prose.
        Idempotent. Prints JSON summary of pages touched.

    sweep.py scan-references [wiki_dir]
        Scan vault extractions for arXiv/DOI references and append any not
        already represented in the vault to a `## source-requests` block in
        `.curator/log.md`. Dedups across runs via `.curator/.requested-refs`
        (append-only). Prints JSON summary of refs found / logged / skipped.

Design notes
------------
- sweep.py is workspace-agent-editable. It lives at `.curator/sweep.py` in
  the workspace; the pristine reference ships with the skill at
  `<skill>/scripts/sweep.py` and is NOT hash-guarded. CURATE may edit the
  workspace copy to try better hygiene heuristics (every edit is diffed
  and logged).
- Source naming, citation stems, display titles, and frontmatter parsing
  live in `naming.py` (hash-guarded). sweep.py imports from there.
- Only `fix-*` subcommands write. `scan` is pure read.
- Uses only stdlib. Runs in well under a second even on a 1000-page wiki.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import (  # noqa: E402
    FRONTMATTER_TYPES,
    SKIP_FILES,
    WIKILINK_RE,
    read_frontmatter,
)

FRONTMATTER_REQUIRED = {"title", "type", "created"}


def wiki_pages(wiki_dir: Path) -> list:
    return [p for p in wiki_dir.rglob("*.md")
            if p.name not in SKIP_FILES and "_suspect" not in p.parts]


_NO_DEPLURAL = {"analysis", "basis", "bias", "chaos", "corpus", "thesis",
                 "atlas", "lens", "bus", "gas", "plus", "canvas", "status",
                 "focus", "radius", "virus", "census", "consensus"}


def normalize_slug(stem: str) -> str:
    """Fuzzy normalization for duplicate-slug detection."""
    s = stem.lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    for article in ("a ", "an ", "the "):
        if s.startswith(article):
            s = s[len(article):]
    if s.endswith("s") and not s.endswith("ss") and s not in _NO_DEPLURAL:
        s = s[:-1]
    return s


def scan_wikilinks(pages: list) -> tuple:
    stems_on_disk = {p.stem.lower() for p in pages}
    all_refs = []
    inbound = defaultdict(int)
    for page in pages:
        text = page.read_text()
        own = page.stem.lower()
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip().lower().replace(" ", "-")
            all_refs.append((str(page), target))
            if target != own and target in stems_on_disk:
                inbound[target] += 1
    dead_refs = [(src, tgt) for (src, tgt) in all_refs
                 if tgt not in stems_on_disk]
    return all_refs, dead_refs, dict(inbound)


def scan_duplicate_slugs(pages: list) -> list:
    groups = defaultdict(list)
    for p in pages:
        groups[normalize_slug(p.stem)].append(str(p))
    return [{"key": k, "pages": v} for k, v in groups.items() if len(v) > 1]


def scan_orphans(pages: list, inbound: dict) -> list:
    return [str(p) for p in pages if inbound.get(p.stem.lower(), 0) == 0]


def scan_frontmatter(pages: list) -> list:
    issues = []
    for p in pages:
        fm, _ = read_frontmatter(p.read_text())
        missing = FRONTMATTER_REQUIRED - fm.keys()
        bad_type = fm.get("type") not in FRONTMATTER_TYPES if "type" in fm else False
        if missing or bad_type:
            issues.append({
                "page": str(p),
                "missing": sorted(missing),
                "bad_type": fm.get("type") if bad_type else None,
            })
    return issues


def curator_dir(wiki_dir: Path) -> Path:
    return wiki_dir.parent / ".curator"


def scan_index_drift(wiki_dir: Path, pages: list) -> dict:
    index_path = curator_dir(wiki_dir) / "index.md"
    listed = set()
    if index_path.exists():
        for m in WIKILINK_RE.finditer(index_path.read_text()):
            listed.add(m.group(1).strip().lower().replace(" ", "-"))
    on_disk = {p.stem.lower() for p in pages}
    return {
        "on_disk_not_in_index": sorted(on_disk - listed),
        "in_index_not_on_disk": sorted(listed - on_disk),
    }


def _vault_files_covered_by_stubs(wiki_dir: Path) -> tuple:
    """Returns (covered_hashes: set, covered_paths: set) from source stubs."""
    sources_dir = wiki_dir / "sources"
    hashes = set()
    paths = set()
    if not sources_dir.exists():
        return hashes, paths
    for stub in sources_dir.glob("*.md"):
        fm, _ = read_frontmatter(stub.read_text())
        h = fm.get("vault_sha256", "")
        if h:
            hashes.add(h.lower())
        raw = fm.get("sources", "")
        if isinstance(raw, list):
            for name in raw:
                if name.endswith(".extracted.md"):
                    paths.add(name.lower())
        else:
            for name in re.findall(r"[\w./-]+\.extracted\.md", raw):
                paths.add(name.lower())
    return hashes, paths


def scan_missing_source_stubs(wiki_dir: Path) -> list:
    vault_dir = wiki_dir.parent / "vault"
    if not vault_dir.exists():
        return []
    covered_hashes, covered_paths = _vault_files_covered_by_stubs(wiki_dir)
    missing = []
    for f in sorted(vault_dir.glob("*.extracted.md")):
        if f.name.lower() in covered_paths:
            continue
        import hashlib
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if sha.lower() in covered_hashes:
            continue
        missing.append(str(f))
    return missing


def cmd_scan(wiki_dir: Path):
    pages = wiki_pages(wiki_dir)
    _, dead_refs, inbound = scan_wikilinks(pages)
    report = {
        "wiki_dir": str(wiki_dir),
        "page_count": len(pages),
        "dead_wikilinks": [{"source": s, "target": t} for (s, t) in dead_refs],
        "duplicate_slugs": scan_duplicate_slugs(pages),
        "orphans": scan_orphans(pages, inbound),
        "frontmatter_issues": scan_frontmatter(pages),
        "index_drift": scan_index_drift(wiki_dir, pages),
        "missing_source_stubs": scan_missing_source_stubs(wiki_dir),
    }
    report["hygiene_debt"] = (
        len(report["dead_wikilinks"])
        + len(report["duplicate_slugs"])
        + len(report["frontmatter_issues"])
        + len(report["index_drift"]["on_disk_not_in_index"])
        + len(report["index_drift"]["in_index_not_on_disk"])
        + len(report["missing_source_stubs"])
    )
    print(json.dumps(report, indent=2))


def cmd_fix_source_stubs(wiki_dir: Path):
    """Create wiki/sources/<topic>.md for every vault extraction without a stub.

    Uses naming.parse_source_meta + naming.citation_stem to build the
    filename and naming.source_display_title for the frontmatter title.
    Idempotent.
    """
    import hashlib
    from naming import citation_stem, parse_source_meta, source_display_title, TYPE_PREFIX

    vault_dir = wiki_dir.parent / "vault"
    sources_dir = wiki_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    covered_hashes, covered_paths = _vault_files_covered_by_stubs(wiki_dir)
    used_stems = {p.stem.lower() for p in sources_dir.glob("*.md")}
    created = []
    skipped = 0
    for extracted in sorted(vault_dir.glob("*.extracted.md")):
        sha = hashlib.sha256(extracted.read_bytes()).hexdigest()
        if extracted.name.lower() in covered_paths or sha.lower() in covered_hashes:
            skipped += 1
            continue

        meta = parse_source_meta(extracted)
        clean_stem = citation_stem(meta).lower() or extracted.stem.replace(".extracted", "")

        if clean_stem in used_stems:
            n = 2
            while f"{clean_stem}-{n}" in used_stems:
                n += 1
            clean_stem = f"{clean_stem}-{n}"
        used_stems.add(clean_stem)

        stub_path = sources_dir / f"{clean_stem}.md"
        fm, body = read_frontmatter(extracted.read_text())
        display = source_display_title(meta)
        title = f"{TYPE_PREFIX['source']} {display}"

        summary_lines = []
        for line in body.split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("<!--"):
                continue
            summary_lines.append(line)
            if sum(len(l) for l in summary_lines) > 400:
                break
        summary = " ".join(summary_lines)[:500]
        if not summary:
            summary = f"Source extraction for {display}. (vault:{extracted.name})"

        stub = (
            f"---\n"
            f"title: {title}\n"
            f"type: source\n"
            f"created: {fm.get('date', '2026-04-12')}\n"
            f"updated: 2026-04-12\n"
            f"sources: [{extracted.name}]\n"
            f"vault_sha256: {sha}\n"
            f"---\n\n"
            f"{summary} (vault:{extracted.name})\n"
        )
        stub_path.write_text(stub)
        created.append(str(stub_path))
    print(json.dumps({"created": len(created), "skipped": skipped,
                      "created_paths": created}, indent=2))


def cmd_fix_index(wiki_dir: Path):
    """Rewrite .curator/index.md so it matches the pages on disk.

    index.md lives in .curator/ (auto-generated, not git-tracked), so
    regenerating it never produces a wiki commit. Preserves any prose
    before the first list item (treated as a hand-written header/intro).
    Everything after is regenerated, grouped by subdirectory.
    """
    pages = wiki_pages(wiki_dir)
    cur = curator_dir(wiki_dir)
    cur.mkdir(parents=True, exist_ok=True)
    index_path = cur / "index.md"
    header = "# Index\n\n"
    if index_path.exists():
        old = index_path.read_text()
        # Preserve everything up to & including the first blank line after a
        # prose paragraph (non-heading, non-list). Generated `## subdir\n\n- [[..]]`
        # blocks start with `#` or `-`, so they never re-capture on later runs.
        m = re.search(r"(?m)^[^#\-*\s].*$", old)
        if m:
            blank = re.search(r"\n\s*\n", old[m.start():])
            end = m.start() + blank.end() if blank else len(old)
            header = old[:end].rstrip() + "\n\n"

    by_dir = defaultdict(list)
    for p in pages:
        subdir = p.parent.relative_to(wiki_dir).as_posix() or "."
        by_dir[subdir].append(p.stem)

    sections = [header]
    for subdir in sorted(by_dir):
        sections.append(f"## {subdir}\n\n")
        for stem in sorted(by_dir[subdir]):
            sections.append(f"- [[{stem}]]\n")
        sections.append("\n")
    new_index = "".join(sections)
    before = index_path.read_text() if index_path.exists() else ""
    index_path.write_text(new_index)
    print(json.dumps({
        "rewrote": True,
        "page_count": len(pages),
        "subdir_count": len(by_dir),
        "size_change": len(new_index) - len(before),
    }, indent=2))


_FENCED_CODE_RE = re.compile(r"(?ms)^```.*?^```")
_DOUBLE_PERCENT_RE = re.compile(r"%%+")


def _collapse_double_percent(text: str) -> str:
    """Replace `%%` with `%` outside fenced code blocks. Skips frontmatter."""
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


def cmd_fix_percent_escapes(wiki_dir: Path):
    """Strip `%%` hidden-comment sequences from wiki page bodies."""
    pages = wiki_pages(wiki_dir)
    touched = []
    for p in pages:
        old = p.read_text()
        new = _collapse_double_percent(old)
        if new != old:
            p.write_text(new)
            touched.append(str(p.relative_to(wiki_dir)))
    print(json.dumps({
        "touched": len(touched),
        "pages": touched,
    }, indent=2))


# Reference patterns. Deliberately narrow — only unambiguous forms —
# to avoid flooding the log with false positives from version numbers,
# phone numbers, or page refs.
_ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]{2,80})\b", re.IGNORECASE)


def _extract_refs(text: str) -> set:
    """Return a set of `arxiv:ID` / `doi:ID` strings found in text."""
    refs = set()
    for m in _ARXIV_RE.finditer(text):
        refs.add(f"arxiv:{m.group(1).lower()}")
    for m in _DOI_RE.finditer(text):
        refs.add(f"doi:{m.group(1).lower()}")
    return refs


_SOURCE_URL_RE = re.compile(r"(?mi)^\s*source_url:\s*(\S+)\s*$")


def _vault_primary_refs(vault_files: list) -> set:
    """Refs represented BY vault files (not just mentioned IN them).

    A vault extraction's inner frontmatter carries `source_url` pointing at
    the paper/blog the extraction represents. We extract arXiv IDs / DOIs
    from those URLs only — a citation inside the body is a mention, not a
    presence.
    """
    primary = set()
    for f in vault_files:
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in _SOURCE_URL_RE.finditer(text):
            url = m.group(1)
            primary |= _extract_refs(url)
            # arXiv URLs like arxiv.org/abs/1706.03762 don't match the
            # "arXiv:" prefix pattern; capture the bare ID directly.
            am = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})",
                           url, re.IGNORECASE)
            if am:
                primary.add(f"arxiv:{am.group(1).lower()}")
    return primary


def cmd_scan_references(wiki_dir: Path):
    """Log external references (arXiv / DOI) not yet in the vault.

    Walks `vault/*.extracted.md`, extracts reference patterns, drops any
    already represented in the vault or previously logged, and appends
    survivors under `## source-requests` in `.curator/log.md`.
    """
    vault_dir = wiki_dir.parent / "vault"
    cur = curator_dir(wiki_dir)
    cur.mkdir(parents=True, exist_ok=True)
    requested_path = cur / ".requested-refs"
    log_path = cur / "log.md"

    if not vault_dir.exists():
        print(json.dumps({"found": 0, "logged": 0, "skipped_in_vault": 0,
                          "skipped_already_requested": 0,
                          "note": "no vault/ directory"}))
        return

    vault_files = sorted(vault_dir.glob("*.extracted.md"))
    primary = _vault_primary_refs(vault_files)
    all_refs = set()
    per_ref_sources = defaultdict(list)
    for f in vault_files:
        try:
            text = f.read_text()
        except OSError:
            continue
        refs = _extract_refs(text)
        for ref in refs:
            all_refs.add(ref)
            per_ref_sources[ref].append(f.name)

    already_requested = set()
    if requested_path.exists():
        already_requested = {
            line.strip() for line in requested_path.read_text().splitlines()
            if line.strip()
        }

    new_refs = []
    skipped_in_vault = 0
    skipped_already = 0
    for ref in sorted(all_refs):
        if ref in already_requested:
            skipped_already += 1
            continue
        if ref in primary:
            skipped_in_vault += 1
            continue
        new_refs.append(ref)

    if new_refs:
        import datetime as _dt
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [f"\n## source-requests {ts}\n"]
        for ref in new_refs:
            srcs = ", ".join(sorted(set(per_ref_sources[ref]))[:3])
            lines.append(f"- `{ref}` — referenced by: {srcs}\n")
        with log_path.open("a") as fh:
            fh.writelines(lines)
        with requested_path.open("a") as fh:
            for ref in new_refs:
                fh.write(ref + "\n")

    print(json.dumps({
        "found": len(all_refs),
        "logged": len(new_refs),
        "skipped_in_vault": skipped_in_vault,
        "skipped_already_requested": skipped_already,
        "new_refs": new_refs,
    }, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=[
        "scan", "fix-source-stubs", "fix-index", "fix-percent-escapes",
        "scan-references",
    ])
    ap.add_argument("wiki", nargs="?", default="wiki")
    args = ap.parse_args()

    wiki_dir = Path(args.wiki).resolve()
    if not wiki_dir.exists():
        print(json.dumps({"error": f"wiki dir not found: {wiki_dir}"}))
        sys.exit(1)

    if args.command == "scan":
        cmd_scan(wiki_dir)
    elif args.command == "fix-source-stubs":
        cmd_fix_source_stubs(wiki_dir)
    elif args.command == "fix-index":
        cmd_fix_index(wiki_dir)
    elif args.command == "fix-percent-escapes":
        cmd_fix_percent_escapes(wiki_dir)
    elif args.command == "scan-references":
        cmd_scan_references(wiki_dir)


if __name__ == "__main__":
    main()
