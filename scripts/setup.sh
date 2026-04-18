#!/usr/bin/env bash
set -e

echo "=== Curiosity Engine Setup ==="

# Resolve paths. SCRIPT_DIR is the installed skill's scripts/ directory;
# TEMPLATE_DIR is its sibling template/ — the single source of truth for
# the wiki and curator skeleton copied into each new workspace.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_DIR="$SKILL_ROOT/template"

# Ensure `uv` is available. The skill's canonical Python invocation is
# `uv run python3 ...`, which auto-discovers the workspace `.venv`. Without
# uv the allowlist won't match and every python command triggers approval.
if ! command -v uv >/dev/null 2>&1; then
    if [ -t 0 ] && [ -t 1 ]; then
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

# Working directory layout:
#   vault/                 raw sources
#   wiki/                  content-only, git-tracked
#     sources/ entities/ concepts/ analyses/ evidence/ facts/
#   .curator/              curator state, NOT tracked by wiki's git
#   CLAUDE.md              workspace instructions (mirrors SKILL.md)
#   .claude/settings.json  auto-allow permissions
mkdir -p vault/raw wiki/{sources,entities,concepts,analyses,evidence,facts}
touch vault/.gitkeep vault/raw/.gitkeep
for d in sources entities concepts analyses evidence facts; do
    touch "wiki/$d/.gitkeep"
done
mkdir -p .curator

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
# are user-tuned (worker_model, parallel_workers, epoch_seconds) and a
# refresh would blow those away.
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
    if [ -t 0 ] && [ -t 1 ]; then
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

# config.json: copy if missing; leave user-tuned values alone otherwise.
# Schema additions (new config keys) print a warning rather than overwriting.
if [ ! -f ".curator/config.json" ]; then
    cp "$TEMPLATE_DIR/config.json" ".curator/config.json"
    echo "  Created .curator/config.json"
elif ! cmp -s "$TEMPLATE_DIR/config.json" ".curator/config.json"; then
    echo ""
    echo "  NOTE: .curator/config.json differs from the skill template."
    echo "  Not auto-refreshing (preserves your worker_model/epoch_seconds/etc."
    echo "  tuning). Diff against $TEMPLATE_DIR/config.json to pick up any new keys."
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

# Drop the agent-editable sweep.py workspace copy alongside the pristine
# reference at $SKILL_ROOT/scripts/sweep.py. The skill's reference copy is
# what the guard treats as the baseline; the workspace copy may be edited
# by CURATE.
#
# The workspace copy needs to resolve `from naming import ...` against the
# skill's scripts/ dir, which isn't next to .curator/. We write the skill
# scripts path to `.curator/.skill_path` so sweep.py's import-fallback can
# find naming.py. This file is always refreshed (cheap, idempotent).
printf '%s\n' "$SKILL_ROOT/scripts" > .curator/.skill_path

if [ ! -f .curator/sweep.py ]; then
    cp "$SKILL_ROOT/scripts/sweep.py" .curator/sweep.py
    echo "  Created .curator/sweep.py (agent-editable workspace copy)"
elif ! grep -q '.skill_path' .curator/sweep.py; then
    # Existing workspace copy predates the skill-path fallback and will
    # fail on `from naming import ...`. Refresh from the skill reference.
    # Backs up the agent-editable version so any CURATE optimizations
    # that landed are not lost.
    cp .curator/sweep.py .curator/sweep.py.bak
    cp "$SKILL_ROOT/scripts/sweep.py" .curator/sweep.py
    echo "  Refreshed stale .curator/sweep.py (backed up to sweep.py.bak)"
fi

refresh_template_md "$TEMPLATE_DIR/CLAUDE.md" "CLAUDE.md"

# Generate Claude Code settings inline. Auto-allows:
#   - git commands scoped via `git -C wiki <cmd>` AND `git -C */wiki <cmd>`
#   - `uv run python3` invocations of skill scripts at this exact absolute path
#   - `uv run python3 .curator/sweep.py` (the workspace sweep copy)
#   - bash evolve_guard.sh
#   - date
# The `uv run` prefix picks up the workspace `.venv` so kuzu etc. resolve.
regenerate_settings=0
if [ ! -s .claude/settings.json ]; then
    regenerate_settings=1
elif ! uv run --no-project python3 -c "import json, sys; json.load(open('.claude/settings.json'))" >/dev/null 2>&1; then
    regenerate_settings=1
elif ! grep -q 'uv run python3' .claude/settings.json; then
    # Pre-uv settings file: allowlist still has `python3 ...` prefixes, but
    # the skill now invokes `uv run python3 ...`. Without regen, every
    # script call prompts for approval and breaks autonomous loops.
    echo "  Existing .claude/settings.json predates the uv-run switch."
    if [ -t 0 ] && [ -t 1 ]; then
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
            echo "  Leaving settings.json alone. Expect approval prompts until the"
            echo "  allowlist is updated to use 'uv run python3' prefixes."
            ;;
    esac
fi

if [ "$regenerate_settings" = "1" ]; then
    mkdir -p .claude
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
      "Bash(uv run python3 $SKILL_ROOT/scripts/lint_scores.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/vault_search.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/vault_index.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/local_ingest.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/scrub_check.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/score_diff.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/sweep.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/epoch_summary.py:*)",
      "Bash(uv run python3 $SKILL_ROOT/scripts/graph.py:*)",
      "Bash(uv run python3 .curator/sweep.py:*)",
      "Bash(bash $SKILL_ROOT/scripts/evolve_guard.sh:*)",
      "Bash(date:*)"
    ]
  }
}
EOF
    echo "  Created .claude/settings.json (auto-allow git -C wiki + uv run python3 skill scripts)"
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
if [ -t 0 ] && [ -t 1 ]; then
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
        uv run python3 "$SCRIPT_DIR/sweep.py" resync-stems wiki >/dev/null \
            && uv run python3 "$SCRIPT_DIR/sweep.py" fix-index wiki >/dev/null \
            && uv run python3 "$SCRIPT_DIR/graph.py" rebuild wiki >/dev/null \
            && echo "  Migration pass complete. Review with: git -C wiki diff --stat"
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
