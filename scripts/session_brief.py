#!/usr/bin/env python3
"""session_brief.py — generate a per-(code-repo, branch) brief that the
coding agent reads at session start.

Closes the loop the engineer feels: a fresh agent in a fresh session
gets yesterday's context for the files in their current branch's diff,
without re-deriving everything from grep + read.

Output goes to `<code-repo>/.curiosity/session-brief.md` (per-machine,
gitignored). Cheap to overwrite — different engineers see different
briefs because their branches differ.

Inputs:
  - pointer file (project tag + workspace path)
  - `git diff <base>...HEAD --name-only` for files in flight
  - workspace's wiki/ — entities, analyses, notes/ tagged with project
  - workspace's .curator/activity.log — recent project events

The brief is a digest, not a doc. Wikilinks resolve on click in
Obsidian / VS Code+Foam / the bundled viewer.

Usage:
  session_brief.py [--repo PATH] [--workspace PATH] [--days N] [--quiet]
                              # default: cwd as repo, resolve workspace
                              # via pointer, 14-day activity window

Stdlib only. Hash-guarded.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import code_repo  # noqa: E402

DEFAULT_DAYS = 14
MAX_ANALYSES = 8
MAX_NOTES = 6
MAX_ENTITIES_PER_FILE = 3


# ---------------------------------------------------------------------------
# Frontmatter parsing (minimal — we only need a few keys)
# ---------------------------------------------------------------------------


def _read_fm(path: Path) -> dict:
    """Read a markdown file's YAML frontmatter as a flat dict.

    Tolerant: missing frontmatter or unparseable values yield {}.
    Only top-level scalar keys + simple `[a, b, c]` arrays are parsed —
    that's all we need here.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return {}
    block = text[3:end]
    out: dict = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1]
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            v = [x.strip().strip('"') for x in inner.split(",") if x.strip()]
        elif v.lower() in ("true", "false"):
            v = (v.lower() == "true")
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Git context
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path, timeout: int = 10) -> str:
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd, capture_output=True, text=True, check=True, timeout=timeout,
        )
        return r.stdout
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return ""


def _branch(repo: Path) -> str:
    return (_git(["rev-parse", "--abbrev-ref", "HEAD"], repo).strip()
            or "(detached)")


def _resolve_base_ref(repo: Path) -> str:
    """Pick a reasonable base for the diff. Prefer 'main' or 'master',
    fall back to HEAD~5 (covers the local-only / pre-trunk-merge case)."""
    for candidate in ("main", "master"):
        if _git(["rev-parse", "--verify", candidate], repo).strip():
            return candidate
    # Detached or no main — use a recent ancestor.
    if _git(["rev-parse", "--verify", "HEAD~5"], repo).strip():
        return "HEAD~5"
    return "HEAD"


