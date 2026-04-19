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


def _alive_pids(spawned_path: Path) -> list:
    """Return [(pid, session_id), ...] for processes in .spawned still alive."""
    if not spawned_path.exists():
        return []
    out = []
    for line in spawned_path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        pid = parts[0]
        sid = parts[1]
        try:
            os.kill(int(pid), 0)
            out.append((pid, sid))
        except (ProcessLookupError, PermissionError, ValueError):
            continue
    return out


def _exit_reason(log_path: Path) -> str:
    """Categorize an exited session's log tail into one signal.

    Mirrors the few patterns actually seen in practice: rate-limit
    messages from claude -p, clean `Ran N CURATE epochs` completion
    summaries from the orchestrator, permission denials, and empty
    logs (silent early-exit). Keeps signatures wide so we don't miss
    a crash.
    """
    try:
        text = log_path.read_text()[-4000:]
    except OSError:
        return "unreadable"
    if not text.strip():
        return "silent"
    lower = text.lower()
    if "hit your limit" in lower or "rate limit" in lower or "429" in lower:
        return "rate-limited"
    if "approval" in lower and ("denied" in lower or "not in allow" in lower
                                 or "permission" in lower):
        return "permission-denied"
    if "unknown command" in lower:
        return "bad-command"
    if "ran " in lower and "curate epoch" in lower:
        return "clean-exit"
    if "stopped" in lower or "stop-check" in lower or "saturation" in lower:
        return "clean-exit"
    return "other"


def _format_status(workspace: Path, watch_mode: bool = False) -> str:
    """Compose a compact status block from .spawned, .claims, and git log."""
    import datetime as _dt
    spawned = workspace / ".curator" / ".spawned"
    claims_path = workspace / ".curator" / ".claims"
    sessions_dir = workspace / ".curator" / "sessions"
    alive = _alive_pids(spawned)
    alive_pids = {p for p, _ in alive}

    all_entries = []
    if spawned.exists():
        for line in spawned.read_text().splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 2:
                all_entries.append((parts[0], parts[1]))
    total_spawned = len(all_entries)

    # Categorize exited sessions by log signal.
    exit_counts = {}
    for pid, sid in all_entries:
        if pid in alive_pids:
            continue
        log = sessions_dir / f"{sid}.log"
        reason = _exit_reason(log) if log.exists() else "no-log"
        exit_counts[reason] = exit_counts.get(reason, 0) + 1
    exit_str = ", ".join(f"{k}={v}" for k, v in sorted(exit_counts.items())) or "—"

    # Claims — group by operation
    claims = []
    if claims_path.exists():
        now = int(time.time())
        for line in claims_path.read_text().splitlines():
            p = line.strip().split("|")
            if len(p) != 4:
                continue
            try:
                started = int(p[2])
            except ValueError:
                continue
            if now - started > 3600:
                continue  # stale — claims.py drops these on next write
            claims.append({"page": p[0], "session": p[1], "op": p[3]})
    by_op = {}
    for c in claims:
        by_op[c["op"]] = by_op.get(c["op"], 0) + 1
    ops_str = ", ".join(f"{k}={v}" for k, v in sorted(by_op.items())) or "—"

    # Recent commits (last 5 min)
    try:
        log_out = subprocess.check_output(
            ["git", "-C", str(workspace / "wiki"), "log",
             "--since=5 minutes ago", "--format=%cr|%s"],
            stderr=subprocess.DEVNULL,
        ).decode().splitlines()
    except subprocess.CalledProcessError:
        log_out = []

    now_str = _dt.datetime.now().strftime("%H:%M:%S")
    lines = [
        f"CURATE status — {now_str}",
        f"  sessions: {len(alive)} alive / {total_spawned} spawned",
        f"  exited:   {total_spawned - len(alive)}  [{exit_str}]",
        f"  claims:   {len(claims)} pages in flight  [{ops_str}]",
        f"  recent:   {len(log_out)} commits in last 5 min",
    ]
    for entry in log_out[:3]:
        if "|" in entry:
            when, subj = entry.split("|", 1)
            lines.append(f"            {when}  {subj[:90]}")
    if not log_out:
        lines.append("            (no commits yet)")
    if watch_mode:
        lines.append("  [Ctrl-C to stop watching; sessions continue in background]")
    return "\n".join(lines)


