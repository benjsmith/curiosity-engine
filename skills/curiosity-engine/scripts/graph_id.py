#!/usr/bin/env python3
"""Stable graph identity and namespaced page ids.

A graph id is a URI (`ceg://local/<name>`, `ceg://git/<owner>/<repo>`,
`ceg://<host>/<name>`). It is stamped once, in `.curator/graph-id`,
and is not a secret. Page ids are `<mount-or-name>:<wiki-relative-path>`.
The mount alias is chosen by the workspace that attaches the graph;
this module only knows the graph's own name, the last segment of the URI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def workspace_of(path: Path) -> Path:
    """Return the workspace root for a wiki directory or a workspace."""
    path = path.resolve()
    if path.name == "wiki" and (path.parent / ".curator").is_dir():
        return path.parent
    if (path / "wiki").is_dir():
        return path
    return path


def stamp_path(workspace: Path) -> Path:
    return workspace_of(workspace) / ".curator" / "graph-id"


def read_graph_id(workspace: Path) -> str:
    p = stamp_path(workspace)
    if not p.is_file():
        return ""
    line = p.read_text(encoding="utf-8").strip().splitlines()
    return line[0].strip() if line else ""


def write_graph_id(workspace: Path, graph_id: str) -> Path:
    graph_id = (graph_id or "").strip()
    if not graph_id or any(c in graph_id for c in "\n\r\t "):
        raise ValueError("graph_id must be a single token")
    p = stamp_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(graph_id + "\n", encoding="utf-8")
    return p


def graph_name(graph_id: str) -> str:
    """Short name used inside a page id. Not the workspace mount alias."""
    text = (graph_id or "graph").rstrip("/")
    leaf = text.split("/")[-1] or "graph"
    if ":" in leaf and not leaf.startswith("ceg"):
        leaf = leaf.split(":")[-1]
    leaf = _NAME_RE.sub("-", leaf).strip("-") or "graph"
    return leaf[:80]


def page_id(graph_id: str, rel: str, *, alias: str = "") -> str:
    """`<alias>:<path>` when a mount alias is known, else `<name>:<path>`."""
    prefix = (alias or graph_name(graph_id)).strip() or "graph"
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if rel.endswith(".md"):
        rel = rel[:-3]
    return f"{prefix}:{rel}"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 3 and args[0] == "stamp":
        write_graph_id(Path(args[1]), args[2])
        print(read_graph_id(Path(args[1])))
        return 0
    if len(args) >= 2 and args[0] == "show":
        print(read_graph_id(Path(args[1])))
        return 0
    print("usage: graph_id.py stamp WORKSPACE GRAPH_ID | show WORKSPACE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
