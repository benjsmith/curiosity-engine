#!/usr/bin/env bash
set -e

echo "=== Curiosity Engine Setup ==="

# Resolve paths. SCRIPT_DIR is the installed skill's scripts/ directory;
# TEMPLATE_DIR is its sibling template/ — the single source of truth for
# the wiki skeleton copied into each new workspace.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/template"

# Create working directory structure
mkdir -p vault wiki/{sources,entities,concepts,analyses}
touch vault/.gitkeep

# Copy templates into the working directory if not already present
for f in schema.md index.md log.md; do
    if [ ! -f "wiki/$f" ]; then
        cp "$TEMPLATE_DIR/$f" "wiki/$f"
        echo "  Created wiki/$f"
    fi
done

if [ ! -f CLAUDE.md ]; then
    cp "$TEMPLATE_DIR/CLAUDE.md" CLAUDE.md
    echo "  Created CLAUDE.md"
fi

# Drop in Claude Code settings that auto-allow:
#   - git commands scoped via `git -C wiki <cmd>` (only affects the wiki subdir)
#   - python3 invocations of skill scripts at this exact absolute path
#   - the evolve_guard.sh helper
# __SKILL_DIR__ placeholder is substituted with the installed skill path so
# python rules match the literal command prefix the matcher sees.
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ ! -f .claude/settings.json ]; then
    mkdir -p .claude
    sed "s|__SKILL_DIR__|$SKILL_ROOT|g" "$TEMPLATE_DIR/.claude/settings.json" \
        > .claude/settings.json
    echo "  Created .claude/settings.json (auto-allow git -C wiki + skill scripts)"
fi

# Initialize wiki as its own git repo
if [ ! -d wiki/.git ]; then
    (cd wiki && git init -q && git add -A && git commit -q -m "init: curiosity engine wiki")
    echo "  Initialized wiki git repo"
fi

# Initialize vault FTS5 index
python3 "$SCRIPT_DIR/vault_index.py" --init

echo ""
echo "Ready. Open Claude Code here and try:"
echo '  > add ~/some-paper.pdf to the vault'
echo '  > what do I know about X?'
echo '  > run the curator for 10 cycles'
