#!/usr/bin/env bash
set -e

echo "=== Curiosity Engine Setup ==="

# Interactive-mode predicate. The historical check was a bare
# `[ -t 0 ] && [ -t 1 ]`, but that misfires under coding-agent CLIs that
# allocate a PTY for the subprocess without any way to forward user
# keystrokes (GitHub Copilot Chat in VS Code is the prominent case):
# every `read -r reply` then blocks indefinitely. Callers — most
# importantly update.sh's migration pass — set
# CURIOSITY_ENGINE_NONINTERACTIVE=1 to force the non-TTY branch
# regardless of what isatty(3) reports.
_is_interactive() {
    [ "${CURIOSITY_ENGINE_NONINTERACTIVE:-0}" != "1" ] && [ -t 0 ] && [ -t 1 ]
}

# Pre-flight checks. Fail fast with clear messages instead of failing
# cryptically deep in the script. The three hard requirements: git (the
# wiki IS a git repo), python3 ≥ 3.9 (scripts use `from __future__ import
# annotations` + newer typing forms), and a working shell (already here
# since we're running).
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git not found on PATH. The wiki is a git repository — install git first:"
    echo "  macOS:  brew install git  (or xcode-select --install)"
    echo "  Linux:  apt install git / dnf install git / pacman -S git"
    echo "  Windows: install Git for Windows, then run this under Git Bash or WSL"
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found on PATH. Install Python 3.9 or newer first."
    exit 1
fi
_py_major=$(python3 -c "import sys; print(sys.version_info.major)")
_py_minor=$(python3 -c "import sys; print(sys.version_info.minor)")
_py_version="${_py_major}.${_py_minor}"
if [ "$_py_major" -lt 3 ] || { [ "$_py_major" -eq 3 ] && [ "$_py_minor" -lt 9 ]; }; then
    echo "ERROR: Python $_py_version found; curiosity-engine needs Python 3.9 or newer."
    echo "       Upgrade Python (pyenv, asdf, or your distro package manager) and rerun."
    exit 1
fi

# Resolve paths. SCRIPT_DIR is the installed skill's scripts/ directory;
# TEMPLATE_DIR is its sibling template/ — the single source of truth for
# the wiki and curator skeleton copied into each new workspace.
#
# Path discipline: the skill is typically installed at
# ~/.claude/skills/<name> which is a symlink to ~/.agents/skills/<name>
# (or wherever npx-skills dropped the real tree). Claude Code's
# `<skill_path>` substitution is NOT stable across sessions — sometimes
# it resolves the symlink (physical path), sometimes it doesn't
# (logical path). If the allowlist only has one form, the other trips
# an approval prompt. So we compute both and emit allowlist entries for
# each when they differ; for direct-clone installs they're equal and
# de-dupe naturally.
_src_dir="$(dirname "$0")"
# Two independent derivations: `cd` without -P preserves the symlink
# (logical); `cd` followed by `pwd -P` canonicalizes (physical). Deriving
# one from the other would collapse both to the same value, so each path
# starts from the original $0 source dir.
#
# Portability note. Claude Code's skill loader substitutes `<skill_path>`
# at invocation time; setup.sh can always derive its own scripts dir from
# $0. Orchestration prompts that run under other coding-agent CLIs may
# need CURIOSITY_ENGINE_SCRIPTS_DIR exported in the environment to stand
# in for `<skill_path>/scripts`. That export is a runtime concern (used
# by the orchestrator), not a setup-time one — setup.sh itself doesn't
# depend on it.
SCRIPT_DIR_LOGICAL="$(cd "$_src_dir" && pwd)"
SCRIPT_DIR_PHYSICAL="$(cd "$_src_dir" && pwd -P)"
SKILL_ROOT_LOGICAL="$(dirname "$SCRIPT_DIR_LOGICAL")"
SKILL_ROOT_PHYSICAL="$(dirname "$SCRIPT_DIR_PHYSICAL")"
SCRIPT_DIR="$SCRIPT_DIR_PHYSICAL"    # internal file ops — unambiguous
SKILL_ROOT="$SKILL_ROOT_PHYSICAL"
TEMPLATE_DIR="$SKILL_ROOT/template"
SKILL_ROOTS=("$SKILL_ROOT_PHYSICAL")
if [ "$SKILL_ROOT_LOGICAL" != "$SKILL_ROOT_PHYSICAL" ]; then
    SKILL_ROOTS+=("$SKILL_ROOT_LOGICAL")
fi
# npx-skills lays the install out as .agents/skills/<name> (physical)
# with ~/.claude/skills/<name> as a symlink to it. If setup.sh was
# invoked via the physical path (e.g. `bash ~/.agents/skills/...`),
# SCRIPT_DIR_LOGICAL and SCRIPT_DIR_PHYSICAL resolve to the same path
# and the allowlist only gets the physical form — but Claude Code at
# runtime invokes scripts via the .claude/skills/ logical path, so
# prefix matching fails and users hit approval prompts. Probe for the
# sibling form directly and include whichever exists.
#
# Use variables for the patterns: bash parameter substitution treats
# `\/` in the replacement string as a literal `\/` (preserving the
# backslash), which would give `/Users/foo\/.claude\/...` and break
# the `-d` check. Variable interpolation sidesteps the escape problem.
_agents_seg=".agents/skills"
_claude_seg=".claude/skills"
for _alt in "${SKILL_ROOT_PHYSICAL/$_agents_seg/$_claude_seg}" \
            "${SKILL_ROOT_PHYSICAL/$_claude_seg/$_agents_seg}"; do
    if [ "$_alt" != "$SKILL_ROOT_PHYSICAL" ] && [ -d "$_alt" ]; then
        case " ${SKILL_ROOTS[*]} " in
            *" $_alt "*) ;;
            *) SKILL_ROOTS+=("$_alt") ;;
        esac
    fi
done

