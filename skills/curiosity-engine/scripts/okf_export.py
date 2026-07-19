#!/usr/bin/env python3
"""okf_export.py — project a CE wiki into an Open Knowledge Format bundle.

The Open Knowledge Format (OKF, Google Cloud, v0.1 Draft, Apache-2.0) is a
markdown-native knowledge-interchange format: a "Knowledge Bundle" is a
hierarchical directory of markdown "Concept" files, each a YAML frontmatter
block plus a markdown body. The only required frontmatter field is `type`;
links are plain markdown links; `index.md` / `log.md` are reserved. See
`docs/okf-interop.md` for the full mapping and the rationale.

This is a **read-only projection** of the wiki — the same posture as
`wiki_render.py`. Markdown is CE's source of truth; an OKF bundle is one more
derived view of it, alongside `data.json` and the kuzu graph. Nothing here
mutates `wiki/`, and no CE-only structure is lost: constructs OKF has no home
for (typed edges, the entity IRI, `same_as`, class-table shapes, the raw
citation list) round-trip through `x_ce_*` extension keys, which OKF consumers
are required to preserve.

Usage:
    okf_export.py build <wiki_dir> [--output-dir DIR] [--copy-assets]
                                   [--no-sources] [--date YYYY-MM-DD]

Emits a JSON manifest to stdout. Deterministic (sorted pages, stable key
order) so bundles diff cleanly under git — the only date-bearing output is the
generated `log.md` heading (override with `--date` for reproducible builds).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from naming import (  # noqa: E402
    SKIP_FILES,
    read_frontmatter,
)

OKF_VERSION = "0.1"

# Alias-capturing wikilink pattern. `naming.WIKILINK_RE` discards the pipe
# alias; we need it to preserve link display text, so mirror the pattern
# `sweep.py` uses for the same reason.
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
_CITATION_RE = re.compile(r"\s*\(vault:([^)]+)\)")
_IMG_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# Leading `[xx] ` doc-type prefix on CE titles (`"[con] Foo"` → `Foo`).
_TITLE_PREFIX_RE = re.compile(r"^\s*\[[A-Za-z][^\]]*\]\s*")

# Subdirectory → OKF `type` fallback when a page omits frontmatter `type`.
_SUBDIR_TYPE = {
    "entities": "entity", "concepts": "concept", "sources": "source",
    "analyses": "analysis", "evidence": "evidence", "facts": "fact",
    "tables": "table", "figures": "figure", "notes": "note",
    "todos": "todo-list", "projects": "project",
}

# Authority-prefixed external ids (CE `same_as` / `source_url`) → resolvable
# URIs for the OKF `resource` field. The workspace-relative CE `iri` is
# deliberately NOT a resource (it leaks the origin workspace name and is
# meaningless in a consumer bundle); it travels only in `x_ce_iri`.
_AUTHORITY_URI = {
    "doi": lambda v: f"https://doi.org/{v}",
    "wikidata": lambda v: f"https://www.wikidata.org/wiki/{v}",
    "pubchem": lambda v: (
        f"https://pubchem.ncbi.nlm.nih.gov/compound/{v[3:]}"
        if v.upper().startswith("CID") else
        f"https://pubchem.ncbi.nlm.nih.gov/compound/{v}"
    ),
    "uniprot": lambda v: f"https://www.uniprot.org/uniprotkb/{v}",
    "ensembl": lambda v: f"https://www.ensembl.org/id/{v}",
    "entrez": lambda v: f"https://www.ncbi.nlm.nih.gov/gene/{v}",
    "pdb": lambda v: f"https://www.rcsb.org/structure/{v}",
    "orcid": lambda v: f"https://orcid.org/{v}",
}


def _output_root() -> Path:
    return Path.home() / ".cache" / "curiosity-engine" / "okf-export"


def _wiki_pages(wiki_dir: Path) -> list[Path]:
    return [p for p in sorted(wiki_dir.rglob("*.md"))
            if p.name not in SKIP_FILES and "_suspect" not in p.parts]


def _strip_title_prefix(title: str) -> str:
    """`"[con] Foo"` → `"Foo"`. Leaves un-prefixed titles unchanged."""
    return _TITLE_PREFIX_RE.sub("", title or "").strip()


def _needs_quote(s: str) -> bool:
    """True when a YAML scalar must be quoted to parse as a plain string.

    Guards the same failure `sweep.py cmd_fix_frontmatter_quotes` guards: a
    value beginning `[`/`{` reads as a flow collection, a bare `:` or `#`
    can start a mapping/comment, and reserved words parse as bool/null.
    """
    if s == "":
        return True
    if s[0] in "[]{}#&*!|>%@`\"'?,-:" or s[0].isspace():
        return True
    if s[-1].isspace():
        return True
    if ": " in s or " #" in s or "\n" in s:
        return True
    if s.lower() in {"true", "false", "null", "yes", "no", "~", "on", "off"}:
        return True
    return False


def _yaml_scalar(v) -> str:
    """Render a Python value as a single-line YAML scalar."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if _needs_quote(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _yaml_flow_list(items: list) -> str:
    return "[" + ", ".join(_yaml_scalar(x) for x in items) + "]"


