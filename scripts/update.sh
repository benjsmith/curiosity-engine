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
# Flow (identical for humans and agents — strictly two-step):
#
#   1. User: "update the skill"
#   2. Caller invokes update.sh (no args) — prints release notes (git
#      install) or update plan (npx install), then exits 0 without
#      changes. The script never prompts: a TTY-based [y/N] prompt would
#      hang under coding-agent CLIs that allocate a PTY but can't
#      forward keystrokes (notably GitHub Copilot Chat in VS Code).
#   3. Caller shows the output; user confirms.
#   4. Caller re-invokes update.sh --yes — auto-commits any dirty wiki
#      edits, applies the update, runs setup.sh's migration pass with
#      CURIOSITY_ENGINE_NONINTERACTIVE=1 exported so its prompts also
#      stay non-blocking.

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
# npx-skills tracks installs by bare skill name (the repo portion of
# the slug), not the full owner/repo form — `npx skills update -g
# owner/repo` reports "No installed skills found matching". Derive the
# bare name by stripping everything up to and including the last `/`.
# Works both for a full slug (`benjsmith/curiosity-engine` →
# `curiosity-engine`) and for a bare name the user may have typed
# directly into the config (left unchanged).
SKILL_NAME="${SLUG##*/}"

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
    echo "  Will run:   npx skills update -g $SKILL_NAME"
    echo ""
    echo "  Detailed release notes aren't available for npx-skills installs —"
    echo "  inspect the upstream repo on GitHub if you want the full log before"
    echo "  proceeding."
    echo ""
fi

# ── Approval gate (strictly two-step, no TTY prompt).
#
# Older revisions tried a [y/N] prompt when both stdin and stdout were
# TTYs. That hangs under GitHub Copilot Chat in VS Code: Copilot
# allocates a PTY for the bash subprocess (so the TTY check passes) but
# has no channel to forward user keystrokes into it, so `read` blocks
# forever. The two-step flow is universal — humans just copy-paste the
# `--yes` re-invocation echoed below.
APPROVED=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y) APPROVED=1 ;;
    esac
done
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
    echo "Running npx skills update -g $SKILL_NAME ..."

    # Codex CLI sandboxes network/cache access by default; npx-skills
    # then blocks on a network call without surfacing useful stderr,
    # which looks like an indefinite hang. Print the workaround
    # up-front when we detect we're inside Codex so the user knows
    # what to do if the wrapper below trips.
    if [ -n "$CODEX_HOME" ] || [ -n "$CODEX_SANDBOX" ] || [ -n "$CODEX_SESSION_ID" ]; then
        echo "  Note: detected Codex CLI. npx-skills needs network + ~/.npm cache"
        echo "  access. If this call hangs, Codex's sandbox is blocking the network"
        echo "  request silently. Two paths out:"
        echo "    (a) ctrl+c, then re-run THIS script with Codex's \"approve /"
        echo "        run with escalated permissions\" answer when it asks"
        echo "        (network + write to ~/.npm/_npx is the request to approve), or"
        echo "    (b) run 'bash $SCRIPT_DIR/update.sh --yes' from a non-sandboxed"
        echo "        shell."
        echo "  If neither is available, switch install channel: clone the skill"
        echo "  via git so update.sh uses 'git pull' instead of npx."
        echo ""
    fi

    # Hard timeout so the sandbox-blocking case (or any other npx
    # cache/registry stall) fails loudly within 3 minutes instead of
    # hanging the agent indefinitely. macOS ships neither `timeout`
    # nor `gtimeout` by default — fall back to unwrapped exec when
    # neither is on PATH.
    if command -v timeout >/dev/null 2>&1; then
        _timeout_cmd="timeout 180"
    elif command -v gtimeout >/dev/null 2>&1; then
        _timeout_cmd="gtimeout 180"
    else
        _timeout_cmd=""
    fi

    # npx-skills exits 0 even when it can't find the named skill, so
    # capture the output and check for its "No installed skills found"
    # signal explicitly to avoid a silent no-op.
    set +e
    _npx_out="$($_timeout_cmd npx skills update -g "$SKILL_NAME" 2>&1)"
    _npx_status=$?
    set -e
    echo "$_npx_out"
    if [ "$_npx_status" -eq 124 ]; then
        echo ""
        echo "ERROR: npx skills update timed out after 180s."
        echo "       Most likely cause: sandboxed network/cache access."
        echo "       In Codex, re-run with Codex's escalation approval"
        echo "       (network + write to ~/.npm/_npx). Outside Codex,"
        echo "       verify the npm registry and ~/.npm cache are reachable."
        exit 1
    fi
    if [ "$_npx_status" -ne 0 ]; then
        echo ""
        echo "ERROR: npx skills update exited $_npx_status. See output above."
        exit 1
    fi
    if echo "$_npx_out" | grep -qi "No installed skills found matching"; then
        echo ""
        echo "ERROR: npx-skills did not recognise '$SKILL_NAME'. Installed skills:"
        npx skills list -g 2>&1 | grep -E '^[[:space:]]*[a-z]' | head -20
        echo ""
        echo "Set update_source_slug in .curator/config.json to a value whose last"
        echo "segment matches one of the installed skill names above."
        exit 1
    fi
fi

echo ""
echo "Running setup.sh (migration pass) ..."
# Force non-interactive mode so setup.sh's prompts can't reintroduce the
# Copilot-PTY hang we just removed above. setup.sh treats this as
# equivalent to a non-TTY shell and uses each prompt's documented default.
CURIOSITY_ENGINE_NONINTERACTIVE=1 bash "$SCRIPT_DIR/setup.sh"

echo ""
echo "=== Update complete ==="
if [ "$UPDATE_METHOD" = "git" ]; then
    new_sha="$(git -C "$SKILL_ROOT" rev-parse HEAD)"
    echo "Skill: $local_sha → $new_sha"
else
    echo "Skill refreshed via npx-skills ($SLUG)."
fi
