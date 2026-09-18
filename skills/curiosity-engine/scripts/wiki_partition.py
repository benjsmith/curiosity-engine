"""wiki_partition.py — CE-owned workspace partition (split) spike.

Charter: CE owns page move/copy + wiki partition. Switchbay may still own
workspace registry / tab chrome after a split.

This module is intentionally self-contained (no curiosity-merge dependency):
  1. Resolve page refs → wiki/*.md
  2. Copy selected pages (+ transitive vault: citations + wiki-relative figures)
     into a NEW target workspace outside the source tree
  3. Write `.curator/splits/` manifests on BOTH sides
  4. MOVE policy: relocate source pages under wiki/.deleted/<stamp>/ (recoverable)

Shells call POST /api/split on the CE viewer; they register the new workspace
themselves. Feature-flag dual-stack until umbrella parity checklist is green.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable

from naming import CITATION_RE

Progress = Callable[[str], None]

_IMG_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class PartitionError(Exception):
    """User-visible partition failure."""


def sanitize_name(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("-", (name or "").strip()).strip("-.")
    return cleaned


def _wiki_pages(wiki_dir: Path) -> list[Path]:
    if not wiki_dir.is_dir():
        return []
    return [
        p
        for p in wiki_dir.rglob("*.md")
        if not any(part.startswith(".") for part in p.relative_to(wiki_dir).parts)
        and "_suspect" not in p.parts
    ]


def match_page_ref(ref: str, all_pages: list[Path], wiki_dir: Path) -> Path | None:
    """Resolve bare stem or wiki-relative path (optional .md) case-insensitively."""
    target = ref.strip().lower().replace(" ", "-")
    if target.endswith(".md"):
        target = target[: -len(".md")]
    if not target or ".." in Path(target).parts:
        return None
    for p in all_pages:
        rel = str(p.relative_to(wiki_dir)).lower().replace(".md", "")
        if p.stem.lower() == target or rel == target:
            return p
    return None


def validate_refs(workspace: Path, refs: list[str]) -> list[str]:
    """Return refs that do not resolve to a wiki page."""
    wiki = workspace / "wiki"
    pages = _wiki_pages(wiki)
    return [r for r in refs if match_page_ref(r, pages, wiki) is None]


def resolve_pages(workspace: Path, refs: list[str]) -> list[Path]:
    wiki = workspace / "wiki"
    pages = _wiki_pages(wiki)
    out: list[Path] = []
    seen: set[Path] = set()
    for ref in refs:
        hit = match_page_ref(ref, pages, wiki)
        if hit is None:
            raise PartitionError(f"not a wiki page: {ref}")
        if hit not in seen:
            out.append(hit)
            seen.add(hit)
    return out


def _safe_target(workspace: Path, target: Path) -> Path:
    ws = workspace.resolve()
    dest = target.expanduser().resolve()
    if dest.exists():
        raise PartitionError(f"target already exists: {dest}")
    try:
        dest.relative_to(ws)
        raise PartitionError(f"refusing to write partition inside workspace: {dest}")
    except ValueError:
        pass
    if dest == ws:
        raise PartitionError("target cannot be the source workspace")
    return dest


def _collect_vault(pages: list[Path], vault_dir: Path) -> list[Path]:
    if not vault_dir.is_dir():
        return []
    cited: set[str] = set()
    for p in pages:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in CITATION_RE.finditer(text):
            cited.add(m.group(1).strip())
    out: list[Path] = []
    seen: set[Path] = set()
    root = vault_dir.resolve()
    for rel in sorted(cited):
        if not rel or ".." in Path(rel).parts or Path(rel).is_absolute():
            continue
        candidate = (vault_dir / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and candidate not in seen:
            out.append(candidate)
            seen.add(candidate)
    return out


def _normalise_figure_path(path: str) -> str:
    path = path.strip().split("|", 1)[0].strip()
    if path.startswith("figures/_assets/"):
        return path
    if path.startswith("_assets/"):
        return "figures/" + path
    if "/" not in path:
        return "figures/_assets/" + path
    return path


def _collect_figures(pages: list[Path], wiki_dir: Path) -> list[Path]:
    refs: set[str] = set()
    for p in pages:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _IMG_EMBED_RE.finditer(text):
            refs.add(_normalise_figure_path(m.group(1)))
        for m in _MD_IMAGE_RE.finditer(text):
            refs.add(_normalise_figure_path(m.group(1)))
    out: list[Path] = []
    seen: set[Path] = set()
    root = wiki_dir.resolve()
    for rel in sorted(refs):
        if not rel or ".." in Path(rel).parts or Path(rel).is_absolute():
            continue
        suffix = Path(rel).suffix.lower()
        if suffix and suffix not in _FIGURE_SUFFIXES:
            continue
        candidate = (wiki_dir / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and candidate not in seen:
            out.append(candidate)
            seen.add(candidate)
    return out


def _copy_tree_files(files: list[Path], src_root: Path, dest_root: Path) -> list[str]:
    rels: list[str] = []
    for f in files:
        rel = f.relative_to(src_root)
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        rels.append(str(rel).replace("\\", "/"))
    return sorted(rels)


def _page_ref(page: Path, wiki_dir: Path) -> str:
    return str(page.relative_to(wiki_dir)).replace("\\", "/")[: -len(".md")]


def _write_manifests(
    workspace: Path,
    target: Path,
    name: str,
    move: list[str],
    copy: list[str],
    stamp: str,
) -> None:
    body = {
        "name": name,
        "created": stamp,
        "source": str(workspace),
        "target": str(target),
        "moved": move,
        "copied": copy,
        "schema": "ce-wiki-partition/1",
    }
    for side, extra in ((workspace, {"role": "source"}), (target, {"role": "target"})):
        d = side / ".curator" / "splits"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stamp}-{name}.json").write_text(
            json.dumps({**body, **extra}, indent=2) + "\n",
            encoding="utf-8",
        )


def _prune_moved(
    workspace: Path, move_pages: list[Path], stamp: str
) -> tuple[list[str], list[str]]:
    """Relocate moved pages under wiki/.deleted/<stamp>/ (recoverable)."""
    wiki = workspace / "wiki"
    pruned: list[str] = []
    errors: list[str] = []
    trash_root = wiki / ".deleted" / stamp
    for page in move_pages:
        ref = _page_ref(page, wiki)
        try:
            rel = page.relative_to(wiki)
            dst = trash_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(page), str(dst))
            pruned.append(ref)
        except OSError as e:
            errors.append(f"{ref}: {e}")
    return pruned, errors


def partition_workspace(
    workspace: Path,
    target: Path,
    move: list[str],
    copy: list[str],
    *,
    name: str | None = None,
    progress: Progress | None = None,
) -> dict:
    """Full pipeline. On failure before prune, removes the half-built target.

    Returns stats dict. Source is untouched until export + manifests succeed;
    MOVE prune runs last via recoverable .deleted relocate.
    """
    def _progress(step: str) -> None:
        if progress:
            progress(step)

    refs = list(dict.fromkeys([*(move or []), *(copy or [])]))
    if not refs:
        raise PartitionError("nothing selected")
    workspace = workspace.resolve()
    if not (workspace / "wiki").is_dir():
        raise PartitionError(f"workspace has no wiki/: {workspace}")
    dest = _safe_target(workspace, target)
    label = sanitize_name(name or dest.name)
    if not label:
        raise PartitionError("name required")

    move_u = list(dict.fromkeys(move or []))
    copy_u = list(dict.fromkeys(copy or []))
    # A ref listed in both → MOVE wins (export once, then prune).
    copy_u = [r for r in copy_u if r not in set(move_u)]

    try:
        _progress(f"resolving {len(refs)} pages")
        all_pages = resolve_pages(workspace, refs)
        move_pages = resolve_pages(workspace, move_u) if move_u else []
        wiki = workspace / "wiki"
        vault = workspace / "vault"

        _progress("creating target layout")
        dest.mkdir(parents=True, exist_ok=False)
        (dest / "wiki").mkdir()
        (dest / "vault").mkdir()
        (dest / ".curator").mkdir()

        _progress("copying wiki pages")
        pages_rel = _copy_tree_files(all_pages, wiki, dest / "wiki")

        _progress("copying vault citations")
        vault_files = _collect_vault(all_pages, vault)
        vault_rel = (
            _copy_tree_files(vault_files, vault, dest / "vault") if vault_files else []
        )

        _progress("copying figure embeds")
        figures = _collect_figures(all_pages, wiki)
        figures_rel = (
            _copy_tree_files(figures, wiki, dest / "wiki") if figures else []
        )

        stamp = time.strftime("%Y%m%d-%H%M%S")
        move_refs = [_page_ref(p, wiki) for p in move_pages]
        copy_refs = [
            _page_ref(p, wiki)
            for p in all_pages
            if _page_ref(p, wiki) not in set(move_refs)
        ]
        _progress("writing split manifests")
        _write_manifests(workspace, dest, label, move_refs, copy_refs, stamp)

    except Exception:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise

    _progress(f"pruning {len(move_pages)} moved pages")
    pruned, prune_errors = _prune_moved(workspace, move_pages, stamp)

    return {
        "ok": True,
        "name": label,
        "target": str(dest),
        "exported": len(pages_rel),
        "pages": pages_rel,
        "moved": len(pruned),
        "copied": len(copy_refs),
        "vault": len(vault_rel),
        "figures": len(figures_rel),
        "prune_errors": prune_errors,
        "stamp": stamp,
    }
