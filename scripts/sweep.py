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

    sweep.py fix-source-stubs [wiki_dir] [--cited-only]
        Deterministic backfill: for every file in `vault/` without a
        corresponding stub in `wiki/sources/`, create one from the vault
        file's extracted-text frontmatter and a short auto-summary.
        Idempotent. Prints JSON summary of what was created.

        `--cited-only` is the tiered-vault mode: only create stubs for
        vault files already cited by non-source wiki pages via
        `(vault:<path>)`. Uncited vault material stays searchable (FTS5,
        semantic) without cluttering the wiki. Useful once the vault
        grows past ~500 sources.

    sweep.py fix-index [wiki_dir]
        Rewrite `.curator/index.md` so it matches the pages on disk.
        Preserves any top-of-file prose (before the first list item).
        Prints JSON summary of drift resolved.

    sweep.py fix-percent-escapes [wiki_dir]
        Collapse `%%` → `%` in wiki page bodies outside fenced code blocks.
        Obsidian renders `%%…%%` as a hidden comment; LLMs occasionally
        emit it (LaTeX escape habit) which silently eats page prose.
        Idempotent. Prints JSON summary of pages touched.

    sweep.py fix-spaced-wikilinks [wiki_dir]
        Rewrite `[[Title Case]]` wikilinks to `[[kebab-case|Title Case]]`
        when the normalised form matches an existing page stem. Obsidian
        treats `[[Foo Bar]]` as a literal lookup and auto-creates an
        empty `Foo Bar.md` on click even when `foo-bar.md` exists; this
        rewrite kills that foot-gun while preserving the original display
        text. sweep.py scan's normal dead-wikilink scan doesn't catch
        these (it normalises before comparing). Idempotent.

    sweep.py fix-orphan-root-files [wiki_dir]
        Remove empty (zero-byte) `.md` files at `wiki/` top level. These
        are almost always Obsidian auto-create artefacts from clicks on
        unresolved wikilinks; the wiki's convention is pages-under-
        subdirs, so any empty file directly in `wiki/` is suspect.
        Scoped narrowly: top-level only (not `**/*.md`), size 0 only.
        Idempotent.

    sweep.py scan-references [wiki_dir]
        Scan vault extractions for arXiv/DOI references and append any not
        already represented in the vault to a `## source-requests` block in
        `.curator/log.md`. Dedups across runs via `.curator/.requested-refs`
        (append-only). Prints JSON summary of refs found / logged / skipped.

    sweep.py resync-stems [wiki_dir]
        Re-derive every `wiki/sources/*.md` filename from current naming.py,
        rename divergent files, and rewrite inbound wikilinks across the
        whole wiki. Idempotent: emits renames=0 when already in sync. Used
        when a skill update changes the citation-stem convention (e.g.
        `topic-author-year` → `author-year-topic`) so existing workspaces
        pick up the new scheme without losing content or cross-references.
        Does NOT touch `.curator/log.md` (append-only history), the vault
        FTS5 index (indexes vault, not wiki), or frontmatter `sources:`
        lists (those point at vault extraction filenames, not stems).
        Prints JSON summary of renames and pages whose wikilinks changed.

    sweep.py concept-candidates [wiki_dir] [--min-inbound N]
        Find missing wikilink targets that the wiki is already asking for:
        stems referenced as `[[target]]` from ≥N distinct pages with no
        corresponding `wiki/<any>/target.md` on disk. Ranked by inbound
        count. Used by CURATE Phase 1 to drive demand-promotion:
        wiki-observed demand → new `entities/<stem>.md` (proper
        nouns: model families, orgs, frameworks, benchmarks) or
        `concepts/<stem>.md` (abstract terms: algorithms, methods,
        patterns). The candidates list doesn't classify; the
        promotion worker does. Default
        min-inbound=3 filters out one-off typos and drive-by mentions.
        Prints ranked JSON (top 20 by default).

    sweep.py evidence-candidates [wiki_dir] [--min-inbound N]
        Twin of concept-candidates on the evidence side. Finds vault
        sources that are cited by ≥N distinct non-source wiki pages but
        have no `evidence/*.md` anchored to them — the wiki is quietly
        re-referencing the source across contexts without a consolidated
        anchor. Used by CURATE Phase 1's create-mode evidence bucket.
        Default min-inbound=3, same threshold as concept-candidates.
        Ranked JSON (top 20 by default).

    sweep.py pending-multimodal [wiki_dir]
        List vault extractions tagged `multimodal_recommended: true` in
        their frontmatter — PDFs where the fast pypdf pass either failed
        sanity or the doc has math/tables the text extractor mangled.
        The agent processes each entry by reading the original source
        (path in `kept_as` frontmatter field) multimodally and
        overwriting the `.extracted.md` body + updating frontmatter to
        `extraction_method: multimodal`, `multimodal_recommended: false`.
        Prints a JSON queue. No writes — pure read op.