# Ensure `uv` is available. The skill's canonical Python invocation is
# `uv run python3 ...`, which auto-discovers the workspace `.venv`. Without
# uv the allowlist won't match and every python command triggers approval.
if ! command -v uv >/dev/null 2>&1; then
    if _is_interactive; then
        printf "uv not found. Install uv from astral.sh? [Y/n] "
        read -r reply_uv || reply_uv="y"
    else
        reply_uv="y"
    fi
    case "$reply_uv" in
        ""|y|Y|yes|YES)
            echo "  Installing uv ..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
            # shellcheck disable=SC1091
            [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
            export PATH="$HOME/.local/bin:$PATH"
            ;;
        *)
            echo "  Cannot proceed without uv. Install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
            ;;
    esac
fi

# Workspace-local uv cache (sandbox-safe by default). Coding-agent CLIs
# with strict filesystem sandboxes (Codex CLI is the prominent case) deny
# reads outside the workspace, including uv's default cache at
# ~/.cache/uv/. Every `uv run` then trips an escalation prompt because uv
# touches its cache on every invocation. Fix it at the source: tell uv to
# keep its cache inside the workspace via uv.toml. uv auto-discovers the
# file from cwd; no env vars, no per-host config.
#
# The cache itself is seeded by APFS / reflink clone of the existing
# global cache when possible — instant, and shares storage with the
# original until divergence (so N workspaces don't pay N × full-cache on
# disk). On filesystems without reflink support (older Linux, Windows,
# cross-volume installs) we fall back to a real recursive copy (one-time
# disk cost) or an empty directory (uv populates from network lazily).
mkdir -p .curator
if [ ! -f uv.toml ]; then
    cat > uv.toml <<'EOF'
# Workspace-local uv cache. Keeps uv's reads/writes inside the workspace
# so coding-agent CLIs with strict filesystem sandboxes (Codex CLI, etc.)
# don't escalate on every `uv run`. Harmless under Claude Code — uv
# auto-discovers this file from cwd. Written by curiosity-engine setup.sh;
# safe to delete if you want uv to use its global cache instead.
cache-dir = ".curator/uv-cache"
EOF
    echo "  Wrote uv.toml (cache-dir = .curator/uv-cache)"
fi
if [ ! -d .curator/uv-cache ]; then
    _src_cache="${UV_CACHE_DIR:-$HOME/.cache/uv}"
    if [ -d "$_src_cache" ]; then
        # Try BSD clone (`cp -c`, macOS APFS) first, then GNU reflink
        # (`cp --reflink=auto`, Linux btrfs/XFS), then a plain recursive
        # copy. Each shell only understands one of the first two flags;
        # the unknown-flag case fails immediately and falls through.
        if cp -c -R "$_src_cache" .curator/uv-cache 2>/dev/null; then
            echo "  Cloned $_src_cache → .curator/uv-cache (APFS clone — ~zero extra disk)"
        elif cp --reflink=auto -R "$_src_cache" .curator/uv-cache 2>/dev/null; then
            echo "  Cloned $_src_cache → .curator/uv-cache (reflink — ~zero extra disk)"
        elif cp -R "$_src_cache" .curator/uv-cache 2>/dev/null; then
            _cache_size=$(du -sh .curator/uv-cache 2>/dev/null | cut -f1)
            echo "  Copied $_src_cache → .curator/uv-cache (no reflink support; ${_cache_size:-unknown size} on disk)"
        else
            mkdir -p .curator/uv-cache
            echo "  Created empty .curator/uv-cache (clone/copy failed; uv will populate from network)"
        fi
    else
        mkdir -p .curator/uv-cache
        echo "  Created empty .curator/uv-cache (no source cache at $_src_cache; uv will populate from network on first run)"
    fi
fi
# Keep uv-cache out of any outer git repo wrapping the workspace. The
# wiki repo lives at wiki/ and is unaffected; this guards the case where
# the workspace itself is also under version control.
if [ ! -f .curator/.gitignore ] || ! grep -qE "^/?uv-cache(/|$)" .curator/.gitignore 2>/dev/null; then
    if [ ! -f .curator/.gitignore ]; then
        printf '# Workspace-local uv cache (seeded by setup.sh — regenerable)\nuv-cache/\n' > .curator/.gitignore
    else
        printf '\n# Workspace-local uv cache (seeded by setup.sh — regenerable)\nuv-cache/\n' >> .curator/.gitignore
    fi
fi

# Detect .venv drift. When the user upgrades system Python, the existing
# .venv is still bound to the old interpreter — if that interpreter is
# gone, the venv is silently broken; if it's still there, rerunning
# setup.sh would otherwise no-op (the `if [ ! -d .venv ]` guard below
# skips recreation). Catch both cases. Silent when no drift.
_rebuild_venv=0
_drift_reason=""
if [ -d .venv ]; then
    if ! .venv/bin/python3 --version >/dev/null 2>&1; then
        _rebuild_venv=1
        _drift_reason="old interpreter missing (Python likely upgraded since last setup)"
    elif [ -f .venv/pyvenv.cfg ]; then
        _venv_py=$(grep -E "^version[[:space:]]*=" .venv/pyvenv.cfg | head -1 | sed 's/^version[[:space:]]*=[[:space:]]*//' | tr -d ' ')
        _venv_mm=$(echo "$_venv_py" | cut -d. -f1,2)
        _cur_mm="${_py_major}.${_py_minor}"
        if [ -n "$_venv_mm" ] && [ "$_venv_mm" != "$_cur_mm" ]; then
            _drift_reason=".venv is on Python $_venv_py; current python3 is $_py_version"
            if _is_interactive; then
                echo ""
                echo "  $_drift_reason"
                printf "  Rebuild .venv on Python $_py_version? [y/N] "
                read -r _reply_rebuild || _reply_rebuild="n"
                case "$_reply_rebuild" in
                    y|Y|yes|YES) _rebuild_venv=1 ;;
                esac
            else
                # Non-interactive: surface the drift but keep the existing
                # venv. Rebuilding without confirmation risks surprising
                # users who deliberately pinned the venv to a different
                # Python. To rebuild: `rm -rf .venv && ./setup.sh`.
                echo ""
                echo "  NOTE: $_drift_reason"
                echo "        Keeping existing venv (non-interactive)."
                echo "        Rebuild manually: rm -rf .venv && rerun setup.sh"
            fi
        fi
    fi
