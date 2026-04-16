#!/usr/bin/env bash
set -e

echo "=== Curiosity Engine Setup ==="

# Resolve paths. SCRIPT_DIR is the installed skill's scripts/ directory;
# TEMPLATE_DIR is its sibling template/ — the single source of truth for
# the wiki and curator skeleton copied into each new workspace.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE_DIR="$SKILL_ROOT/template"

# Working directory layout:
#   vault/                 raw sources
#   wiki/                  content-only, git-tracked
#     sources/ entities/ concepts/ analyses/ evidence/ facts/
#   .curator/              curator state, NOT tracked by wiki's git
#   CLAUDE.md              workspace instructions (mirrors SKILL.md)
#   .claude/settings.json  auto-allow permissions
mkdir -p vault wiki/{sources,entities,concepts,analyses,evidence,facts}
touch vault/.gitkeep
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
#   - python3 invocations of skill scripts at this exact absolute path
#   - python3 .curator/sweep.py (the workspace sweep copy)
#   - bash evolve_guard.sh
#   - date
regenerate_settings=0
if [ ! -s .claude/settings.json ]; then
    regenerate_settings=1
elif ! python3 -c "import json, sys; json.load(open('.claude/settings.json'))" >/dev/null 2>&1; then
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
      "Bash(python3 $SKILL_ROOT/scripts/lint_scores.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/vault_search.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/vault_index.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/local_ingest.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/scrub_check.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/score_diff.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/sweep.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/epoch_summary.py:*)",
      "Bash(python3 $SKILL_ROOT/scripts/graph.py:*)",
      "Bash(python3 .curator/sweep.py:*)",
      "Bash(bash $SKILL_ROOT/scripts/evolve_guard.sh:*)",
      "Bash(date:*)"
    ]
  }
}
EOF
    echo "  Created .claude/settings.json (auto-allow git -C wiki + skill scripts + .curator/sweep.py)"
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
    echo "Install caveman compression? Saves ~30-40% tokens on reads,"
    echo "~10-15% on analysis writes, ~30-40% on other page writes."
    printf "Install? [y/N] "
    read -r reply || reply="n"
    case "$reply" in
        y|Y|yes|YES)
            if command -v npx >/dev/null 2>&1; then
                echo "  Installing JuliusBrussee/caveman via npx skills ..."
                npx skills add JuliusBrussee/caveman || echo "  (install failed — re-run: npx skills add JuliusBrussee/caveman)"
                echo "  Levels configured in .curator/config.json:"
                echo "    read=ultra, write_analysis=lite, write_other=ultra"
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
python3 "$SCRIPT_DIR/vault_index.py" --init

echo ""
echo "Ready. Open Claude Code here and try:"
echo '  > add ~/some-paper.pdf to the vault'
echo '  > what do I know about X?'
echo '  > curate for an hour'
