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
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running directly without installing naming.py — same package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from naming import read_frontmatter, set_frontmatter_field  # noqa: E402

# Project name discipline: lowercase letter, then any of
# letters/digits/hyphens, ending on a letter or digit. Keeps stems
# stable across the wiki, the registry, and the home-page filename.
_NAME_RE = re.compile(r"^[a-z]([a-z0-9-]*[a-z0-9])?$")

REGISTRY = Path(".curator/projects.json")
WIKI = Path("wiki")
HOMES = WIKI / "projects"
DELETED_ROOT = WIKI / ".deleted"


# Wikilink rewrite — used by rename. Matches `[[stem]]` and
# `[[stem|alias]]`. Anchor on word boundaries inside the brackets so
# `[[foo]]` doesn't get confused with `[[foobar]]`.
def _wikilink_pattern(stem: str) -> re.Pattern:
    return re.compile(r"\[\[" + re.escape(stem) + r"(\|[^\]\n]*)?\]\]")


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


def _iter_wiki_pages():
    """Yield (path, projects_list, full_text) for every .md file under
    wiki/ excluding the .deleted/ tree and dotfile-prefixed paths."""
    if not WIKI.exists():
        return
    for p in WIKI.rglob("*.md"):
        if any(part.startswith(".") for part in p.parts):
            continue
        text = p.read_text()
        fm, _ = read_frontmatter(text)
        raw = fm.get("projects") or []
        if isinstance(raw, str):
            raw = [raw]
        yield p, list(raw), text


def _projects_value(sorted_names: list[str]) -> str:
    return f"[{', '.join(sorted_names)}]" if sorted_names else "[]"


def _rewrite_wikilinks(src_stem: str, dst_stem: str) -> int:
    """Rewrite `[[src]]` and `[[src|alias]]` to `[[dst]]` /
    `[[dst|alias]]` across the wiki. Returns count of links rewritten."""
    pat = _wikilink_pattern(src_stem)
    count = 0
    for p in WIKI.rglob("*.md"):
        if any(part.startswith(".") for part in p.parts):
            continue
        text = p.read_text()
        new_text, n = pat.subn(lambda m: f"[[{dst_stem}{m.group(1) or ''}]]", text)
        if n:
            p.write_text(new_text)
            count += n
    return count