fi
if [ "$_rebuild_venv" = "1" ]; then
    echo "  Removing old .venv and rebuilding on Python $_py_version ($_drift_reason) ..."
    rm -rf .venv
fi

# Create workspace venv + install kuzu. `uv run` from the workspace root
# auto-discovers `./.venv` — no activation needed. kuzu backs graph.py,
# lint_scores.py, and epoch_summary.py.
if [ ! -d .venv ]; then
    echo "  Creating workspace .venv via uv ..."
    uv venv
fi
if ! uv run --no-project python3 -c "import kuzu" >/dev/null 2>&1; then
    echo "  Installing kuzu into .venv ..."
    uv pip install kuzu
fi
# pypdf: small, pure-Python PDF text extraction. Used by local_ingest.py
# as the fast-tier PDF path. Installed unconditionally — it's lightweight
# (~2 MB) and PDFs are a mainline ingest format.
if ! uv run --no-project python3 -c "import pypdf" >/dev/null 2>&1; then
    echo "  Installing pypdf (PDF text extraction) into .venv ..."
    uv pip install pypdf
fi
# PyYAML: used by tables.py to parse class-entity table schemas from
# entity-page frontmatter. Lightweight (pure-Python, ~300KB) and the
# pinnacle of standard in the Python world.
if ! uv run --no-project python3 -c "import yaml" >/dev/null 2>&1; then
    echo "  Installing PyYAML (class-table schema parser) into .venv ..."
    uv pip install pyyaml
fi
# pypdfium2 + Pillow: pypdfium2 renders PDF pages as bitmaps for
# figures.py extract/regen; Pillow is its standard companion for
# PIL-format output. Installed unconditionally — both are small
# (~5 MB combined) and required for any figure page whose origin
# is `extracted`.
if ! uv run --no-project python3 -c "import pypdfium2" >/dev/null 2>&1; then
    echo "  Installing pypdfium2 (PDF page rendering) into .venv ..."
    uv pip install pypdfium2
fi
if ! uv run --no-project python3 -c "import PIL" >/dev/null 2>&1; then
    echo "  Installing Pillow (PNG encoding for pypdfium2) into .venv ..."
    uv pip install Pillow
fi
# pdfplumber: layered on top of pypdf in local_ingest.py to recover
# bordered tables as GFM under `## Extracted tables`. Bordered tables
# (chemistry buffers, gene-expression grids, benchmark scores) recover
# well; borderless / multi-line-cell layouts fall through to the
# multimodal-upgrade flag. ~10 MB; only PDF-related.
if ! uv run --no-project python3 -c "import pdfplumber" >/dev/null 2>&1; then
    echo "  Installing pdfplumber (PDF table extraction) into .venv ..."
    uv pip install pdfplumber
fi
# openpyxl: stdlib-equivalent for the spreadsheet world. local_ingest.py
# uses it to convert .xlsx workbooks to per-sheet GFM tables. Pure
# Python (~5 MB).
if ! uv run --no-project python3 -c "import openpyxl" >/dev/null 2>&1; then
    echo "  Installing openpyxl (XLSX extraction) into .venv ..."
    uv pip install openpyxl
fi
# python-pptx: needed for slide-table extraction in local_ingest.py.
# Reads .pptx natively without LibreOffice; ~3 MB.
if ! uv run --no-project python3 -c "import pptx" >/dev/null 2>&1; then
    echo "  Installing python-pptx (PPTX extraction) into .venv ..."
    uv pip install python-pptx
fi

# Working directory layout:
#   vault/                 raw sources
#   wiki/                  content-only, git-tracked
#     sources/ entities/ concepts/ analyses/ evidence/ facts/
#   .curator/              curator state, NOT tracked by wiki's git
#   CLAUDE.md              workspace instructions (mirrors SKILL.md)
#   .claude/settings.json  auto-allow permissions
mkdir -p vault/raw wiki/{sources,entities,concepts,analyses,evidence,facts,tables,figures,notes,todos,projects}
touch vault/.gitkeep vault/raw/.gitkeep
for d in sources entities concepts analyses evidence facts tables figures notes todos projects; do
    touch "wiki/$d/.gitkeep"
done
mkdir -p .curator
mkdir -p .claude/commands

# Notes/todos staging pages. The todos class-table schema lives on the
# concept hub `wiki/todos.md` (seeded a few lines below from
# template/todos-overview.md) — there's no separate entity page. Skip
# if already present so user edits are preserved.
_seed_notes_or_todos_stub() {
    local path="$1"; local title="$2"; local type="$3"; local hub="$4"; local intro="$5"
    if [ ! -f "$path" ]; then
        cat > "$path" <<EOF
---
title: "$title"
type: $type
created: $(date +%Y-%m-%d)
updated: $(date +%Y-%m-%d)
---

Part of [[$hub]].

$intro

## active

EOF
    fi
}
_seed_notes_or_todos_stub wiki/notes/new.md '[note] new (default /note landing; curator drains)' note notes \
    'Default landing for `/note` without a topic cue. Drop free-form bullets here — the curator drains them into topic files (`notes/<topic>.md`) on the next sweep, routed by the first `[[wikilink]]` in the bullet, by an explicit `topic: <slug>` tag, or to [[for-attention]] if neither.'
_seed_notes_or_todos_stub wiki/notes/for-attention.md '[note] for-attention (notes awaiting user topic)' note notes \
    'Notes the auto-router could not classify (no `[[wikilink]]`, no `topic:` tag). Add a `[[wikilink]]` or a `topic: <slug>` to a bullet to route it on the next sweep, or wait for the curator to infer the topic during a CURATE run.'

# Landing + hub pages. `[ ! -s ]` covers both absent AND zero-byte
# (an Obsidian click-artefact or a pre-hub-era empty stub) so the
# template gets installed in either case. User edits are preserved
# because a non-empty file is never overwritten.
if [ ! -s wiki/index.md ] && [ -f "$TEMPLATE_DIR/wiki-index.md" ]; then
    cp "$TEMPLATE_DIR/wiki-index.md" wiki/index.md
    echo "  Seeded wiki/index.md (landing page)"
