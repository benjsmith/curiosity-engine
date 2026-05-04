#!/usr/bin/env bash
# viewer.sh — build/serve the custom wiki viewer (graph-first, D3-based).
#
# Curiosity-engine-native browser-based wiki view. The build script
# (wiki_render.py) walks wiki/, queries the kuzu graph, copies the
# template/wiki-view/ tree (HTML, CSS, JS, vendor bundles) and emits
# a single static-site bundle into
#   ~/.cache/curiosity-engine/wiki-view/<workspace>/
#
# Vendor libraries (D3 + Fuse.js) are committed in-repo at
# template/wiki-view/static/vendor/ and copied into each workspace
# bundle by wiki_render.py's static-tree walk. No CDN download at
# build time — the bundles travel with the skill, ensuring offline
# builds and closing the supply-chain risk of a compromised CDN.
# Versions + sha256 hashes are recorded in RELEASE_CHECKLIST.md.
#
# Usage:
#   viewer.sh build           # Build the bundle
#   viewer.sh serve [port]    # Build + serve on http://localhost:<port> (default 8090)
#   viewer.sh open  [port]    # serve + open in default browser

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(dirname "$SCRIPT_DIR")"
WORKSPACE="$(pwd)"
WIKI_ABS="$WORKSPACE/wiki"

if [ ! -d "$WIKI_ABS" ]; then
    echo "ERROR: no wiki/ directory in current path ($WORKSPACE)" >&2
    exit 1
fi

WORKSPACE_NAME="$(basename "$WORKSPACE")"
OUTPUT_ROOT="$HOME/.cache/curiosity-engine/wiki-view"
OUTPUT_DIR="$OUTPUT_ROOT/$WORKSPACE_NAME"

build() {
    uv run python3 "$SCRIPT_DIR/wiki_render.py" build "$WIKI_ABS" \
        --output-dir "$OUTPUT_DIR"
}

# Resolve a free port. Prefer the requested one (default 8090); on
# collision, interactive mode prompts kill/auto/cancel; non-interactive
# auto-picks the next free port.
_pick_port() {
    local start_port="${1:-8090}"
    local port="$start_port"
    if lsof -ti:"$port" >/dev/null 2>&1; then
        local holder holder_cmd
        holder="$(lsof -ti:"$port" | head -1)"
        holder_cmd="$(ps -p "$holder" -o comm= 2>/dev/null | tr -d ' ')"
        if [ -t 0 ] && [ -t 1 ]; then
            echo "Port $port in use (pid $holder${holder_cmd:+ — $holder_cmd})." >&2
            echo "  [1] kill it and reuse" >&2
            echo "  [2] serve on next free port" >&2
            echo "  [3] cancel" >&2
            printf "Choice [1/2/3]: " >&2
            local reply
            read -r reply || reply=3
            case "$reply" in
                1) kill "$holder" 2>/dev/null; sleep 1 ;;
                2) while lsof -ti:"$port" >/dev/null 2>&1; do port=$((port + 1)); done ;;
                *) echo "cancelled." >&2; return 1 ;;
            esac
        else
            while lsof -ti:"$port" >/dev/null 2>&1; do port=$((port + 1)); done
            echo "Port $start_port in use; auto-selected $port." >&2
        fi
    fi
    echo "$port"
}

cmd="${1:-serve}"
case "$cmd" in
    build)
        build
        ;;
    serve)
        build
        port="$(_pick_port "${2:-8090}")" || exit 1
        echo "Wiki viewer serving $OUTPUT_DIR at http://localhost:$port"
        echo "(Ctrl+C to stop; in-viewer edits and uploads write directly back to wiki/ + vault/raw/)"
        uv run --no-project python3 "$SCRIPT_DIR/viewer_server.py" "$OUTPUT_DIR" "$WORKSPACE" "$port"
        ;;
    open)
        build
        port="$(_pick_port "${2:-8090}")" || exit 1
        url="http://localhost:$port"
        echo "Wiki viewer serving $OUTPUT_DIR at $url"
        (sleep 1 && (command -v open >/dev/null && open "$url" || \
                     command -v xdg-open >/dev/null && xdg-open "$url" || \
                     echo "(open $url manually)")) &
        uv run --no-project python3 "$SCRIPT_DIR/viewer_server.py" "$OUTPUT_DIR" "$WORKSPACE" "$port"
        ;;
    *)
        echo "Usage: viewer.sh {build|serve [port]|open [port]}" >&2
        exit 2
        ;;
esac
