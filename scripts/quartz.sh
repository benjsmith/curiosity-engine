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

cmd="${1:-serve}"
case "$cmd" in
    build)
        npx quartz build
        ;;
    serve)
        echo "Quartz serving $WIKI_ABS at http://localhost:8080"
        echo "(Ctrl+C to stop; the curator's writes will appear on the next build)"
        npx quartz build --serve
        ;;
    open)
        echo "Quartz serving $WIKI_ABS at http://localhost:8080"
        (sleep 4 && (command -v open >/dev/null && open http://localhost:8080 || \
                     command -v xdg-open >/dev/null && xdg-open http://localhost:8080 || \
                     echo "(open http://localhost:8080 manually)")) &
        npx quartz build --serve
        ;;
    *)
        echo "Usage: quartz.sh {build|serve|open}" >&2
        exit 2
        ;;
esac