def cmd_rename(args: argparse.Namespace) -> int:
    """Rename project <from> to <to>, or absorb <from> into <to> if
    <to> already exists. Mechanical link rewrite — no deleted-table
    snapshot. Use `delete` instead if recoverability is needed."""
    src = args.from_name
    dst = args.to_name
    if not _is_valid_name(src) or not _is_valid_name(dst):
        print(
            f"ERROR: invalid project name. Use kebab-case: "
            f"lowercase letters and digits separated by hyphens, "
            f"starting with a letter.",
            file=sys.stderr,
        )
        return 2
    if src == dst:
        print(f"ERROR: source and destination are the same ({src!r}).", file=sys.stderr)
        return 2

    reg = _load_registry()
    src_meta = reg["projects"].get(src)
    if not src_meta or src_meta.get("deleted_at"):
        print(f"ERROR: source project {src!r} not found (or already deleted).", file=sys.stderr)
        return 1
    dst_meta = reg["projects"].get(dst)
    dst_exists = dst_meta is not None and not dst_meta.get("deleted_at")

    # Step 1: rewrite project tags on every page.
    pages_changed = 0
    for path, projects_list, text in _iter_wiki_pages():
        if src not in projects_list:
            continue
        new_set = set(projects_list)
        new_set.discard(src)
        new_set.add(dst)
        new_text = set_frontmatter_field(text, "projects", _projects_value(sorted(new_set)))
        if new_text != text:
            path.write_text(new_text)
            pages_changed += 1

    # Step 2: home pages.
    src_home = HOMES / f"{src}.md"
    dst_home = HOMES / f"{dst}.md"
    if dst_exists and dst_home.exists():
        # Absorption: src home goes away; rewrite [[src]] wikilinks to [[dst]].
        if src_home.exists():
            src_home.unlink()
    elif src_home.exists():
        # Pure rename: move home page and rewrite its frontmatter title + projects.
        src_home.rename(dst_home)
        text = dst_home.read_text()
        text = set_frontmatter_field(text, "projects", _projects_value([dst]))
        text = re.sub(
            r'^title:\s*"\[proj\]\s+' + re.escape(src) + r'"',
            f'title: "[proj] {dst}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        dst_home.write_text(text)

    wikilinks_rewritten = _rewrite_wikilinks(src, dst)

    # Step 3: registry.
    if dst_exists:
        # Absorption: keep dst's metadata, drop src.
        reg["projects"].pop(src)
    else:
        # Pure rename: carry src's metadata over under the new name.
        new_meta = dict(src_meta)
        new_meta["home_page"] = f"projects/{dst}.md"
        reg["projects"].pop(src)
        reg["projects"][dst] = new_meta
    _save_registry(reg)

    action = "absorbed into" if dst_exists else "renamed to"
    print(
        f"project {src!r} {action} {dst!r}: "
        f"{pages_changed} page(s) re-tagged, "
        f"{wikilinks_rewritten} wikilink(s) rewritten."
    )
    print("Run `graph.py rebuild wiki` to refresh the kuzu graph.")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    """Soft-delete a project. Single-tagged pages move to
    wiki/.deleted/<name>/; multi-tagged pages just drop the tag.
    A manifest at .deleted/<name>/_manifest.json records what
    happened so `restore` can reverse it."""
    name = args.name
    if not _is_valid_name(name):
        print(f"ERROR: invalid project name {name!r}.", file=sys.stderr)
        return 2

    reg = _load_registry()
    meta = reg["projects"].get(name)
    if not meta or meta.get("deleted_at"):
        print(f"ERROR: project {name!r} not found (or already deleted).", file=sys.stderr)
        return 1

    deleted_dir = DELETED_ROOT / name
    deleted_dir.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    untagged: list[str] = []
    for path, projects_list, text in _iter_wiki_pages():
        if name not in projects_list:
            continue
        rel = str(path.relative_to(WIKI))
        if len(projects_list) == 1:
            dest = deleted_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            moved.append(rel)
        else:
            new_set = sorted(set(projects_list) - {name})
            new_text = set_frontmatter_field(text, "projects", _projects_value(new_set))
            if new_text != text:
                path.write_text(new_text)
                untagged.append(rel)

    manifest = {
        "project": name,
        "deleted_at": _now_iso(),
        "moved_pages": moved,
        "untagged_pages": untagged,
    }
    (deleted_dir / "_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    reg["projects"][name]["deleted_at"] = manifest["deleted_at"]
    _save_registry(reg)

    print(
        f"project {name!r} soft-deleted: "
        f"{len(moved)} page(s) moved to wiki/.deleted/{name}/, "
        f"{len(untagged)} multi-tagged page(s) had tag dropped."
    )
    print(f"  Restore: projects.py restore {name}")
    print(f"  Hard-delete: projects.py purge {name}")
    print(
        "  Note: vault files associated with the deleted scope were "
        "NOT moved in this wave; vault hygiene is deferred to a "
        "future wave."
    )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a soft-deleted project: move pages back from
    wiki/.deleted/<name>/ and re-add the tag to multi-tagged pages
    listed in the manifest. Idempotent if the project is already live."""
    name = args.name
    if not _is_valid_name(name):
        print(f"ERROR: invalid project name {name!r}.", file=sys.stderr)
        return 2

    reg = _load_registry()
    meta = reg["projects"].get(name)
    if not meta:
        print(f"ERROR: project {name!r} unknown.", file=sys.stderr)
        return 1
    if not meta.get("deleted_at"):
        print(f"project {name!r} is not currently deleted; nothing to do.")
        return 0

    deleted_dir = DELETED_ROOT / name
    manifest_path = deleted_dir / "_manifest.json"
    if not manifest_path.exists():
        print(
            f"ERROR: no manifest at {manifest_path}; cannot restore.",
            file=sys.stderr,
        )
        return 1
    manifest = json.loads(manifest_path.read_text())

    restored = 0
    for rel in manifest.get("moved_pages", []):
        src = deleted_dir / rel
        dst = WIKI / rel
        if not src.exists():
            print(f"  WARN: {src} missing; skipping", file=sys.stderr)
            continue
        if dst.exists():
            print(
                f"  WARN: {dst} already exists; leaving the .deleted "
                "copy in place. Resolve manually.",
                file=sys.stderr,
            )
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        restored += 1

    re_tagged = 0
    for rel in manifest.get("untagged_pages", []):
        path = WIKI / rel
        if not path.exists():
            continue
        text = path.read_text()
        fm, _ = read_frontmatter(text)
        raw = fm.get("projects") or []
        if isinstance(raw, str):
            raw = [raw]
        if name in raw:
            continue  # already restored or never untagged
        new_set = sorted(set(raw) | {name})
        new_text = set_frontmatter_field(text, "projects", _projects_value(new_set))
        if new_text != text:
            path.write_text(new_text)
            re_tagged += 1

    # Archive the manifest under .deleted/.history/ so restore stays
    # idempotent (re-running won't find it). Purge can clean it later.
    history_dir = DELETED_ROOT / ".history"
    history_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.rename(
        history_dir / f"{name}-restored-{_now_iso().replace(':', '')}.json"
    )
    # Remove now-empty deleted_dir if nothing else lives there.
    try:
        next(deleted_dir.rglob("*"))
    except StopIteration:
        deleted_dir.rmdir()

    reg["projects"][name]["deleted_at"] = None
    _save_registry(reg)

    print(
        f"project {name!r} restored: "
        f"{restored} page(s) moved back, "
        f"{re_tagged} multi-tagged page(s) re-tagged."
    )
    print("Run `graph.py rebuild wiki` to refresh the kuzu graph.")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    """Hard-delete a soft-deleted project: remove
    wiki/.deleted/<name>/ entirely and drop the registry entry.
    Refuses to purge a project that isn't currently soft-deleted —
    `delete` first if you want to remove a live project."""
    name = args.name
    if not _is_valid_name(name):
        print(f"ERROR: invalid project name {name!r}.", file=sys.stderr)
        return 2

    reg = _load_registry()
    meta = reg["projects"].get(name)
    if not meta:
        # Already gone. Idempotent.
        deleted_dir = DELETED_ROOT / name
        if deleted_dir.exists():
            shutil.rmtree(deleted_dir)
            print(f"removed orphan wiki/.deleted/{name}/ (no registry entry).")
        else:
            print(f"project {name!r} unknown; nothing to purge.")
        return 0
    if not meta.get("deleted_at"):
        print(
            f"ERROR: project {name!r} is currently active. "
            f"Run `projects.py delete {name}` first if you want to remove it.",
            file=sys.stderr,
        )
        return 1

    deleted_dir = DELETED_ROOT / name
    if deleted_dir.exists():
        shutil.rmtree(deleted_dir)
    reg["projects"].pop(name)
    _save_registry(reg)
    print(f"project {name!r} purged: wiki/.deleted/{name}/ removed, registry entry dropped.")
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

    p_rename = sub.add_parser(
        "rename",
        help="Rename project <from> to <to>, or absorb <from> into <to> if <to> exists",
    )
    p_rename.add_argument("from_name", metavar="from", help="Source project name")
    p_rename.add_argument("to_name", metavar="to", help="Destination project name")
    p_rename.set_defaults(func=cmd_rename)

    p_delete = sub.add_parser(
        "delete",
        help="Soft-delete: single-tagged pages → wiki/.deleted/<name>/; multi-tagged pages drop the tag",
    )
    p_delete.add_argument("name", help="Project name")
    p_delete.set_defaults(func=cmd_delete)

    p_restore = sub.add_parser(
        "restore",
        help="Restore a soft-deleted project: move pages back, re-tag multi-tagged pages from manifest",
    )
    p_restore.add_argument("name", help="Project name")
    p_restore.set_defaults(func=cmd_restore)

    p_purge = sub.add_parser(
        "purge",
        help="Hard-delete: remove wiki/.deleted/<name>/ and drop registry entry. Project must be soft-deleted first.",
    )
    p_purge.add_argument("name", help="Project name")
    p_purge.set_defaults(func=cmd_purge)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
