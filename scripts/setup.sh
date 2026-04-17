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

# Copy human-edited templates into .curator/ if not already present
for f in schema.md prompts.md config.json; do
    if [ ! -f ".curator/$f" ]; then
        cp "$TEMPLATE_DIR/$f" ".curator/$f"
        echo "  Created .curator/$f"
    fi
done

# Warn if workspace prompts.md predates LINK. We don't auto-edit the
# human-edited copy, but the user needs to know the LINK operation
# requires the new `link_proposer`/`link_classifier` templates.
if [ -f .curator/prompts.md ] && ! grep -q '^## link_proposer' .curator/prompts.md 2>/dev/null; then
    echo ""
    echo "NOTE: .curator/prompts.md predates the LINK operation."
    echo "  To enable LINK, merge these sections from the skill template:"
    echo "    $TEMPLATE_DIR/prompts.md"
    echo "  (sections: 'link_proposer' and 'link_classifier')"
    echo ""
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
if [ ! -f .curator/sweep.py ]; then
    cp "$SKILL_ROOT/scripts/sweep.py" .curator/sweep.py
    echo "  Created .curator/sweep.py (agent-editable workspace copy)"
fi

if [ ! -f CLAUDE.md ]; then
    cp "$TEMPLATE_DIR/CLAUDE.md" CLAUDE.md
    echo "  Created CLAUDE.md"
fi

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

echo ""
echo "Ready. Open Claude Code here and try:"
echo '  > add ~/some-paper.pdf to the vault'
echo '  > what do I know about X?'
echo '  > curate for an hour'
