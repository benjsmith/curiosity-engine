#!/usr/bin/env bash
# dispatch_worker.sh — coding-agent-CLI-agnostic subagent dispatch shim.
#
# Purpose. The curator orchestrator needs to dispatch fresh-context workers
# (one per wiki page in a wave) and fresh-context reviewers (one per wave).
# Claude Code has this built in as the Agent tool — the orchestrator calls
# it natively and this script is never invoked. Other coding-agent CLIs
# (Codex CLI, Gemini CLI, OpenClaude, etc.) have their own subagent
# mechanisms; this script is the canonical interface for plugging them
# in without branching prompts or orchestrator code.
#
# Interface.
#   dispatch_worker.sh --model <id> --prompt-file <path> [--timeout <sec>]
# Emits one JSON object on stdout — exactly what the worker prompt
# specifies as its return shape (e.g. {"page": "...", "new_text": "..."})
# — and exits 0 on success. Any non-zero exit means the dispatch failed;
# the orchestrator treats the worker as rejected.
#
# Single-session fallback. On CLIs that don't support subagent dispatch
# at all (Copilot Chat in VS Code), set CURIOSITY_ENGINE_DISPATCH_MODE=single
# in the workspace and the orchestrator will run workers sequentially in
# its own context with explicit role-reset prompts (see SKILL.md's
# "Single-session fallback" section). This script is not invoked in that
# mode; the note is here for discoverability.
#
# Customisation. Replace the stub below with the invocation your CLI
# accepts. Keep the stdout contract (one JSON object) so the orchestrator
# parses the result the same way regardless of vendor.
#
# This script is intentionally NOT hash-guarded — it is a user-customised
# shim, not part of the correctness-critical core.

set -eu

model=""
prompt_file=""
timeout_sec=""

while [ $# -gt 0 ]; do
    case "$1" in
        --model)
            model="$2"; shift 2 ;;
        --prompt-file)
            prompt_file="$2"; shift 2 ;;
        --timeout)
            timeout_sec="$2"; shift 2 ;;
        *)
            echo "{\"error\": \"unknown arg: $1\"}" >&2
            exit 2 ;;
    esac
done

if [ -z "$model" ] || [ -z "$prompt_file" ]; then
    echo '{"error": "dispatch_worker.sh requires --model and --prompt-file"}' >&2
    exit 2
fi

if [ ! -f "$prompt_file" ]; then
    echo "{\"error\": \"prompt file not found: $prompt_file\"}" >&2
    exit 2
fi

# ----- VENDOR-SPECIFIC INVOCATION GOES HERE -----
#
# Uncomment and edit one of these for your CLI:
#
# # Codex CLI (hypothetical subagent command)
# exec codex run-agent --model "$model" --prompt-file "$prompt_file"
#
# # Gemini CLI
# exec gemini run --model "$model" --prompt "$(cat "$prompt_file")"
#
# # Ollama via its native API (single-shot, no streaming)
# exec curl -s http://localhost:11434/api/generate \
#     -d "{\"model\": \"$model\", \"prompt\": $(jq -Rs . < "$prompt_file"), \"stream\": false, \"format\": \"json\"}" \
#     | jq -r '.response'

echo '{"error": "dispatch_worker.sh is a stub — edit it for your coding-agent CLI, or set CURIOSITY_ENGINE_DISPATCH_MODE=single to use the single-session fallback"}' >&2
exit 3
