#!/usr/bin/env python3
"""spawn.py - launch N parallel CURATE sessions against one workspace.

Each session is an independent `claude` invocation running the /curate
skill in the workspace. They coordinate via `claims.py` so they pick
disjoint pages. Before spawning, `spawn.py` measures per-session memory
footprint and system capacity and warns if the requested N would
overcommit — with a concrete safe number to use instead.

Modes
-----
    spawn.py <N>                       measure, warn if unsafe, spawn N sessions
    spawn.py <N> --force               spawn without the safety gate
    spawn.py <N> --dry-run             measure and print the command; don't spawn
    spawn.py --measure-only            print resource numbers; exit

Safety
------
    - Memory estimate uses a short real `claude` launch to measure RSS
      (~5s). Safe N = floor(available_memory * 0.7 / per_session_MB).
      Override with --force.
    - Spawned sessions log to `.curator/sessions/sess-<id>.log`; PIDs
      and session IDs written to `.curator/.spawned` so the user can
      monitor or kill them later.
    - Claude Code's rate limits are the other hard ceiling. We can't
      measure those locally; see the note printed alongside the spawn.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _system_memory_mb():
    """Return (total_mb, available_mb). Covers Linux and macOS."""
    # Linux: /proc/meminfo
    if os.path.exists("/proc/meminfo"):
        fields = {}
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            try:
                fields[k.strip()] = int(v.strip().split()[0]) // 1024  # kB → MB
            except (ValueError, IndexError):
                pass
        return fields.get("MemTotal", 0), fields.get("MemAvailable",
                                                       fields.get("MemFree", 0))
    # macOS: sysctl + vm_stat
    try:
        total_bytes = int(subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"]).decode().strip())
        total_mb = total_bytes // (1024 * 1024)
        vs = subprocess.check_output(["vm_stat"]).decode()
        page_size = 4096
        free = inactive = 0
        for line in vs.splitlines():
            if "page size of" in line:
                try:
                    page_size = int(line.split()[-2])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("Pages free:"):
                free = int(line.split(":", 1)[1].strip().rstrip("."))
            elif line.startswith("Pages inactive:"):
                inactive = int(line.split(":", 1)[1].strip().rstrip("."))
        avail_mb = ((free + inactive) * page_size) // (1024 * 1024)
        return total_mb, avail_mb
    except Exception:
        return 0, 0


def _claude_available():
    return shutil.which("claude") is not None


def _measure_claude_rss(timeout_s: float = 8.0) -> int:
    """Start a trivial claude invocation, sample RSS, return peak MB.

    Uses a one-shot `claude -p <short prompt>` since that's the closest
    thing to what a spawned CURATE session looks like at startup. Returns
    0 if claude isn't on PATH or measurement times out.
    """
    if not _claude_available():
        return 0
    try:
        proc = subprocess.Popen(
            ["claude", "-p", "Reply with 'ok' only."],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError):
        return 0
    peak_mb = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if proc.poll() is not None:
            break
        try:
            rss_kb = int(subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(proc.pid)],
                stderr=subprocess.DEVNULL,
            ).decode().strip())
            peak_mb = max(peak_mb, rss_kb // 1024)
        except (subprocess.CalledProcessError, ValueError):
            break
        time.sleep(0.2)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return peak_mb


def _resource_report(verbose: bool):
    total_mb, avail_mb = _system_memory_mb()
    rss_mb = _measure_claude_rss()
    # Floor per-session at 200MB to avoid divide-by-zero or over-optimistic
    # estimates when the trivial one-shot claude returns in <1s.
    effective_rss = max(rss_mb, 200)
    safe_n = max(1, int((avail_mb * 0.7) // effective_rss)) if avail_mb else 0
    if verbose:
        print(f"  total memory:             {total_mb:>6} MB", file=sys.stderr)
        print(f"  available memory:         {avail_mb:>6} MB", file=sys.stderr)
        print(f"  measured claude RSS:      {rss_mb:>6} MB "
              f"(using {effective_rss} MB as safety floor)", file=sys.stderr)
        print(f"  safe concurrent sessions: {safe_n:>6}", file=sys.stderr)
    return {
        "total_mb": total_mb,
        "available_mb": avail_mb,
        "claude_rss_mb": rss_mb,
        "safe_n": safe_n,
    }


def _spawn_one(workspace: Path, session_id: str) -> int:
    """Launch one background `claude -p /curate` inside `workspace`.

    Returns the child PID. stdout + stderr land in
    `.curator/sessions/sess-<id>.log`.
    """
    log_dir = workspace / ".curator" / "sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{session_id}.log"
    # Session ID is passed as an env var the orchestrator can read when
    # composing `claims claim/release` calls. CURIOSITY_SESSION is
    # descriptive; the orchestrator learns about it from SKILL.md.
    env = os.environ.copy()
    env["CURIOSITY_SESSION"] = session_id
    proc = subprocess.Popen(
        ["claude", "-p", "/curate"],
        cwd=str(workspace),
        stdin=subprocess.DEVNULL,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,  # detach so parent shell can exit cleanly
    )
    return proc.pid


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel CURATE launcher")
    ap.add_argument("n", type=int, nargs="?", default=None,
                    help="number of parallel sessions to spawn")
    ap.add_argument("--workspace", default=".",
                    help="workspace path (default: cwd)")
    ap.add_argument("--force", action="store_true",
                    help="spawn even if the resource gate says unsafe")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure + report, print commands, do not spawn")
    ap.add_argument("--measure-only", action="store_true",
                    help="print resource numbers and exit")
    args = ap.parse_args()

    workspace = Path(args.workspace).resolve()
    if not (workspace / "wiki").exists():
        print(f"error: {workspace} is not a curiosity-engine workspace "
              "(no wiki/ dir)", file=sys.stderr)
        sys.exit(2)

    if args.measure_only:
        print("Measuring system resources (~8s) ...", file=sys.stderr)
        report = _resource_report(verbose=True)
        print(json.dumps(report, indent=2))
        return

    if args.n is None or args.n < 1:
        print("error: must provide N (number of sessions) as a positive int",
              file=sys.stderr)
        sys.exit(2)

    print("Measuring system resources (~8s) ...", file=sys.stderr)
    report = _resource_report(verbose=True)
    safe_n = report["safe_n"]

    if report["available_mb"] and args.n > safe_n and not args.force:
        print(f"\nerror: requested {args.n} sessions exceeds safe capacity "
              f"({safe_n}).", file=sys.stderr)
        print(f"       rerun with --force to override, or use {safe_n} "
              f"sessions.", file=sys.stderr)
        sys.exit(1)

    if not _claude_available():
        print("\nwarning: `claude` CLI not found on PATH. Printing commands "
              "for manual launch.", file=sys.stderr)
        for i in range(args.n):
            sid = f"sess-{int(time.time())}-{i:03d}"
            print(f"  (cd {workspace} && CURIOSITY_SESSION={sid} claude -p /curate) &")
        return

    if args.dry_run:
        print("\nDry run — would spawn:", file=sys.stderr)
        for i in range(args.n):
            sid = f"sess-{int(time.time())}-{i:03d}"
            print(f"  session {sid}")
        return

    spawned = []
    t0 = int(time.time())
    for i in range(args.n):
        sid = f"sess-{t0}-{i:03d}"
        pid = _spawn_one(workspace, sid)
        spawned.append({"pid": pid, "session_id": sid})

    pids_file = workspace / ".curator" / ".spawned"
    with pids_file.open("a") as f:
        for s in spawned:
            f.write(f"{s['pid']}|{s['session_id']}|{t0}\n")

    print(json.dumps({
        "spawned": len(spawned),
        "sessions": spawned,
        "logs": str(workspace / ".curator" / "sessions"),
        "note": (
            "Claude Code rate limits (tokens/min, requests/min) are the "
            "non-local ceiling we can't measure here — if spawned sessions "
            "stall, check your account tier. Kill all: "
            f"for p in $(cut -d'|' -f1 {pids_file}); do kill $p; done"
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
