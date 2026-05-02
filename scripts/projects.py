#!/usr/bin/env python3
"""
Project registry and lifecycle commands.

A project is a logical grouping of wiki pages and vault sources.
Project membership lives on individual pages as a `projects: [...]`
frontmatter field, populated by the `classify-projects` sweep op
from the citation graph (semantic similarity step deferred to a
later wave).

This script is the registry/home-page side: it owns
`.curator/projects.json` and `wiki/projects/<name>.md` files.
The classifier and the recency-weighted planner read what this
script writes.

Subcommands implemented in this wave:
  create <name> [--description "..."]   create a project + home page
  list [--include-deleted]              list known projects
  exists <name>                         exit 0 if exists, 1 otherwise

Subcommands deferred to wave 1b/1c (per docs/multi-project.md):
  rename, delete, restore, purge

The orchestrator's conversational layer is responsible for slugifying
human-readable names ("Project A" -> "project-a") and confirming with
the user before calling this script. This script itself is strict:
it rejects names that aren't already valid kebab-case slugs so it
can't silently rename behind the user's back.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Project name discipline: lowercase letter, then any of
# letters/digits/hyphens, ending on a letter or digit. Keeps stems
# stable across the wiki, the registry, and the home-page filename.
_NAME_RE = re.compile(r"^[a-z]([a-z0-9-]*[a-z0-9])?$")

REGISTRY = Path(".curator/projects.json")
HOMES = Path("wiki/projects")


def _is_valid_name(s: str) -> bool:
    return bool(_NAME_RE.match(s))


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_registry() -> dict:
    if not REGISTRY.exists():
        return {"projects": {}}
    raw = json.loads(REGISTRY.read_text())
    raw.setdefault("projects", {})
    return raw


def _save_registry(reg: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")


def _read_existing_description(home: Path) -> str:
    """Best-effort read of `description:` from an existing home-page
    frontmatter. Done with a tiny inline parser to avoid a hard import
    dependency on naming.py at script-load time (this script ships in
    the same scripts/ dir but I want it usable before naming.py is
    available — and naming.py drops unknown keys, so a quoted-string
    description that the user pre-wrote in some other YAML style would
    get lost). Returns empty string when not present."""
    try:
        text = home.read_text()
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    block = text[3:end]
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith("description:"):
            continue
        val = line[len("description:"):].strip()
        # Strip surrounding quotes if present.
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        return val
    return ""


def _format_home_page(name: str, description: str) -> str:
    desc = description.strip()
    body = (
        desc if desc
        else "_No description yet — the curator will summarise this project from imported content as pages are tagged with it._"
    )
    today = _today()
    fm_lines = [
        "---",
        f'title: "[proj] {name}"',
        "type: project",
        f"created: {today}",
        f"updated: {today}",
        f"projects: [{name}]",
    ]
    if desc:
        # Quote with double quotes; escape any inner double quotes.
        safe = desc.replace('"', '\\"')
        fm_lines.append(f'description: "{safe}"')
    fm_lines.append("---")
    return (
        "\n".join(fm_lines)
        + "\n\n"
        + body
        + "\n\n"
        + "## pages\n\n"
        + "_Populated by the curator as pages are classified into this project._\n"
    )


def cmd_create(args: argparse.Namespace) -> int:
    name = args.name
    if not _is_valid_name(name):
        print(
            f"ERROR: invalid project name {name!r}. "
            "Use kebab-case: lowercase letters and digits separated by hyphens, "
            "starting with a letter (e.g. 'speech-recognition').",
            file=sys.stderr,
        )
        return 2

    reg = _load_registry()
    existing = reg["projects"].get(name)
    if existing and existing.get("deleted_at") is None:
        print(f"ERROR: project {name!r} already exists.", file=sys.stderr)
        return 1

    HOMES.mkdir(parents=True, exist_ok=True)
    home = HOMES / f"{name}.md"

    description = (args.description or "").strip()
    if home.exists():
        # Manual mode: home page was created by hand. Leave it alone;
        # only register it. Recover the description from frontmatter
        # if the user supplied one there.
        if not description:
            description = _read_existing_description(home)
        action = "registered (home page existed)"
    else:
        home.write_text(_format_home_page(name, description))
        action = "created"

    reg["projects"][name] = {
        "created_at": _now_iso(),
        "deleted_at": None,
        "description": description,
        "home_page": f"projects/{name}.md",
    }
    _save_registry(reg)
    print(f"project {name!r} {action} (home: {home})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    reg = _load_registry()
    projects = reg.get("projects", {})
    rows: list[tuple[str, str, str]] = []
    for project_name in sorted(projects):
        meta = projects[project_name]
        deleted = meta.get("deleted_at")
        if deleted and not args.include_deleted:
            continue
        status = "deleted" if deleted else "active"
        desc = (meta.get("description") or "").splitlines()[0] if meta.get("description") else ""
        if len(desc) > 60:
            desc = desc[:57] + "..."
        rows.append((project_name, status, desc))

    if not rows:
        print("(no projects registered)")
        return 0

    name_w = max(len(r[0]) for r in rows)
    for project_name, status, desc in rows:
        print(f"{project_name:<{name_w}}  {status:<8}  {desc}")
    return 0


def cmd_exists(args: argparse.Namespace) -> int:
    reg = _load_registry()
    meta = reg.get("projects", {}).get(args.name)
    if meta and meta.get("deleted_at") is None:
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Project registry and lifecycle commands for curiosity-engine."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create a project + home page (idempotent register if home page exists)")
    p_create.add_argument("name", help="Project name (kebab-case slug)")
    p_create.add_argument("--description", help="One-line project description for the home page")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="List known projects")
    p_list.add_argument("--include-deleted", action="store_true", help="Include soft-deleted projects in the listing")
    p_list.set_defaults(func=cmd_list)

    p_exists = sub.add_parser("exists", help="Exit 0 if project exists (and isn't soft-deleted), 1 otherwise")
    p_exists.add_argument("name", help="Project name")
    p_exists.set_defaults(func=cmd_exists)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
