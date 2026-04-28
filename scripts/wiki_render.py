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


# Palette — derived from Tableau 10 with a few adjustments so all 10
# canonical types + Unclassified are clearly distinguishable. Single
# source of truth: this dict is emitted into data.json so the front-end
# can read it without a CSS parse; CSS mirrors the values via :root
# vars (manually kept in sync — runtime palette from data.json wins
# at SVG fill time).
PALETTE = {
    "source":       "#4a7ab8",  # steel blue
    "sources":      "#4a7ab8",
    "entity":       "#f28e2b",  # bright orange
    "entities":     "#f28e2b",
    "concept":      "#59a14f",  # green
    "concepts":     "#59a14f",
    "analysis":     "#a76aaa",  # mauve purple
    "analyses":     "#a76aaa",
    "evidence":     "#e8c547",  # golden yellow
    "fact":         "#d44a47",  # crimson red
    "facts":        "#d44a47",
    "table":        "#4ec0c5",  # cyan-teal
    "tables":       "#4ec0c5",
    "figure":       "#e377c2",  # rose pink
    "figures":      "#e377c2",
    "note":         "#9aa0a8",  # silver
    "notes":        "#9aa0a8",
    "todo":         "#c8744a",  # terracotta
    "todo-list":    "#c8744a",
    "unclassified": "#6b7080",  # slate grey — pages whose type doesn't
                                  # match a canonical category yet
    "default":      "#7a7a7a",  # neutral fallback (rarely visible)
}

# Canonical type set. Pages whose frontmatter `type:` isn't here get
# bucketed as `unclassified` in the rendered viewer so they don't
# disappear into the silent `default` grey. The on-disk frontmatter is
# never rewritten — this is render-time normalisation only.
KNOWN_TYPES = frozenset({
    "source", "sources", "entity", "entities",
    "concept", "concepts", "analysis", "analyses",
    "evidence", "fact", "facts", "table", "tables",
    "figure", "figures", "note", "notes",
    "todo", "todo-list",
})


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
# Image embeds: Obsidian transclusion `![[path]]` and standard markdown
# `![alt](path)`. Both rewrite asset paths to bundle-relative form.
_IMG_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# Pipe-table block: a header row, a separator row of dashes (with
# optional `:` alignment markers), and one or more body rows. The
# leading/trailing pipes are optional in CommonMark; we require both
# header and separator to start with `|` to keep parsing tractable.
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)*\s*\|?\s*$")


def _split_table_row(raw: str) -> list[str]:
    """Split a pipe-table row on `|`, stripping the optional leading
    and trailing pipes and whitespace around each cell."""
    s = raw.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _parse_table_alignments(sep: str) -> list[str]:
    """Parse `| :--- | :---: | ---: |` into per-column alignment hints
    (`left` / `center` / `right` / `""`)."""
    aligns = []
    for cell in _split_table_row(sep):
        cell = cell.strip()
        left = cell.startswith(":")
        right = cell.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        elif left:
            aligns.append("left")
        else:
            aligns.append("")
    return aligns


def _normalise_asset_path(path: str) -> str:
    """Map a figure-asset path to its bundle-relative form.

    Cases:
      `figures/_assets/X.png`  → unchanged (already absolute-from-wiki)
      `_assets/X.png`          → `figures/_assets/X.png` (figure-page-relative form)
      `X.png` (no slash)       → `figures/_assets/X.png` (legacy short-form)
      anything else            → unchanged
    """
    path = path.strip()
    if path.startswith("figures/_assets/"):
        return path
    if path.startswith("_assets/"):
        return "figures/" + path
    if "/" not in path:
        return "figures/_assets/" + path
    return path


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

    # Image embeds before wikilinks — `![[X]]` would otherwise look like
    # a wikilink with a `!` character outside it.
    def _img_embed(m):
        target = m.group(1)
        # Obsidian's `![[X|alt]]` syntax — split on pipe if present.
        alt = ""
        if "|" in target:
            target, alt = target.split("|", 1)
        path = _normalise_asset_path(target)
        return f'<img class="wiki-img" src="{_html_escape(path)}" alt="{_html_escape(alt)}">'

    def _md_image(m):
        alt = m.group(1)
        path = _normalise_asset_path(m.group(2))
        return f'<img class="wiki-img" src="{_html_escape(path)}" alt="{_html_escape(alt)}">'

    line = _IMG_EMBED_RE.sub(_img_embed, line)
    line = _MD_IMAGE_RE.sub(_md_image, line)
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