Design notes
------------
- sweep.py is hash-guarded by evolve_guard.sh. CURATE cannot edit it at
  runtime. Improvement ideas land as prose notes under
  `## improvement-suggestions` in `.curator/log.md` for the human
  maintainer to evaluate — no agent-generated code enters execution.
- Source naming, citation stems, display titles, and frontmatter parsing
  live in `naming.py` (also hash-guarded). sweep.py imports from there.
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


def scan_spaced_wikilinks(pages: list) -> list:
    """Wikilinks whose raw target has a space or uppercase letter but
    whose normalised form matches an existing stem.

    sweep.py's normal dead-wikilink scan lowercases + hyphenates before
    comparing, so `[[Foo Bar]]` → `foo-bar` → considered live. But
    Obsidian does no normalisation — to Obsidian, `[[Foo Bar]]` is an
    unresolved filename lookup, and clicking it auto-creates an empty
    `Foo Bar.md` in the vault. The two tools disagree; this catches
    the cases where they do.
    """
    stems_on_disk = {p.stem.lower() for p in pages}
    bad = []
    for page in pages:
        text = page.read_text()
        for m in WIKILINK_RE.finditer(text):
            inner = m.group(1).strip()
            target = inner.split("|", 1)[0]
            if " " not in target and target == target.lower():
                continue
            normalized = target.lower().replace(" ", "-")
            if normalized in stems_on_disk:
                bad.append({
                    "source": str(page),
                    "raw": target,
                    "normalized": normalized,
                })
    return bad


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
    spaced = scan_spaced_wikilinks(pages)
    empty_roots = [str(f.relative_to(wiki_dir)) for f in wiki_dir.glob("*.md")
                    if f.is_file() and not f.is_symlink() and f.stat().st_size == 0]
    report = {
        "wiki_dir": str(wiki_dir),
        "page_count": len(pages),
        "dead_wikilinks": [{"source": s, "target": t} for (s, t) in dead_refs],
        "spaced_wikilinks": spaced,
        "empty_root_files": empty_roots,
        "duplicate_slugs": scan_duplicate_slugs(pages),
        "orphans": scan_orphans(pages, inbound),
        "frontmatter_issues": scan_frontmatter(pages),
        "index_drift": scan_index_drift(wiki_dir, pages),
        "missing_source_stubs": scan_missing_source_stubs(wiki_dir),
    }
    report["hygiene_debt"] = (
        len(report["dead_wikilinks"])
        + len(report["spaced_wikilinks"])
        + len(report["empty_root_files"])
        + len(report["duplicate_slugs"])
        + len(report["frontmatter_issues"])
        + len(report["index_drift"]["on_disk_not_in_index"])
        + len(report["index_drift"]["in_index_not_on_disk"])
        + len(report["missing_source_stubs"])
    )
    print(json.dumps(report, indent=2))