fi
# Hub pages for the notes / todos surfaces. Bucket stubs carry a
# `Part of [[notes|todos]].` wikilink that targets these pages, which
# keeps them connected in Obsidian's graph view instead of floating
# as an isolated cluster of empty nodes.
if [ ! -s wiki/notes.md ] && [ -f "$TEMPLATE_DIR/notes-overview.md" ]; then
    cp "$TEMPLATE_DIR/notes-overview.md" wiki/notes.md
    echo "  Seeded wiki/notes.md (notes surface overview)"
fi
if [ ! -s wiki/todos.md ] && [ -f "$TEMPLATE_DIR/todos-overview.md" ]; then
    cp "$TEMPLATE_DIR/todos-overview.md" wiki/todos.md
    echo "  Seeded wiki/todos.md (todos surface overview)"
fi
_seed_notes_or_todos_stub wiki/todos/day.md '[todo] day-priority' todo-list todos \
    'Todos for today or the next few days. Add a `- [ ]` line below; tick the box to mark it done — the curator will move completed items to this year archive on the next sweep.'
_seed_notes_or_todos_stub wiki/todos/month.md '[todo] month-priority' todo-list todos \
    'Todos for the coming month. Add directly here, or add to [[unfiled]] with a `priority: month` tag and the curator will move it on the next sweep.'
_seed_notes_or_todos_stub wiki/todos/year.md '[todo] year-priority' todo-list todos \
    'Todos for this year — the catch-all bucket and the default destination for `/todo` when no temporal cue is given. Add directly, or add to [[unfiled]] without a priority tag and the curator will land them here.'
_seed_notes_or_todos_stub wiki/todos/unfiled.md '[todo] unfiled (priority pending)' todo-list todos \
    'New todos that have not yet been filed. Add a `- [ ]` line below; include an optional `priority: day`, `priority: month`, or `priority: year` tag and the curator will move it to the matching bucket on the next sweep. No tag → defaults to year.'

