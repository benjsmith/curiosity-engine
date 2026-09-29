#!/usr/bin/env python3
"""Import an Obsidian vault into a CE workspace as ``wiki/``.

**Mapping (documented choice):** Obsidian vault root → CE ``wiki/``.

CE already treats ``wiki/`` as Obsidian-shaped markdown with ``[[wikilinks]]``
(see ``docs/viewers.md`` — open folder as vault → ``<workspace>/wiki``).
Import copies notes/assets into ``wiki/`` so Atlas / classic wiki see them
without a Work/symlink dance. Wikilink text is preserved verbatim.

``.obsidian/`` is copied under ``wiki/.obsidian/`` for round-trip. Other
junk/dot dirs (``.trash``, ``.git``, ``node_modules``, …) are skipped.

**Source sandbox:** absolute folder/zip paths must resolve under ``$HOME``
or ``$CE_WORKSPACE_HOME`` (same contract as ``workspace_registry``). Zip
members are zip-slip checked before extract.

**Targets:**
  * into current workspace (default): merge into ``<workspace>/wiki/``
  * ``--target DIR``: bootstrap a minimal CE workspace at DIR, then import

CLI (conceptual ``ce import-obsidian <path>``)::

    python3 obsidian_import.py <vault-or.zip> [--workspace DIR] [--target DIR]
                                              [--register] [--force]

API: ``POST /api/import/obsidian`` — JSON ``{path}`` or multipart zip
(honours ``CE_PUBLIC_BASE`` via viewer path rewrite).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable

import workspace_registry

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

SKIP_DIR_NAMES = frozenset({
    ".trash",
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
})
KEEP_DOT_DIRS = frozenset({".obsidian"})

MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_ZIP_MEMBERS = 50_000
SCHEMA = "ce-obsidian-import/1"


class ObsidianImportError(Exception):
    """User-visible import failure."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


def _norm(p: Path) -> Path:
    return Path(p).expanduser().resolve()


def resolve_source_path(path: str | Path) -> Path:
    """Absolute source under home/workspace sandbox; refuse escapes."""
    raw = str(path or "").strip()
    if not raw:
        raise ObsidianImportError("path required")
    if "\x00" in raw:
        raise ObsidianImportError("invalid path")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ObsidianImportError("source must be an absolute path")
    try:
        resolved = p.resolve()
    except (OSError, ValueError) as e:
        raise ObsidianImportError(f"resolve failed: {e}") from e
    if not workspace_registry.is_within_sandbox(resolved):
        raise ObsidianImportError(
            f"path escapes workspace sandbox ({workspace_registry._home_root()})",
            status=403,
        )
    return resolved


def looks_like_obsidian_vault(root: Path) -> bool:
    if not root.is_dir():
        return False
    if (root / ".obsidian").is_dir():
        return True
    for p in root.rglob("*.md"):
        parts = p.relative_to(root).parts
        if any(part.startswith(".") and part not in KEEP_DOT_DIRS for part in parts[:-1]):
            continue
        return True
    return False


def count_wikilinks(text: str) -> int:
    return len(WIKILINK_RE.findall(text or ""))


def _should_skip_dir(name: str) -> bool:
    if name in KEEP_DOT_DIRS:
        return False
    if name in SKIP_DIR_NAMES:
        return True
    if name.startswith("."):
        return True
    return False


def iter_vault_files(vault_root: Path) -> Iterable[Path]:
    root = vault_root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for name in filenames:
            if name in (".DS_Store", "Thumbs.db"):
                continue
            yield Path(dirpath) / name


