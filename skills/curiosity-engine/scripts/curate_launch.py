#!/usr/bin/env python3
"""curate_launch.py — spawn a detached curate session in the workspace.

`/curate` from inside a code-repo cwd would otherwise burn the
engineer's coding-session context with operational chatter and pollute
the transcript that `/distill` needs to read. The launcher splits the
two: the engineer's coding session stays interactive in the code-repo
cwd; the curate loop runs as a separate process in the workspace cwd
with its own session jsonl.

Mechanics:

  1. Detect the active host CLI. Today supports Claude Code via
     `claude -p`. Other hosts fall back to a stdout message.
  2. Compose a one-shot prompt that triggers the standard CURATE loop.
  3. Spawn the host CLI as a detached process (`start_new_session=True`,
     stdout/stderr redirected to `<workspace>/.curator/sessions/<id>.log`).
     cwd of the spawned process is the workspace, so the host loads the
     workspace's CLAUDE.md / settings.json and the session jsonl lands
     in `~/.claude/projects/<flatpath-of-workspace>/` — separate from
     the engineer's coding-session project dir.
  4. Write a status file at
     `<workspace>/.curator/sessions/<id>.status.json` with PID, log path,
     start time. `curate_status.py` reads it.

Usage:
  curate_launch.py --workspace <path> [--prompt "<override>"]
                                        [--host claude|codex|gemini|copilot]

Stdlib only. Hash-guarded.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROMPT = (
    "Run the standard CURATE loop on this workspace. Detect host as you "
    "would normally; load .curator/schema.md, .curator/prompts.md, and "
    ".curator/config.json. Run waves until wallclock_max_hours or natural "
    "saturation. Log progress to .curator/log.md per the existing CURATE "
    "convention. No questions, no follow-ups. End with a one-line summary."
)


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _detect_host() -> str:
    """Return one of: claude, codex, gemini, copilot, unknown.

    Uses the env-var fingerprints documented in SKILL.md § Bash discipline
    host registry.
    """
    if os.environ.get("CLAUDECODE") == "1":
        return "claude"
    if os.environ.get("CODEX_HOME") or shutil.which("codex"):
        return "codex"
    if os.environ.get("GEMINI_API_KEY") and shutil.which("gemini"):
        return "gemini"
    if os.environ.get("TERM_PROGRAM") == "vscode":
        return "copilot"  # best guess
    if shutil.which("claude"):
        return "claude"
    return "unknown"


def _spawn_claude_code(workspace: Path, log_path: Path, prompt: str) -> int:
    """Spawn `claude -p <prompt>` detached in the workspace cwd.

    Returns the spawned PID. Raises on launch failure.
    """
    binary = shutil.which("claude")
    if not binary:
        raise FileNotFoundError("`claude` not found on PATH")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "w", buffering=1)

    proc = subprocess.Popen(
        [binary, "-p", prompt],
        cwd=str(workspace),
        stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def cmd_launch(args):
    workspace = Path(args.workspace).expanduser().resolve()
    if not (workspace / ".curator").is_dir():
        print(f"not a CE workspace: {workspace}", file=sys.stderr)
        sys.exit(1)

    host = args.host or _detect_host()
    if host not in ("claude",):
        # Phase 4 v1 supports Claude Code detach. Other hosts fall back
        # in-session — return that intent to the caller (slash command),
        # which prints a banner and runs curate inline.
        print(json.dumps({
            "status": "fallback-in-session",
            "host": host,
            "reason": (f"detached curate not yet implemented for host "
                       f"`{host}`; the agent should run curate inline in "
                       f"the current session and warn that context will fill"),
        }))
        sys.exit(0)

    prompt = args.prompt or DEFAULT_PROMPT
    session_id = f"curate-{_now_id()}"
    sessions_dir = workspace / ".curator" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    log_path = sessions_dir / f"{session_id}.log"
    status_path = sessions_dir / f"{session_id}.status.json"

    try:
        pid = _spawn_claude_code(workspace, log_path, prompt)
    except (FileNotFoundError, OSError) as e:
        print(json.dumps({
            "status": "launch-failed",
            "error": str(e),
        }), file=sys.stderr)
        sys.exit(1)

    status = {
        "id": session_id,
        "host": host,
        "pid": pid,
        "workspace": str(workspace),
        "log": str(log_path),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_preview": prompt[:200],
    }
    status_path.write_text(json.dumps(status, indent=2))

    print(json.dumps({
        "status": "launched",
        "id": session_id,
        "pid": pid,
        "log": str(log_path),
        "status_file": str(status_path),
        "workspace": str(workspace),
    }, indent=2))


def main():
    p = argparse.ArgumentParser(prog="curate_launch.py", description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--prompt", default=None,
                   help="override the default curate prompt")
    p.add_argument("--host", default=None,
                   choices=["claude", "codex", "gemini", "copilot"])
    args = p.parse_args()
    cmd_launch(args)


if __name__ == "__main__":
    main()
