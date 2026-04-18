#!/usr/bin/env python3
"""claims.py - page-level coordination for parallel CURATE sessions.

Multiple CURATE sessions on one workspace were previously racing on the
same wiki pages (silent last-writer-wins) and graph/sweep resources.
`claims` tracks which pages each session is currently working on so
sibling sessions can plan around them. State lives at
`.curator/.claims` (pipe-delimited, human-inspectable, append-safe under
`fcntl.flock` held on a sidecar `.curator/.claims.lock`).

Format:
    <page_path>|<session_id>|<started_at_unix>|<operation>

Stale claims (age > MAX_CLAIM_AGE_SECONDS) are dropped on every read so a
crashed session's claims free up automatically. No PID liveness check —
PIDs get reused and cause false positives; age-based is enough.

Subcommands
-----------
    claims.py list [--wiki PATH]
        Print current (non-stale) claims as JSON.

    claims.py claim <session_id> <operation> <page> [<page>...] [--wiki PATH]
        Atomically add claims. Exit 0 if all pages successfully claimed,
        exit 1 if any of the requested pages is already claimed by
        another session (the conflicting pages are printed; none of the
        requested pages are claimed in that case — all-or-nothing).

    claims.py release <session_id> [<page>...] [--wiki PATH]
        Remove claims for `<session_id>`. With no pages, releases all
        pages owned by that session. With pages, releases only those.
"""
import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

MAX_CLAIM_AGE_SECONDS = 3600  # 1 hour — well over a 5-min epoch


def _curator_dir(wiki: Path) -> Path:
    return wiki.parent / ".curator"


def _lock_path(cur: Path) -> Path:
    return cur / ".claims.lock"


def _claims_path(cur: Path) -> Path:
    return cur / ".claims"


class _Lock:
    """Exclusive fcntl lock on a sidecar file. Create on demand."""

    def __init__(self, cur: Path):
        cur.mkdir(parents=True, exist_ok=True)
        self.path = _lock_path(cur)
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "a+")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _read_claims(cur: Path, drop_stale: bool = True) -> list:
    path = _claims_path(cur)
    if not path.exists():
        return []
    now = int(time.time())
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 4:
            continue
        try:
            started = int(parts[2])
        except ValueError:
            continue
        if drop_stale and now - started > MAX_CLAIM_AGE_SECONDS:
            continue
        entries.append({
            "page": parts[0],
            "session": parts[1],
            "started_at": started,
            "operation": parts[3],
        })
    return entries


def _write_claims(cur: Path, entries: list) -> None:
    path = _claims_path(cur)
    lines = [
        f"{e['page']}|{e['session']}|{e['started_at']}|{e['operation']}"
        for e in entries
    ]
    path.write_text(("\n".join(lines) + "\n") if lines else "")


def cmd_list(wiki: Path) -> None:
    cur = _curator_dir(wiki)
    with _Lock(cur):
        entries = _read_claims(cur)
    print(json.dumps({"claims": entries}, indent=2))


def cmd_claim(wiki: Path, session: str, operation: str, pages: list) -> None:
    cur = _curator_dir(wiki)
    with _Lock(cur):
        entries = _read_claims(cur)
        claimed_now = {e["page"] for e in entries}
        conflicts = [p for p in pages if p in claimed_now]
        if conflicts:
            print(json.dumps({"claimed": [], "conflicts": conflicts}))
            sys.exit(1)
        now = int(time.time())
        for p in pages:
            entries.append({
                "page": p,
                "session": session,
                "started_at": now,
                "operation": operation,
            })
        _write_claims(cur, entries)
    print(json.dumps({"claimed": pages, "conflicts": []}))


def cmd_release(wiki: Path, session: str, pages: list) -> None:
    cur = _curator_dir(wiki)
    with _Lock(cur):
        entries = _read_claims(cur, drop_stale=False)
        if pages:
            target = set(pages)
            remaining = [
                e for e in entries
                if not (e["session"] == session and e["page"] in target)
            ]
        else:
            remaining = [e for e in entries if e["session"] != session]
        _write_claims(cur, remaining)
    released = len(entries) - len(remaining)
    print(json.dumps({
        "released_session": session,
        "released": released,
        "remaining": len(remaining),
    }))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wiki", default="wiki", help="workspace wiki dir (default: wiki)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    pa = sub.add_parser("claim")
    pa.add_argument("session")
    pa.add_argument("operation",
                    help="editorial | exploration | connection | question | facts | evidence")
    pa.add_argument("pages", nargs="+")

    pr = sub.add_parser("release")
    pr.add_argument("session")
    pr.add_argument("pages", nargs="*")

    args = ap.parse_args()
    wiki = Path(args.wiki).resolve()

    if args.cmd == "list":
        cmd_list(wiki)
    elif args.cmd == "claim":
        cmd_claim(wiki, args.session, args.operation, args.pages)
    elif args.cmd == "release":
        cmd_release(wiki, args.session, args.pages)


if __name__ == "__main__":
    main()