def ensure_workspace_skeleton(dest: Path, *, force: bool = False) -> Path:
    """Create minimal CE workspace markers at dest (wiki/, vault/, .curator/)."""
    dest = _norm(dest)
    if not workspace_registry.is_within_sandbox(dest):
        raise ObsidianImportError(
            f"target escapes workspace sandbox ({workspace_registry._home_root()})",
            status=403,
        )
    if dest.exists() and any(dest.iterdir()) and not force:
        if not (
            (dest / "wiki").is_dir()
            or (dest / ".curator" / "config.json").is_file()
        ):
            raise ObsidianImportError(
                f"target exists and is not an empty/CE workspace: {dest}"
            )
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "wiki").mkdir(exist_ok=True)
    (dest / "vault").mkdir(exist_ok=True)
    (dest / "vault" / "raw").mkdir(exist_ok=True)
    curator = dest / ".curator"
    curator.mkdir(exist_ok=True)
    cfg = curator / "config.json"
    if not cfg.is_file():
        cfg.write_text(
            json.dumps(
                {
                    "wiki_viewer_mode": "obsidian",
                    "imported_from": "obsidian",
                    "schema": SCHEMA,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return dest


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> Path:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    members = zf.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ObsidianImportError("zip has too many members")
    total = 0
    for info in members:
        total += info.file_size
        if total > MAX_ZIP_BYTES:
            raise ObsidianImportError(
                "zip too large (uncompressed >200 MB)", status=413
            )
        name = info.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        if name.startswith("/") or name.startswith("../") or "/../" in f"/{name}/":
            raise ObsidianImportError(f"zip slip refused: {info.filename}")
        target = (dest / name).resolve()
        try:
            target.relative_to(dest)
        except ValueError as e:
            raise ObsidianImportError(f"zip slip refused: {info.filename}") from e
    zf.extractall(dest)
    children = [p for p in dest.iterdir() if p.name not in ("__MACOSX",)]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest


def extract_zip_bytes(data: bytes, work_dir: Path) -> Path:
    if not data:
        raise ObsidianImportError("empty zip")
    if len(data) > MAX_ZIP_BYTES:
        raise ObsidianImportError("zip too large", status=413)
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    zip_path = work_dir / "upload.zip"
    zip_path.write_bytes(data)
    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if zf.testzip() is not None:
                raise ObsidianImportError("corrupt zip")
            return _safe_extract_zip(zf, extract_dir)
    except zipfile.BadZipFile as e:
        raise ObsidianImportError(f"not a zip: {e}") from e


def extract_zip_path(zip_path: Path, work_dir: Path) -> Path:
    zip_path = resolve_source_path(zip_path)
    if not zip_path.is_file():
        raise ObsidianImportError("zip path must be a file")
    try:
        data = zip_path.read_bytes()
    except OSError as e:
        raise ObsidianImportError(f"read failed: {e}") from e
    return extract_zip_bytes(data, work_dir)


def copy_vault_into_wiki(
    vault_root: Path,
    wiki_dir: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    vault_root = vault_root.resolve()
    wiki_dir = wiki_dir.resolve()
    if not looks_like_obsidian_vault(vault_root):
        raise ObsidianImportError(
            f"not an Obsidian vault (no .obsidian/ and no .md): {vault_root}"
        )
    wiki_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    skipped: list[str] = []
    wikilink_total = 0
    md_files = 0

    for src in iter_vault_files(vault_root):
        rel = src.relative_to(vault_root)
        dest = (wiki_dir / rel).resolve()
        try:
            dest.relative_to(wiki_dir)
        except ValueError as e:
            raise ObsidianImportError(f"refusing path escape: {rel}") from e
        if dest.exists() and not force:
            skipped.append(rel.as_posix())
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(rel.as_posix())
        if src.suffix.lower() == ".md":
            md_files += 1
            try:
                text = src.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            wikilink_total += count_wikilinks(text)

    return {
        "copied": copied,
        "skipped": skipped,
        "files_copied": len(copied),
        "files_skipped": len(skipped),
        "md_files": md_files,
        "wikilink_occurrences": wikilink_total,
    }


def import_vault(
    source: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
    target: str | Path | None = None,
    zip_bytes: bytes | None = None,
    zip_filename: str | None = None,
    register: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Import Obsidian vault from folder path or zip into a CE workspace."""
    if source is not None and zip_bytes is not None:
        raise ObsidianImportError("provide path or zip, not both")
    if source is None and zip_bytes is None:
        raise ObsidianImportError("path or zip required")

    if target is not None:
        dest_ws = ensure_workspace_skeleton(Path(target), force=force)
        mode = "new"
    else:
        if workspace is None:
            raise ObsidianImportError("workspace required when target is unset")
        dest_ws = _norm(workspace)
        if not workspace_registry.is_within_sandbox(dest_ws):
            raise ObsidianImportError(
                f"workspace escapes sandbox ({workspace_registry._home_root()})",
                status=403,
            )
        ensure_workspace_skeleton(dest_ws, force=True)
        mode = "into_current"

    wiki_dir = dest_ws / "wiki"
    staging: Path | None = None
    vault_root: Path
    source_label: str

    try:
        if zip_bytes is not None:
            wb = dest_ws / ".workbench" / "obsidian-import"
            wb.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix="zip-", dir=str(wb)))
            vault_root = extract_zip_bytes(zip_bytes, staging)
            source_label = zip_filename or "upload.zip"
        else:
            src_path = resolve_source_path(source)  # type: ignore[arg-type]
            if src_path.is_file() and src_path.suffix.lower() == ".zip":
                wb = dest_ws / ".workbench" / "obsidian-import"
                wb.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix="zip-", dir=str(wb)))
                vault_root = extract_zip_path(src_path, staging)
                source_label = str(src_path)
            elif src_path.is_dir():
                vault_root = src_path
                source_label = str(src_path)
            else:
                raise ObsidianImportError(
                    "source must be a directory or .zip file"
                )

        if vault_root.resolve() == wiki_dir.resolve():
            raise ObsidianImportError("source vault is the destination wiki/")

        stats = copy_vault_into_wiki(vault_root, wiki_dir, force=force)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    registered = False
    reg_error = None
    if register:
        try:
            workspace_registry.register(dest_ws, set_active=False)
            registered = True
        except TypeError:
            # Older/newer kw spelling
            try:
                workspace_registry.register(dest_ws, set_active=False)
                registered = True
            except Exception as e:
                registered = False
                reg_error = str(e)
        except Exception as e:
            registered = False
            reg_error = str(e)

    receipt_dir = dest_ws / ".curator" / "imports"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": SCHEMA,
        "ok": True,
        "mode": mode,
        "mapping": "obsidian-vault-root → wiki/",
        "source": source_label,
        "workspace": str(dest_ws),
        "wiki_root": str(wiki_dir),
        "wikilinks_preserved": True,
        **stats,
        "registered": registered,
    }
    if reg_error:
        receipt["register_error"] = reg_error
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (receipt_dir / f"{stamp}-obsidian.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("import-obsidian", "obsidian", "import"):
        argv = argv[1:]

    ap = argparse.ArgumentParser(
        prog="ce import-obsidian",
        description=(
            "Import an Obsidian vault (folder or zip) into a CE workspace as wiki/. "
            "Preserves [[wikilinks]]. Mapping: Obsidian vault root → wiki/."
        ),
    )
    ap.add_argument("path", help="Absolute path to Obsidian vault folder or .zip")
    ap.add_argument(
        "--workspace",
        default=None,
        help="Existing CE workspace (default: cwd). Ignored when --target is set.",
    )
    ap.add_argument(
        "--target",
        default=None,
        help="Create/use a new CE workspace at this absolute path and import there.",
    )
    ap.add_argument(
        "--register",
        action="store_true",
        help="Register the destination workspace in CE workspace registry.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files under wiki/ on name collision.",
    )
    ap.add_argument("--json", action="store_true", help="Print receipt JSON only.")
    args = ap.parse_args(argv)

    ws = args.workspace or (None if args.target else os.getcwd())
    try:
        result = import_vault(
            args.path,
            workspace=ws,
            target=args.target,
            register=args.register,
            force=args.force,
        )
    except ObsidianImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Imported {result['files_copied']} files "
            f"({result['md_files']} md, {result['wikilink_occurrences']} wikilinks) "
            f"→ {result['wiki_root']}"
        )
        print(f"mapping: {result['mapping']}")
        if result.get("files_skipped"):
            print(f"skipped existing: {result['files_skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
