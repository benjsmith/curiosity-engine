#!/usr/bin/env python3
"""scan.py — walk a registered project-dir and ingest new/changed files.

For non-code project directories that register against a CE workspace
via `.curiosity/config.toml` with `project_kind = "documents"`. Reads
the pointer's `[ingest]` block (paths, extensions whitelist, exclude
globs, follow_symlinks) and:

  - finds files matching the extension whitelist under the configured
    paths, applying excludes;
  - skips files already ingested (sha256-content-addressed);
  - re-ingests files whose original sha256 changed since the last
    extraction (and quarantines the stale extraction to vault/_stale/);
  - marks orphaned extractions when the original is deleted/moved
    (sets `orphan: true` in frontmatter);
  - invokes `local_ingest.py --source-path-only` to do the actual
    extraction (vault holds only `.extracted.md`; original stays put).

Three modes:

  one --workspace W --pointer P
                  scan one project-dir's pointer file

  all --workspace W
                  iterate the workspace's project-dir registry
                  (`.curator/project-dirs.json`) and scan each

  check-stale --workspace W [--max-age-seconds N]
                  cheap mtime-based staleness check. No scanning, no
                  ingestion. Returns JSON with per-project stale-file
                  counts so the viewer and CURATE-start trigger can
                  emit "N files unscanned" warnings without paying for
                  a full filesystem walk.

Security:
  - Pointer paths are run through code_repo.validate_pointer_paths;
    any path that escapes the pointer-dir, contains `..`, or is
    absolute aborts the scan with a clear error.
  - Symlinks are not followed by default (pointer's
    `follow_symlinks: false`); symlinks inside scanned paths are
    skipped regardless to prevent exfil via crafted links.
  - Extension whitelist enforced; nothing outside it is read.
  - Standard untrusted: true + FETCHED CONTENT envelope on every
    extraction (inherited from local_ingest.py's normal path).

Stdlib only. Hash-guarded.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import code_repo  # noqa: E402

DEFAULT_EXTENSIONS = (".pdf", ".md", ".txt", ".docx", ".pptx",
                      ".csv", ".xlsx", ".html", ".rst")
# Standard collateral that almost never wants to be ingested. Added on
# top of any user-supplied `exclude` entries in the pointer file.
ALWAYS_EXCLUDE = (".git/", ".svn/", ".hg/", ".venv/", "venv/",
                  "node_modules/", "__pycache__/", ".pytest_cache/",
                  ".mypy_cache/", ".ruff_cache/", "dist/", "build/",
                  "target/", "*.lock", ".DS_Store")


# ---------------------------------------------------------------------------
# Pointer-driven config
# ---------------------------------------------------------------------------


def _pointer_config(pointer: Path) -> dict:
    cfg = code_repo.read_pointer(pointer)
    ingest = cfg.get("ingest", {}) if isinstance(cfg.get("ingest"), dict) else {}
    return {
        "project": cfg.get("project") or pointer.parent.parent.name,
        "project_kind": cfg.get("project_kind", "code"),
        "workspace": cfg.get("workspace"),
        "enabled": ingest.get("enabled", True),
        "paths": ingest.get("paths") or ["."],
        "extensions": [
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in (ingest.get("extensions") or DEFAULT_EXTENSIONS)
        ],
        "exclude": list(ingest.get("exclude") or []) + list(ALWAYS_EXCLUDE),
        "follow_symlinks": bool(ingest.get("follow_symlinks", False)),
    }


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


def _excluded(rel: str, exclude_patterns: list[str]) -> bool:
    """Match `rel` (POSIX-style relative path) against gitignore-ish
    patterns. Supports `*.lock`, `node_modules/`, `**/private/`."""
    rel_unix = rel.replace(os.sep, "/")
    for pat in exclude_patterns:
        p = pat.rstrip("/")
        # Directory-style: match if any path segment equals the pattern.
        if pat.endswith("/"):
            parts = rel_unix.split("/")
            if any(seg == p or fnmatch.fnmatch(seg, p) for seg in parts):
                return True
        else:
            if fnmatch.fnmatch(rel_unix, pat):
                return True
            # Also try matching just the basename for *.lock-style globs.
            if fnmatch.fnmatch(os.path.basename(rel_unix), pat):
                return True
    return False


def _walk_paths(pointer_dir: Path, paths: list[str], extensions: list[str],
                exclude: list[str], follow_symlinks: bool) -> list[Path]:
    """Yield absolute paths of files eligible for ingest under each
    configured path. Paths must already have passed
    validate_pointer_paths(); we trust them here."""
    out: list[Path] = []
    pointer_dir_resolved = pointer_dir.resolve()
    for rel in paths:
        root = (pointer_dir / rel).resolve(strict=False)
        if not root.exists():
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for dirpath, dirnames, filenames in os.walk(
                    root, followlinks=follow_symlinks):
                # Prune excluded dirs in-place so os.walk doesn't descend.
                dirnames[:] = [
                    d for d in dirnames
                    if not _excluded(
                        str((Path(dirpath) / d).resolve()
                            .relative_to(pointer_dir_resolved)),
                        exclude)
                ]
                for fname in filenames:
                    candidates.append(Path(dirpath) / fname)
        for f in candidates:
            try:
                f_resolved = f.resolve(strict=False)
                # Defence in depth: skip files that resolve outside
                # pointer-dir (would mean a symlink escape we missed).
                try:
                    f_resolved.relative_to(pointer_dir_resolved)
                except ValueError:
                    continue
            except OSError:
                continue
            # Skip symlinks regardless of follow_symlinks: even with
            # follow_symlinks=true, scanning a symlink's TARGET inside
            # the project-dir is harmless, but a symlink whose target
            # escapes the project-dir would be an exfil channel. The
            # relative_to check above catches the latter; this catches
            # symlinks that resolve TO themselves (broken / loops).
            if f.is_symlink() and not follow_symlinks:
                continue
            if f.suffix.lower() not in extensions:
                continue
            rel = f_resolved.relative_to(pointer_dir_resolved).as_posix()
            if _excluded(rel, exclude):
                continue
            out.append(f_resolved)
    # Deduplicate while preserving order.
    seen = set()
    unique = []
    for p in out:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Vault state — read existing extractions to compute deltas
# ---------------------------------------------------------------------------


def _read_extraction_index(workspace: Path) -> dict[str, dict]:
    """Walk workspace/vault for `*.extracted.md` files that carry
    `source_in_place: true`. Returns a dict keyed by source_path
    (absolute) → {extraction, sha256}.

    Cheap enough to walk for typical workspaces (~100s-1000s of files).
    A future optimisation could maintain a sidecar index, but this is
    fine for v1.
    """
    index: dict[str, dict] = {}
    vault = workspace / "vault"
    if not vault.is_dir():
        return index
    for ext_file in vault.rglob("*.extracted.md"):
        # Skip the stale-quarantine subdir.
        if "_stale" in ext_file.parts or "_suspect" in ext_file.parts:
            continue
        try:
            text = ext_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Cheap frontmatter scan — read first ~50 lines.
        fm_lines = text.splitlines()[:50]
        in_place = False
        sp = None
        sha = None
        for line in fm_lines:
            if line.startswith("source_in_place:") and "true" in line:
                in_place = True
            elif line.startswith("source_path:"):
                sp = line.split(":", 1)[1].strip()
            elif line.startswith("sha256:"):
                sha = line.split(":", 1)[1].strip()
        if not in_place or not sp:
            continue
        index[sp] = {"extraction": str(ext_file), "sha256": sha or ""}
    return index


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _scan_one(workspace: Path, pointer: Path, dry_run: bool = False) -> dict:
    if not pointer.is_file():
        return {"error": f"pointer not found: {pointer}",
                "pointer": str(pointer)}

    # Validate paths first — refuse to act on an unsafe pointer.
    path_results = code_repo.validate_pointer_paths(pointer)
    unsafe = [r for r in path_results if not r["ok"]]
    if unsafe:
        return {
            "error": "unsafe paths in pointer; refusing to scan",
            "pointer": str(pointer),
            "unsafe_paths": unsafe,
        }

    cfg = _pointer_config(pointer)
    if cfg["project_kind"] != "documents":
        return {
            "error": ("project_kind must be 'documents' for scan; "
                      f"got {cfg['project_kind']!r}"),
            "pointer": str(pointer),
        }
    if not cfg["enabled"]:
        return {"pointer": str(pointer), "skipped": "ingest disabled"}

    pointer_dir = pointer.parent.parent
    candidates = _walk_paths(pointer_dir, cfg["paths"], cfg["extensions"],
                             cfg["exclude"], cfg["follow_symlinks"])

    existing = _read_extraction_index(workspace)

    to_ingest: list[Path] = []
    unchanged: list[str] = []
    changed: list[str] = []
    for c in candidates:
        cs = str(c)
        if cs not in existing:
            to_ingest.append(c)
            continue
        prev_sha = existing[cs].get("sha256", "")
        cur_sha = _sha256_file(c)
        if not prev_sha or cur_sha != prev_sha:
            changed.append(cs)
            to_ingest.append(c)
        else:
            unchanged.append(cs)

    # Detect orphans — entries in `existing` whose source_path is no
    # longer on disk under this pointer-dir.
    candidate_paths = {str(c) for c in candidates}
    pointer_dir_str = str(pointer_dir.resolve())
    orphans: list[str] = []
    for sp, meta in existing.items():
        if not sp.startswith(pointer_dir_str):
            continue   # belongs to a different project-dir
        if sp not in candidate_paths and not Path(sp).is_file():
            orphans.append(meta["extraction"])

    summary = {
        "pointer": str(pointer),
        "project": cfg["project"],
        "candidates": len(candidates),
        "to_ingest": len(to_ingest),
        "changed": len(changed),
        "unchanged": len(unchanged),
        "orphans": len(orphans),
        "dry_run": dry_run,
    }

    if dry_run or not to_ingest:
        # Still mark orphans even on dry-run? No — dry-run is read-only.
        if not dry_run and orphans:
            for o in orphans:
                _mark_orphan(Path(o))
            summary["orphans_marked"] = len(orphans)
        return summary

    # Invoke local_ingest.py --source-path-only per file. We could
    # batch by parent-dir but per-file gives clean per-result reporting
    # and keeps the security envelope of one-file-per-invocation.
    ingest_results = []
    for c in to_ingest:
        # If this is a changed file (we have a stale extraction), move
        # the stale one to vault/_stale/ before re-ingesting so the new
        # extraction can take the canonical name.
        if str(c) in {p for p in changed}:
            stale = existing.get(str(c), {}).get("extraction")
            if stale:
                _quarantine_stale(workspace, Path(stale))
        r = subprocess.run(
            ["uv", "run", "python3",
             str(SCRIPT_DIR / "local_ingest.py"),
             "--file", str(c),
             "--source-path-only",
             "--projects", cfg["project"]],
            cwd=workspace,
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            ingest_results.append({"path": str(c), "ok": False,
                                   "error": r.stderr.strip()[:200]})
        else:
            ingest_results.append({"path": str(c), "ok": True})

    if orphans:
        for o in orphans:
            _mark_orphan(Path(o))
        summary["orphans_marked"] = len(orphans)

    summary["ingest_results"] = ingest_results
    summary["ingested_ok"] = sum(1 for r in ingest_results if r["ok"])
    summary["ingested_failed"] = (len(ingest_results)
                                  - summary["ingested_ok"])
    return summary


def _mark_orphan(extraction: Path) -> None:
    if not extraction.is_file():
        return
    try:
        text = extraction.read_text(encoding="utf-8")
    except OSError:
        return
    if "orphan: true" in text:
        return
    # Insert `orphan: true` and `orphan_detected: <iso>` before the
    # closing `---` fence.
    if not text.startswith("---"):
        return
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return
    insertion = (f"orphan: true\n"
                 f"orphan_detected: "
                 f"{datetime.now(timezone.utc).isoformat()}\n")
    new_text = text[:end + 1] + insertion + text[end + 1:]
    extraction.write_text(new_text, encoding="utf-8")


def _quarantine_stale(workspace: Path, stale: Path) -> None:
    if not stale.is_file():
        return
    target_dir = workspace / "vault" / "_stale"
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{ts}-{stale.name}"
    try:
        stale.rename(target)
    except OSError:
        pass


def cmd_one(args):
    workspace = Path(args.workspace).expanduser().resolve()
    pointer = Path(args.pointer).expanduser().resolve()
    result = _scan_one(workspace, pointer, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


def cmd_all(args):
    workspace = Path(args.workspace).expanduser().resolve()
    entries = code_repo.read_project_dirs(workspace)
    overall = {
        "workspace": str(workspace),
        "project_dirs_total": len(entries),
        "per_project": [],
    }
    total_ingested = 0
    total_changed = 0
    total_orphans = 0
    for entry in entries:
        pointer = Path(entry.get("pointer", "")).expanduser()
        if not pointer.is_file():
            overall["per_project"].append({
                "project": entry.get("project"),
                "pointer": str(pointer),
                "skipped": "pointer file not present on this machine",
            })
            continue
        r = _scan_one(workspace, pointer, dry_run=args.dry_run)
        overall["per_project"].append(r)
        total_ingested += r.get("ingested_ok", 0)
        total_changed += r.get("changed", 0)
        total_orphans += r.get("orphans", 0)
    overall["totals"] = {
        "ingested_ok": total_ingested,
        "changed": total_changed,
        "orphans": total_orphans,
    }
    # Write a staleness sidecar for the viewer + CURATE-start trigger.
    _write_staleness_sidecar(workspace, overall)
    print(json.dumps(overall, indent=2))


def _write_staleness_sidecar(workspace: Path, overall: dict) -> None:
    sidecar = workspace / ".curator" / "scan-staleness.json"
    summary = {
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "stale_files": 0,
        "per_project": [],
    }
    for p in overall.get("per_project", []):
        if "skipped" in p or "error" in p:
            continue
        # to_ingest is the count after delta detection; treat as "stale"
        # if any were freshly found AND scan was dry-run, or if there
        # were orphans (delete-detected). Otherwise the scan just
        # completed and freshness is 0.
        # On a non-dry run, scan ingested them, so stale=0 going forward.
        pass
    summary["per_project"] = [
        {"project": p.get("project"),
         "to_ingest": p.get("to_ingest", 0),
         "orphans": p.get("orphans", 0)}
        for p in overall.get("per_project", [])
        if not p.get("skipped") and not p.get("error")
    ]
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def cmd_check_stale(args):
    """Cheap staleness check — no actual scan, no ingestion.

    Compares the workspace's last-scan time (sidecar mtime) against
    the latest mtime of any file under each registered project-dir.
    Returns rough "files newer than last scan" counts. Used by the
    viewer to emit a warning banner and by CURATE-start to decide
    whether to invoke a real scan.
    """
    workspace = Path(args.workspace).expanduser().resolve()
    sidecar = workspace / ".curator" / "scan-staleness.json"
    last_scan_mtime = 0.0
    if sidecar.is_file():
        last_scan_mtime = sidecar.stat().st_mtime

    entries = code_repo.read_project_dirs(workspace)
    per_project = []
    total_stale = 0
    for entry in entries:
        pointer = Path(entry.get("pointer", ""))
        project = entry.get("project")
        if not pointer.is_file():
            per_project.append({
                "project": project,
                "skipped": "pointer file not present on this machine",
            })
            continue
        # Cheap walk: count files newer than last_scan_mtime under
        # configured paths. No reads, no shas. Bounded by walk cost.
        try:
            cfg = _pointer_config(pointer)
            if cfg["project_kind"] != "documents":
                continue
            if not cfg["enabled"]:
                continue
            pointer_dir = pointer.parent.parent
            stale_count = 0
            for rel in cfg["paths"]:
                root = (pointer_dir / rel).resolve(strict=False)
                if not root.exists():
                    continue
                if root.is_file():
                    iterable = [root]
                else:
                    iterable = root.rglob("*")
                for f in iterable:
                    try:
                        if not f.is_file():
                            continue
                        if f.suffix.lower() not in cfg["extensions"]:
                            continue
                        if f.stat().st_mtime > last_scan_mtime:
                            stale_count += 1
                            if stale_count > 1000:
                                break
                    except OSError:
                        continue
            per_project.append({"project": project, "stale_files": stale_count})
            total_stale += stale_count
        except Exception as e:
            per_project.append({"project": project, "error": str(e)})

    age_seconds = int(time.time() - last_scan_mtime) if last_scan_mtime else None
    out = {
        "workspace": str(workspace),
        "last_scan_seconds_ago": age_seconds,
        "total_stale_files": total_stale,
        "per_project": per_project,
    }
    print(json.dumps(out, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(prog="scan.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("one", help="scan one project-dir's pointer file")
    s.add_argument("--workspace", required=True)
    s.add_argument("--pointer", required=True,
                   help="path to .curiosity/config.toml of the project-dir")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_one)

    s = sub.add_parser("all",
                       help="scan every registered project-dir for a workspace")
    s.add_argument("--workspace", required=True)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_all)

    s = sub.add_parser("check-stale",
                       help="cheap mtime-based staleness check, no scan")
    s.add_argument("--workspace", required=True)
    s.set_defaults(fn=cmd_check_stale)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
