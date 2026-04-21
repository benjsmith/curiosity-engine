#!/usr/bin/env bash
# evolve_guard.sh — reward-hacking guard for the CURATE loop.
#
# Hash-guards every skill script that scores, gates, evaluates, or
# composes wiki structure, plus itself. Records a fingerprint at wave
# start, compares at wave end. Drift aborts the wave.
#
# There is no agent-editable code path. Improvement ideas land as prose
# notes in .curator/log.md (## improvement-suggestions) for the human
# maintainer to evaluate. No agent-generated code enters execution.
#
# Usage:
#   evolve_guard.sh hash                    # print fingerprint to stdout
#   evolve_guard.sh snapshot <outfile>      # write fingerprint to outfile
#   evolve_guard.sh check <snapshotfile>    # compare snapshot vs current; exit 0/1
#
# The snapshot/check pair replaces the earlier stdin-based verify mode so
# the whole flow stays inside the curiosity-engine bash discipline rule
# (no pipes, no heredocs, one arg-based command per bash call).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUARDED=(
    "$SCRIPT_DIR/evolve_guard.sh"
    "$SCRIPT_DIR/lint_scores.py"
    "$SCRIPT_DIR/score_diff.py"
    "$SCRIPT_DIR/epoch_summary.py"
    "$SCRIPT_DIR/scrub_check.py"
    "$SCRIPT_DIR/naming.py"
    "$SCRIPT_DIR/graph.py"
    "$SCRIPT_DIR/sweep.py"
    "$SCRIPT_DIR/tables.py"
)

sha256_cmd() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

fingerprint() {
    for f in "${GUARDED[@]}"; do
        if [ ! -f "$f" ]; then
            echo "MISSING:$(basename "$f")"
        else
            printf '%s:%s\n' "$(sha256_cmd "$f")" "$(basename "$f")"
        fi
    done
}

case "${1:-}" in
    hash)
        fingerprint
        ;;
    snapshot)
        if [ -z "${2:-}" ]; then
            echo "usage: evolve_guard.sh snapshot <outfile>" >&2
            exit 2
        fi
        fingerprint > "$2"
        echo "wrote $2"
        ;;
    check)
        if [ -z "${2:-}" ] || [ ! -f "$2" ]; then
            echo "usage: evolve_guard.sh check <snapshotfile>" >&2
            exit 2
        fi
        expected="$(cat "$2")"
        actual="$(fingerprint)"
        if [ "$expected" = "$actual" ]; then
            echo "ok"
            exit 0
        fi
        echo "DRIFT"
        echo "--- expected ---"
        echo "$expected"
        echo "--- actual ---"
        echo "$actual"
        exit 1
        ;;
    *)
        echo "usage: evolve_guard.sh {hash|snapshot <file>|check <file>}" >&2
        exit 2
        ;;
esac