def _cited_vault_paths(wiki_dir: Path) -> set:
    """Vault paths referenced by (vault:...) citations in non-source pages."""
    from naming import CITATION_RE
    cited = set()
    for p in wiki_pages(wiki_dir):
        rel = str(p.relative_to(wiki_dir))
        if rel.startswith("sources/"):
            continue
        for m in CITATION_RE.finditer(p.read_text()):
            cited.add(m.group(1).strip())
    return cited


def cmd_fix_source_stubs(wiki_dir: Path, cited_only: bool = False):
    """Create wiki/sources/<topic>.md for every vault extraction without a stub.

    Uses naming.parse_source_meta + naming.citation_stem to build the
    filename and naming.source_display_title for the frontmatter title.
    Idempotent.

    `cited_only=True` restricts creation to vault files already cited by
    non-source wiki pages — the tiered-vault mode. Uncited sources stay
    in the vault (FTS5 + semantic searchable) but don't get a wiki page.
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
    skipped_unnamed = []
    skipped_uncited = 0
    cited_filter = _cited_vault_paths(wiki_dir) if cited_only else None
    # Guard against producing garbage stubs when naming fails. Topics
    # matching a generic section heading (abstract, overview, ...) indicate
    # parse_source_meta couldn't find a real title and fell through to
    # `## Abstract` etc. — better to skip than manufacture `abstract-2.md`.
    _GENERIC_TOPICS = {
        "abstract", "introduction", "overview", "summary", "conclusion",
        "references", "contents", "background", "discussion", "results",
    }
    for extracted in sorted(vault_dir.glob("*.extracted.md")):
        if cited_filter is not None and extracted.name not in cited_filter:
            skipped_uncited += 1
            continue
        sha = hashlib.sha256(extracted.read_bytes()).hexdigest()
        if extracted.name.lower() in covered_paths or sha.lower() in covered_hashes:
            skipped += 1
            continue

        meta = parse_source_meta(extracted)
        if meta["topic"].lower() in _GENERIC_TOPICS:
            skipped_unnamed.append(extracted.name)
            continue
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
    out = {"created": len(created), "skipped": skipped,
           "skipped_unnamed": skipped_unnamed,
           "created_paths": created}
    if cited_only:
        out["skipped_uncited"] = skipped_uncited
    print(json.dumps(out, indent=2))


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


_WIKILINK_WITH_ALIAS_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]")


def cmd_fix_spaced_wikilinks(wiki_dir: Path):
    """Rewrite [[Title Case]] → [[kebab-case|Title Case]] when the
    normalised form matches an existing page stem.

    If the target already has a pipe-alias (`[[Raw|Display]]`), rewrite
    just the target portion, keeping the display text. If not, add the
    original raw target as the display alias so rendered prose keeps
    its capitalisation.
    """
    pages = wiki_pages(wiki_dir)
    stems_on_disk = {p.stem.lower() for p in pages}
    touched = []
    total_fixed = 0

    def _fix_factory(page_stem_lower: str):
        def _fix(m):
            nonlocal total_fixed
            target = m.group(1).strip()
            alias_suffix = m.group(2) or ""  # starts with pipe, or empty
            if " " not in target and target == target.lower():
                return m.group(0)
            normalized = target.lower().replace(" ", "-")
            if normalized not in stems_on_disk:
                return m.group(0)
            # Self-link edge case — preserve display if any
            if alias_suffix:
                new_link = f"[[{normalized}{alias_suffix}]]"
            else:
                new_link = f"[[{normalized}|{target}]]"
            total_fixed += 1
            return new_link
        return _fix

    for page in pages:
        text = page.read_text()
        new_text = _WIKILINK_WITH_ALIAS_RE.sub(_fix_factory(page.stem.lower()), text)
        if new_text != text:
            page.write_text(new_text)
            touched.append(str(page.relative_to(wiki_dir)))
    print(json.dumps({
        "pages_touched": len(touched),
        "links_fixed": total_fixed,
        "pages": touched,
    }, indent=2))


def cmd_fix_orphan_root_files(wiki_dir: Path):
    """Remove empty (zero-byte) .md files at wiki/ top level.

    Scoped narrowly: glob is `*.md` (not recursive), size must be 0,
    symlinks skipped. Captures Obsidian click-artefacts without
    touching any real content. Idempotent.
    """
    removed = []
    for f in sorted(wiki_dir.glob("*.md")):
        if not f.is_file() or f.is_symlink():
            continue
        try:
            if f.stat().st_size != 0:
                continue
        except OSError:
            continue
        try:
            f.unlink()
            removed.append(f.name)
        except OSError as e:
            print(f"sweep fix-orphan-root-files: failed to remove {f}: {e}",
                  file=sys.stderr)
    print(json.dumps({"removed": len(removed), "files": removed}, indent=2))


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


def cmd_resync_stems(wiki_dir: Path):
    """Rename source stubs + wikilinks to match the current citation_stem.

    For each file under `wiki/sources/`:
      1. Read its frontmatter to locate the underlying vault extraction.
      2. Compute the correct stem via `naming.citation_stem(parse_source_meta(...))`.
      3. If the current filename stem differs, rename the file (with
         collision-safe numeric suffixes matching fix-source-stubs).
    Then walk every wiki page and substitute `[[old_stem]]` /
    `[[old_stem|label]]` with the new stem.
    """
    from naming import CITATION_RE, citation_stem, parse_source_meta

    vault_dir = wiki_dir.parent / "vault"
    sources_dir = wiki_dir / "sources"
    if not sources_dir.exists():
        print(json.dumps({"renames": 0, "pages_touched": 0, "note": "no sources/ dir"}))
        return

    current_stems = {p.stem.lower() for p in sources_dir.glob("*.md")}
    renames = []  # list of (old_stem, new_stem)
    skipped_no_vault = []

    for stub in sorted(sources_dir.glob("*.md")):
        text = stub.read_text()
        fm, body = read_frontmatter(text)
        srcs = fm.get("sources", [])
        if isinstance(srcs, str):
            # read_frontmatter doesn't parse multi-line YAML lists — the
            # value for `sources:` becomes an empty string and the actual
            # items sit in lines that don't match `key: value`. Recover by
            # regex across the whole text for *.extracted.md paths.
            srcs = re.findall(r"[\w./-]+\.extracted\.md", text)
        srcs = [s for s in srcs if s]
        if not srcs:
            # Last-resort fallback: scan body for `(vault:X.extracted.md)`
            # citations, which source stubs always carry at least once.
            srcs = [m.group(1) for m in CITATION_RE.finditer(body)]
        # parse_source_meta expects a text file. Prefer .extracted.md
        # entries over raw binaries (some stubs reference .pdf directly
        # in vault/raw/ when ingest landed them there without a sibling
        # extraction). Falls back to the raw list if no text entry is
        # available — parse_source_meta tolerates binaries via
        # errors='replace' but produces garbage metadata from them, so
        # preferring text paths keeps stems stable.
        text_srcs = [s for s in srcs if s.endswith(".extracted.md")]
        if text_srcs:
            srcs = text_srcs
        if not srcs:
            skipped_no_vault.append(stub.name)
            continue
        vault_file = vault_dir / srcs[0]
        if not vault_file.exists() or not vault_file.is_file():
            skipped_no_vault.append(stub.name)
            continue
        # If the only available source is binary, skip the stub rather
        # than rename it to a hash of garbled decoded bytes.
        if not srcs[0].endswith(".extracted.md"):
            skipped_no_vault.append(stub.name)
            continue

        meta = parse_source_meta(vault_file)
        correct = citation_stem(meta).lower()
        if not correct or correct == stub.stem.lower():
            continue

        # Collision-safe target: if `correct` is already taken by some OTHER
        # stub, append -2, -3, ... until unique.
        new_stem = correct
        if new_stem in current_stems - {stub.stem.lower()}:
            n = 2
            while f"{new_stem}-{n}" in current_stems:
                n += 1
            new_stem = f"{new_stem}-{n}"

        new_path = sources_dir / f"{new_stem}.md"
        stub.rename(new_path)
        current_stems.discard(stub.stem.lower())
        current_stems.add(new_stem)
        renames.append((stub.stem, new_stem))

    pages_touched = []
    if renames:
        rename_map = {old: new for old, new in renames}
        # Match `[[<stem>]]` or `[[<stem>|label]]`. Anchor on exact stem
        # match so we don't rewrite a longer stem that contains an old
        # stem as a substring.
        pattern = re.compile(
            r"\[\[(" + "|".join(re.escape(o) for o in rename_map) + r")(\|[^\]]*)?\]\]"
        )

        def _sub(m):
            return f"[[{rename_map[m.group(1)]}{m.group(2) or ''}]]"

        for page in wiki_pages(wiki_dir):
            text = page.read_text()
            new_text = pattern.sub(_sub, text)
            if new_text != text:
                page.write_text(new_text)
                pages_touched.append(str(page.relative_to(wiki_dir)))

    print(json.dumps({
        "renames": len(renames),
        "rename_pairs": [{"old": o, "new": n} for o, n in renames],
        "pages_touched": len(pages_touched),
        "pages_touched_paths": pages_touched,
        "skipped_no_vault": skipped_no_vault,
    }, indent=2))


def cmd_resync_prefixes(wiki_dir: Path):
    """Rename pages in `wiki/tables/` and `wiki/figures/` so their
    filename stems carry the type's prefix (`tbl-` / `fig-`), then
    rewrite inbound wikilinks across the wiki.

    Idempotent — pages already carrying the correct prefix are left
    alone. Mirrors the resync-stems convention: one commit-worthy
    migration that re-derives state from `naming.STEM_PREFIX`.
    """
    from naming import STEM_PREFIX

    subdir_for_type = {
        "summary-table": "tables",
        "figure": "figures",
    }
    renames = []

    for page_type, subdir in subdir_for_type.items():
        prefix = STEM_PREFIX.get(page_type, "")
        if not prefix:
            continue
        target_dir = wiki_dir / subdir
        if not target_dir.is_dir():
            continue
        existing_stems = {p.stem.lower() for p in target_dir.glob("*.md")}
        for page in sorted(target_dir.glob("*.md")):
            stem = page.stem
            if stem.startswith(prefix):
                continue
            # Read frontmatter to confirm the page is actually of the
            # expected type. Protects against accidentally renaming
            # unrelated files a human dropped into the subdir.
            try:
                fm, _ = read_frontmatter(page.read_text())
            except Exception:
                fm = {}
            if fm.get("type") != page_type:
                continue
            new_stem = f"{prefix}{stem}"
            # Collision-safe target.
            candidate = new_stem
            n = 2
            while candidate.lower() in existing_stems and candidate != stem:
                candidate = f"{new_stem}-{n}"
                n += 1
            new_path = target_dir / f"{candidate}.md"
            page.rename(new_path)
            existing_stems.discard(stem.lower())
            existing_stems.add(candidate.lower())
            renames.append((stem, candidate))

    pages_touched = []
    if renames:
        rename_map = {old: new for old, new in renames}
        pattern = re.compile(
            r"\[\[(" + "|".join(re.escape(o) for o in rename_map) + r")(\|[^\]]*)?\]\]"
        )

        def _sub(m):
            return f"[[{rename_map[m.group(1)]}{m.group(2) or ''}]]"

        for page in wiki_pages(wiki_dir):
            text = page.read_text()
            new_text = pattern.sub(_sub, text)
            if new_text != text:
                page.write_text(new_text)
                pages_touched.append(str(page.relative_to(wiki_dir)))

    print(json.dumps({
        "renames": len(renames),
        "rename_pairs": [{"old": o, "new": n} for o, n in renames],
        "pages_touched": len(pages_touched),
        "pages_touched_paths": pages_touched,
    }, indent=2))


def cmd_evidence_candidates(wiki_dir: Path, min_inbound: int = 3,
                             limit: int = 20) -> None:
    """Rank vault sources by reuse demand with no existing evidence anchor.

    Demand signal for evidence is symmetric with concept-candidates:
    count distinct non-source pages citing each vault source; filter to
    sources with no existing evidence/*.md anchored to them (where
    "anchored to them" means the evidence page's body cites that vault
    path). Sources at >= min_inbound are surfaced, ranked.

    Rationale. The old frontier trigger ("zero non-source citations")
    went to zero the moment INGEST created an entity/concept page that
    cites the source. That's binary; once it's cited once, the signal
    dies — even though the source may be cited 10 times across the wiki
    without a consolidated anchor. This trigger keeps the signal alive:
    popular sources without an evidence page stay on the list until one
    is created.
    """
    from naming import CITATION_RE

    pages = wiki_pages(wiki_dir)
    demand = defaultdict(set)
    evidence_covered = set()
    for p in pages:
        rel = str(p.relative_to(wiki_dir))
        text = p.read_text()
        if rel.startswith("sources/"):
            continue
        for m in CITATION_RE.finditer(text):
            vault_path = m.group(1).strip()
            if rel.startswith("evidence/"):
                evidence_covered.add(vault_path)
            else:
                demand[vault_path].add(rel)

    candidates = []
    for vault_path, citing_pages in demand.items():
        if len(citing_pages) < min_inbound:
            continue
        if vault_path in evidence_covered:
            continue
        candidates.append({
            "vault_path": vault_path,
            "distinct_citers": len(citing_pages),
            "citing_pages": sorted(citing_pages)[:10],
        })
    candidates.sort(key=lambda c: (-c["distinct_citers"], c["vault_path"]))
    print(json.dumps({"candidates": candidates[:limit]}, indent=2))


def cmd_figure_candidates(wiki_dir: Path, min_inbound: int = 2,
                            limit: int = 20) -> None:
    """Rank PDF vault sources by reuse demand with no existing figure pages.

    Demand signal for figures, symmetric with evidence-candidates but
    with a looser inbound threshold (default 2, not 3) — a single
    strong source referenced by two concept/evidence pages often has
    at least one visually-anchored fact worth extracting, whereas
    evidence anchors justify a higher bar.

    "Has figure pages" = any wiki/figures/*.md whose `sources:`
    frontmatter includes the vault extraction path. Only PDFs are
    surfaced: the binary must live beside the extraction so
    figures.py render-all has something to render.
    """
    from naming import CITATION_RE

    pages = wiki_pages(wiki_dir)
    vault_dir = wiki_dir.parent / "vault"

    figure_covered = set()
    fig_dir = wiki_dir / "figures"
    if fig_dir.is_dir():
        for fp in fig_dir.glob("*.md"):
            try:
                fm, _ = read_frontmatter(fp.read_text())
            except Exception:
                continue
            if fm.get("type") != "figure":
                continue
            srcs = fm.get("sources", [])
            if isinstance(srcs, str):
                srcs = [srcs]
            for s in srcs:
                figure_covered.add(s.strip())

    demand = defaultdict(set)
    for p in pages:
        rel = str(p.relative_to(wiki_dir))
        if rel.startswith("sources/") or rel.startswith("figures/"):
            continue
        text = p.read_text()
        for m in CITATION_RE.finditer(text):
            demand[m.group(1).strip()].add(rel)

    candidates = []
    for vault_path, citing_pages in demand.items():
        if len(citing_pages) < min_inbound:
            continue
        if vault_path in figure_covered:
            continue
        # Only PDFs — derive the binary path from the extraction path
        # by stripping `.extracted.md` and checking for a sibling `.pdf`.
        if not vault_path.endswith(".extracted.md"):
            continue
        pdf_rel = vault_path[: -len(".extracted.md")] + ".pdf"
        pdf_abs = vault_dir / pdf_rel
        if not pdf_abs.exists():
            continue
        # Skip sources where the figure-extraction pass has already run
        # (may have produced zero figures legitimately). Without this
        # filter, every cited-but-empty source re-surfaces every wave.
        ext_abs = vault_dir / vault_path
        if ext_abs.exists():
            try:
                fm, _ = read_frontmatter(ext_abs.read_text())
            except Exception:
                fm = {}
            if fm.get("figures_extracted"):
                continue
        candidates.append({
            "vault_extraction": vault_path,
            "vault_pdf": pdf_rel,
            "distinct_citers": len(citing_pages),
            "citing_pages": sorted(citing_pages)[:10],
        })
    candidates.sort(key=lambda c: (-c["distinct_citers"], c["vault_extraction"]))
    print(json.dumps({"candidates": candidates[:limit]}, indent=2))


def cmd_pending_figures(wiki_dir: Path) -> None:
    """List PDF vault extractions with no figures_extracted flag.

    Completion signal (distinct from demand). Useful for `where has
    the figure-extraction pass not yet run?`. Source-level — does not
    check whether any downstream figure pages were actually created.
    """
    vault_dir = wiki_dir.parent / "vault"
    queue = []
    if not vault_dir.exists():
        print(json.dumps({"queue": [], "count": 0,
                          "note": "no vault/ directory"}))
        return
    for ext in sorted(vault_dir.glob("*.extracted.md")):
        pdf = ext.with_suffix("")  # strip .md → leaves .extracted
        pdf = pdf.with_suffix("")  # strip .extracted → stem
        pdf = pdf.with_suffix(".pdf")
        if not pdf.exists():
            continue
        try:
            fm, _ = read_frontmatter(ext.read_text())
        except Exception:
            fm = {}
        if fm.get("figures_extracted"):
            continue
        queue.append({
            "extracted": ext.name,
            "pdf": pdf.name,
        })
    print(json.dumps({"queue": queue, "count": len(queue)}, indent=2))


def cmd_pending_multimodal(wiki_dir: Path) -> None:
    """List vault extractions flagged for multimodal upgrade.

    Reads `multimodal_recommended: true` frontmatter tags set by
    local_ingest.py on PDFs that either failed the fast-extraction
    sanity check or look like they have math/tables. The agent consumes
    this queue: for each entry, re-read the original source
    multimodally, overwrite `.extracted.md` body + frontmatter.
    """
    vault_dir = wiki_dir.parent / "vault"
    queue = []
    if not vault_dir.exists():
        print(json.dumps({"queue": [], "count": 0,
                          "note": "no vault/ directory"}))
        return
    for f in sorted(vault_dir.glob("*.extracted.md")):
        try:
            fm, _ = read_frontmatter(f.read_text())
        except Exception:
            continue
        flag = str(fm.get("multimodal_recommended", "")).lower() == "true"
        if not flag:
            continue
        queue.append({
            "extracted": f.name,
            "original": fm.get("kept_as", ""),
            "extraction_quality": fm.get("extraction_quality", ""),
            "has_math": str(fm.get("has_math", "")).lower() == "true",
            "has_tables": str(fm.get("has_tables", "")).lower() == "true",
            "sanity_note": fm.get("sanity_note", ""),
        })
    print(json.dumps({"queue": queue, "count": len(queue)}, indent=2))


def cmd_concept_candidates(wiki_dir: Path, min_inbound: int = 3,
                            limit: int = 20) -> None:
    """Rank missing wikilink targets by demand (count of distinct pages).

    Mirrors the scan-wikilinks walk but groups dead refs by target
    instead of counting them flat. A target is a "demanded concept"
    when at least `min_inbound` distinct pages link to it and no file
    under `wiki/` has it as its stem (any subdirectory — a wikilink
    target doesn't specify a subdir, so a sources/transformer.md
    would satisfy a [[transformer]] link).
    """
    pages = wiki_pages(wiki_dir)
    existing_stems = {p.stem.lower() for p in pages}
    demand = defaultdict(set)
    for page in pages:
        text = page.read_text()
        own = page.stem.lower()
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip().lower().replace(" ", "-")
            if not target or target == own or target in existing_stems:
                continue
            demand[target].add(str(page.relative_to(wiki_dir)))
    candidates = [
        {
            "target": t,
            "inbound": len(srcs),
            "referenced_by": sorted(srcs)[:10],
        }
        for t, srcs in demand.items()
        if len(srcs) >= min_inbound
    ]
    candidates.sort(key=lambda c: (-c["inbound"], c["target"]))
    print(json.dumps({"candidates": candidates[:limit]}, indent=2))


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
        "fix-spaced-wikilinks", "fix-orphan-root-files",
        "scan-references", "resync-stems", "resync-prefixes",
        "concept-candidates",
        "evidence-candidates", "figure-candidates",
        "pending-multimodal", "pending-figures",
    ])
    ap.add_argument("wiki", nargs="?", default="wiki")
    ap.add_argument("--min-inbound", type=int, default=3,
                    help="concept-candidates: minimum distinct pages that "
                         "must reference a missing stem before it's a "
                         "candidate (default 3)")
    ap.add_argument("--limit", type=int, default=20,
                    help="concept-candidates: max candidates returned "
                         "(default 20)")
    ap.add_argument("--cited-only", action="store_true",
                    help="fix-source-stubs: only create stubs for vault "
                         "files already cited by non-source wiki pages "
                         "(tiered-vault mode)")
    args = ap.parse_args()

    wiki_dir = Path(args.wiki).resolve()
    if not wiki_dir.exists():
        print(json.dumps({"error": f"wiki dir not found: {wiki_dir}"}))
        sys.exit(1)

    if args.command == "scan":
        cmd_scan(wiki_dir)
    elif args.command == "fix-source-stubs":
        cmd_fix_source_stubs(wiki_dir, cited_only=args.cited_only)
    elif args.command == "fix-index":
        cmd_fix_index(wiki_dir)
    elif args.command == "fix-percent-escapes":
        cmd_fix_percent_escapes(wiki_dir)
    elif args.command == "fix-spaced-wikilinks":
        cmd_fix_spaced_wikilinks(wiki_dir)
    elif args.command == "fix-orphan-root-files":
        cmd_fix_orphan_root_files(wiki_dir)
    elif args.command == "scan-references":
        cmd_scan_references(wiki_dir)
    elif args.command == "resync-stems":
        cmd_resync_stems(wiki_dir)
    elif args.command == "resync-prefixes":
        cmd_resync_prefixes(wiki_dir)
    elif args.command == "concept-candidates":
        cmd_concept_candidates(wiki_dir, min_inbound=args.min_inbound,
                                limit=args.limit)
    elif args.command == "evidence-candidates":
        cmd_evidence_candidates(wiki_dir, min_inbound=args.min_inbound,
                                 limit=args.limit)
    elif args.command == "figure-candidates":
        cmd_figure_candidates(wiki_dir,
                                min_inbound=(args.min_inbound if args.min_inbound != 3 else 2),
                                limit=args.limit)
    elif args.command == "pending-figures":
        cmd_pending_figures(wiki_dir)
    elif args.command == "pending-multimodal":
        cmd_pending_multimodal(wiki_dir)


if __name__ == "__main__":
    main()
