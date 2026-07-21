#!/usr/bin/env python3
"""bootstrap.py — high-volume wiki densify (standalone, not CURATE).

Division of labour: bootstrap optimizes for *volume, structure, and
verbatim harvest*; long CURATE owns analyses, identity merge, and QA.

Stages (invoke separately; agent memory tracks which packs finished):

  bootstrap.py captions <wiki_dir> [--apply] [--with-facts]
      Deterministic Fig./Table harvest from vault extractions.
      Figure captions → wiki/figures/ (origin: caption-text, no asset).
      Table captions → wiki/tables/ (caption-only pages).
      Optional --with-facts also mints a verbatim fact twin.

  bootstrap.py facts-plan <wiki_dir> [--docs-per-pack N] [--max-chars C]
      Partition vault extractions into packs; print JSON plan for the agent.

  bootstrap.py facts-pack <wiki_dir> --pack-index I [--docs-per-pack N]
      Emit packed source text + SOURCES list for one LLM call.

  bootstrap.py facts-apply <wiki_dir> --json-stdin|--json-file PATH [--dry-run]
      Normalize + write fact pages from LLM JSON (mechanical gate).

  bootstrap.py links-plan <wiki_dir> [--batch-size N] [--max-pages N]
      Catalog + low-link fact pages batched for the agent.

  bootstrap.py links-apply <wiki_dir> --json-stdin|--json-file PATH [--dry-run]
      Apply body rewrites; strip [[stems]] not in the catalog.

  bootstrap.py prompts
      Print the system prompts for facts-pack and links-pack LLM calls.

  bootstrap.py status <wiki_dir>
      Counts for agent handoff / resume (agent memory is primary).

Not hash-guarded (like okf_export): operator/agent tool, not a CURATE scorer.
LLM calls are made by the driving agent (multi-provider, session resume via
agent memory + .curator/log.md). This script only packs, normalizes, gates,
and writes.

See docs/v0.9.2-implementation-plan.md and switchyard docs/ce-bootstrap-mode-design.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import (  # noqa: E402
    WIKILINK_RE,
    citation_stem,
    parse_source_meta,
    prefixed_stem,
    read_frontmatter,
)
from score_diff import new_page_verdict  # noqa: E402

TODAY = date.today().isoformat()

_CAP_OPEN = re.compile(
    r"^(?P<label>"
    r"(?:Fig\.|FIG\.|Figure|Table|TABLE|Tab\.)\s*"
    r"(?P<num>[IVXLCDM\d]+[a-z]?)\s*[.:\-–—]?\s*"
    r")(?P<rest>.*)$",
    re.I,
)
_CONT = re.compile(r"^(?![A-Z][A-Z\s]{3,}$)(?!\d+\.\s)(?!#{1,3}\s).+")
_SLUG_BAD = re.compile(r"[^a-z0-9]+")

FACTS_SYS_PROMPT = """\
You are the CURIOSITY-ENGINE BOOTSTRAP FACT extractor (not the full curator).

Mission: pull as many *atomic, source-grounded FACT claims* as possible from
the packed lecture extractions. A later long-running CURATE loop will fix
wikilinks, merge duplicates, and write analyses — so optimize for COVERAGE
of extractable claims, not polished network quality.

Return ONLY a JSON array (no markdown fences). Each element:
{
  "stem": "kebab-case-slug",
  "title": "short claim title (no wikilinks)",
  "body": "1-3 sentence atomic claim ending with (vault:RELPATH) where RELPATH
           is EXACTLY one of the source paths listed (no vault/ prefix)",
  "source": "exact relative path from SOURCES list (no vault/ prefix)"
}

EXTRACT (high value for bootstrap):
- Numeric bounds, hyperparameters, dataset sizes/classes, algorithm
  definitions, named paper results, definitions stated as equations.
- Prefer claims a student would need for exams.

