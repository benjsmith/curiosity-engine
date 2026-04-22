#!/usr/bin/env bash
# update.sh — in-session skill update for a curiosity-engine workspace.
#
# Replaces the manual "exit session → cd to skill dir → git pull → cd back
# → run setup.sh → restart session" dance with a single agent-callable
# command. Typical agent flow:
#
#   1. User: "update the skill"
#   2. Agent calls update.sh (no args) — prints release notes, exits 0
#      without changes because non-interactive callers need --yes.
#   3. Agent shows notes to the user; user confirms.
#   4. Agent re-invokes update.sh --yes — auto-commits any dirty wiki
#      edits, pulls the skill, runs setup.sh's migration pass.
#
# A human running in a terminal sees the release notes and gets a
# [y/N] prompt instead of the --yes requirement.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(dirname "$SCRIPT_DIR")"
WORKSPACE="$(pwd)"

if [ ! -d "$WORKSPACE/wiki" ] || [ ! -d "$WORKSPACE/.curator" ]; then
    echo "ERROR: run update.sh from a curiosity-engine workspace root."
    echo "       Expected wiki/ and .curator/ in $(pwd)."
    exit 1
fi

if [ ! -d "$SKILL_ROOT/.git" ]; then
    echo "ERROR: skill install at $SKILL_ROOT is not a git repo — cannot self-update."
    echo "       Reinstall the skill via its original install command instead."
    exit 1
fi

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

# Approval gate. --yes/-y skips the prompt; interactive tty falls back
# to a [y/N] prompt; non-interactive without --yes prints a hint and
# exits cleanly so the agent can relay notes to the user before re-
# invoking with consent.
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

# Auto-commit dirty wiki so setup.sh's migration pass isn't skipped
# (it refuses to run against a dirty wiki). Canned message; user can
# squash / reword later if they want.
if [ -d "$WORKSPACE/wiki/.git" ] && [ -n "$(git -C "$WORKSPACE/wiki" status --porcelain)" ]; then
    echo ""
    echo "Auto-committing pending wiki changes ..."
    git -C "$WORKSPACE/wiki" add -A
    git -C "$WORKSPACE/wiki" commit -q -m "wip: auto-commit before skill update"
fi

echo ""
echo "Pulling skill ..."
git -C "$SKILL_ROOT" pull --quiet --ff-only

echo ""
echo "Running setup.sh (migration pass) ..."
bash "$SCRIPT_DIR/setup.sh"

echo ""
echo "=== Update complete ==="
new_sha="$(git -C "$SKILL_ROOT" rev-parse HEAD)"
echo "Skill: $local_sha → $new_sha"