def _emit_frontmatter(fields: list[tuple], table_block: str | None) -> str:
    """Emit an ordered frontmatter block. `fields` is a list of (key, value)
    pairs already in the desired order; None/empty values are skipped. A raw
    `table:` shape block, if present, is preserved verbatim as a YAML literal
    block scalar under `x_ce_table`.
    """
    lines = ["---"]
    for key, value in fields:
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            lines.append(f"{key}: {_yaml_flow_list(value)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    if table_block:
        lines.append("x_ce_table: |")
        for raw in table_block.splitlines():
            lines.append("  " + raw if raw else "")
    lines.append("---")
    return "\n".join(lines)


def _extract_table_block(text: str) -> str | None:
    """Return the raw `table:` frontmatter block (key line + its indented
    children) verbatim, or None. `read_frontmatter` deliberately can't parse
    the nested shape mapping, so we lift it textually to round-trip it.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm_lines = text[3:end].split("\n")
    out: list[str] = []
    capturing = False
    for raw in fm_lines:
        if not capturing and re.match(r"^table:\s*$", raw):
            capturing = True
            out.append(raw)
            continue
        if capturing:
            if raw and raw[0] in (" ", "\t"):
                out.append(raw)
            else:
                break
    return "\n".join(out) if out else None


def _first_sentence(body: str) -> str:
    """Derive a one-sentence `description` from the first prose paragraph.
    Strips wikilink markup and citations so the result is clean text.
    """
    for raw in body.split("\n"):
        line = raw.strip()
        if not line or line.startswith(("#", "-", "*", "|", ">", "```", "![")):
            continue
        line = _WIKILINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), line)
        line = _CITATION_RE.sub("", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if not line:
            continue
        m = re.match(r"(.+?[.!?])(\s|$)", line)
        return (m.group(1) if m else line).strip()
    return ""


def _resource_uri(fm: dict) -> str:
    """Map a page's external identity to an OKF `resource` URI, or ''."""
    url = fm.get("source_url", "")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    same_as = fm.get("same_as") or []
    if isinstance(same_as, str):
        same_as = [same_as]
    for pair in same_as:
        if ":" not in str(pair):
            continue
        auth, _, ident = str(pair).partition(":")
        fn = _AUTHORITY_URI.get(auth.strip().lower())
        if fn and ident.strip():
            return fn(ident.strip())
    return ""


def _transform_body(
    body: str, stems_to_path: dict, page_titles: dict,
    copy_assets: bool, counters: dict,
) -> tuple[str, list[str]]:
    """Rewrite a CE body into OKF markdown. Returns (body, citations).

    - `[[stem|Display]]` → `[Display](/subdir/stem.md)` (bundle-absolute).
      Unresolved links degrade to plain display text and are counted.
    - `(vault:path)` citations are lifted out of prose into a returned list
      (rendered as a `# Citations` section by the caller and mirrored into
      `x_ce_citations`).
    - image embeds resolve to bundle-relative paths under `--copy-assets`,
      else degrade to an italic placeholder.
    """
    def _wikilink(m):
        target = m.group(1).strip()
        alias = (m.group(2) or "").strip()
        key = target.lower().replace(" ", "-")
        rel = stems_to_path.get(key)
        if rel:
            display = alias or page_titles.get(rel) or target
            counters["links"] += 1
            return f"[{display}](/{rel})"
        counters["broken_links"] += 1
        return alias or target

    def _img_embed(m):
        target = m.group(1)
        alt = ""
        if "|" in target:
            target, alt = target.split("|", 1)
        return _asset(target.strip(), alt.strip())

    def _md_image(m):
        return _asset(m.group(2).strip(), m.group(1).strip())

    def _asset(path: str, alt: str) -> str:
        norm = path
        if not path.startswith("figures/_assets/"):
            if path.startswith("_assets/"):
                norm = "figures/" + path
            elif "/" not in path:
                norm = "figures/_assets/" + path
        if copy_assets:
            counters["assets"] += 1
            return f"![{alt}](/{norm})"
        return f"_(figure: {alt or Path(norm).name})_"

    body = _IMG_EMBED_RE.sub(_img_embed, body)
    body = _MD_IMAGE_RE.sub(_md_image, body)
    body = _WIKILINK_RE.sub(_wikilink, body)

    citations: list[str] = []
    seen = set()
    for m in _CITATION_RE.finditer(body):
        c = m.group(1).strip()
        if c not in seen:
            seen.add(c)
            citations.append(c)
    body = _CITATION_RE.sub("", body)
    # Collapse whitespace left where inline citations were removed.
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r" +([.,;:])", r"\1", body)
    return body.strip() + "\n", citations


