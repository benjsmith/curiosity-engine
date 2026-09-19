"""CE filebrowser FS mutations — vault/ + wiki/ sandbox (Phase 2b++).

Switchbay Step E ships delete / duplicate / reveal / stat over the whole
workspace. CE intake prefers vault/wiki path semantics (architecture.md
three-object model) and adds create / rename / move so the embed surface
can mutate without shell round-trips.

All relative paths are re-resolved under the workspace and refused if they
escape it, leave vault|wiki, or land in a protected / hidden component.
Trash lands in workspace `.workbench/trash/` (Switchbay fallback shape);
OS trash helpers are optional best-effort.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from filebrowser_tree import SKIP_DIRS, TREE_ROOTS

ALLOWED_ROOTS = frozenset(TREE_ROOTS)
PROTECTED_TOP = frozenset({"node_modules", "venv", "__pycache__", "dist", "build"})
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._+-]+$")


class FileOpError(Exception):
    """User-visible FS failure (bad path, missing file, escape, etc.)."""


def _norm_rel(rel: str) -> str:
    raw = (rel or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    return raw.strip("/")


def resolve(workspace: Path, rel: str, *, must_exist: bool = False) -> Path:
    """Resolve a vault/ or wiki/ relative path; refuse escapes."""
    raw = _norm_rel(rel)
    if not raw or raw.startswith("/") or "\x00" in raw:
        raise FileOpError("invalid path")
    parts = Path(raw).parts
    if ".." in parts:
        raise FileOpError("path may not contain ..")
    if not parts or parts[0] not in ALLOWED_ROOTS:
        raise FileOpError("path must start with vault/ or wiki/")
    for part in parts:
        if part.startswith("."):
            raise FileOpError(f"refusing to touch hidden path component {part}")
        if part in SKIP_DIRS or part in PROTECTED_TOP:
            raise FileOpError(f"refusing to touch {part}/")
    ws = Path(workspace).resolve()
    try:
        target = (ws / raw).resolve()
    except (OSError, ValueError) as e:
        raise FileOpError(f"resolve failed: {e}") from e
    try:
        relative = target.relative_to(ws)
    except ValueError as e:
        raise FileOpError("path escapes workspace") from e
    if not relative.parts or relative.parts[0] not in ALLOWED_ROOTS:
        raise FileOpError("path escapes vault/wiki sandbox")
    # Belt-and-brace: every component of the resolved relative path.
    for part in relative.parts:
        if part.startswith(".") or part in SKIP_DIRS or part in PROTECTED_TOP:
            raise FileOpError(f"refusing to touch {part}/")
    if must_exist and not target.exists():
        raise FileOpError("not found")
    return target


def _rel_of(workspace: Path, target: Path) -> str:
    return target.resolve().relative_to(Path(workspace).resolve()).as_posix()


def stat(workspace: Path, rel: str) -> dict:
    target = resolve(workspace, rel, must_exist=True)
    s = target.stat()
    return {
        "path": _norm_rel(rel),
        "size": s.st_size,
        "mtime": s.st_mtime,
        "kind": "dir" if target.is_dir() else "file",
    }


def _unique_sibling(parent: Path, stem: str, suffix: str) -> Path:
    candidate = parent / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = parent / f"{stem} {n}{suffix}"
        n += 1
    return candidate


def create(
    workspace: Path,
    rel: str,
    *,
    kind: str = "file",
    content: str = "",
) -> str:
    """Create a file (parents must exist) or empty directory under vault|wiki."""
    kind = (kind or "file").strip().lower()
    if kind not in ("file", "dir", "directory"):
        raise FileOpError("kind must be file or dir")
    raw = _norm_rel(rel)
    if not raw:
        raise FileOpError("invalid path")
    parts = Path(raw).parts
    if len(parts) < 2:
        raise FileOpError("path must be under vault/ or wiki/ (not the root itself)")
    leaf = parts[-1]
    if not leaf or leaf.startswith(".") or not _SAFE_SEGMENT.match(leaf):
        raise FileOpError("invalid name")
    # Validate full destination path (may not exist yet).
    resolve(workspace, raw)
    parent_rel = Path(*parts[:-1]).as_posix()
    parent = resolve(workspace, parent_rel, must_exist=True)
    if not parent.is_dir():
        raise FileOpError("parent must be a directory")
    target = parent / leaf
    if target.exists():
        raise FileOpError("already exists")
    if kind in ("dir", "directory"):
        target.mkdir(parents=False, exist_ok=False)
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        target.write_text(content, encoding="utf-8")
    return _rel_of(workspace, target)


def mkdir(workspace: Path, rel: str) -> str:
    return create(workspace, rel, kind="dir")


def rename(workspace: Path, src: str, dst: str) -> str:
    """Rename or relocate within vault|wiki. ``dst`` is a full relative path."""
    source = resolve(workspace, src, must_exist=True)
    dest_raw = _norm_rel(dst)
    if not dest_raw:
        raise FileOpError("invalid destination")
    # Destination must not exist; parent must exist.
    dest_parent_rel = Path(dest_raw).parent.as_posix()
    if dest_parent_rel in ("", "."):
        raise FileOpError("destination must be under vault/ or wiki/")
    parent = resolve(workspace, dest_parent_rel, must_exist=True)
    if not parent.is_dir():
        raise FileOpError("destination parent must be a directory")
    leaf = Path(dest_raw).name
    if not leaf or leaf.startswith(".") or not _SAFE_SEGMENT.match(leaf):
        raise FileOpError("invalid destination name")
    dest = parent / leaf
    # Validate resolved dest stays in sandbox (even if not yet existing).
    resolve(workspace, dest_raw)
    if dest.exists():
        raise FileOpError("destination already exists")
    if source.resolve() == dest.resolve():
        return _rel_of(workspace, source)
    # Refuse moving a directory into itself.
    if source.is_dir():
        try:
            dest.resolve().relative_to(source.resolve())
            raise FileOpError("cannot move a directory into itself")
        except ValueError:
            pass
    shutil.move(str(source), str(dest))
    return _rel_of(workspace, dest)


def move(workspace: Path, src: str, dst: str) -> str:
    """Move ``src`` to ``dst``.

    If ``dst`` names an existing directory, place the source basename inside it.
    Otherwise treat ``dst`` as the full destination path (same as rename).
    """
    source = resolve(workspace, src, must_exist=True)
    dest_raw = _norm_rel(dst)
    if not dest_raw:
        raise FileOpError("invalid destination")
    # Probe whether dst is an existing dir without requiring existence for files.
    try:
        dest_probe = resolve(workspace, dest_raw, must_exist=False)
    except FileOpError:
        raise
    if dest_probe.is_dir():
        dest_raw = f"{dest_raw.rstrip('/')}/{source.name}"
    return rename(workspace, src, dest_raw)


def delete(workspace: Path, rel: str) -> str:
    """Delete-to-trash. Files always; empty directories only.

    Prefer OS trash when available; fall back to ``.workbench/trash/``.
    Returns a human-readable destination for UI toasts.
    """
    target = resolve(workspace, rel, must_exist=True)
    if target.is_dir():
        try:
            next(target.iterdir())
            raise FileOpError("delete: directory not empty")
        except StopIteration:
            pass
    cmd: list[str] | None = None
    if sys.platform == "darwin" and os.path.exists("/usr/bin/trash"):
        cmd = ["/usr/bin/trash", str(target)]
    elif sys.platform.startswith("linux") and shutil.which("gio"):
        cmd = ["gio", "trash", str(target)]
    if cmd is not None:
        try:
            res = subprocess.run(
                cmd, capture_output=True, timeout=10, check=False
            )
            if res.returncode == 0:
                return "the system Trash"
        except (OSError, subprocess.TimeoutExpired):
            pass
    ws = Path(workspace).resolve()
    bin_dir = ws / ".workbench" / "trash"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = bin_dir / f"{stamp}-{target.name}"
    n = 2
    while dest.exists():
        dest = bin_dir / f"{stamp}-{n}-{target.name}"
        n += 1
    shutil.move(str(target), str(dest))
    return f".workbench/trash/{dest.name}"


def duplicate(workspace: Path, rel: str) -> str:
    """Copy a file to a sibling with a unique `` copy`` suffix."""
    src = resolve(workspace, rel, must_exist=True)
    if not src.is_file():
        raise FileOpError("source must be a file")
    candidate = _unique_sibling(src.parent, f"{src.stem} copy", src.suffix)
    # Validate candidate stays sandboxed before writing.
    resolve(workspace, _rel_of(workspace, candidate))
    shutil.copy2(src, candidate)
    return _rel_of(workspace, candidate)