def _render_table(
    header: str, sep: str, body_rows: list[str],
    stems_to_path: dict[str, str],
) -> str:
    """Render a GFM pipe-table block to a `<table>`. Header cells get
    `<th>`, body cells get `<td>`, and per-column `align="…"` attrs
    are emitted when the separator row carries `:` alignment markers."""
    aligns = _parse_table_alignments(sep)
    head_cells = _split_table_row(header)
    # Pad alignments out / truncate so each header cell has one.
    if len(aligns) < len(head_cells):
        aligns = aligns + [""] * (len(head_cells) - len(aligns))

    def _td(tag: str, cell: str, align: str) -> str:
        attr = f' align="{align}"' if align else ""
        return f"<{tag}{attr}>{_render_inline(cell, stems_to_path)}</{tag}>"

    out = ['<table class="md-table">', "<thead><tr>"]
    for i, cell in enumerate(head_cells):
        out.append(_td("th", cell, aligns[i] if i < len(aligns) else ""))
    out.append("</tr></thead>")
    out.append("<tbody>")
    for row in body_rows:
        cells = _split_table_row(row)
        # Pad / truncate cells to the header width so a malformed row
        # doesn't desync column alignment.
        if len(cells) < len(head_cells):
            cells = cells + [""] * (len(head_cells) - len(cells))
        elif len(cells) > len(head_cells):
            cells = cells[:len(head_cells)]
        out.append("<tr>")
        for i, cell in enumerate(cells):
            out.append(_td("td", cell, aligns[i] if i < len(aligns) else ""))
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _render_body(body: str, stems_to_path: dict[str, str]) -> str:
    """Minimal markdown-to-HTML renderer. Block-level: headings, bullet
    lists (one level), code fences, GFM pipe tables, paragraphs.
    Inline: as `_render_inline`. No nested lists, no images-in-prose —
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

    lines = body.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
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
            i += 1
            continue
        if in_code:
            code_buf.append(raw)
            i += 1
            continue

        m = _HEADING_RE.match(raw)
        if m:
            flush_list()
            flush_para()
            level = len(m.group(1))
            text = _render_inline(m.group(2), stems_to_path)
            out.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # Pipe-table detection — header line + separator on the next
        # line. Body rows continue until a non-pipe / blank line.
        # Doing this before the bullet check avoids a `| - |` row from
        # being mistaken for a list item.
        if "|" in raw and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_list()
            flush_para()
            header = raw
            sep = lines[i + 1]
            body_rows: list[str] = []
            j = i + 2
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip() or "|" not in nxt:
                    break
                body_rows.append(nxt)
                j += 1
            out.append(_render_table(header, sep, body_rows, stems_to_path))
            i = j
            continue

        bm = _BULLET_RE.match(raw)
        if bm:
            flush_para()
            list_buf.append(_render_inline(bm.group(2), stems_to_path))
            i += 1
            continue

        if not raw.strip():
            flush_list()
            flush_para()
            i += 1
            continue

        flush_list()
        para_buf.append(_render_inline(raw, stems_to_path))
        i += 1

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
        raw_type = (fm.get("type", "") if isinstance(fm, dict) else "") or ""
        # Normalise unknown / missing types into a single Unclassified
        # bucket so the viewer surfaces them for human review instead
        # of letting them sink into the silent `default` grey.
        ptype = raw_type if raw_type in KNOWN_TYPES else "unclassified"
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

    # Drop any node whose id isn't in page_data (graph drift) and
    # reconcile each surviving node's type/title with the freshly-
    # parsed frontmatter — kuzu can carry stale values from a build
    # done before the file was re-seeded, and on-disk wins.
    page_ids = set(page_data.keys())
    nodes = [n for n in nodes if n["id"] in page_ids]
    for n in nodes:
        fresh = page_data.get(n["id"])
        if fresh:
            n["type"] = fresh["type"]
            n["title"] = fresh["title"]
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

    # Mirror figure assets so the modal's <img> tags resolve. Folder
    # is small (gitignored PNGs) and copies are cheap; copy every time
    # so removed/renamed assets don't linger in the bundle.
    assets_src = wiki_dir / "figures" / "_assets"
    assets_dst = output_dir / "figures" / "_assets"
    if assets_dst.exists():
        shutil.rmtree(assets_dst)
    if assets_src.is_dir():
        shutil.copytree(assets_src, assets_dst)

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
