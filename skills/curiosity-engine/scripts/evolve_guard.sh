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
    "$SCRIPT_DIR/derived_cache.py"
    "$SCRIPT_DIR/score_diff.py"
    "$SCRIPT_DIR/epoch_summary.py"
    "$SCRIPT_DIR/scrub_check.py"
    "$SCRIPT_DIR/naming.py"
    "$SCRIPT_DIR/graph.py"
    "$SCRIPT_DIR/entity_gate.py"
    "$SCRIPT_DIR/embedder.py"
    "$SCRIPT_DIR/sweep.py"
    "$SCRIPT_DIR/tables.py"
    "$SCRIPT_DIR/shape_check.py"
    "$SCRIPT_DIR/figures.py"
    "$SCRIPT_DIR/restyle.py"
    "$SCRIPT_DIR/scan.py"
    "$SCRIPT_DIR/code_repo.py"
    "$SCRIPT_DIR/code_capture.py"
    "$SCRIPT_DIR/session_drainer.py"
    "$SCRIPT_DIR/session_brief.py"
    "$SCRIPT_DIR/curate_launch.py"
    "$SCRIPT_DIR/curate_status.py"
)

sha256_cmd() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

mtime_cmd() {
    # Epoch seconds. BSD (macOS) and GNU stat disagree on the flag.
    if stat -f %m "$1" >/dev/null 2>&1; then
        stat -f %m "$1"
    else
        stat -c %Y "$1"
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

# Files rewritten by one `skills update` / setup.sh run land within a
# couple of seconds of each other. 300s is generous enough to absorb a
# slow install without being wide enough to cover an agent editing one
# script, pausing, and editing another.
CLUSTER_WINDOW=300

case "${1:-}" in
    hash)
        fingerprint
        ;;
    snapshot)
        if [ -z "${2:-}" ]; then
            echo "usage: evolve_guard.sh snapshot <outfile>" >&2
            exit 2
        fi
        # The timestamp is what lets `check` tell a maintainer reinstall
        # from an agent editing a guarded script mid-wave. Both are byte
        # changes; only their timing distinguishes them. ISO for humans and
        # grep, epoch for arithmetic — parsing ISO back to epoch portably
        # across BSD and GNU date is more trouble than storing both.
        {
            printf '# snapshot_ts: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            printf '# snapshot_epoch: %s\n' "$(date -u +%s)"
            fingerprint
        } > "$2"
        echo "wrote $2"
        ;;
    check)
        if [ -z "${2:-}" ] || [ ! -f "$2" ]; then
            echo "usage: evolve_guard.sh check <snapshotfile>" >&2
            exit 2
        fi
        expected="$(grep -v '^#' "$2" || true)"
        snap_epoch="$(grep '^# snapshot_epoch:' "$2" | head -1 | awk '{print $3}' || true)"
        snap_ts="$(grep '^# snapshot_ts:' "$2" | head -1 | awk '{print $3}' || true)"
        actual="$(fingerprint)"

        # Per-file drift + mtime survey in one pass.
        drifted=""
        oldest_all=""
        newest_all=""
        oldest_drifted=""
        for f in "${GUARDED[@]}"; do
            base="$(basename "$f")"
            exp_sha="$(printf '%s\n' "$expected" | grep -F ":$base" | head -1 | cut -d: -f1 || true)"
            if [ ! -f "$f" ]; then
                cur_sha="MISSING"
                m=""
            else
                cur_sha="$(sha256_cmd "$f")"
                m="$(mtime_cmd "$f")"
                if [ -z "$oldest_all" ] || [ "$m" -lt "$oldest_all" ]; then oldest_all="$m"; fi
                if [ -z "$newest_all" ] || [ "$m" -gt "$newest_all" ]; then newest_all="$m"; fi
            fi
            if [ "$exp_sha" != "$cur_sha" ]; then
                drifted="$drifted $base"
                if [ -n "$m" ]; then
                    if [ -z "$oldest_drifted" ] || [ "$m" -lt "$oldest_drifted" ]; then
                        oldest_drifted="$m"
                    fi
                fi
            fi
        done

        # Does the whole guarded set look like it was rewritten in one go,
        # after this snapshot was taken? That is the reinstall signature: a
        # tampering agent edits one or two scripts and leaves the rest with
        # their original mtimes.
        whole_set_rewritten=0
        if [ -n "$snap_epoch" ] && [ -n "$oldest_all" ] && [ -n "$newest_all" ]; then
            if [ "$oldest_all" -gt "$snap_epoch" ] \
               && [ "$((newest_all - oldest_all))" -le "$CLUSTER_WINDOW" ]; then
                whole_set_rewritten=1
            fi
        fi

        if [ -z "$drifted" ]; then
            if [ "$whole_set_rewritten" -eq 1 ]; then
                echo "STALE_SNAPSHOT"
                echo "No content drift, but every guarded script was rewritten after"
                echo "this snapshot was taken (${snap_ts}) — a reinstall landed mid-run."
                echo "Re-snapshot before the next wave: evolve_guard.sh snapshot $2"
                exit 0
            fi
            echo "ok"
            exit 0
        fi

        # Backward compatibility: a snapshot written before timestamps
        # existed carries no epoch, so it cannot distinguish anything.
        # Behave exactly as before — any drift is DRIFT.
        if [ -z "$snap_epoch" ]; then
            echo "DRIFT"
            echo "(snapshot has no timestamp; wrote before this check existed —"
            echo " cannot distinguish a reinstall. Re-snapshot to enable that.)"
            echo "--- drifted ---"
            echo "$drifted"
            exit 1
        fi

        # A changed file whose mtime predates the snapshot is not possible
        # from an honest write, so it is never excused.
        if [ -n "$oldest_drifted" ] && [ "$oldest_drifted" -le "$snap_epoch" ]; then
            echo "DRIFT"
            echo "A guarded script changed but its mtime predates the snapshot"
            echo "(${snap_ts}) — inconsistent with any legitimate write."
            echo "--- drifted ---"
            echo "$drifted"
            exit 1
        fi

        if [ "$whole_set_rewritten" -eq 1 ]; then
            echo "STALE_SNAPSHOT"
            echo "Guarded scripts changed, but every one of them was rewritten"
            echo "together after this snapshot (${snap_ts}) — the signature of a"
            echo "skill reinstall between waves, not of a mid-wave edit."
            echo "Content differs in:$drifted"
            echo "Treat the wave's work as valid and re-snapshot:"
            echo "  evolve_guard.sh snapshot $2"
            exit 0
        fi

        echo "DRIFT"
        echo "Guarded scripts changed without the whole-set rewrite signature"
        echo "of a reinstall (snapshot ${snap_ts})."
        echo "--- drifted ---"
        echo "$drifted"
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