def _build_concept(
    rel: str, text: str, stems_to_path: dict, page_titles: dict,
    subdir_of: dict, vault_to_stub: dict, copy_assets: bool,
    no_sources: bool, counters: dict,
) -> str:
    fm, body = read_frontmatter(text)
    fm = fm if isinstance(fm, dict) else {}
    stem = Path(rel).stem

    ce_type = fm.get("type") or _SUBDIR_TYPE.get(subdir_of.get(rel, ""), "concept")
    title = _strip_title_prefix(fm.get("title", "")) or stem.replace("-", " ").title()
    description = fm.get("description") or _first_sentence(body)
    resource = _resource_uri(fm)
    tags: list[str] = []
    for k in ("aliases", "projects"):
        v = fm.get(k)
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            tags.extend(str(x) for x in v)
    timestamp = fm.get("updated") or fm.get("created") or ""

    new_body, citations = _transform_body(
        body, stems_to_path, page_titles, copy_assets, counters)

    same_as = fm.get("same_as")
    if isinstance(same_as, str):
        same_as = [same_as]

    fields = [
        ("type", ce_type),
        ("title", title),
        ("description", description),
        ("resource", resource),
        ("tags", tags),
        ("timestamp", timestamp),
        # x_ce_* extension keys — OKF consumers must preserve unknown keys, so
        # these carry CE-only structure losslessly across a round-trip.
        ("x_ce_type", fm.get("type") or ""),
        ("x_ce_entity_class", fm.get("entity_class") or ""),
        ("x_ce_iri", fm.get("iri") or ""),
        ("x_ce_same_as", same_as or []),
        ("x_ce_citations", citations),
    ]
    table_block = _extract_table_block(text)
    front = _emit_frontmatter(fields, table_block)

    parts = [front, "", new_body.rstrip()]
    if citations:
        parts.append("")
        parts.append("# Citations")
        parts.append("")
        for c in citations:
            # If the cited vault source has an exported stub page, link it;
            # else keep the raw vault marker (spec-legal plain text). The
            # citation names a vault `.extracted.md` file, which maps to a
            # `sources/` stub via that stub's `sources:` frontmatter.
            src_rel = vault_to_stub.get(c)
            if src_rel and not no_sources:
                parts.append(f"- [{page_titles.get(src_rel, c)}](/{src_rel})")
            else:
                parts.append(f"- (vault:{c})")
    return "\n".join(parts).rstrip() + "\n"


def _write_indexes(bundle: Path, concepts: list[tuple], date: str) -> None:
    """Write reserved `index.md` files (per directory + root) and `log.md`.

    `concepts` is a list of (rel, title, description) for every emitted
    concept. Per-directory indexes provide OKF "progressive disclosure"; the
    root index carries the `okf_version` declaration.
    """
    by_dir: dict[str, list[tuple]] = {}
    for rel, title, desc in concepts:
        subdir = str(Path(rel).parent) if str(Path(rel).parent) != "." else ""
        by_dir.setdefault(subdir, []).append((rel, title, desc))

    for subdir in sorted(k for k in by_dir if k):
        lines = [f"# {subdir}", ""]
        for rel, title, desc in sorted(by_dir[subdir]):
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- [{title}](/{rel}){suffix}")
        (bundle / subdir / "index.md").write_text("\n".join(lines) + "\n")

    root_lines = [
        "---", f'okf_version: "{OKF_VERSION}"', "---", "",
        "# Knowledge Bundle", "",
        "Exported from a Curiosity Engine wiki. Each subdirectory groups "
        "concepts of one kind; open a subdirectory's `index.md` for its "
        "listing.", "",
    ]
    for subdir in sorted(k for k in by_dir if k):
        root_lines.append(f"- [{subdir}](/{subdir}/index.md) "
                          f"({len(by_dir[subdir])})")
    root_bare = by_dir.get("", [])
    for rel, title, desc in sorted(root_bare):
        suffix = f" — {desc}" if desc else ""
        root_lines.append(f"- [{title}](/{rel}){suffix}")
    (bundle / "index.md").write_text("\n".join(root_lines) + "\n")

    total = sum(len(v) for v in by_dir.values())
    log_lines = [
        "# Log", "",
        f"## {date}", "",
        f"**Export.** Projected {total} concepts from a Curiosity Engine "
        f"wiki into OKF v{OKF_VERSION}.", "",
    ]
    (bundle / "log.md").write_text("\n".join(log_lines) + "\n")