def _watch(workspace: Path, interval: float = 2.0) -> None:
    """Live status. ANSI cursor control if stdout is a TTY.

    Non-TTY stdout (pipes, Claude Code Bash tool, log redirection) doesn't
    render the cursor-control escape codes — looping just scrolls the
    same block over and over. In that case we print one snapshot and
    point the user at the interactive invocation instead.
    """
    if not sys.stdout.isatty():
        print(_format_status(workspace, watch_mode=False))
        print()
        print("[non-TTY: printed one snapshot instead of a live loop. "
              "For in-place updates run `spawn.py --watch` from a terminal.]",
              file=sys.stderr)
        return
    try:
        first = True
        while True:
            block = _format_status(workspace, watch_mode=True)
            if not first:
                sys.stdout.write("\033[2J\033[H")
            else:
                sys.stdout.write("\033[H\033[J")
                first = False
            sys.stdout.write(block + "\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nWatch stopped; sessions continue in background.",
              file=sys.stderr)


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
    # Use a natural-language prompt rather than "/curate". The skill
    # activates via description matchers on words like "curate",
    # "iterate", "improve" — it is NOT a registered slash command, so
    # `claude -p /curate` would exit with "Unknown command: /curate" and
    # run zero work. Setup also writes `.claude/commands/curate.md` for
    # interactive /curate convenience, but spawn.py stays on natural
    # language so it works even in workspaces that haven't been
    # re-setup to install the slash command.
    prompt = (
        "Run the curiosity-engine CURATE loop in this workspace until "
        f"interrupted. Use session ID {session_id} for the claims "
        "coordination in `.curator/.claims` (see the Parallel sessions "
        "section of the skill's SKILL.md). Do not stop after one epoch."
    )
    proc = subprocess.Popen(
        ["claude", "-p", prompt],
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
                    help="number of parallel sessions to spawn. Default: "
                         "enters the live dashboard after spawning. Pass "
                         "--no-watch to background-detach instead.")
    ap.add_argument("--workspace", default=".",
                    help="workspace path (default: cwd)")
    ap.add_argument("--force", action="store_true",
                    help="spawn even if the resource gate says unsafe")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure + report, print commands, do not spawn")
    ap.add_argument("--measure-only", action="store_true",
                    help="print resource numbers and exit")
    ap.add_argument("--no-watch", action="store_true",
                    help="after spawning, detach silently instead of "
                         "entering the dashboard. Sessions run in the "
                         "background; monitor later via `spawn.py --watch`.")
    ap.add_argument("--watch", action="store_true",
                    help="monitor existing sessions (no spawning). With "
                         "spawn, watch is already the default; this flag "
                         "is redundant but accepted.")
    ap.add_argument("--status", action="store_true",
                    help="print status block once and exit")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="dashboard refresh interval in seconds (default 2.0)")
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

    if args.status:
        print(_format_status(workspace))
        return

    # --watch alone (no n): enter watch mode. spawn.py N --watch spawns
    # first then watches. No n and no watch: error.
    if args.n is None:
        if args.watch:
            _watch(workspace, interval=args.interval)
            return
        print("error: must provide N (number of sessions), or use --watch / "
              "--status / --measure-only", file=sys.stderr)
        sys.exit(2)
    if args.n < 1:
        print("error: N must be a positive int", file=sys.stderr)
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
            prompt = (f"Run the curiosity-engine CURATE loop in this "
                      f"workspace until interrupted. Session ID {sid}.")
            print(f"  (cd {workspace} && CURIOSITY_SESSION={sid} "
                  f"claude -p {prompt!r}) &")
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

    if args.no_watch:
        # Background-detach: print the full JSON summary so the user has
        # the PIDs and kill-command handy, then exit.
        print(json.dumps({
            "spawned": len(spawned),
            "sessions": spawned,
            "logs": str(workspace / ".curator" / "sessions"),
            "note": (
                "Claude Code rate limits (tokens/min, requests/min) are the "
                "non-local ceiling we can't measure here — if spawned sessions "
                "stall, check your account tier. Kill all: "
                f"for p in $(cut -d'|' -f1 {pids_file}); do kill $p; done. "
                "Monitor later: `uv run python3 spawn.py --watch`."
            ),
        }, indent=2))
        return

    # Default: enter the dashboard. Brief confirmation line so the user
    # sees what was spawned before the dashboard paints.
    print(f"\nSpawned {len(spawned)} session(s); entering dashboard "
          f"(Ctrl-C to detach; sessions keep running)...\n",
          file=sys.stderr)
    time.sleep(0.8)
    _watch(workspace, interval=args.interval)


if __name__ == "__main__":
    main()
