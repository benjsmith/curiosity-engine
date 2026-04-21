#!/usr/bin/env python3
"""figures.py — figure extraction, integrity, and regeneration.

Figures are wiki pages (`wiki/figures/<stem>.md`) that wrap a binary
asset (`assets/figures/<asset>`) with provenance + cross-references.
Assets are NOT git-tracked — they are regenerated deterministically
from their source (a PDF under `vault/`) via the metadata in the
figure page's frontmatter.

Subcommands:
  extract <source_path> --page N [--asset NAME] [--dpi 150]
      Render page N of a PDF to assets/figures/<asset>. Idempotent —
      no-op when the asset already exists. Asset defaults to
      `<source-stem>-p<N>.png` so multiple figures sharing a page
      share the same asset file.

  check <wiki_dir>
      For each wiki/figures/*.md, verify its asset exists. JSON
      report with {ok, missing_extracted, missing_created}.

  regen <wiki_dir>
      Run check; for each extracted figure whose asset is missing,
      re-run the extraction. Created figures (origin: created) are
      listed — they cannot be auto-regenerated from the source alone.

  list <wiki_dir>
      Enumerate figure pages with their asset reference and origin.

  mark-extracted <extraction_md_path> [--timestamp ISO]
      Write `figures_extracted: <ISO>` into the extraction's
      frontmatter. Idempotent — overwrites an existing field. Called
      by the CURATE orchestrator at the end of the figure-extraction
      pass (regardless of whether any figures were produced).

  pages <source_path>
      Report page count and per-page rendered-asset names for a
      PDF. Used by the CURATE orchestrator to decide how many pages
      to feed into a multimodal figure-extractor worker.

  render-all <source_path> --wiki <wiki_dir> [--dpi 150]
      Render every page of a PDF into assets/figures/<stem>-pN.png,
      one per page. Idempotent — existing PNGs are skipped. Used as
      the pre-processing step before dispatching a figure-extractor
      worker so all pages are available as PNGs.

All subcommands emit one JSON line on stdout. Exit code reflects
whether the requested work succeeded (0 = ok; 1 = argument/format
error; 2 = external tool failure, e.g. pypdfium2 missing).

Hash-guarded by evolve_guard.sh.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import read_frontmatter  # noqa: E402


DEFAULT_DPI = 150
ASSET_SUBDIR = "figures"
FIGURE_SUBDIR = "figures"


def _workspace_root(wiki_dir: Path) -> Path:
    """Given a wiki/ path, return its parent workspace root.

    Used to resolve assets/ and vault/ siblings of the wiki.
    """
    return wiki_dir.resolve().parent


def _assets_dir(wiki_dir: Path) -> Path:
    return _workspace_root(wiki_dir) / "assets" / ASSET_SUBDIR


def _default_asset_name(source_path: Path, page: int) -> str:
    """`<source-stem>-p<N>.png` — shared across figures on the same page."""
    return f"{source_path.stem}-p{page}.png"


def _render_pdf_page(pdf_path: Path, page: int, out_png: Path,
                       dpi: int) -> None:
    """Render a single PDF page as PNG at the given DPI.

    Raises RuntimeError if pypdfium2 isn't installed or the page is
    out of range. The caller is responsible for ensuring the parent
    directory of out_png exists.
    """
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pypdfium2 not installed — run `uv pip install pypdfium2` "
            "(or re-run setup.sh which installs it automatically)"
        ) from exc

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        n = len(pdf)
        if page < 1 or page > n:
            raise RuntimeError(
                f"page {page} out of range for {pdf_path} (has {n} pages)"
            )
        # pypdfium2 uses 0-indexed pages internally; our user-facing
        # numbering is 1-indexed to match PDF reader conventions.
        pdf_page = pdf[page - 1]
        # DPI → scale factor: pypdfium2 renders at 72 DPI by default
        # when scale=1.0, so scale = dpi / 72.
        scale = dpi / 72.0
        bitmap = pdf_page.render(scale=scale)
        pil_image = bitmap.to_pil()
        pil_image.save(str(out_png), format="PNG")
    finally:
        pdf.close()


def _pdf_page_count(pdf_path: Path) -> int:
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pypdfium2 not installed — run `uv pip install pypdfium2`"
        ) from exc
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()


def cmd_pages(args) -> int:
    source = Path(args.source).resolve()
    if not source.exists():
        print(json.dumps({"ok": False, "error": f"source not found: {source}"}))
        return 1
    if source.suffix.lower() != ".pdf":
        print(json.dumps({"ok": False, "error": f"only .pdf sources supported: {source}"}))
        return 1
    try:
        n = _pdf_page_count(source)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    pages = [{"page": i, "asset": _default_asset_name(source, i)}
             for i in range(1, n + 1)]
    print(json.dumps({"ok": True, "source": str(source),
                       "page_count": n, "pages": pages}))
    return 0


def cmd_render_all(args) -> int:
    source = Path(args.source).resolve()
    if not source.exists():
        print(json.dumps({"ok": False, "error": f"source not found: {source}"}))
        return 1
    if source.suffix.lower() != ".pdf":
        print(json.dumps({"ok": False, "error": f"only .pdf sources supported: {source}"}))
        return 1

    wiki_dir = Path(args.wiki).resolve() if args.wiki else None
    assets_dir = _assets_dir(wiki_dir) if wiki_dir else Path(args.assets_dir).resolve()
    assets_dir.mkdir(parents=True, exist_ok=True)

    try:
        n = _pdf_page_count(source)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    rendered, skipped, failed = [], [], []
    for page in range(1, n + 1):
        asset_name = _default_asset_name(source, page)
        out_png = assets_dir / asset_name
        if out_png.exists() and not args.force:
            skipped.append(asset_name)
            continue
        try:
            _render_pdf_page(source, page, out_png, args.dpi)
            rendered.append(asset_name)
        except RuntimeError as exc:
            failed.append({"page": page, "error": str(exc)})

    pages_out = [{"page": i,
                   "asset": _default_asset_name(source, i),
                   "asset_path": str(assets_dir / _default_asset_name(source, i))}
                  for i in range(1, n + 1)]
    result = {
        "ok": not failed,
        "source": str(source),
        "page_count": n,
        "count_rendered": len(rendered),
        "count_skipped": len(skipped),
        "count_failed": len(failed),
        "rendered": rendered,
        "skipped": skipped,
        "failed": failed,
        "pages": pages_out,
    }
    print(json.dumps(result))
    return 0 if result["ok"] else 2


def cmd_extract(args) -> int:
    source = Path(args.source).resolve()
    if not source.exists():
        print(json.dumps({"ok": False,
                          "error": f"source not found: {source}"}))
        return 1
    if source.suffix.lower() != ".pdf":
        print(json.dumps({"ok": False,
                          "error": f"only .pdf sources supported: {source}"}))
        return 1

    wiki_dir = Path(args.wiki).resolve() if args.wiki else None
    assets_dir = _assets_dir(wiki_dir) if wiki_dir else Path(args.assets_dir).resolve()
    assets_dir.mkdir(parents=True, exist_ok=True)

    asset_name = args.asset or _default_asset_name(source, args.page)
    out_png = assets_dir / asset_name

    if out_png.exists() and not args.force:
        print(json.dumps({
            "ok": True,
            "asset": asset_name,
            "asset_path": str(out_png),
            "rendered": False,
            "note": "already exists (use --force to re-render)",
        }))
        return 0

    try:
        _render_pdf_page(source, args.page, out_png, args.dpi)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    print(json.dumps({
        "ok": True,
        "asset": asset_name,
        "asset_path": str(out_png),
        "rendered": True,
        "dpi": args.dpi,
        "source_page": args.page,
    }))
    return 0


def _iter_figure_pages(wiki_dir: Path):
    """Yield (path, frontmatter) for each wiki/figures/*.md."""
    fig_dir = wiki_dir / FIGURE_SUBDIR
    if not fig_dir.is_dir():
        return
    for md in sorted(fig_dir.glob("*.md")):
        try:
            fm, _ = read_frontmatter(md.read_text())
        except Exception:
            fm = {}
        if fm.get("type") != "figure":
            continue
        yield md, fm


def cmd_check(args) -> int:
    wiki_dir = Path(args.wiki).resolve()
    assets_dir = _assets_dir(wiki_dir)
    ok, missing_extracted, missing_created = [], [], []
    for md, fm in _iter_figure_pages(wiki_dir):
        asset = fm.get("asset", "")
        origin = fm.get("origin", "")
        if not asset:
            missing_extracted.append({
                "page": str(md.relative_to(wiki_dir.parent)),
                "reason": "no asset field in frontmatter",
            })
            continue
        asset_path = assets_dir / asset
        if asset_path.exists():
            ok.append(str(md.relative_to(wiki_dir.parent)))
            continue
        entry = {
            "page": str(md.relative_to(wiki_dir.parent)),
            "asset": asset,
            "source_path": fm.get("source_path", ""),
            "source_page": fm.get("source_page", ""),
            "extraction_method": fm.get("extraction_method", ""),
            "source_analysis": fm.get("source_analysis", ""),
        }
        if origin == "created":
            missing_created.append(entry)
        else:
            missing_extracted.append(entry)

    result = {
        "ok": len(missing_extracted) == 0 and len(missing_created) == 0,
        "count_ok": len(ok),
        "count_missing_extracted": len(missing_extracted),
        "count_missing_created": len(missing_created),
        "missing_extracted": missing_extracted,
        "missing_created": missing_created,
    }
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def cmd_regen(args) -> int:
    wiki_dir = Path(args.wiki).resolve()
    assets_dir = _assets_dir(wiki_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    workspace = _workspace_root(wiki_dir)

    regenerated, skipped, failed, created_missing = [], [], [], []

    for md, fm in _iter_figure_pages(wiki_dir):
        asset = fm.get("asset", "")
        origin = fm.get("origin", "")
        if not asset:
            failed.append({"page": str(md.relative_to(workspace)),
                            "reason": "no asset field"})
            continue
        asset_path = assets_dir / asset
        if asset_path.exists():
            skipped.append(asset)
            continue
        if origin == "created":
            created_missing.append({
                "page": str(md.relative_to(workspace)),
                "asset": asset,
                "source_analysis": fm.get("source_analysis", ""),
            })
            continue
        method = fm.get("extraction_method", "pdf_page_render")
        source_rel = fm.get("source_path", "")
        source_page_raw = fm.get("source_page", "")
        if method != "pdf_page_render":
            failed.append({"page": str(md.relative_to(workspace)),
                            "reason": f"unsupported extraction_method: {method}"})
            continue
        if not source_rel:
            failed.append({"page": str(md.relative_to(workspace)),
                            "reason": "no source_path in frontmatter"})
            continue
        try:
            source_page = int(str(source_page_raw))
        except (TypeError, ValueError):
            failed.append({"page": str(md.relative_to(workspace)),
                            "reason": f"invalid source_page: {source_page_raw!r}"})
            continue
        source_abs = (workspace / source_rel).resolve()
        if not source_abs.exists():
            failed.append({"page": str(md.relative_to(workspace)),
                            "reason": f"source not found: {source_rel}"})
            continue
        try:
            _render_pdf_page(source_abs, source_page, asset_path, args.dpi)
        except RuntimeError as exc:
            failed.append({"page": str(md.relative_to(workspace)),
                            "reason": str(exc)})
            continue
        regenerated.append(asset)

    result = {
        "ok": len(failed) == 0 and len(created_missing) == 0,
        "count_regenerated": len(regenerated),
        "count_skipped": len(skipped),
        "count_failed": len(failed),
        "count_created_missing": len(created_missing),
        "regenerated": regenerated,
        "failed": failed,
        "created_missing": created_missing,
    }
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def cmd_mark_extracted(args) -> int:
    """Write figures_extracted: <ISO> into the extraction's frontmatter.

    Operates on the outer frontmatter block (the provenance block
    written by local_ingest.py). Preserves the inner FETCHED CONTENT
    block verbatim. Fails loudly on files without a leading --- fence.
    """
    import datetime
    path = Path(args.path).resolve()
    if not path.exists() or not path.is_file():
        print(json.dumps({"ok": False, "error": f"file not found: {path}"}))
        return 1
    text = path.read_text()
    if not text.startswith("---"):
        print(json.dumps({"ok": False,
                          "error": f"no frontmatter in {path}"}))
        return 1
    end = text.find("\n---", 3)
    if end == -1:
        print(json.dumps({"ok": False,
                          "error": f"unterminated frontmatter in {path}"}))
        return 1
    fm_block = text[3:end].strip()
    body = text[end + 4:]
    ts = args.timestamp or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = fm_block.split("\n") if fm_block else []
    found = False
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if stripped.startswith("figures_extracted:"):
            indent = ln[: len(ln) - len(stripped)]
            lines[i] = f"{indent}figures_extracted: {ts}"
            found = True
            break
    if not found:
        lines.append(f"figures_extracted: {ts}")
    new_fm = "\n".join(lines)
    new_text = f"---\n{new_fm}\n---{body}"
    path.write_text(new_text)
    print(json.dumps({"ok": True, "path": str(path),
                        "figures_extracted": ts,
                        "updated": found, "added": not found}))
    return 0


def cmd_list(args) -> int:
    wiki_dir = Path(args.wiki).resolve()
    assets_dir = _assets_dir(wiki_dir)
    workspace = _workspace_root(wiki_dir)
    rows = []
    for md, fm in _iter_figure_pages(wiki_dir):
        asset = fm.get("asset", "")
        asset_exists = bool(asset) and (assets_dir / asset).exists()
        rows.append({
            "page": str(md.relative_to(workspace)),
            "origin": fm.get("origin", ""),
            "asset": asset,
            "asset_exists": asset_exists,
            "source_path": fm.get("source_path", ""),
            "source_page": fm.get("source_page", ""),
            "relates_to": fm.get("relates_to", []),
        })
    print(json.dumps({"ok": True, "count": len(rows), "figures": rows}))
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_extract = sub.add_parser("extract", help="render a PDF page as PNG")
    ap_extract.add_argument("source", help="path to source PDF")
    ap_extract.add_argument("--page", type=int, required=True,
                             help="1-indexed page number")
    ap_extract.add_argument("--asset", default=None,
                             help="asset filename (default: <stem>-p<N>.png)")
    ap_extract.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap_extract.add_argument("--wiki", default=None,
                             help="wiki dir; asset goes into its sibling assets/figures/")
    ap_extract.add_argument("--assets-dir", default=None,
                             help="override assets dir (if --wiki not given)")
    ap_extract.add_argument("--force", action="store_true",
                             help="re-render even if asset already exists")
    ap_extract.set_defaults(func=cmd_extract)

    ap_check = sub.add_parser("check", help="verify all figure assets exist")
    ap_check.add_argument("wiki", help="path to wiki directory")
    ap_check.set_defaults(func=cmd_check)

    ap_regen = sub.add_parser("regen",
                                help="regenerate missing extracted assets")
    ap_regen.add_argument("wiki", help="path to wiki directory")
    ap_regen.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap_regen.set_defaults(func=cmd_regen)

    ap_list = sub.add_parser("list", help="enumerate figure pages")
    ap_list.add_argument("wiki", help="path to wiki directory")
    ap_list.set_defaults(func=cmd_list)

    ap_mark = sub.add_parser("mark-extracted",
                                help="write figures_extracted: <ISO> to an extraction's frontmatter")
    ap_mark.add_argument("path", help="path to the .extracted.md file")
    ap_mark.add_argument("--timestamp", default=None,
                          help="override timestamp (default: now UTC)")
    ap_mark.set_defaults(func=cmd_mark_extracted)

    ap_pages = sub.add_parser("pages", help="report page count for a PDF")
    ap_pages.add_argument("source", help="path to source PDF")
    ap_pages.set_defaults(func=cmd_pages)

    ap_render_all = sub.add_parser("render-all",
                                      help="render every page of a PDF")
    ap_render_all.add_argument("source", help="path to source PDF")
    ap_render_all.add_argument("--wiki", default=None,
                                help="wiki dir; assets go in sibling assets/figures/")
    ap_render_all.add_argument("--assets-dir", default=None,
                                help="override assets dir (if --wiki not given)")
    ap_render_all.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap_render_all.add_argument("--force", action="store_true",
                                help="re-render even if page asset already exists")
    ap_render_all.set_defaults(func=cmd_render_all)

    args = ap.parse_args(argv)
    if args.cmd == "extract" and not args.wiki and not args.assets_dir:
        print(json.dumps({"ok": False,
                          "error": "extract needs --wiki or --assets-dir"}))
        return 1
    if args.cmd == "render-all" and not args.wiki and not args.assets_dir:
        print(json.dumps({"ok": False,
                          "error": "render-all needs --wiki or --assets-dir"}))
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
