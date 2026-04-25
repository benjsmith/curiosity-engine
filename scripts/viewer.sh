#!/usr/bin/env bash
# viewer.sh — build/serve the custom wiki viewer (graph-first, D3-based).
#
# This is the curiosity-engine-native alternative to quartz.sh. The
# build script (wiki_render.py) walks wiki/, queries the kuzu graph,
# and emits a single static-site bundle into
#   ~/.cache/curiosity-engine/wiki-view/<workspace>/
#
# Vendor libraries (D3 + Fuse.js) are downloaded once into a shared
# location and copied into each workspace bundle, so the rendered
# site stays self-contained and offline-capable.
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
VENDOR_DIR="$HOME/.cache/curiosity-engine/wiki-view-vendor"

D3_URL="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
FUSE_URL="https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js"

ensure_vendor() {
    mkdir -p "$VENDOR_DIR"
    if [ ! -s "$VENDOR_DIR/d3.min.js" ]; then
        echo "  Downloading D3 ..."
        curl -fsSL -o "$VENDOR_DIR/d3.min.js" "$D3_URL" \
            || { echo "  Download failed; viewer needs $D3_URL" >&2; exit 1; }
    fi
    if [ ! -s "$VENDOR_DIR/fuse.min.js" ]; then
        echo "  Downloading Fuse.js ..."
        curl -fsSL -o "$VENDOR_DIR/fuse.min.js" "$FUSE_URL" \
            || { echo "  Download failed; viewer needs $FUSE_URL" >&2; exit 1; }
    fi
}

build() {
    ensure_vendor
    mkdir -p "$OUTPUT_DIR/static/vendor"
    cp "$VENDOR_DIR/d3.min.js"   "$OUTPUT_DIR/static/vendor/d3.min.js"
    cp "$VENDOR_DIR/fuse.min.js" "$OUTPUT_DIR/static/vendor/fuse.min.js"
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
        echo "(Ctrl+C to stop; rerun 'viewer.sh build' to refresh after wiki edits)"
        cd "$OUTPUT_DIR"
        uv run --no-project python3 -m http.server "$port" --bind 127.0.0.1
        ;;
    open)
        build
        port="$(_pick_port "${2:-8090}")" || exit 1
        url="http://localhost:$port"
        echo "Wiki viewer serving $OUTPUT_DIR at $url"
        (sleep 1 && (command -v open >/dev/null && open "$url" || \
                     command -v xdg-open >/dev/null && xdg-open "$url" || \
                     echo "(open $url manually)")) &
        cd "$OUTPUT_DIR"
        uv run --no-project python3 -m http.server "$port" --bind 127.0.0.1
        ;;
    *)
        echo "Usage: viewer.sh {build|serve [port]|open [port]}" >&2
        exit 2
        ;;
esac
