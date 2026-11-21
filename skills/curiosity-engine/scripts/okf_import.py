#!/usr/bin/env python3
"""Import an OKF bundle or a curiosity-engine wiki without merging it.

Two destinations, both outside the curated wiki:

* ``cache`` — an isolated wiki (its own ``wiki/`` and ``.curator/graph-id``)
  that a mount or ``graph_serve.py`` can read. Nothing is copied into the
  caller's ``wiki/``.
* ``vault`` — one verbatim, untrusted source file per concept, under
  ``vault/ext/okf/``. The citation ratchet still decides what becomes a
  wiki page. This is the import rule described in SKILL.md. The changelog
  once pointed at ``docs/okf-interop.md`` for that rule; that file is not
  in the tree.

A bundle is either a directory of markdown concepts (OKF) or a workspace
with a ``wiki/`` directory.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from graph_id import page_id, write_graph_id

_BEGIN = "<!-- BEGIN FETCHED CONTENT okf-import -->"
_END = "<!-- END FETCHED CONTENT -->"


def _concepts(bundle: Path) -> list[tuple[str, Path]]:
    """Relative posix path (no .md) and file, for every concept."""
    bundle = bundle.resolve()
    wiki = bundle / "wiki" if (bundle / "wiki").is_dir() else bundle
    out: list[tuple[str, Path]] = []
    if not wiki.is_dir():
        return out
    for path in sorted(wiki.rglob("*.md")):
        if any(part in {".git", ".curator", "_assets"} for part in path.parts):
            continue
        rel = path.relative_to(wiki).as_posix()
        if rel.endswith("/index.md") or rel == "index.md" or rel == "log.md":
            continue
        stem = rel[:-3] if rel.endswith(".md") else rel
        out.append((stem, path))
    return out


def _frame(text: str, *, source_url: str, sha: str) -> str:
    body = text.strip() + "\n"
    fm = [
        "---",
        "type: source",
        "untrusted: true",
        f"source_url: {source_url}",
        f"sha256: {sha}",
        "source_type: okf",
        "---",
        "",
        _BEGIN,
        body.rstrip(),
        _END,
        "",
    ]
    return "\n".join(fm)


def import_cache(bundle: Path, cache: Path, graph_id: str) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    wiki = cache / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    copied = []
    for rel, src in _concepts(bundle):
        dest = wiki / f"{rel}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        copied.append(rel)
    write_graph_id(cache, graph_id)
    manifest = cache / ".curator" / "page-ids.txt"
    lines = [page_id(graph_id, rel) for rel in copied]
    manifest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return {"graph_id": graph_id, "pages": copied, "cache": str(cache)}


def import_vault(bundle: Path, workspace: Path, graph_id: str) -> dict:
    dest_root = workspace / "vault" / "ext" / "okf"
    dest_root.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, src in _concepts(bundle):
        raw = src.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        safe = rel.replace("/", "__")
        dest = dest_root / f"{safe}.md"
        url = f"{graph_id}#{rel}" if graph_id else f"okf://{rel}"
        dest.write_text(_frame(raw, source_url=url, sha=sha), encoding="utf-8")
        written.append(dest.relative_to(workspace).as_posix())
    return {"graph_id": graph_id, "vault": written}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="okf_import.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cache = sub.add_parser("cache", help="materialise an isolated wiki cache")
    cache.add_argument("bundle", type=Path)
    cache.add_argument("--cache", type=Path, required=True)
    cache.add_argument("--graph-id", required=True)

    vault = sub.add_parser("vault", help="land concepts as untrusted vault sources")
    vault.add_argument("bundle", type=Path)
    vault.add_argument("--into", type=Path, required=True)
    vault.add_argument("--graph-id", default="")

    args = parser.parse_args(argv)
    if not args.bundle.is_dir():
        print(f"not a directory: {args.bundle}", file=sys.stderr)
        return 2
    if args.cmd == "cache":
        result = import_cache(args.bundle, args.cache, args.graph_id)
    else:
        result = import_vault(args.bundle, args.into, args.graph_id)
    print(f"pages: {len(result.get('pages') or result.get('vault') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