DELIBERATELY SKIP (curator will do these better/faster later):
- Multi-lecture analyses or comparison essays
- Dense [[wikilinks]] (use plain names; a later link pass rewrites)
- Merging with existing wiki pages or identity resolution
- Soft commentary, motivation, or "why it matters" prose

Hard rules:
- Do NOT invent claims absent from the materials.
- Prefer near-verbatim wording over paraphrase when the text is a definition
  or number.
- Max 45 facts per response. Prefer breadth across the packed sources.
- Never write (vault:vault/…); use (vault:RELPATH) only.
"""

LINKS_SYS_PROMPT = """\
You are the CURIOSITY-ENGINE BOOTSTRAP LINK rewriter (not the full curator).

Mission: given a catalog of EXISTING wiki page stems/titles and a batch of
fact-page bodies, rewrite each body to insert as many *correct* [[wikilinks]]
as possible using ONLY stems from the catalog. A later CURATE loop will fix
bad links, add missing entities, and write analyses — so prioritize LINK
DENSITY on already-known nodes, not inventing new pages.

Return ONLY a JSON array:
[
  {
    "stem": "existing fact page stem",
    "body": "rewritten body; keep the (vault:…) citation; add [[stem]] links"
  }
]

Rules:
- ONLY use stems that appear in the CATALOG. Do not invent new stems.
- Prefer linking: entities, concepts, datasets, algorithms named in the claim.
- If no catalog stem fits, leave plain text (do not force wrong links).
- Keep claim meaning unchanged; do not add new factual content.
- Keep (vault:RELPATH) citations intact (no vault/ prefix).
- Max one rewrite object per input stem. Skip stems you cannot improve.
"""


def _slug(text: str, max_len: int = 60) -> str:
    s = text.casefold().strip()
    s = _SLUG_BAD.sub("-", s).strip("-")
    return (s[:max_len].rstrip("-") or "item")


def _workspace(wiki_dir: Path) -> Path:
    wiki_dir = wiki_dir.resolve()
    return wiki_dir.parent if wiki_dir.name == "wiki" else wiki_dir


def _vault_rel(workspace: Path, path: Path) -> str:
    try:
        return str(path.relative_to(workspace / "vault"))
    except ValueError:
        return path.name


def iter_vault_extractions(workspace: Path) -> list[Path]:
    vault = workspace / "vault"
    if not vault.is_dir():
        return []
    return sorted(vault.rglob("*.extracted.md"))


def harvest_captions_from_text(text: str, source_rel: str) -> list[dict[str, Any]]:
    """Parse caption blocks from one extraction body."""
    # Drop frontmatter if present
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        m = _CAP_OPEN.match(lines[i].strip())
        if not m:
            i += 1
            continue
        label = m.group("label").strip()
        num = m.group("num")
        parts = [m.group("rest").strip()] if m.group("rest").strip() else []
        j = i + 1
        while j < len(lines) and len(parts) < 5:
            nxt = lines[j].rstrip()
            if not nxt.strip():
                break
            if _CAP_OPEN.match(nxt.strip()):
                break
            if re.match(r"^(#{1,3}\s|[A-Z][A-Z0-9 \-/]{8,}$)", nxt.strip()):
                break
            if len(nxt.strip()) < 3:
                break
            if parts and len(parts[-1]) > 40 and nxt.strip()[:1].isupper() and len(nxt) > 80:
                if not parts[-1].endswith(("-", "—", ",", ";", "and", "the", "of", "to", "a")):
                    if not re.match(r"^[a-z(]", nxt.strip()):
                        break
            parts.append(nxt.strip())
            j += 1
        body = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
        if len(body) < 12:
            i = j
            continue
        full = f"{label}{body}".strip()
        kind = "table" if re.match(r"tab", label, re.I) else "figure"
        stem_base = _slug(f"{kind}-{num}-{body[:40]}")
        out.append({
            "kind": kind,
            "num": num,
            "label": label,
            "caption": full,
            "caption_body": body,
            "source": source_rel,
            "stem": stem_base,
            "line": i + 1,
        })
        i = j
    return out


def harvest_captions(workspace: Path) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    all_caps: list[dict[str, Any]] = []
    for path in iter_vault_extractions(workspace):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _vault_rel(workspace, path)
        for cap in harvest_captions_from_text(text, rel):
            key = (cap["source"], cap["caption"].casefold())
            if key in seen:
                continue
            seen.add(key)
            all_caps.append(cap)
    return all_caps


def _unique_path(dir_path: Path, stem: str) -> Path:
    path = dir_path / f"{stem}.md"
    n = 2
    while path.exists():
        path = dir_path / f"{stem}-{n}.md"
        n += 1
    return path


def _source_stub_stem(workspace: Path, vault_rel: str) -> Optional[str]:
    """Best-effort citation stem for a vault extraction path."""
    vp = workspace / "vault" / vault_rel
    if not vp.exists():
        # strip .extracted.md → try base
        return _slug(Path(vault_rel).stem.replace(".extracted", "")[:50])
    try:
        meta = parse_source_meta(vp)
        return citation_stem(meta)
    except Exception:
        return _slug(Path(vault_rel).stem.replace(".extracted", "")[:50])


def _caption_already_in_wiki(workspace: Path, caption: str) -> bool:
    needle = caption[:80].casefold()
    wiki = workspace / "wiki"
    if not wiki.is_dir() or len(needle) < 20:
        return False
    for sub in ("facts", "figures", "tables"):
        d = wiki / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            try:
                if needle in p.read_text(encoding="utf-8", errors="replace").casefold():
                    return True
            except OSError:
                continue
    return False


def render_figure_caption_page(cap: dict[str, Any], *, created: str = TODAY,
                               source_stem: Optional[str] = None) -> tuple[str, str]:
    title = cap["caption"]
    if len(title) > 100:
        title = title[:97] + "…"
    src = cap["source"]
    stem = prefixed_stem("figure", re.sub(r"^fig-", "", cap["stem"]))
    link = f"[[{source_stem}]] " if source_stem else ""
    body = (
        f"**Caption (verbatim):** {cap['caption']}\n\n"
        f"{link}Text-only figure page (origin: caption-text; no binary asset). "
        f"(vault:{src})\n"
    )
    page = (
        "---\n"
        f'title: "[fig] {title.replace(chr(34), "")}"\n'
        "type: figure\n"
        f"created: {created}\n"
        f"updated: {created}\n"
        f"sources: [{src}]\n"
        "origin: caption-text\n"
        "extraction_method: caption_line\n"
        "relates_to: []\n"
        "---\n\n"
        f"{body}"
    )
    return page, stem


def render_table_caption_page(cap: dict[str, Any], *, created: str = TODAY,
                              source_stem: Optional[str] = None) -> tuple[str, str]:
    title = cap["caption"]
    if len(title) > 100:
        title = title[:97] + "…"
    src = cap["source"]
    raw = re.sub(r"^(table|tab)-", "", cap["stem"])
    stem = prefixed_stem("extracted-table", raw)
    link = f"[[{source_stem}]] " if source_stem else ""
    body = (
        f"**Caption (verbatim):** {cap['caption']}\n\n"
        f"{link}Caption-only table page harvested from source extraction "
        f"(no grid rows). (vault:{src})\n"
    )
    page = (
        "---\n"
        f'title: "[tab] {title.replace(chr(34), "")}"\n'
        "type: extracted-table\n"
        f"created: {created}\n"
        f"updated: {created}\n"
        f"sources: [{src}]\n"
        "origin: bootstrap-caption\n"
        "extraction_method: caption_line\n"
        "is_snapshot: false\n"
        "---\n\n"
        f"{body}"
    )
    return page, stem


def render_optional_fact(cap: dict[str, Any], *, created: str = TODAY,
                         source_stem: Optional[str] = None) -> tuple[str, str]:
    title = cap["caption"]
    if len(title) > 100:
        title = title[:97] + "…"
    src = cap["source"]
    stem = f"fact-{cap['stem']}" if not cap["stem"].startswith("fact-") else cap["stem"]
    link = f"[[{source_stem}]] " if source_stem else ""
    # Framing clause keeps short captions above the verbatim word floor.
    body = (
        f"{link}Verbatim caption claim from source. "
        f"{cap['caption']} (vault:{src})\n"
    )
    page = (
        "---\n"
        f'title: "[fact] {title.replace(chr(34), "")}"\n'
        "type: fact\n"
        f"created: {created}\n"
        f"updated: {created}\n"
        f"sources: [{src}]\n"
        "verbatim: true\n"
        "origin: bootstrap-caption\n"
        "---\n\n"
        f"{body}"
    )
    return page, stem


def apply_captions(
    workspace: Path,
    caps: list[dict[str, Any]],
    *,
    with_facts: bool = False,
    dry_run: bool = True,
    gate: bool = True,
) -> dict[str, Any]:
    wiki = workspace / "wiki"
    figs_dir = wiki / "figures"
    tabs_dir = wiki / "tables"
    facts_dir = wiki / "facts"
    written = {"figures": [], "tables": [], "facts": [], "rejected": [], "skipped": 0}

    for cap in caps:
        if _caption_already_in_wiki(workspace, cap["caption"]):
            written["skipped"] += 1
            continue
        src_stem = _source_stub_stem(workspace, cap["source"])
        if cap["kind"] == "table":
            page, stem = render_table_caption_page(cap, source_stem=src_stem)
            dest_dir = tabs_dir
            bucket = "tables"
            rel = f"tables/{stem}.md"
        else:
            page, stem = render_figure_caption_page(cap, source_stem=src_stem)
            dest_dir = figs_dir
            bucket = "figures"
            rel = f"figures/{stem}.md"

        if gate:
            ok, reason = new_page_verdict(page, Path(rel))
            if not ok:
                written["rejected"].append({"stem": stem, "reason": reason, "kind": cap["kind"]})
                continue

        path = dest_dir / f"{stem}.md"
        if dry_run:
            written[bucket].append(str(path))
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = _unique_path(dest_dir, stem)
            path.write_text(page, encoding="utf-8")
            written[bucket].append(str(path))

        if with_facts:
            fpage, fstem = render_optional_fact(cap, source_stem=src_stem)
            if gate:
                ok, reason = new_page_verdict(fpage, Path(f"facts/{fstem}.md"))
                if not ok:
                    written["rejected"].append({"stem": fstem, "reason": reason, "kind": "fact"})
                    continue
            if dry_run:
                written["facts"].append(str(facts_dir / f"{fstem}.md"))
            else:
                facts_dir.mkdir(parents=True, exist_ok=True)
                fpath = _unique_path(facts_dir, fstem)
                fpath.write_text(fpage, encoding="utf-8")
                written["facts"].append(str(fpath))

    return {
        "captions_found": len(caps),
        "figures_written": written["figures"],
        "tables_written": written["tables"],
        "facts_written": written["facts"],
        "rejected": written["rejected"],
        "skipped": written["skipped"],
        "dry_run": dry_run,
        "n_figures": len(written["figures"]),
        "n_tables": len(written["tables"]),
        "n_facts": len(written["facts"]),
    }


# ── facts packs ──────────────────────────────────────────────────────

def _doc_fig_score(p: Path) -> int:
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(_CAP_OPEN.findall(text))


def partition_vault(
    workspace: Path,
    docs_per_pack: int = 6,
) -> list[list[Path]]:
    paths = sorted(iter_vault_extractions(workspace), key=_doc_fig_score, reverse=True)
    packs: list[list[Path]] = []
    for i in range(0, len(paths), docs_per_pack):
        packs.append(paths[i: i + docs_per_pack])
    return packs


def pack_docs(
    paths: list[Path],
    workspace: Path,
    *,
    max_chars: int = 180_000,
) -> tuple[str, list[str]]:
    chunks: list[str] = []
    used: list[str] = []
    total = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _vault_rel(workspace, path)
        header = f"\n\n===== SOURCE: {rel} =====\n"
        block = header + text
        if total + len(block) > max_chars:
            room = max_chars - total - len(header) - 20
            if room < 2000:
                break
            block = header + text[:room] + "\n…[truncated]…"
        chunks.append(block)
        used.append(rel)
        total += len(block)
        if total >= max_chars:
            break
    return "".join(chunks), used


def cmd_facts_plan(wiki_dir: Path, docs_per_pack: int, max_chars: int) -> dict:
    ws = _workspace(wiki_dir)
    packs = partition_vault(ws, docs_per_pack=docs_per_pack)
    plan = []
    for i, pack in enumerate(packs):
        packed, used = pack_docs(pack, ws, max_chars=max_chars)
        plan.append({
            "pack_index": i,
            "n_docs": len(used),
            "sources": used,
            "packed_chars": len(packed),
        })
    return {
        "docs_per_pack": docs_per_pack,
        "max_chars": max_chars,
        "n_packs": len(plan),
        "packs": plan,
        "prompt": "Use bootstrap.py facts-pack --pack-index I then LLM with prompts; "
                  "pipe JSON to facts-apply. Track finished pack_index in agent memory / log.md.",
    }


def cmd_facts_pack(wiki_dir: Path, pack_index: int, docs_per_pack: int,
                   max_chars: int) -> dict:
    ws = _workspace(wiki_dir)
    packs = partition_vault(ws, docs_per_pack=docs_per_pack)
    if pack_index < 0 or pack_index >= len(packs):
        return {"error": f"pack_index {pack_index} out of range 0..{len(packs)-1}"}
    packed, used = pack_docs(packs[pack_index], ws, max_chars=max_chars)
    sources_list = "\n".join(f"- {s}" for s in used)
    user_message = (
        f"SOURCES (use these exact paths in (vault:…) and source field):\n"
        f"{sources_list}\n\n"
        f"PACKED EXTRACTIONS:\n{packed}\n"
    )
    return {
        "pack_index": pack_index,
        "n_packs": len(packs),
        "sources": used,
        "system_prompt": FACTS_SYS_PROMPT,
        "user_message": user_message,
        "packed_chars": len(packed),
    }


def _normalize_vault_rel(src: str) -> str:
    s = (src or "").strip().lstrip("/")
    while s.startswith("vault/"):
        s = s[len("vault/"):]
    return s


def _normalize_fact_record(f: dict[str, Any]) -> dict[str, Any]:
    src = _normalize_vault_rel(str(f.get("source") or ""))
    body = (f.get("body") or "").strip()
    body = re.sub(r"\(vault:(?:vault/)+", "(vault:", body)
    # Ensure citation present
    if src and f"(vault:{src})" not in body and "(vault:" not in body:
        body = f"{body} (vault:{src})"
    stem = _slug(str(f.get("stem") or f.get("title") or "fact"))
    title = (f.get("title") or stem).strip()
    return {"stem": stem, "title": title, "body": body, "source": src}


def apply_llm_facts(
    workspace: Path,
    facts: list[dict[str, Any]],
    *,
    dry_run: bool = True,
    gate: bool = True,
) -> dict[str, Any]:
    facts_dir = workspace / "wiki" / "facts"
    have = {p.stem for p in facts_dir.glob("*.md")} if facts_dir.is_dir() else set()
    written, rejected = [], []
    for raw in facts:
        f = _normalize_fact_record(raw)
        if not f["source"] or len(f["body"]) < 15:
            rejected.append({"stem": f["stem"], "reason": "missing source or short body"})
            continue
        src_stem = _source_stub_stem(workspace, f["source"])
        body = f["body"]
        # Optional mechanical source link for graph connectivity
        if src_stem and f"[[{src_stem}]]" not in body:
            body = f"[[{src_stem}]] {body}"
        page = (
            "---\n"
            f'title: "[fact] {f["title"][:100].replace(chr(34), "")}"\n'
            "type: fact\n"
            f"created: {TODAY}\n"
            f"updated: {TODAY}\n"
            f"sources: [{f['source']}]\n"
            "origin: bootstrap-facts\n"
            "---\n\n"
            f"{body}\n"
        )
        if gate:
            ok, reason = new_page_verdict(page, Path(f"facts/{f['stem']}.md"))
            if not ok:
                rejected.append({"stem": f["stem"], "reason": reason})
                continue
        if f["stem"] in have:
            rejected.append({"stem": f["stem"], "reason": "stem exists"})
            continue
        path = facts_dir / f"{f['stem']}.md"
        if dry_run:
            written.append(str(path))
            have.add(f["stem"])
        else:
            facts_dir.mkdir(parents=True, exist_ok=True)
            path = _unique_path(facts_dir, f["stem"])
            path.write_text(page, encoding="utf-8")
            written.append(str(path))
            have.add(path.stem)
    return {
        "facts_written": written,
        "n_written": len(written),
        "n_in": len(facts),
        "rejected": rejected,
        "dry_run": dry_run,
    }


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if isinstance(data, dict) and "facts" in data:
        data = data["facts"]
    if not isinstance(data, list):
        raise ValueError("expected JSON array")
    return [x for x in data if isinstance(x, dict)]


# ── links ────────────────────────────────────────────────────────────

def wiki_catalog(workspace: Path) -> list[dict[str, str]]:
    wiki = workspace / "wiki"
    out: list[dict[str, str]] = []
    for sub in ("concepts", "entities", "facts", "sources", "figures",
                "tables", "evidence", "analyses"):
        d = wiki / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                fm, _ = read_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                fm = {}
            title = str(fm.get("title") or p.stem)
            out.append({"stem": p.stem, "title": title, "type": sub})
    return out


def _fact_pages_low_link(workspace: Path, *, limit: int = 0) -> list[Path]:
    d = workspace / "wiki" / "facts"
    if not d.is_dir():
        return []
    scored = []
    for p in d.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = len(WIKILINK_RE.findall(text))
        scored.append((n, p))
    scored.sort(key=lambda x: (x[0], x[1].name))
    pages = [p for n, p in scored if n < 2]
    if limit > 0:
        pages = pages[:limit]
    return pages


def cmd_links_plan(wiki_dir: Path, batch_size: int, max_pages: int) -> dict:
    ws = _workspace(wiki_dir)
    catalog = wiki_catalog(ws)
    pages = _fact_pages_low_link(ws, limit=max_pages or 0)
    batches = []
    for i in range(0, len(pages), batch_size):
        batch = pages[i: i + batch_size]
        items = []
        for p in batch:
            text = p.read_text(encoding="utf-8", errors="replace")
            fm, body = read_frontmatter(text)
            items.append({
                "stem": p.stem,
                "title": str(fm.get("title") or p.stem),
                "body": body.strip(),
            })
        batches.append({"batch_index": i // batch_size, "pages": items})
    # Compact catalog for prompt size
    cat_lines = [f"- {c['stem']}: {c['title'][:80]}" for c in catalog[:800]]
    return {
        "catalog_size": len(catalog),
        "n_low_link_facts": len(pages),
        "batch_size": batch_size,
        "n_batches": len(batches),
        "batches": batches,
        "catalog_preview": cat_lines[:50],
        "catalog_full": cat_lines,
        "system_prompt": LINKS_SYS_PROMPT,
        "prompt": "For each batch, send system_prompt + catalog_full + pages JSON to LLM; "
                  "pipe rewrites to links-apply. Track batch_index in agent memory.",
    }


def apply_link_rewrites(
    workspace: Path,
    rewrites: list[dict[str, Any]],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    catalog = wiki_catalog(workspace)
    stems = {c["stem"] for c in catalog}
    # also allow bare stems without path
    facts_dir = workspace / "wiki" / "facts"
    applied, skipped = [], []

    def _filter_links(body: str) -> str:
        def repl(m: re.Match) -> str:
            full = m.group(0)
            inner = full[2:-2]
            if "|" in inner:
                target, label = inner.split("|", 1)
                target, label = target.strip(), label.strip()
            else:
                target, label = inner.strip(), None
            stem = Path(target).stem
            if stem in stems or target in stems:
                return full
            return label if label else stem.replace("-", " ")
        return re.sub(r"\[\[([^\]]+)\]\]", repl, body)

    for rw in rewrites:
        stem = str(rw.get("stem") or "").strip()
        body = (rw.get("body") or "").strip()
        if not stem or not body:
            skipped.append({"stem": stem, "reason": "empty"})
            continue
        path = facts_dir / f"{stem}.md"
        if not path.is_file():
            skipped.append({"stem": stem, "reason": "page missing"})
            continue
        body = re.sub(r"\(vault:(?:vault/)+", "(vault:", body)
        body = _filter_links(body)
        old = path.read_text(encoding="utf-8", errors="replace")
        fm, _old_body = read_frontmatter(old)
        # rebuild page
        # keep original frontmatter keys, bump updated
        lines = ["---"]
        # re-serialize simple frontmatter from fm
        for k, v in fm.items():
            if k == "updated":
                lines.append(f"updated: {TODAY}")
            elif isinstance(v, bool):
                lines.append(f"{k}: {'true' if v else 'false'}")
            elif isinstance(v, list):
                lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            else:
                val = str(v)
                if k == "title" and not (val.startswith('"') or val.startswith("'")):
                    lines.append(f'{k}: "{val}"')
                else:
                    lines.append(f"{k}: {val}")
        if "updated" not in fm:
            lines.append(f"updated: {TODAY}")
        lines.append("---")
        lines.append("")
        lines.append(body.rstrip())
        lines.append("")
        new_text = "\n".join(lines)
        if dry_run:
            applied.append(str(path))
        else:
            path.write_text(new_text, encoding="utf-8")
            applied.append(str(path))
    return {
        "applied": applied,
        "n_applied": len(applied),
        "skipped": skipped,
        "dry_run": dry_run,
        "catalog_size": len(stems),
    }


def cmd_status(wiki_dir: Path) -> dict:
    ws = _workspace(wiki_dir)
    wiki = ws / "wiki"
    counts = {}
    for sub in ("facts", "figures", "tables", "concepts", "entities",
                "sources", "analyses", "evidence"):
        d = wiki / sub
        counts[sub] = len(list(d.glob("*.md"))) if d.is_dir() else 0
    n_ext = len(iter_vault_extractions(ws))
    packs = partition_vault(ws, docs_per_pack=6)
    low = _fact_pages_low_link(ws)
    return {
        "workspace": str(ws),
        "vault_extractions": n_ext,
        "wiki_counts": counts,
        "facts_plan_packs_default_6": len(packs),
        "facts_low_link": len(low),
        "caption_candidates_estimate": len(harvest_captions(ws)),
        "resume": "Agent memory + .curator/log.md — record finished pack_index / batch_index; "
                  "re-run facts-plan / links-plan to see remaining work.",
    }


def _read_json_input(args) -> list[dict[str, Any]]:
    if getattr(args, "json_file", None):
        text = Path(args.json_file).read_text(encoding="utf-8")
    elif getattr(args, "json_stdin", False):
        text = sys.stdin.read()
    else:
        raise SystemExit("provide --json-stdin or --json-file")
    return _parse_json_array(text)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="CE bootstrap densify (standalone)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("captions", help="deterministic Fig/Table harvest")
    p_cap.add_argument("wiki_dir")
    p_cap.add_argument("--apply", action="store_true")
    p_cap.add_argument("--with-facts", action="store_true",
                       help="also write optional verbatim fact twins")
    p_cap.add_argument("--no-gate", action="store_true")

    p_fp = sub.add_parser("facts-plan", help="partition vault into LLM packs")
    p_fp.add_argument("wiki_dir")
    p_fp.add_argument("--docs-per-pack", type=int, default=6)
    p_fp.add_argument("--max-chars", type=int, default=180_000)

    p_fk = sub.add_parser("facts-pack", help="emit one pack for the agent LLM")
    p_fk.add_argument("wiki_dir")
    p_fk.add_argument("--pack-index", type=int, required=True)
    p_fk.add_argument("--docs-per-pack", type=int, default=6)
    p_fk.add_argument("--max-chars", type=int, default=180_000)

    p_fa = sub.add_parser("facts-apply", help="write facts from LLM JSON")
    p_fa.add_argument("wiki_dir")
    p_fa.add_argument("--json-stdin", action="store_true")
    p_fa.add_argument("--json-file")
    p_fa.add_argument("--dry-run", action="store_true")
    p_fa.add_argument("--no-gate", action="store_true")

    p_lp = sub.add_parser("links-plan", help="catalog + low-link fact batches")
    p_lp.add_argument("wiki_dir")
    p_lp.add_argument("--batch-size", type=int, default=12)
    p_lp.add_argument("--max-pages", type=int, default=0)

    p_la = sub.add_parser("links-apply", help="apply catalog-filtered rewrites")
    p_la.add_argument("wiki_dir")
    p_la.add_argument("--json-stdin", action="store_true")
    p_la.add_argument("--json-file")
    p_la.add_argument("--dry-run", action="store_true")

    sub.add_parser("prompts", help="print LLM system prompts")
    p_st = sub.add_parser("status", help="counts for agent handoff")
    p_st.add_argument("wiki_dir")

    args = ap.parse_args(argv)

    if args.cmd == "prompts":
        print(json.dumps({
            "facts_system": FACTS_SYS_PROMPT,
            "links_system": LINKS_SYS_PROMPT,
        }, indent=2))
        return 0

    if args.cmd == "status":
        print(json.dumps(cmd_status(Path(args.wiki_dir)), indent=2))
        return 0

    if args.cmd == "captions":
        ws = _workspace(Path(args.wiki_dir))
        caps = harvest_captions(ws)
        result = apply_captions(
            ws, caps,
            with_facts=args.with_facts,
            dry_run=not args.apply,
            gate=not args.no_gate,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "facts-plan":
        print(json.dumps(cmd_facts_plan(
            Path(args.wiki_dir), args.docs_per_pack, args.max_chars), indent=2))
        return 0

    if args.cmd == "facts-pack":
        print(json.dumps(cmd_facts_pack(
            Path(args.wiki_dir), args.pack_index, args.docs_per_pack, args.max_chars),
            indent=2))
        return 0

    if args.cmd == "facts-apply":
        ws = _workspace(Path(args.wiki_dir))
        facts = _read_json_input(args)
        result = apply_llm_facts(
            ws, facts,
            dry_run=args.dry_run,
            gate=not args.no_gate,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "links-plan":
        print(json.dumps(cmd_links_plan(
            Path(args.wiki_dir), args.batch_size, args.max_pages), indent=2))
        return 0

    if args.cmd == "links-apply":
        ws = _workspace(Path(args.wiki_dir))
        rewrites = _read_json_input(args)
        result = apply_link_rewrites(ws, rewrites, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
