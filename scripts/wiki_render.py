#!/usr/bin/env python3
"""wiki_render.py — build the static-site bundle for the custom wiki viewer.

Walks `wiki/`, renders each page's markdown body to HTML, resolves
`[[wikilinks]]` against the on-disk page set, and queries the kuzu
graph for nodes + edges. Writes a single `data.json` plus the static
HTML/CSS/JS shell into a workspace-scoped output dir under
`~/.cache/curiosity-engine/wiki-view/<workspace>/`.

Usage:
    wiki_render.py build <wiki_dir> [--output-dir DIR]
    wiki_render.py palette                # print the colour palette as JSON

The build is deterministic — same inputs produce the same output bytes
(modulo the `generated_at` timestamp). Re-run after every wiki edit;
viewer.sh's `serve` subcommand is idempotent against a stale cache.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from naming import (  # noqa: E402
    SKIP_FILES,
    WIKILINK_RE,
    read_frontmatter,
)


# Palette B — modern saturated. Single source of truth: this dict is
# emitted into data.json so the front-end can read it without a CSS
# parse, and CSS keeps the same values via :root vars (kept in sync
# manually; if they drift, the front-end's runtime palette wins).
PALETTE = {
    "sources":   "#6b8be8",  # electric blue
    "entities":  "#e8a06b",  # amber
    "concepts":  "#6be8b3",  # mint
    "concept":   "#6be8b3",  # alias used by hub pages
    "analyses":  "#e86b9b",  # pink
    "evidence":  "#e8d96b",  # yellow
    "facts":     "#b36be8",  # violet
    "tables":    "#6be8e8",  # cyan
    "figures":   "#e86b6b",  # coral
    "notes":     "#909090",  # silver
    "todo-list": "#ffae42",  # amber accent
    "default":   "#7a7a7a",  # neutral fallback
}


def _output_root() -> Path:
    return Path.home() / ".cache" / "curiosity-engine" / "wiki-view"


def _wiki_pages(wiki_dir: Path) -> list[Path]:
    return [p for p in sorted(wiki_dir.rglob("*.md"))
            if p.name not in SKIP_FILES and "_suspect" not in p.parts]


_FENCE_RE = re.compile(r"^```")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITAL_RE = re.compile(r"(?<![*])\*([^*\n]+)\*(?![*])")
_CODE_RE = re.compile(r"`([^`]+)`")
_CITATION_RE = re.compile(r"\(vault:([^)]+)\)")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.+)$")


def _render_inline(line: str, stems_to_path: dict[str, str]) -> str:
    """Render inline markdown: bold, italic, code, wikilinks, citations.

    Order matters: code spans first (they swallow other syntax inside),
    then wikilinks (so the [[X|Y]] form survives bold/italic that might
    contain pipes), then bold/italic, then citations.
    """
    # Wikilinks: replace before HTML-escaping so we control the anchor.
    def _wikilink(m):
        inner = m.group(1)
        if "|" in inner:
            target, display = inner.split("|", 1)
        else:
            target, display = inner, inner
        target = target.strip()
        display = display.strip()
        target_lc = target.lower().replace(" ", "-")
        path = stems_to_path.get(target_lc)
        if path:
            page_id = path[:-3] if path.endswith(".md") else path  # strip .md
            return f'<a class="wikilink" data-page="{_html_escape(page_id)}" href="#page={_html_escape(page_id)}">{_html_escape(display)}</a>'
        return f'<a class="wikilink unresolved">{_html_escape(display)}</a>'

    # Tokenise inline code first to protect contents from other passes.
    # Use a placeholder so subsequent regexes don't see backticked text.
    code_spans: list[str] = []

    def _code_stash(m):
        code_spans.append(m.group(1))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    line = _CODE_RE.sub(_code_stash, line)
    line = WIKILINK_RE.sub(_wikilink, line)
    # Citations to vault sources — render as muted superscript-style link.
    def _cite(m):
        return f'<span class="cite">[{_html_escape(m.group(1))}]</span>'
    line = _CITATION_RE.sub(_cite, line)
    line = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", line)
    line = _ITAL_RE.sub(lambda m: f"<em>{m.group(1)}</em>", line)
    # Restore code spans.
    for i, code in enumerate(code_spans):
        line = line.replace(f"\x00CODE{i}\x00", f"<code>{_html_escape(code)}</code>")
    return line


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _render_body(body: str, stems_to_path: dict[str, str]) -> str:
    """Minimal markdown-to-HTML renderer. Block-level: headings, bullet
    lists (one level), code fences, paragraphs. Inline: as
    `_render_inline`. No tables, no nested lists, no images-in-prose —
    we ship a small subset on purpose; deeper formatting goes through
    Obsidian or VS Code preview, not the static viewer.
    """
    out: list[str] = []
    in_code = False
    code_lang = ""
    code_buf: list[str] = []
    list_buf: list[str] = []
    para_buf: list[str] = []

    def flush_list():
        if list_buf:
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in list_buf) + "</ul>")
            list_buf.clear()

    def flush_para():
        if para_buf:
            out.append("<p>" + " ".join(para_buf) + "</p>")
            para_buf.clear()

    for raw in body.split("\n"):
        if _FENCE_RE.match(raw):
            if in_code:
                out.append(f'<pre><code class="lang-{_html_escape(code_lang)}">'
                           + "\n".join(_html_escape(c) for c in code_buf)
                           + "</code></pre>")
                code_buf = []
                code_lang = ""
                in_code = False
            else:
                flush_list()
                flush_para()
                in_code = True
                code_lang = raw[3:].strip()
            continue
        if in_code:
            code_buf.append(raw)
            continue

        m = _HEADING_RE.match(raw)
        if m:
            flush_list()
            flush_para()
            level = len(m.group(1))
            text = _render_inline(m.group(2), stems_to_path)
            out.append(f"<h{level}>{text}</h{level}>")
            continue

        bm = _BULLET_RE.match(raw)
        if bm:
            flush_para()
            list_buf.append(_render_inline(bm.group(2), stems_to_path))
            continue

        if not raw.strip():
            flush_list()
            flush_para()
            continue

        flush_list()
        para_buf.append(_render_inline(raw, stems_to_path))

    flush_list()
    flush_para()
    if in_code:
        out.append("<pre><code>" + "\n".join(_html_escape(c) for c in code_buf) + "</code></pre>")
    return "\n".join(out)


def _build_graph(wiki_dir: Path, page_paths: set[str]) -> tuple[list[dict], list[dict]]:
    """Read kuzu graph and return (nodes, edges) restricted to WikiPage
    nodes whose path is in `page_paths` (i.e. survives any out-of-sync
    state between graph and disk). Falls back to a wiki-only graph (no
    edges) if kuzu isn't available or the db is missing.
    """
    kuzu_path = wiki_dir.parent / ".curator" / "graph.kuzu"
    nodes: list[dict] = []
    edges: list[dict] = []
    degree: dict[str, int] = {}

    try:
        import kuzu  # type: ignore
        if not kuzu_path.exists():
            raise FileNotFoundError("graph.kuzu missing")
        db = kuzu.Database(str(kuzu_path), read_only=True)
        conn = kuzu.Connection(db)
        rows = conn.execute("MATCH (p:WikiPage) RETURN p.path, p.type, p.title")
        while rows.has_next():
            path, ptype, title = rows.get_next()
            if path not in page_paths:
                continue
            nodes.append({
                "id": path[:-3] if path.endswith(".md") else path,
                "path": path,
                "type": ptype or "default",
                "title": title or path,
            })
        for rel_table, edge_kind in [("WikiLink", "wikilink"), ("Depicts", "depicts")]:
            rs = conn.execute(
                f"MATCH (a:WikiPage)-[:{rel_table}]->(b:WikiPage) RETURN a.path, b.path"
            )
            while rs.has_next():
                src, dst = rs.get_next()
                if src not in page_paths or dst not in page_paths:
                    continue
                src_id = src[:-3] if src.endswith(".md") else src
                dst_id = dst[:-3] if dst.endswith(".md") else dst
                edges.append({"source": src_id, "target": dst_id, "type": edge_kind})
                degree[src_id] = degree.get(src_id, 0) + 1
                degree[dst_id] = degree.get(dst_id, 0) + 1
    except Exception as e:
        print(f"  warn: graph query failed ({e}); rendering nodes-only view",
              file=sys.stderr)
        for path in sorted(page_paths):
            page_id = path[:-3] if path.endswith(".md") else path
            nodes.append({
                "id": page_id,
                "path": path,
                "type": "default",
                "title": path,
            })

    for n in nodes:
        n["degree"] = degree.get(n["id"], 0)

    return nodes, edges


def cmd_build(wiki_dir: Path, output_dir: Path) -> None:
    pages = _wiki_pages(wiki_dir)
    page_paths: set[str] = set()
    page_data: dict[str, dict[str, Any]] = {}
    stems_to_path: dict[str, str] = {}

    for p in pages:
        rel = str(p.relative_to(wiki_dir))
        page_paths.add(rel)
        stems_to_path[Path(rel).stem.lower()] = rel

    for p in pages:
        rel = str(p.relative_to(wiki_dir))
        text = p.read_text(errors="replace")
        fm, body = read_frontmatter(text)
        title = fm.get("title", Path(rel).stem) if isinstance(fm, dict) else Path(rel).stem
        ptype = fm.get("type", "default") if isinstance(fm, dict) else "default"
        page_id = rel[:-3] if rel.endswith(".md") else rel
        # Frontmatter property table for the modal header. Skip keys
        # already shown elsewhere or that aren't user-meaningful.
        properties = {}
        if isinstance(fm, dict):
            for k, v in fm.items():
                if k in ("title", "type"):
                    continue
                if v is None:
                    continue
                properties[k] = v
        page_data[page_id] = {
            "id": page_id,
            "title": title,
            "type": ptype,
            "path": rel,
            "properties": properties,
            "body_html": _render_body(body, stems_to_path),
        }

    nodes, edges = _build_graph(wiki_dir, page_paths)

    # Drop any node whose id isn't in page_data (graph drift). Drop
    # any edge whose endpoint isn't in the surviving node set.
    page_ids = set(page_data.keys())
    nodes = [n for n in nodes if n["id"] in page_ids]
    surviving = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in surviving and e["target"] in surviving]

    workspace_name = wiki_dir.parent.name

    data = {
        "workspace": workspace_name,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "palette": PALETTE,
        "nodes": nodes,
        "edges": edges,
        "pages": page_data,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "data.json").write_text(json.dumps(data, separators=(",", ":")))

    # Copy the static shell from the skill template tree.
    static_src = Path(__file__).resolve().parent.parent / "template" / "wiki-view"
    if static_src.is_dir():
        for src in static_src.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(static_src)
            dst = output_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            content = src.read_bytes()
            if src.name == "index.html":
                # Substitute workspace name into the title bar.
                content = content.replace(
                    b"{{WORKSPACE}}",
                    workspace_name.encode("utf-8"),
                )
            dst.write_bytes(content)

    print(json.dumps({
        "ok": True,
        "workspace": workspace_name,
        "output": str(output_dir),
        "pages": len(page_data),
        "nodes": len(nodes),
        "edges": len(edges),
    }, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["build", "palette"])
    ap.add_argument("wiki", nargs="?", default="wiki")
    ap.add_argument("--output-dir", type=str, default=None,
                    help="override the default ~/.cache/.../<workspace>/ path")
    args = ap.parse_args()

    if args.command == "palette":
        print(json.dumps(PALETTE, indent=2))
        return

    wiki_dir = Path(args.wiki).resolve()
    if not wiki_dir.is_dir():
        print(json.dumps({"error": f"wiki dir not found: {wiki_dir}"}))
        sys.exit(1)

    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = _output_root() / wiki_dir.parent.name

    cmd_build(wiki_dir, output_dir)


if __name__ == "__main__":
    main()
