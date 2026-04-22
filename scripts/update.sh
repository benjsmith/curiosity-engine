#!/usr/bin/env bash
# update.sh — in-session skill update for a curiosity-engine workspace.
#
# Replaces the manual "exit session → update skill → cd back → run
# setup.sh → restart session" dance with a single agent-callable
# command. Handles both install channels:
#
#   * git-clone install → `git pull` in the skill dir
#   * npx-skills install (no .git in skill dir) → `npx skills update -g
#     <slug>`, where slug comes from `update_source_slug` in the workspace's
#     `.curator/config.json`. Defaults to the upstream value seeded by
#     setup.sh; fork users edit the key once in their workspace config.
#
# Typical agent flow:
#
#   1. User: "update the skill"
#   2. Agent calls update.sh (no args) — prints release notes (git install)
#      or update plan (npx install), exits 0 without changes because
#      non-interactive callers need --yes.
#   3. Agent shows the output to the user; user confirms.
#   4. Agent re-invokes update.sh --yes — auto-commits any dirty wiki
#      edits, applies the update, runs setup.sh's migration pass.
#
# A human running in a terminal sees the output and gets a [y/N]
# prompt instead of the --yes requirement.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(dirname "$SCRIPT_DIR")"
WORKSPACE="$(pwd)"

if [ ! -d "$WORKSPACE/wiki" ] || [ ! -d "$WORKSPACE/.curator" ]; then
    echo "ERROR: run update.sh from a curiosity-engine workspace root."
    echo "       Expected wiki/ and .curator/ in $(pwd)."
    exit 1
fi

# Determine install channel. Git wins when the skill dir carries a
# .git (direct clone); otherwise fall back to npx-skills if available.
if [ -d "$SKILL_ROOT/.git" ]; then
    UPDATE_METHOD="git"
elif command -v npx >/dev/null 2>&1; then
    UPDATE_METHOD="npx"
else
    echo "ERROR: skill install at $SKILL_ROOT is not a git repo and npx is"
    echo "       not on PATH — update.sh has no self-update path available."
    echo "       Reinstall the skill via its original install command."
    exit 1
fi

# Slug only matters for the npx path, but read it early so the plan
# output can show it. Value comes from the workspace config
# (editable per-workspace); falls back to the upstream default so
# freshly-seeded workspaces work without extra user action.
SLUG="$(uv run --no-project python3 -c "
import json, sys
try:
    print(json.load(open('.curator/config.json')).get('update_source_slug', ''))
except Exception:
    pass
" 2>/dev/null || true)"
if [ -z "$SLUG" ]; then
    SLUG="benjsmith/curiosity-engine"
fi

# ── Preview stage. Collect update plan + optional release notes into
#    a shared block so the approval gate logic below is identical across
#    methods.
if [ "$UPDATE_METHOD" = "git" ]; then
    echo "Fetching updates from $SKILL_ROOT ..."
    git -C "$SKILL_ROOT" fetch --quiet

    local_sha="$(git -C "$SKILL_ROOT" rev-parse HEAD)"
    upstream_ref="$(git -C "$SKILL_ROOT" rev-parse --abbrev-ref '@{u}' 2>/dev/null || true)"
    if [ -z "$upstream_ref" ]; then
        echo "ERROR: current skill branch has no upstream tracking ref."
        echo "       Set one with: git -C $SKILL_ROOT branch --set-upstream-to=origin/<branch>"
        exit 1
    fi
    upstream_sha="$(git -C "$SKILL_ROOT" rev-parse "$upstream_ref")"

    if [ "$local_sha" = "$upstream_sha" ]; then
        echo "Already up to date ($local_sha)."
        exit 0
    fi

    echo ""
    echo "=== Release notes ==="
    echo "  Local:    $local_sha"
    echo "  Upstream: $upstream_sha  ($upstream_ref)"
    echo ""
    git -C "$SKILL_ROOT" log --pretty=format:'  %h  %s' "$local_sha..$upstream_sha"
    echo ""
    echo ""
else
    # npx-skills path. No upstream commit log is available without a
    # second network trip to GitHub — keep it simple and surface the
    # plan. Users can read commits upstream if they want the full log.
    echo ""
    echo "=== npx-skills update plan ==="
    echo "  Skill dir:  $SKILL_ROOT (no .git)"
    echo "  Slug:       $SLUG  (from .curator/config.json → update_source_slug)"
    echo "  Will run:   npx skills update -g $SLUG"
    echo ""
    echo "  Detailed release notes aren't available for npx-skills installs —"
    echo "  inspect the upstream repo on GitHub if you want the full log before"
    echo "  proceeding."
    echo ""
fi

# ── Approval gate (shared across methods).
APPROVED=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y) APPROVED=1 ;;
    esac
done
if [ "$APPROVED" = "0" ]; then
    if [ -t 0 ] && [ -t 1 ]; then
        printf "Apply update? [y/N] "
        read -r reply || reply="n"
        case "$reply" in
            y|Y|yes|YES) APPROVED=1 ;;
        esac
    fi
fi
if [ "$APPROVED" = "0" ]; then
    echo "Update not applied. Re-run with --yes to proceed:"
    echo "  bash $SCRIPT_DIR/update.sh --yes"
    exit 0
fi

# ── Apply stage (shared preamble: auto-commit dirty wiki so setup.sh's
#    migration pass isn't skipped).
if [ -d "$WORKSPACE/wiki/.git" ] && [ -n "$(git -C "$WORKSPACE/wiki" status --porcelain)" ]; then
    echo ""
    echo "Auto-committing pending wiki changes ..."
    git -C "$WORKSPACE/wiki" add -A
    git -C "$WORKSPACE/wiki" commit -q -m "wip: auto-commit before skill update"
fi

if [ "$UPDATE_METHOD" = "git" ]; then
    echo ""
    echo "Pulling skill ..."
    git -C "$SKILL_ROOT" pull --quiet --ff-only
else
    echo ""
    echo "Running npx skills update -g $SLUG ..."
    npx skills update -g "$SLUG"
fi

echo ""
echo "Running setup.sh (migration pass) ..."
bash "$SCRIPT_DIR/setup.sh"

echo ""
echo "=== Update complete ==="
if [ "$UPDATE_METHOD" = "git" ]; then
    new_sha="$(git -C "$SKILL_ROOT" rev-parse HEAD)"
    echo "Skill: $local_sha → $new_sha"
else
    echo "Skill refreshed via npx-skills ($SLUG)."
fi