def _files_in_flight(repo: Path) -> list[str]:
    """Files differing between current HEAD and the resolved base ref."""
    base = _resolve_base_ref(repo)
    if base == "HEAD":
        # Bootstrap repo with a single commit — nothing to compare against.
        return []
    out = _git(["diff", f"{base}...HEAD", "--name-only"], repo)
    return [line.strip() for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Workspace queries
# ---------------------------------------------------------------------------


def _stem_of(path_str: str) -> str:
    """Filename → page-stem candidate. `src/auth/middleware.py` →
    `middleware`."""
    return Path(path_str).stem


def _project_tag(fm: dict) -> str | None:
    p = fm.get("project")
    if isinstance(p, str):
        return p
    if isinstance(p, list) and p:
        return p[0]
    return None


def _projects_match(fm: dict, project: str) -> bool:
    if not project:
        return True
    p = fm.get("project")
    if isinstance(p, str):
        return p == project
    if isinstance(p, list):
        return project in p
    # Also check `projects: [...]` (the multi-project ingest convention)
    pp = fm.get("projects")
    if isinstance(pp, list):
        return project in pp
    return False


def _find_entities_for_file(workspace: Path, file_path: str,
                            project: str) -> list[Path]:
    """Find wiki/entities/ pages whose stem matches the file's basename."""
    stem = _stem_of(file_path).lower()
    if not stem:
        return []
    entities_dir = workspace / "wiki" / "entities"
    if not entities_dir.is_dir():
        return []
    matches = []
    for p in entities_dir.glob("*.md"):
        page_stem = p.stem.lower()
        # Match exact stem, or stem ending in -<stem>, or starting with
        # <stem>- (handles common `[ent]-stem` style page names).
        if (page_stem == stem
                or page_stem.endswith(f"-{stem}")
                or page_stem.startswith(f"{stem}-")):
            fm = _read_fm(p)
            if _projects_match(fm, project):
                matches.append(p)
            if len(matches) >= MAX_ENTITIES_PER_FILE:
                break
    return matches


def _recent_pages(dir_path: Path, project: str, *,
                  since: datetime, limit: int,
                  fm_filter=None) -> list[tuple[Path, dict]]:
    """List pages under dir_path sorted by mtime desc, project-tagged,
    optionally fm-filtered, capped at limit."""
    if not dir_path.is_dir():
        return []
    candidates = []
    for p in dir_path.glob("*.md"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < since:
            continue
        fm = _read_fm(p)
        if not _projects_match(fm, project):
            continue
        if fm_filter is not None and not fm_filter(fm):
            continue
        candidates.append((mtime, p, fm))
    candidates.sort(key=lambda t: t[0], reverse=True)
    return [(p, fm) for _, p, fm in candidates[:limit]]


def _read_activity_log(workspace: Path, project: str,
                       since: datetime, limit: int = 50) -> list[dict]:
    log = workspace / ".curator" / "activity.log"
    if not log.is_file():
        return []
    events = []
    try:
        for line in log.read_text(encoding="utf-8").splitlines()[-2000:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = obj.get("ts") or obj.get("at")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if t < since:
                continue
            projects = obj.get("projects") or []
            if project and project not in projects:
                continue
            events.append(obj)
    except OSError:
        return []
    return events[-limit:]


# ---------------------------------------------------------------------------
# Brief composition
# ---------------------------------------------------------------------------


def _wikilink(page: Path) -> str:
    return f"[[{page.stem}]]"


def _short_summary(page: Path, fm: dict, max_chars: int = 140) -> str:
    """First non-blank prose line from the body, capped."""
    try:
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # Strip frontmatter
    if text.startswith("---"):
        try:
            end = text.index("\n---", 3)
            text = text[end + 4:]
        except ValueError:
            pass
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Strip wikilinks for compactness
        s = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", s)
        if len(s) > max_chars:
            s = s[: max_chars - 1].rsplit(" ", 1)[0] + "…"
        return s
    return ""


def compose_brief(repo: Path, workspace: Path, project: str,
                  days: int) -> str:
    branch = _branch(repo)
    files = _files_in_flight(repo)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    repo_name = repo.name

    out: list[str] = []
    out.append(f"# Session brief — {repo_name} / branch: {branch}")
    out.append(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.")
    out.append(f"Project tag: `{project}`. Workspace: `{workspace}`.")
    out.append("")

    if files:
        out.append(f"## In flight ({len(files)} files vs. base)")
        out.append("")
        for f in files[:30]:
            out.append(f"- `{f}`")
        if len(files) > 30:
            out.append(f"- ... and {len(files) - 30} more")
        out.append("")

        # Entities for in-flight files
        entity_hits: list[tuple[str, Path]] = []
        seen = set()
        for f in files:
            for ent in _find_entities_for_file(workspace, f, project):
                if ent in seen:
                    continue
                seen.add(ent)
                entity_hits.append((f, ent))
        if entity_hits:
            out.append("## Wiki pages for files in flight")
            out.append("")
            for f, ent in entity_hits[:20]:
                fm = _read_fm(ent)
                summary = _short_summary(ent, fm)
                out.append(f"- {_wikilink(ent)} (matches `{f}`)" +
                           (f" — {summary}" if summary else ""))
            out.append("")
    else:
        out.append("## In flight")
        out.append("")
        out.append("No diff vs. base — fresh checkout, on the base branch, or "
                   "no shared base ref. Brief skips the file-in-flight section.")
        out.append("")

    # Recent decisions / analyses
    analyses_dir = workspace / "wiki" / "analyses"
    recent_analyses = _recent_pages(analyses_dir, project, since=since,
                                    limit=MAX_ANALYSES)
    if recent_analyses:
        out.append(f"## Recent analyses ({len(recent_analyses)})")
        out.append("")
        for p, fm in recent_analyses:
            summary = _short_summary(p, fm)
            out.append(f"- {_wikilink(p)}" + (f" — {summary}" if summary else ""))
        out.append("")

    # Recent notes (decisions, gotchas, constraints captured but not yet drained)
    notes_dir = workspace / "wiki" / "notes"
    for kind in ("decisions", "gotchas", "constraints"):
        page = notes_dir / f"{kind}.md"
        if page.is_file():
            fm = _read_fm(page)
            if not _projects_match(fm, project):
                # The notes page itself may not carry project tag, but
                # individual entries inside it might. Keep it for now —
                # the agent reads the page on click.
                pass
            mtime = datetime.fromtimestamp(page.stat().st_mtime, tz=timezone.utc)
            if mtime >= since:
                out.append(f"- {_wikilink(page)} (recent {kind})")
    if any((notes_dir / f"{k}.md").is_file() for k in
           ("decisions", "gotchas", "constraints")):
        out.append("")

    # Recent activity events
    events = _read_activity_log(workspace, project, since=since, limit=20)
    if events:
        out.append(f"## Recent activity ({len(events)} events, last {days}d)")
        out.append("")
        for e in events[-10:]:
            kind = e.get("kind") or e.get("event") or "?"
            page = e.get("page") or ""
            ts = e.get("ts") or e.get("at") or ""
            out.append(f"- `{ts[:10]}` {kind} — {page}")
        out.append("")

    if not files and not recent_analyses and not events:
        out.append("---")
        out.append("")
        out.append("Workspace has nothing tagged with this project yet. As "
                   "captures accumulate (PR merges, sessions, distillations), "
                   "this brief will fill in. See `docs/code-knowledge.md` for "
                   "how capture is wired.")

    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(prog="session_brief.py", description=__doc__)
    p.add_argument("--repo", default=".",
                   help="path to the code repo (default: cwd)")
    p.add_argument("--workspace", default=None,
                   help="workspace path (default: resolve via pointer)")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"activity window in days (default {DEFAULT_DAYS})")
    p.add_argument("--quiet", action="store_true",
                   help="don't print the brief to stdout, just write the file")
    args = p.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".curiosity" / "config.toml").is_file():
        print(f"no .curiosity/config.toml at {repo}", file=sys.stderr)
        sys.exit(1)
    cfg = code_repo.read_pointer(repo / ".curiosity" / "config.toml")
    project = cfg.get("project") or repo.name

    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
    else:
        ws = code_repo.resolve_workspace(repo)
        if ws is None:
            print("could not resolve workspace from pointer; pass --workspace",
                  file=sys.stderr)
            sys.exit(1)
        workspace = ws
    if not (workspace / "wiki").is_dir():
        print(f"workspace not found or missing wiki/: {workspace}",
              file=sys.stderr)
        sys.exit(1)

    brief = compose_brief(repo, workspace, project, args.days)
    out_dir = repo / ".curiosity"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "session-brief.md"
    out.write_text(brief, encoding="utf-8")
    if not args.quiet:
        print(brief)
    print(f"# wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