def cmd_build(
    wiki_dir: Path, output_dir: Path, copy_assets: bool,
    no_sources: bool, date: str,
) -> None:
    pages = _wiki_pages(wiki_dir)
    stems_to_path: dict[str, str] = {}
    subdir_of: dict[str, str] = {}
    page_titles: dict[str, str] = {}
    vault_to_stub: dict[str, str] = {}
    rels: list[str] = []
    for p in pages:
        rel = p.relative_to(wiki_dir).as_posix()
        rels.append(rel)
        stems_to_path[Path(rel).stem.lower()] = rel
        subdir_of[rel] = str(Path(rel).parent) if str(Path(rel).parent) != "." else ""
        fm, _ = read_frontmatter(p.read_text(errors="replace"))
        fm = fm if isinstance(fm, dict) else {}
        page_titles[rel] = _strip_title_prefix(fm.get("title", "")) or Path(rel).stem
        # A source stub represents one or more vault extractions (listed in
        # its `sources:` frontmatter). Map each vault path → the stub so a
        # `(vault:...)` citation can resolve to the exported source page.
        if subdir_of[rel] == "sources":
            srcs = fm.get("sources") or []
            if isinstance(srcs, str):
                srcs = [srcs]
            for s in srcs:
                vault_to_stub[str(s).strip()] = rel

    if no_sources:
        rels = [r for r in rels if subdir_of[r] != "sources"]

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    counters = {"links": 0, "broken_links": 0, "assets": 0}
    manifest_concepts: list[tuple] = []
    for rel in rels:
        text = (wiki_dir / rel).read_text(errors="replace")
        concept = _build_concept(
            rel, text, stems_to_path, page_titles, subdir_of,
            vault_to_stub, copy_assets, no_sources, counters)
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(concept)
        fm, body = read_frontmatter(text)
        desc = (fm.get("description") if isinstance(fm, dict) else "") or _first_sentence(body)
        manifest_concepts.append((rel, page_titles[rel], desc))

    _write_indexes(output_dir, manifest_concepts, date)

    if copy_assets:
        assets_src = wiki_dir / "figures" / "_assets"
        if assets_src.is_dir():
            shutil.copytree(assets_src, output_dir / "figures" / "_assets")

    print(json.dumps({
        "ok": True,
        "workspace": wiki_dir.parent.name,
        "concepts": len(manifest_concepts),
        "links": counters["links"],
        "broken_links": counters["broken_links"],
        "assets": counters["assets"],
        "output": str(output_dir),
    }, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="Export a CE wiki to an OKF bundle.")
    ap.add_argument("command", choices=["build"])
    ap.add_argument("wiki", nargs="?", default="wiki")
    ap.add_argument("--output-dir", default=None,
                    help="override the default ~/.cache/.../okf-export/<workspace>/")
    ap.add_argument("--copy-assets", action="store_true",
                    help="copy figure PNGs and emit real image links")
    ap.add_argument("--no-sources", action="store_true",
                    help="omit source-stub pages (still list citations as vault refs)")
    ap.add_argument("--date", default=None,
                    help="date for the generated log.md heading (default: today, UTC)")
    args = ap.parse_args()

    wiki_dir = Path(args.wiki).resolve()
    if not wiki_dir.is_dir():
        print(json.dumps({"error": f"wiki dir not found: {wiki_dir}"}))
        sys.exit(1)

    output_dir = (Path(args.output_dir).resolve() if args.output_dir
                  else _output_root() / wiki_dir.parent.name)
    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cmd_build(wiki_dir, output_dir, args.copy_assets, args.no_sources, date)


if __name__ == "__main__":
    main()
