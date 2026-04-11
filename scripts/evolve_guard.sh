#!/usr/bin/env bash
# evolve_guard.sh — reward-hacking guard for the EVOLVE meta-loop.
#
# EVOLVE is allowed to edit wiki/schema.md but nothing about the scoring
# pipeline. This script records a fingerprint of the scoring scripts at
# epoch start and verifies it at epoch end. If anything drifted, the
# epoch must be aborted and the schema edit reverted.
#
# Usage:
#   evolve_guard.sh hash        # print JSON fingerprint to stdout
#   evolve_guard.sh verify      # read fingerprint on stdin, exit 0 if match, 1 if not

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUARDED=(
    "$SCRIPT_DIR/compress.py"
    "$SCRIPT_DIR/lint_scores.py"
)

fingerprint() {
    for f in "${GUARDED[@]}"; do
        if [ ! -f "$f" ]; then
            echo "MISSING:$f"
        else
            shasum -a 256 "$f" | awk '{print $1}' | tr -d '\n'
            echo ":$(basename "$f")"
        fi
    done
}

case "${1:-}" in
    hash)
        fingerprint
        ;;
    verify)
        expected="$(cat)"
        actual="$(fingerprint)"
        if [ "$expected" = "$actual" ]; then
            echo "ok"
            exit 0
        else
            echo "DRIFT"
            echo "--- expected ---"
            echo "$expected"
            echo "--- actual ---"
            echo "$actual"
            exit 1
        fi
        ;;
    *)
        echo "usage: evolve_guard.sh {hash|verify}" >&2
        exit 2
        ;;
esac
