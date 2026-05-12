#!/usr/bin/env python3
"""code_repo.py — code-repo detection, pointer-file IO, workspace resolution.

The code-repo mode of curiosity-engine lets a code repo register against
an existing CE workspace via a small `.curiosity/config.toml` pointer
file at the repo root. This script provides the primitives setup.sh and
slash-command handlers use to do that:

  detect [<dir>]                  — exit 0 if dir is a code repo
  is-workspace [<dir>]            — exit 0 if dir is a CE workspace
  default-workspace-root          — emit cross-platform default path
  read-config <pointer-path>      — emit pointer contents as JSON
  write-config <pointer-path>     — write pointer from JSON on stdin
  resolve-workspace [--from <dir>]
                                  — print absolute workspace path; exits
                                    code 2 if no pointer found upward
                                    within the git repo

Stdlib-only (no external dependencies). Hash-guarded by evolve_guard.sh.

Pointer file schema (deliberately small and fixed — no general TOML
parser is required):

    workspace = "~/Documents/curiosity-workspace"
    project = "myapp"
    code_citation_root = "myapp"

    [ingest]
    enabled = true
    paths = ["docs/adr/", "CHANGELOG.md", "README.md"]
    pr_capture = true
    commit_capture = true
    transcript_capture = true

    [brief]
    auto = true
    regenerate_on_pull = false
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# Files whose presence (alongside .git/) classifies a directory as a code
# repo. High-precision set — false positives would mis-route an existing
# user's setup.sh invocation in a non-code directory.
SOURCE_MARKERS = (
    # Python
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    # JS / TS
    "package.json", "deno.json", "deno.jsonc",
    # Rust
    "Cargo.toml",
    # Go
    "go.mod",
    # Ruby
    "Gemfile",
    # Java / JVM
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    # C / C++
    "Makefile", "CMakeLists.txt", "meson.build",
    # PHP
    "composer.json",
    # Swift / iOS
    "Podfile", "Package.swift",
    # Other
    "build.zig",          # Zig
    "mix.exs",            # Elixir
    "rebar.config",       # Erlang
    "shard.yml",          # Crystal
    "stack.yaml",         # Haskell
    "cabal.project",      # Haskell
    "dub.json", "dub.sdl",  # D
    "DESCRIPTION",        # R packages
    "Project.toml",       # Julia
)

# Glob patterns for additional source markers (handles file-pattern
# markers like .NET sln/csproj, Xcode projects, etc.)
SOURCE_MARKER_GLOBS = (
    "*.sln", "*.csproj", "*.fsproj", "*.vbproj",
    "*.xcodeproj", "*.xcworkspace",
)


def is_code_repo(d: Path) -> bool:
    """Return True if `d` looks like a source-code repository."""
    if not (d / ".git").exists():
        return False
    for marker in SOURCE_MARKERS:
        if (d / marker).exists():
            return True
    for pattern in SOURCE_MARKER_GLOBS:
        # Path.glob is iterable; presence of any match is sufficient.
        for _ in d.glob(pattern):
            return True
    return False


def is_workspace(d: Path) -> bool:
    """Return True if `d` is an already-set-up CE workspace.

    Uses two distinctive markers written by setup.sh:
      - .curator/config.json (the curator settings file)
      - wiki/.git/ (the wiki's own git repo)

    Either is sufficient; both being absent means this is not a
    fully-set-up workspace (a fresh dir, a code repo, or a partially
    failed prior setup).
    """
    return (d / ".curator" / "config.json").is_file() or (d / "wiki" / ".git").is_dir()


# ---------------------------------------------------------------------------
# Default workspace path (cross-platform)
# ---------------------------------------------------------------------------


def default_workspace_root() -> str:
    """Resolve a sensible default workspace path.

    Order:
      1. $CURIOSITY_WORKSPACE env var (explicit override)
      2. xdg-user-dir DOCUMENTS / curiosity-workspace (XDG-aware Linux)
      3. $HOME/Documents/curiosity-workspace (macOS / Windows / many Linux)
      4. $HOME/curiosity-workspace (last resort — Documents missing)
    """
    env = os.environ.get("CURIOSITY_WORKSPACE")
    if env:
        return env

    home = Path.home()

    if shutil.which("xdg-user-dir"):
        try:
            result = subprocess.run(
                ["xdg-user-dir", "DOCUMENTS"],
                capture_output=True, text=True, check=True, timeout=5,
            )
            docs = Path(result.stdout.strip())
            if docs.is_dir():
                return str(docs / "curiosity-workspace")
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            pass

    candidate = home / "Documents"
    if candidate.is_dir():
        return str(candidate / "curiosity-workspace")
    return str(home / "curiosity-workspace")


# ---------------------------------------------------------------------------
# Pointer file IO
# ---------------------------------------------------------------------------
#
# Hand-rolled minimal TOML reader/writer. The pointer-file schema is
# small and fixed (top-level scalars + two flat tables), so a full TOML
# parser dependency is unnecessary and would force tomli/tomllib
# version handling across the supported Python range (3.9+).


def _strip_comment(line: str) -> str:
    """Remove a trailing # comment outside of a string literal.

    The pointer-file schema never embeds # inside string values, so
    a single-pass scan that tracks quoted regions is sufficient.
    """
    out = []
    in_str = False
    quote = ""
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\" and i + 1 < len(line):
                out.append(c)
                out.append(line[i + 1])
                i += 2
                continue
            if c == quote:
                in_str = False
            out.append(c)
        else:
            if c in ('"', "'"):
                in_str = True
                quote = c
                out.append(c)
            elif c == "#":
                break
            else:
                out.append(c)
        i += 1
    return "".join(out).rstrip()


def _parse_value(raw: str):
    v = raw.strip()
    if not v:
        return ""
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        return v[1:-1].encode("utf-8").decode("unicode_escape")
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        items = []
        cur = ""
        in_str = False
        quote = ""
        for c in inner:
            if in_str:
                cur += c
                if c == quote:
                    in_str = False
                continue
            if c in ('"', "'"):
                in_str = True
                quote = c
                cur += c
            elif c == ",":
                items.append(_parse_value(cur))
                cur = ""
            else:
                cur += c
        if cur.strip():
            items.append(_parse_value(cur))
        return items
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def read_pointer(path: Path) -> dict:
    """Read a pointer file. Returns nested dict with [section] keys nested."""
    out: dict = {}
    section: dict = out
    section_re = re.compile(r"^\[([A-Za-z0-9_.\-]+)\]$")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        m = section_re.match(line)
        if m:
            section = out.setdefault(m.group(1), {})
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            section[k.strip()] = _parse_value(v)
    return out


def _emit_kv(k: str, v) -> str:
    if isinstance(v, bool):
        return f"{k} = {'true' if v else 'false'}"
    if isinstance(v, list):
        parts = ", ".join(_emit_scalar(x) for x in v)
        return f"{k} = [{parts}]"
    return f"{k} = {_emit_scalar(v)}"


def _emit_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        # Re-escape backslash and double-quote; nothing else in our schema.
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(v)


def write_pointer(path: Path, cfg: dict):
    lines = [
        "# .curiosity/config.toml — committed; routes CE-aware commands run",
        "# in this repo to the named workspace. See docs/code-knowledge.md",
        "# for the full design.",
        "",
    ]
    for k in ("workspace", "project", "code_citation_root"):
        if k in cfg:
            lines.append(_emit_kv(k, cfg[k]))
    if any(k in cfg for k in ("workspace", "project", "code_citation_root")):
        lines.append("")
    for section in ("ingest", "brief"):
        if section in cfg and isinstance(cfg[section], dict):
            lines.append(f"[{section}]")
            for k, v in cfg[section].items():
                lines.append(_emit_kv(k, v))
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------


def expand_workspace_path(s: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(s))).resolve()


def find_pointer_file(start: Path) -> Path | None:
    """Walk up from `start` to find .curiosity/config.toml.

    Bounded by the enclosing git-repo root: never crosses out of the
    repo. This keeps walk-up safe — workspace discovery by directory
    proximity across unrelated workspaces is deliberately not supported.
    """
    start = start.resolve()
    current = start
    while True:
        candidate = current / ".curiosity" / "config.toml"
        if candidate.is_file():
            return candidate
        if (current / ".git").exists():
            return None
        if current.parent == current:
            return None
        current = current.parent


def resolve_workspace(start: Path) -> Path | None:
    """Resolve the active workspace path for a code repo at `start`.

    Order:
      1. $CURIOSITY_WORKSPACE env var (engineer-machine override)
      2. workspace key in .curiosity/config.toml at git-bounded walk-up
      3. None (caller decides what to do)
    """
    env = os.environ.get("CURIOSITY_WORKSPACE")
    if env:
        return Path(os.path.expanduser(env)).resolve()
    pointer = find_pointer_file(start)
    if pointer is None:
        return None
    cfg = read_pointer(pointer)
    raw = cfg.get("workspace")
    if not raw:
        return None
    return expand_workspace_path(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_detect(args):
    d = Path(args.dir or ".").resolve()
    sys.exit(0 if is_code_repo(d) else 1)


def cmd_is_workspace(args):
    d = Path(args.dir or ".").resolve()
    sys.exit(0 if is_workspace(d) else 1)


def cmd_default_workspace_root(args):
    print(default_workspace_root())


def cmd_read_config(args):
    cfg = read_pointer(Path(args.path))
    json.dump(cfg, sys.stdout, indent=2)
    sys.stdout.write("\n")


def cmd_write_config(args):
    cfg = json.load(sys.stdin)
    write_pointer(Path(args.path), cfg)


def cmd_resolve_workspace(args):
    start = Path(args.start or ".").resolve()
    ws = resolve_workspace(start)
    if ws is None:
        sys.exit(2)
    print(ws)


def main():
    p = argparse.ArgumentParser(prog="code_repo.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("detect", help="exit 0 if dir is a code repo")
    s.add_argument("dir", nargs="?")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("is-code-repo", help="alias of detect")
    s.add_argument("dir", nargs="?")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("is-workspace", help="exit 0 if dir is a CE workspace")
    s.add_argument("dir", nargs="?")
    s.set_defaults(fn=cmd_is_workspace)

    s = sub.add_parser("default-workspace-root",
                       help="emit cross-platform default workspace path")
    s.set_defaults(fn=cmd_default_workspace_root)

    s = sub.add_parser("read-config", help="emit pointer contents as JSON")
    s.add_argument("path")
    s.set_defaults(fn=cmd_read_config)

    s = sub.add_parser("write-config", help="write pointer from JSON on stdin")
    s.add_argument("path")
    s.set_defaults(fn=cmd_write_config)

    s = sub.add_parser("resolve-workspace",
                       help="resolve workspace path; exit 2 if no pointer")
    s.add_argument("--from", dest="start", default=".")
    s.set_defaults(fn=cmd_resolve_workspace)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