# Copy slash commands into the workspace's .claude/commands/ directory.
# These register /day, /month, /year, /todo, /note for Claude Code
# sessions opened in this workspace. Non-Claude-Code CLIs (Codex,
# Copilot Chat, Gemini CLI) will ignore the directory; users fall back
# to natural-language invocation which the agent handles the same way.
if [ -d "$TEMPLATE_DIR/claude-commands" ]; then
    for _cmd in "$TEMPLATE_DIR/claude-commands"/*.md; do
        [ -f "$_cmd" ] || continue
        _cmd_name="$(basename "$_cmd")"
        if [ ! -f ".claude/commands/$_cmd_name" ]; then
            cp "$_cmd" ".claude/commands/$_cmd_name"
        fi
    done
fi

# Figure asset PNGs live inside the wiki at wiki/figures/_assets/ so
# they're inside the Obsidian vault scope (clean inline rendering)
# and inside the static viewer's bundle path so its <img> tags resolve.
# The folder is gitignored in the wiki repo because the binaries are
# regenerable from vault PDFs via figures.py regen — committing them
# would bloat the repo for no portability gain. The `_` prefix is a
# widely-recognised "supporting files, not content" convention that
# also makes it easy for users to hide the folder from Obsidian's
# graph view with a `-path:_assets` filter.
mkdir -p wiki/figures/_assets
_wiki_gitignore="wiki/.gitignore"
_gitignore_line="/figures/_assets/"
if [ ! -f "$_wiki_gitignore" ] || ! grep -qE "^/?figures/_assets(/|$)" "$_wiki_gitignore" 2>/dev/null; then
    if [ ! -f "$_wiki_gitignore" ]; then
        printf '# Figure asset PNGs — regenerated from vault PDFs by figures.py\n%s\n' "$_gitignore_line" > "$_wiki_gitignore"
    else
        printf '\n# Figure asset PNGs — regenerated from vault PDFs by figures.py\n%s\n' "$_gitignore_line" >> "$_wiki_gitignore"
    fi
    echo "  Added $_gitignore_line to wiki/.gitignore"
fi

# Refresh markdown templates that drift as the skill evolves. The skill
# periodically adds new operations, prompt spec updates, or allowlist-
# breaking command changes; workspaces that don't pick those up show up
# as agent-side approval prompts and stale instructions. On every run:
#   * absent      → install fresh (initial setup case)
#   * identical   → leave alone
#   * different   → back up with timestamp, install fresh, optionally
#                    union-merge the backup back in so workspace additions
#                    are preserved
#
# config.json is handled separately (copy-if-missing) because its values
# are user-tuned (worker_model, parallel_workers, saturation thresholds)
# and a refresh would blow those away.
refresh_template_md() {
    local src="$1"
    local dst="$2"
    if [ ! -f "$dst" ]; then
        cp "$src" "$dst"
        echo "  Created $dst"
        return
    fi
    if cmp -s "$src" "$dst"; then
        return
    fi
    local ts backup
    ts=$(date +%Y%m%d-%H%M%S)
    backup="${dst}.bak.${ts}"
    cp "$dst" "$backup"
    echo ""
    echo "  $dst differs from the skill template."
    echo "  Backed up to: $backup"
    local reply_merge="n"
    if _is_interactive; then
        printf "  Auto-merge workspace edits with the refreshed template (union merge via git merge-file)? [y/N] "
        read -r reply_merge || reply_merge="n"
    fi
    cp "$src" "$dst"
    case "$reply_merge" in
        y|Y|yes|YES)
            if git merge-file --union "$dst" /dev/null "$backup" >/dev/null 2>&1; then
                echo "  Union-merged. Review $dst for duplicated sections from overlapping edits."
            else
                echo "  Union merge failed; left fresh template in place. Manually diff against $backup if needed."
            fi
            ;;
        *)
            echo "  Fresh template installed. Manually merge from $backup if you had local edits."
            ;;
    esac
}

refresh_template_md "$TEMPLATE_DIR/schema.md" ".curator/schema.md"
refresh_template_md "$TEMPLATE_DIR/prompts.md" ".curator/prompts.md"

# config.json: copy if missing; otherwise merge any keys the template
# has added since the user's config was last written. Additive only —
# never overwrites a value the user has tuned, and descends into nested
# dicts (e.g. the `caveman` block) so added sub-keys land too.
#
# Includes a one-shot migration: if the existing config still uses the
# legacy top-level `worker_model` / `reviewer_model` shape (pre-preset
# era), infer which vendor the values belong to, lift them into a
# matching preset block, set `active_preset`, and drop the top-level
# keys. The standard additive merge then fills in any other seeded
# presets (claude/codex/gemini) the user doesn't already have.
if [ ! -f ".curator/config.json" ]; then
    cp "$TEMPLATE_DIR/config.json" ".curator/config.json"
    echo "  Created .curator/config.json"
else
    uv run --no-project python3 - "$TEMPLATE_DIR/config.json" .curator/config.json <<'PY'
import json, sys
from pathlib import Path
template = json.load(open(sys.argv[1]))
existing_path = Path(sys.argv[2])
existing = json.load(open(existing_path))

def infer_preset_name(worker, reviewer):
    s = (str(worker or "") + " " + str(reviewer or "")).lower()
    if "claude" in s or "anthropic" in s: return "claude"
    if "gpt-" in s or s.startswith("o1") or " o1" in s: return "codex"
    if "gemini" in s: return "gemini"
    if "ollama/" in s or "llama" in s or "qwen" in s: return "ollama"
    return "custom"

migrated = False
if "presets" not in existing and ("worker_model" in existing or "reviewer_model" in existing):
    worker = existing.pop("worker_model", None)
    reviewer = existing.pop("reviewer_model", None)
    name = infer_preset_name(worker, reviewer)
    block = {}
    if worker is not None:   block["worker_model"]   = worker
    if reviewer is not None: block["reviewer_model"] = reviewer
    existing["active_preset"] = name
    existing["presets"] = {name: block}
    migrated = True

added = []
def merge(tmpl, cur, prefix=""):
    for k, v in tmpl.items():
        qname = f"{prefix}{k}"
        if k not in cur:
            cur[k] = v
            added.append(qname)
        elif isinstance(v, dict) and isinstance(cur[k], dict):
            merge(v, cur[k], qname + ".")
merge(template, existing)

if migrated or added:
    existing_path.write_text(json.dumps(existing, indent=2) + "\n")
    if migrated:
        print(f"  Migrated legacy worker_model/reviewer_model into presets.{existing['active_preset']}")
    if added:
        print(f"  Merged {len(added)} new key(s) from template: {', '.join(added)}")
PY
fi
# Drop the config.example.json alongside so users can see cross-vendor
# variants (Anthropic default, Gemini, OpenAI, Ollama fully-local, mixed).
# Always refresh — it's a reference file, never user-tuned.
if [ -f "$TEMPLATE_DIR/config.example.json" ]; then
    cp "$TEMPLATE_DIR/config.example.json" ".curator/config.example.json"
fi

# Initialize auto-generated curator state
if [ ! -f .curator/log.md ]; then
    printf '# Log\n' > .curator/log.md
    echo "  Created .curator/log.md"
fi
if [ ! -f .curator/index.md ]; then
    printf '# Index\n\nNo pages yet.\n' > .curator/index.md
    echo "  Created .curator/index.md"
fi

# No workspace sweep.py copy anymore — sweep.py is hash-guarded by
# evolve_guard.sh alongside every other skill script. The agent cannot
# edit it at runtime. If a previous install left a workspace copy or the
# skill-path marker, remove them (they will otherwise mask the fresh
# guarded version in any call that still points at .curator/sweep.py).
for stale in .curator/sweep.py .curator/sweep.py.bak .curator/.skill_path; do
    [ -e "$stale" ] && rm -f "$stale" && echo "  Removed stale $stale"
done

refresh_template_md "$TEMPLATE_DIR/CLAUDE.md" "CLAUDE.md"

# Generate Claude Code settings inline. Auto-allows:
#   - git commands scoped via `git -C wiki <cmd>` AND `git -C */wiki <cmd>`
#   - `uv run python3` invocations of skill scripts at this exact absolute path
#   - bash evolve_guard.sh
#   - date
# The `uv run` prefix picks up the workspace `.venv` so kuzu etc. resolve.
regenerate_settings=0
if [ ! -s .claude/settings.json ]; then
    regenerate_settings=1
elif ! uv run --no-project python3 -c "import json, sys; json.load(open('.claude/settings.json'))" >/dev/null 2>&1; then
    regenerate_settings=1
else
    # Canary-based drift detection: each skill update that adds new
    # canonical allowlist entries (new scripts, new Edit/Write scopes,
    # etc.) lists one recent entry in CANARY_ENTRIES. If any are missing
    # from the existing settings.json, the file is stale — offer to
    # regenerate (with backup). The last canary always covers the most
    # recent addition, so a single missing check catches workspaces
    # multiple versions behind.
    CANARY_ENTRIES=(
        "uv run python3"                     # pre-uv switch
        "Edit(./wiki/"                       # path-scoped Edit/Write
        "$SKILL_ROOT_LOGICAL/scripts/"       # logical skill path — catches
                                              # pre-dual-path settings that
                                              # only had the physical path
        "Edit(./vault/"                      # post-multimodal-upgrade write
                                              # path for .extracted.md
        "scripts/figures.py"                 # post-figures-feature allowlist
        "Write(/tmp/"                        # post-curate-scratch allowlist
        "scripts/update.sh"                  # post-in-session-update allowlist
        "scripts/naming.py"                  # post-naming-allowlist-gap fix
        "scripts/viewer.sh"                  # post-custom-viewer allowlist
        "scripts/viewer_server.py"           # post-edit-mode allowlist
        "printenv CURATOR_PRESET"            # post-preset-config allowlist
        "scripts/projects.py"                # post-multi-project allowlist
        "scripts/activity_log.py"            # post-activity-log allowlist
        "scripts/planner.py"                 # post-recency-planner allowlist
    )
    missing_canary=""
    for c in "${CANARY_ENTRIES[@]}"; do
        if ! grep -qF "$c" .claude/settings.json; then
            missing_canary="$c"
            break
        fi
    done
    # Anti-canary: stale entries from prior skill versions that should be
    # regenerated away even though the required canaries are all present.
    if [ -z "$missing_canary" ] && grep -qF ".curator/sweep.py:*" .claude/settings.json; then
        missing_canary=".curator/sweep.py (stale: workspace-sweep allowlist from pre-hash-guard era)"
    fi
    # Indicator of the pre-fix allowlist generator that had two bugs:
    # (a) literal `$root` in the Read entry (variable never expanded),
    # and (b) `\/` escape behaviour in path substitution that hid the
    # sibling .claude/skills/ ↔ .agents/skills/ form. Both emit together.
    if [ -z "$missing_canary" ] && grep -qF 'Read($root/**)' .claude/settings.json; then
        missing_canary='Read($root/**) (stale: broken variable-expansion in pre-fix allowlist generator)'
    fi
    # Quartz was removed in favour of the curiosity-engine-native viewer;
    # workspaces still listing scripts/quartz.sh in their allowlist need
    # regen so that stale entry is dropped.
    if [ -z "$missing_canary" ] && grep -qF 'scripts/quartz.sh' .claude/settings.json; then
        missing_canary='scripts/quartz.sh (stale: Quartz removed, viewer.sh replaces it)'
    fi
    if [ -n "$missing_canary" ]; then
        echo "  Existing .claude/settings.json is missing canonical allowlist"
        echo "  entry matching: $missing_canary"
        if _is_interactive; then
            printf "  Regenerate it now? (backs up old file to .claude/settings.json.bak) [Y/n] "
            read -r reply_regen || reply_regen="y"
        else
            reply_regen="y"
        fi
        case "$reply_regen" in
            ""|y|Y|yes|YES)
                cp .claude/settings.json .claude/settings.json.bak
                echo "  Backed up to .claude/settings.json.bak"
                regenerate_settings=1
                ;;
            *)
                echo "  Leaving settings.json alone. Expect approval prompts for any"
                echo "  commands or tools that have been added since install."
                ;;
        esac
    fi
fi

if [ "$regenerate_settings" = "1" ]; then
    mkdir -p .claude
    # Header + git entries + workspace sweep (path-independent).
    cat > .claude/settings.json <<EOF
{
  "permissions": {
    "allow": [
      "Bash(git -C wiki add:*)",
      "Bash(git -C wiki commit:*)",
      "Bash(git -C wiki status:*)",
      "Bash(git -C wiki log:*)",
      "Bash(git -C wiki diff:*)",
      "Bash(git -C wiki revert:*)",
      "Bash(git -C wiki checkout:*)",
      "Bash(git -C wiki rev-parse:*)",
      "Bash(git -C wiki show:*)",
      "Bash(git -C */wiki add:*)",
      "Bash(git -C */wiki commit:*)",
      "Bash(git -C */wiki status:*)",
      "Bash(git -C */wiki log:*)",
      "Bash(git -C */wiki diff:*)",
      "Bash(git -C */wiki revert:*)",
      "Bash(git -C */wiki checkout:*)",
      "Bash(git -C */wiki rev-parse:*)",
      "Bash(git -C */wiki show:*)",
EOF
    # One block of skill-script entries per skill root (logical +
    # physical when they differ under a symlinked install).
    for root in "${SKILL_ROOTS[@]}"; do
        cat >> .claude/settings.json <<EOF
      "Bash(uv run python3 $root/scripts/lint_scores.py:*)",
      "Bash(uv run python3 $root/scripts/vault_search.py:*)",
      "Bash(uv run python3 $root/scripts/vault_index.py:*)",
      "Bash(uv run python3 $root/scripts/local_ingest.py:*)",
      "Bash(uv run python3 $root/scripts/scrub_check.py:*)",
      "Bash(uv run python3 $root/scripts/score_diff.py:*)",
      "Bash(uv run python3 $root/scripts/sweep.py:*)",
      "Bash(uv run python3 $root/scripts/epoch_summary.py:*)",
      "Bash(uv run python3 $root/scripts/graph.py:*)",
      "Bash(uv run python3 $root/scripts/tables.py:*)",
      "Bash(uv run python3 $root/scripts/figures.py:*)",
      "Bash(uv run python3 $root/scripts/naming.py:*)",
      "Bash(uv run python3 $root/scripts/projects.py:*)",
      "Bash(uv run python3 $root/scripts/activity_log.py:*)",
      "Bash(uv run python3 $root/scripts/planner.py:*)",
      "Bash(uv run python3 $root/scripts/wiki_render.py:*)",
      "Bash(uv run python3 $root/scripts/viewer_server.py:*)",
      "Bash(bash $root/scripts/evolve_guard.sh:*)",
      "Bash(bash $root/scripts/viewer.sh:*)",
      "Bash(bash $root/scripts/update.sh:*)",
EOF
    done
    # Footer: workspace-scoped Edit/Write + misc.
    cat >> .claude/settings.json <<EOF
      "Edit(./wiki/**)",
      "Write(./wiki/**)",
      "Edit(./.curator/**)",
      "Write(./.curator/**)",
      "Edit(./vault/**)",
      "Write(./vault/**)",
      "Write(/tmp/**)",
      "Edit(/tmp/**)",
EOF
    # Skill-script read access — the orchestrator occasionally re-reads
    # a hash-guarded script to confirm flag syntax. Safe to allow:
    # scripts are read-only (hash-guarded) and contain no secrets. One
    # entry per available skill-root path (physical + logical if they
    # differ, per the dual-path allowlist logic above).
    for root in "${SKILL_ROOTS[@]}"; do
        printf '      "Read(%s/**)",\n' "$root" >> .claude/settings.json
    done
    cat >> .claude/settings.json <<EOF
      "Bash(date:*)",
      "Bash(printenv CURATOR_PRESET:*)"
    ]
  }
}
EOF
    if [ "${#SKILL_ROOTS[@]}" -gt 1 ]; then
        echo "  Created .claude/settings.json (dual-path allowlist for symlinked skill install)"
    else
        echo "  Created .claude/settings.json (auto-allow git -C wiki + uv run python3 skill scripts + scoped Edit/Write)"
    fi
fi

# Clean up leftover parallel-session state from an earlier skill version.
# If the workspace was set up when spawn.py / claims.py existed, these
# paths may still be present and will otherwise look like active state to
# a human inspecting `.curator/`. Harmless to remove — no recovery value.
for stale in .curator/.spawned .curator/.claims .curator/.claims.lock \
             .curator/.current-batch; do
    [ -e "$stale" ] && rm -f "$stale" && echo "  Removed stale $stale"
done
if [ -d .curator/sessions ]; then
    rm -rf .curator/sessions && echo "  Removed stale .curator/sessions/"
fi
# And the slash command registered by the parallel-sessions era.
if [ -f .claude/commands/curate.md ]; then
    rm -f .claude/commands/curate.md && echo "  Removed stale .claude/commands/curate.md"
fi

# Initialize wiki as its own git repo (content-only; .curator/ is outside)
if [ ! -d wiki/.git ]; then
    (cd wiki && git init -q && git add -A && git commit -q -m "init: curiosity engine wiki")
    echo "  Initialized wiki git repo"
fi

# Optional: install the caveman compression skill.
# Caveman strips predictable grammar tokens (articles, filler adverbs, etc.)
# so the curator burns less context. Used at read-time (ultra: ~30-40% fewer
# input tokens) and write-time (ultra for most pages, lite for analyses).
if _is_interactive; then
    echo ""
    printf "Install caveman skill to save tokens by using terse telegraphic language for reads and writes? [Y/i/n] "
    read -r reply || reply="y"
    case "$reply" in
        ""|y|Y|yes|YES)
            if command -v npx >/dev/null 2>&1; then
                echo "  Installing JuliusBrussee/caveman via npx skills (global, symlinks) ..."
                npx skills add -g -y JuliusBrussee/caveman || echo "  (install failed — re-run: npx skills add -g -y JuliusBrussee/caveman)"
                echo "  Levels configured in .curator/config.json:"
                echo "    read=ultra, write_analysis=lite, write_other=ultra"
            else
                echo "  npx not found. Install later: npx skills add -g -y JuliusBrussee/caveman"
            fi
            ;;
        i|I)
            if command -v npx >/dev/null 2>&1; then
                echo "  Running interactive install: npx skills add JuliusBrussee/caveman"
                echo "  (all CLI options will be shown to you)"
                npx skills add JuliusBrussee/caveman </dev/tty || echo "  (install failed — re-run: npx skills add JuliusBrussee/caveman)"
            else
                echo "  npx not found. Install later: npx skills add JuliusBrussee/caveman"
            fi
            ;;
        *)
            echo "  Skipping caveman. Curator works without it (see SKILL.md)."
            ;;
    esac
fi

# Optional: semantic vault search (sentence-transformers + sqlite-vec).
# Adds ~200MB of model weights and enables hybrid FTS5 + cosine search.
# Most small vaults (<500 sources) don't need this — FTS5 keyword
# search covers the common case. Opt in when you start hitting
# paraphrased queries that miss with keyword alone.
if _is_interactive; then
    echo ""
    printf "Install semantic vault search (sentence-transformers + sqlite-vec, ~200MB)? [y/N] "
    read -r reply_embed || reply_embed="n"
    case "$reply_embed" in
        y|Y|yes|YES)
            # pysqlite3 is needed because macOS system Python's sqlite3 is
            # typically compiled without --enable-loadable-sqlite-extensions,
            # which breaks sqlite-vec. pysqlite3 is a drop-in replacement
            # built from source with extensions enabled. No-op on Linux
            # distros that already have extensions — but the build needs
            # a C compiler. Warn early so the failure (if it happens) has
            # a clear cause in the user's terminal scrollback.
            _has_cc=0
            for _c in cc gcc clang; do
                command -v "$_c" >/dev/null 2>&1 && _has_cc=1 && break
            done
            if [ "$_has_cc" -eq 0 ]; then
                echo ""
                echo "  WARN: no C compiler (cc/gcc/clang) found on PATH."
                echo "        pysqlite3 likely needs to build from source and will fail."
                echo "        Install build tools first:"
                echo "          macOS:  xcode-select --install"
                echo "          Debian/Ubuntu:  apt install build-essential"
                echo "          Fedora/RHEL:    dnf groupinstall 'Development Tools'"
                echo "        Proceeding anyway — the error below will be the compiler's."
                echo ""
            fi
            echo "  Installing sentence-transformers + sqlite-vec (+ pysqlite3) into .venv ..."
            if uv pip install sentence-transformers sqlite-vec pysqlite3; then
                # Flip embedding_enabled to true in config.json so vault_index
                # will compute embeddings on next ingest / --rebuild.
                uv run --no-project python3 -c "
import json
from pathlib import Path
p = Path('.curator/config.json')
cfg = json.loads(p.read_text())
cfg['embedding_enabled'] = True
cfg.setdefault('embedding_model', 'sentence-transformers/all-MiniLM-L6-v2')
p.write_text(json.dumps(cfg, indent=2))
"
                echo "  Enabled embedding_enabled=true in .curator/config.json"
                echo "  To embed the existing vault:"
                echo "    uv run python3 $SCRIPT_DIR/vault_index.py --rebuild"
            else
                echo "  Install failed. Enable later:"
                echo "    uv pip install sentence-transformers sqlite-vec"
            fi
            ;;
        *)
            echo "  Skipping semantic search. Enable later:"
            echo "    uv pip install sentence-transformers sqlite-vec"
            echo "    (then set embedding_enabled=true in .curator/config.json)"
            ;;
    esac
fi

# Initialize vault FTS5 index
uv run python3 "$SCRIPT_DIR/vault_index.py" --init

# Behavioral-migration pass. Each sweep resync-* subcommand is idempotent:
# it re-derives the correct state from the canonical source (naming.py,
# prompts.md, etc.) and only writes when it finds drift. After a skill
# update that changes such a source, this pass propagates the change
# across the existing workspace — renaming stubs, rewriting wikilinks,
# etc. No-op when everything is already in sync.
#
# Guarded by a clean-git check on the wiki repo: if the user has
# uncommitted changes we refuse to touch the wiki, print a note, and
# let them decide. Rationale: a migration may rename 100+ files and
# rewrite wikilinks across every page — the user wants that as a single
# reviewable commit, not tangled with in-progress edits.
if [ -d wiki/.git ]; then
    if [ -n "$(git -C wiki status --porcelain)" ]; then
        echo ""
        echo "  Wiki has uncommitted changes; skipping behavioral-migration pass."
        echo "  Commit or stash your wiki edits and rerun setup.sh to apply."
    else
        echo ""
        echo "  Running behavioral-migration pass (resync-stems, fix-index, graph rebuild) ..."
        uv run python3 "$SCRIPT_DIR/sweep.py" fix-frontmatter-quotes wiki >/dev/null
        # Add or correct the canonical [con]/[ent]/[tbl]/... bracket
        # prefix on every page title, picking the value from
        # naming.TYPE_PREFIX. Catches summary-table pages a worker built
        # without the [tbl] tag and legacy pages with `[concept]` /
        # `[entity]` (full-word) prefixes from earlier skill versions.
        # Idempotent no-op once every title is canonical.
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-title-prefixes wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/sweep.py" dedupe-self-citations wiki >/dev/null
        # Sweep up zero-byte .md files at wiki/ root — almost always
        # Obsidian click-artefacts from unresolved wikilinks (e.g. a
        # literal `[[wikilinks]]` placeholder in a template rendering
        # as a clickable link). Seeded hub pages (index/notes/todos)
        # are populated above, so any remaining top-level empty file
        # is genuinely orphaned. Idempotent no-op when clean.
        uv run python3 "$SCRIPT_DIR/sweep.py" fix-orphan-root-files wiki >/dev/null 2>&1 || true
        # One-shot migration for workspaces whose figure assets still
        # live under workspace/assets/figures/. Moves them into
        # wiki/figures/_assets/, rewrites embed paths to match the
        # configured viewer mode, removes the old empty dirs, adds
        # the new gitignore line. Idempotent no-op once applied.
        uv run python3 "$SCRIPT_DIR/sweep.py" migrate-asset-location wiki >/dev/null 2>&1 || true
        # Retrofit source-stub wikilinks into figure pages that were
        # created before the mechanical-wikilink rule was wired in.
        # Idempotent no-op once all figure pages carry a wikilink.
        uv run python3 "$SCRIPT_DIR/sweep.py" backfill-figure-sourcelinks wiki >/dev/null 2>&1 || true
        # Retrofit `Part of [[notes|todos]].` hub wikilinks into
        # bucket pages seeded before the hub convention existed, so
        # they show as connected in Obsidian's graph view. Idempotent.
        uv run python3 "$SCRIPT_DIR/sweep.py" backfill-bucket-hubs wiki >/dev/null 2>&1 || true
        # Undo sync-todos pollution on hub pages + completion archives
        # from the window where sync-todos parsed inside fenced code
        # blocks. Matches the literal `(todo:T<id>)` template marker to
        # find the bad lines; orphan sqlite rows are purged too.
        # Idempotent no-op once clean.
        uv run python3 "$SCRIPT_DIR/sweep.py" purge-template-todo-artefacts wiki >/dev/null 2>&1 || true
        # Earlier skill versions seeded the todos class-table on
        # wiki/entities/todos.md alongside the wiki/todos.md hub —
        # consolidate to a single source of truth on the hub.
        # Idempotent no-op once the entity page is gone.
        uv run python3 "$SCRIPT_DIR/sweep.py" consolidate-todos-page wiki >/dev/null 2>&1 || true
        # One-shot migration for vault files ingested before the
        # local_ingest suffix-doubling fix (foo.pdf.pdf → foo.pdf).
        # Idempotent no-op once applied.
        uv run python3 "$SCRIPT_DIR/sweep.py" normalize-vault-suffixes wiki >/dev/null 2>&1 || true
        # Sync the canonical todos class-table schema (idempotent — creates
        # the table on first run, re-hashes on schema change) and drain any
        # user-authored todos / notes into their structured homes. The
        # schema lives on wiki/todos.md (concept hub). Earlier skill
        # versions seeded a separate wiki/entities/todos.md; if both
        # files coexist in an existing workspace, consolidate-todos-page
        # below merges and removes the stale entity copy.
        if [ -f wiki/todos.md ]; then
            uv run python3 "$SCRIPT_DIR/tables.py" sync wiki/todos.md >/dev/null 2>&1 || true
        fi
        uv run python3 "$SCRIPT_DIR/sweep.py" sync-todos wiki >/dev/null 2>&1 || true
        uv run python3 "$SCRIPT_DIR/sweep.py" sync-notes wiki >/dev/null 2>&1 || true
        # Align figure-page image-embed syntax with the configured viewer.
        # Default obsidian; user switches to "vscode" for VS Code preview
        # compatibility. Idempotent when already in target form.
        _viewer_mode=$(uv run --no-project python3 -c "
import json, sys
try:
    print(json.load(open('.curator/config.json')).get('wiki_viewer_mode', 'obsidian'))
except Exception:
    print('obsidian')
" 2>/dev/null || echo "obsidian")
        uv run python3 "$SCRIPT_DIR/sweep.py" convert-image-embeds wiki --target "$_viewer_mode" >/dev/null 2>&1 || true
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-stems wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-prefixes wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/sweep.py" fix-index wiki >/dev/null
        uv run python3 "$SCRIPT_DIR/graph.py" rebuild wiki >/dev/null
        # Regenerate any figure assets missing from assets/figures/ (first
        # clone, or the folder was cleaned). Deterministic from vault
        # sources; created-origin figures cannot be auto-regenerated and
        # are surfaced by figures.py check for human review.
        uv run python3 "$SCRIPT_DIR/figures.py" regen wiki >/dev/null 2>&1 || true
        echo "  Migration pass complete. Review with: git -C wiki diff --stat"
        # If resync renamed anything, there are now unstaged changes — we
        # intentionally leave them unstaged so the user inspects + commits
        # with a message of their choosing.
    fi
fi

echo ""
echo "Ready. Open Claude Code here and try:"
echo '  > add ~/some-paper.pdf to the vault'
echo '  > what do I know about X?'
echo '  > curate for an hour'
