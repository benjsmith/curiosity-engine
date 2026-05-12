#!/usr/bin/env python3
"""curate_status.py — report on a detached curate session.

Reads `<workspace>/.curator/sessions/<id>.status.json` and the
companion log file to summarise:
  - alive vs. exited (via os.kill(pid, 0))
  - elapsed wallclock
  - last log lines
  - rough wave / accept-reject counters scraped from the log

Usage:
  curate_status.py --workspace <path> [--id <session-id>]
                          # default: most recent session
  curate_status.py --workspace <path> --list
                          # list all known sessions

Stdlib only. Hash-guarded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still "alive" for our purposes.
        return True
    except OSError:
        return False


def _read_status(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _scrape_log(log: Path, tail_lines: int = 12) -> dict:
    if not log.is_file():
        return {"present": False}
    text = log.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    waves = len(re.findall(r"^\s*###?\s*WAVE\s+\d+", text, flags=re.MULTILINE))
    accepts = len(re.findall(r"\baccept\b", text, flags=re.IGNORECASE))
    rejects = len(re.findall(r"\breject\b", text, flags=re.IGNORECASE))
    return {
        "present": True,
        "size_bytes": log.stat().st_size,
        "lines": len(lines),
        "tail": lines[-tail_lines:],
        "waves_seen": waves,
        "accepts": accepts,
        "rejects": rejects,
    }


def _list_sessions(workspace: Path) -> list[dict]:
    sd = workspace / ".curator" / "sessions"
    if not sd.is_dir():
        return []
    out = []
    for status in sorted(sd.glob("*.status.json")):
        d = _read_status(status)
        if d:
            d["status_file"] = str(status)
            out.append(d)
    return out


def _summary(status: dict) -> dict:
    pid = status.get("pid", 0)
    alive = _alive(pid)
    started = status.get("started_at", "")
    try:
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    except (ValueError, TypeError):
        elapsed = None
    log_info = _scrape_log(Path(status.get("log", "")))
    return {
        "id": status.get("id"),
        "alive": alive,
        "pid": pid,
        "elapsed_seconds": elapsed,
        "log": log_info,
        "workspace": status.get("workspace"),
    }


def cmd_status(args):
    workspace = Path(args.workspace).expanduser().resolve()
    if args.list:
        sessions = _list_sessions(workspace)
        print(json.dumps([_summary(s) for s in sessions], indent=2))
        return

    sd = workspace / ".curator" / "sessions"
    if not sd.is_dir():
        print(json.dumps({"status": "no-sessions"}, indent=2))
        return

    if args.id:
        status_path = sd / f"{args.id}.status.json"
        if not status_path.is_file():
            print(json.dumps({"status": "not-found", "id": args.id}, indent=2))
            sys.exit(1)
    else:
        # Most recent by mtime.
        candidates = sorted(sd.glob("*.status.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print(json.dumps({"status": "no-sessions"}, indent=2))
            return
        status_path = candidates[0]

    status = _read_status(status_path)
    if not status:
        print(json.dumps({"status": "unreadable",
                          "path": str(status_path)}, indent=2))
        sys.exit(1)
    print(json.dumps(_summary(status), indent=2))


def main():
    p = argparse.ArgumentParser(prog="curate_status.py", description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--id", default=None)
    p.add_argument("--list", action="store_true")
    args = p.parse_args()
    cmd_status(args)


if __name__ == "__main__":
    main()
