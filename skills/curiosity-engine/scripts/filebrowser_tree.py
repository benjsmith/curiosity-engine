"""CE filebrowser tree walk — vault/ + wiki/ only (Phase 2b).

Switchbay's /api/tree walks the whole workspace. CE intake prefers vault
and wiki path semantics so the embed surface matches the three-object
model (architecture.md). Hidden/dot dirs and a small SKIP set are pruned
during os.walk (never descend).
"""
from __future__ import annotations

import os
from pathlib import Path

# Parallel Switchbay daemon.SKIP_DIRS / fileops._SKIP_DIRS (subset relevant
# under vault/wiki; kept for safety if a nested tool tree appears).
SKIP_DIRS = frozenset(
    {
        ".git",
        ".workbench",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".vite",
        ".cache",
        ".idea",
        ".vscode",
        ".pytest_cache",
        ".curator",
    }
)

# Roots relative to the workspace. Order is stable for UI grouping.
TREE_ROOTS = ("vault", "wiki")


def walk_ce_tree(workspace: Path) -> list[str]:
    """Return sorted relative paths under vault/ and wiki/.

    Paths use forward slashes and always start with ``vault/`` or
    ``wiki/``. Missing roots are skipped (empty list for that root).
    """
    ws = Path(workspace).resolve()
    out: list[str] = []
    for root_name in TREE_ROOTS:
        root = ws / root_name
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in SKIP_DIRS
            ]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                full = Path(dirpath) / fn
                try:
                    rel = full.resolve().relative_to(ws).as_posix()
                except ValueError:
                    continue
                out.append(rel)
    out.sort()
    return out


def tree_payload(workspace: Path) -> dict:
    """JSON body for GET /api/tree."""
    files = walk_ce_tree(workspace)
    return {
        "ok": True,
        "roots": list(TREE_ROOTS),
        "files": files,
        "count": len(files),
    }
