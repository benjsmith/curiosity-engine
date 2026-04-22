#!/usr/bin/env bash
# quartz.sh — build/serve a static-site view of the wiki via Quartz.
#
# Quartz (https://quartz.jzhao.xyz/) renders Obsidian-style wikis to
# a static HTML site with backlinks + a D3 graph view. Install lives
# at ~/.cache/curiosity-engine/quartz (shared across workspaces, so
# setup.sh only downloads Node + Quartz once per machine). This
# script symlinks the current workspace's wiki/ into that install's
# content/ directory and runs Quartz's build or serve command.
#
# Usage:
#   quartz.sh build    # Build static site into ~/.cache/.../quartz/public/
#   quartz.sh serve    # Build + serve on http://localhost:8080 (stays running)
#   quartz.sh open     # Build + serve + open in default browser (stays running)
#
# Invoke from the workspace root (where wiki/ lives). Ctrl+C stops
# the serve mode. The curator's writes land in the rendered site on
# the next build.

set -e

QUARTZ_ROOT="$HOME/.cache/curiosity-engine/quartz"
WIKI_ABS="$(pwd)/wiki"

if [ ! -d "$QUARTZ_ROOT" ]; then
    cat >&2 <<EOF
Quartz is not installed at $QUARTZ_ROOT.
Run setup.sh and accept the Quartz install prompt, or install manually:
  mkdir -p "$(dirname "$QUARTZ_ROOT")"
  git clone --depth 1 https://github.com/jackyzha0/quartz.git "$QUARTZ_ROOT"
  cd "$QUARTZ_ROOT" && npm install
EOF
    exit 1
fi

if [ ! -d "$WIKI_ABS" ]; then
    echo "ERROR: no wiki/ directory in current path ($(pwd))" >&2
    exit 1
fi

# Stage the workspace wiki as Quartz's content. Quartz expects a
# `content/` directory inside its install root. Symlink so edits in
# the real wiki/ are reflected without a copy step.
cd "$QUARTZ_ROOT"
if [ -L content ]; then
    rm content
elif [ -d content ]; then
    # A real content/ dir may have been created by `npx quartz create`
    # during a prior manual setup. Move it aside so users don't lose it.
    mv content "content.saved.$(date +%Y%m%d-%H%M%S)"
fi
ln -s "$WIKI_ABS" content

# Resolve a free port for the serve modes. Prefer 8080 (Quartz default).
# When it's already in use: interactive mode asks whether to kill the
# holder or pick a free port; non-interactive mode auto-picks a free port.
_pick_port() {
    local start_port="${1:-8080}"
    local port="$start_port"
    if lsof -ti:"$port" >/dev/null 2>&1; then
        local holder
        holder="$(lsof -ti:"$port" | head -1)"
        local holder_cmd
        holder_cmd="$(ps -p "$holder" -o comm= 2>/dev/null | tr -d ' ')"
        if [ -t 0 ] && [ -t 1 ]; then
            echo "Port $port is in use (pid $holder${holder_cmd:+ — $holder_cmd})." >&2
            echo "  [1] kill the existing process and reuse $port" >&2
            echo "  [2] serve on an auto-selected free port" >&2
            echo "  [3] cancel" >&2
            printf "Choice [1/2/3]: " >&2
            local reply
            read -r reply || reply=3
            case "$reply" in
                1)
                    kill "$holder" 2>/dev/null
                    # Give the OS a moment to release the socket.
                    sleep 1
                    ;;
                2)
                    while lsof -ti:"$port" >/dev/null 2>&1; do
                        port=$((port + 1))
                    done
                    ;;
                *)
                    echo "cancelled." >&2
                    return 1
                    ;;
            esac
        else
            # Non-interactive — auto-pick a free port, announce it.
            while lsof -ti:"$port" >/dev/null 2>&1; do
                port=$((port + 1))
            done
            echo "Port $start_port in use; auto-selected $port." >&2
        fi
    fi
    echo "$port"
}

cmd="${1:-serve}"
case "$cmd" in
    build)
        npx quartz build
        ;;
    serve)
        _port="$(_pick_port 8080)" || exit 1
        echo "Quartz serving $WIKI_ABS at http://localhost:$_port"
        echo "(Ctrl+C to stop; the curator's writes will appear on the next build)"
        npx quartz build --serve --port "$_port"
        ;;
    open)
        _port="$(_pick_port 8080)" || exit 1
        echo "Quartz serving $WIKI_ABS at http://localhost:$_port"
        (sleep 4 && (command -v open >/dev/null && open "http://localhost:$_port" || \
                     command -v xdg-open >/dev/null && xdg-open "http://localhost:$_port" || \
                     echo "(open http://localhost:$_port manually)")) &
        npx quartz build --serve --port "$_port"
        ;;
    *)
        echo "Usage: quartz.sh {build|serve|open}" >&2
        exit 2
        ;;
esac
