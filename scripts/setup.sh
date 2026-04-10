#!/usr/bin/env bash
set -e

echo "=== Curiosity Engine Setup ==="

# Resolve paths. SCRIPT_DIR is where setup.sh lives (either the cloned repo's
# scripts/ or the installed skill's scripts/). TEMPLATE_DIR is its sibling
# template/ directory, the single source of truth for the wiki skeleton.
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
