#!/usr/bin/env python3
"""session_drainer.py — drain completed agent session transcripts into vault.

Walks the host CLI's session-store directory (Claude Code's
`~/.claude/projects/<flatpath>/*.jsonl`), identifies completed sessions
(file mtime older than --quiet-seconds), and calls code_capture.py to
write each as a vault source.

Critical recursion guard: the workspace's own flatpath is filtered out.
Detached `/curate` sessions live in the workspace's project dir; without
this rule each curate run would generate a transcript that the next
curate run would re-ingest as engineering work.

Tracks processed sessions via `<workspace>/.curator/.processed-sessions`
(one absolute jsonl path per line).

Usage:
  session_drainer.py --workspace <path>
                            # one-shot: drain all newly-complete sessions
  session_drainer.py --workspace <path> --session <path>
                            # drain a specific session, skip the marker
  session_drainer.py --workspace <path> --dry-run
                            # list candidates without capturing

Stdlib only. Hash-guarded.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_QUIET_SECONDS = 300  # 5 min — likely the session has ended


def _flatpath(p: Path) -> str:
    """Mirror Claude Code's project-dir naming: leading `-` plus `/` → `-`."""
    s = str(p.resolve())
    return "-" + s.replace("/", "-")


def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _processed_marker(workspace: Path) -> Path:
    return workspace / ".curator" / ".processed-sessions"


def _read_processed(workspace: Path) -> set[str]:
    m = _processed_marker(workspace)
    if not m.is_file():
        return set()
    try:
        return {line.strip() for line in m.read_text().splitlines()
                if line.strip()}
    except OSError:
        return set()


def _append_processed(workspace: Path, jsonl: Path):
    m = _processed_marker(workspace)
    m.parent.mkdir(parents=True, exist_ok=True)
    with m.open("a") as f:
        f.write(str(jsonl) + "\n")


def _is_complete(jsonl: Path, quiet_seconds: int) -> bool:
    try:
        age = time.time() - jsonl.stat().st_mtime
    except OSError:
        return False
    return age >= quiet_seconds


def _enumerate_candidates(workspace: Path) -> list[Path]:
    projects_dir = _claude_projects_dir()
    if not projects_dir.is_dir():
        return []

    workspace_flatpath = _flatpath(workspace)
    candidates: list[Path] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        # CRITICAL: skip the workspace's own project dir to prevent
        # curate sessions from being re-ingested as engineer work.
        if project_dir.name == workspace_flatpath:
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            candidates.append(jsonl.resolve())
    return candidates


def _drain_one(workspace: Path, jsonl: Path) -> dict:
    """Call code_capture.py session for one jsonl. Returns {status,path}."""
    try:
        # Run from a sensible cwd: prefer the original session's working
        # dir (decoded from flatpath) so code_capture's project/repo
        # resolution sees the right .curiosity/config.toml.
        repo_cwd = _decode_flatpath(jsonl.parent.name)
        cwd = repo_cwd if (repo_cwd and repo_cwd.is_dir()) else workspace
        r = subprocess.run(
            ["uv", "run", "python3", str(SCRIPT_DIR / "code_capture.py"),
             "session",
             "--workspace", str(workspace),
             "--session", str(jsonl)],
            cwd=cwd,
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return {"status": "error", "path": str(jsonl),
                    "stderr": r.stderr.strip()[:500]}
        return {"status": "captured", "path": str(jsonl)}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"status": "error", "path": str(jsonl), "error": str(e)}


def _decode_flatpath(name: str) -> Path | None:
    """Inverse of _flatpath: '-Users-benj-work-myapp' → /Users/benj/work/myapp.

    Lossy on directory names that themselves contain '-', but good enough
    to get a directory that exists and is plausibly the right one. We
    only use this to set cwd for code_capture.py's pointer-file
    resolution; capture itself works regardless.
    """
    if not name.startswith("-"):
        return None
    candidate = "/" + name[1:].replace("-", "/")
    p = Path(candidate)
    if p.is_dir():
        return p
    return None


def cmd_drain(args):
    workspace = Path(args.workspace).expanduser().resolve()
    if not (workspace / ".curator").is_dir():
        print(f"not a CE workspace: {workspace}", file=sys.stderr)
        sys.exit(1)

    if args.session:
        # Single explicit session — bypass marker.
        jsonl = Path(args.session).expanduser().resolve()
        if not jsonl.is_file():
            print(f"session not found: {jsonl}", file=sys.stderr)
            sys.exit(1)
        result = _drain_one(workspace, jsonl)
        if result["status"] == "captured":
            _append_processed(workspace, jsonl)
        print(result)
        return

    processed = _read_processed(workspace)
    candidates = _enumerate_candidates(workspace)
    quiet = args.quiet_seconds

    drained = []
    skipped_active = 0
    skipped_processed = 0
    for jsonl in candidates:
        if str(jsonl) in processed:
            skipped_processed += 1
            continue
        if not _is_complete(jsonl, quiet):
            skipped_active += 1
            continue
        if args.dry_run:
            drained.append({"status": "would-drain", "path": str(jsonl)})
            continue
        result = _drain_one(workspace, jsonl)
        if result["status"] == "captured":
            _append_processed(workspace, jsonl)
        drained.append(result)

    summary = {
        "drained": drained,
        "skipped_active": skipped_active,
        "skipped_already_processed": skipped_processed,
        "total_candidates": len(candidates),
    }
    print(summary)


def main():
    p = argparse.ArgumentParser(prog="session_drainer.py", description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--session", default=None,
                   help="drain a specific jsonl, bypassing the processed marker")
    p.add_argument("--quiet-seconds", type=int, default=DEFAULT_QUIET_SECONDS,
                   help=f"sessions whose mtime is younger than this are "
                        f"considered active and skipped "
                        f"(default {DEFAULT_QUIET_SECONDS})")
    p.add_argument("--dry-run", action="store_true",
                   help="list what would be drained, don't capture")
    args = p.parse_args()
    cmd_drain(args)


if __name__ == "__main__":
    main()
