#!/usr/bin/env python3
"""
Activity log writer + query helper for the multi-project recency planner.

Records two kinds of events as one-event-per-line JSON in
`.curator/activity.log`:

- ingest: a vault file was ingested into the wiki. Carries `ingest_kind`
  ("current" or "archival") so the default-mode planner can filter
  archival ingests out of the activity score (per docs/multi-project.md).
- user_signal: the orchestrator detected a user-driven action
  involving a page (manual edit, triggered analysis, conversational
  request that touched the page). Used to compute the user-signal
  component of the activity score.

Used as a CLI by other scripts and the orchestrator, and importable
as `from activity_log import log_event` for in-process callers.

Subcommands:
  log ingest --page <stem> --source <vault-rel> [--projects a,b] [--archival]
  log user-signal --page <stem> --action <name> [--projects a,b]
  query [--by-page | --by-project | --raw] [--since 7d] [--page X | --project Y]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_PATH = Path(".curator/activity.log")


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _parse_period(s: str) -> timedelta:
    """Parse a short period string. Supports `<n>d`, `<n>h`, `<n>w`."""
    if not s:
        raise ValueError("empty period")
    unit = s[-1]
    n = int(s[:-1])
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "w":
        return timedelta(weeks=n)
    raise ValueError(f"unknown period unit in {s!r} (expected d/h/w)")


def log_event(kind: str, **fields) -> None:
    """Append an event. `kind` is required; remaining fields are
    serialised as-is. `ts` is set from the current UTC time when
    not provided. Library entry point — local_ingest.py and other
    in-process callers use this directly."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": _now_iso(), "kind": kind}
    event.update({k: v for k, v in fields.items() if v is not None})
    with LOG_PATH.open("a") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def _read_events(since: datetime | None = None):
    if not LOG_PATH.exists():
        return
    with LOG_PATH.open() as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is not None:
                try:
                    ts = _parse_iso(ev.get("ts", ""))
                except ValueError:
                    continue
                if ts < since:
                    continue
            yield ev


def _split_projects(s: str | None) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def cmd_log(args: argparse.Namespace) -> int:
    if args.kind == "ingest":
        if not args.page or not args.source:
            print("ERROR: ingest requires --page and --source", file=sys.stderr)
            return 2
        log_event(
            "ingest",
            page=args.page,
            source=args.source,
            ingest_kind="archival" if args.archival else "current",
            projects=_split_projects(args.projects),
        )
        return 0
    if args.kind == "user-signal":
        if not args.page:
            print("ERROR: user-signal requires --page", file=sys.stderr)
            return 2
        log_event(
            "user_signal",
            page=args.page,
            action=args.action or "edited",
            projects=_split_projects(args.projects),
        )
        return 0
    print(f"ERROR: unknown event kind {args.kind!r}", file=sys.stderr)
    return 2


def _aggregate_by_page(events: list) -> dict:
    """Per-page summary. Tracks counts and the most recent timestamps.
    Used by the planner to compute the user_signal_at and
    last_ingest_at for each page."""
    out: dict = defaultdict(lambda: {
        "ingests_current": 0,
        "ingests_archival": 0,
        "user_signals": 0,
        "last_ingest_at": None,
        "last_user_signal_at": None,
        "projects": set(),
    })
    for ev in events:
        page = ev.get("page")
        if not page:
            continue
        d = out[page]
        for proj in ev.get("projects") or []:
            d["projects"].add(proj)
        ts = ev.get("ts")
        if ev.get("kind") == "ingest":
            if ev.get("ingest_kind") == "archival":
                d["ingests_archival"] += 1
            else:
                d["ingests_current"] += 1
            if d["last_ingest_at"] is None or ts > d["last_ingest_at"]:
                d["last_ingest_at"] = ts
        elif ev.get("kind") == "user_signal":
            d["user_signals"] += 1
            if d["last_user_signal_at"] is None or ts > d["last_user_signal_at"]:
                d["last_user_signal_at"] = ts
    # Convert sets to sorted lists for stable JSON output.
    return {k: {**v, "projects": sorted(v["projects"])} for k, v in out.items()}


def _aggregate_by_project(events: list) -> dict:
    """Per-project summary. Computes the inputs the recency planner
    needs: ingests_current_<period>, ingests_archival_<period>,
    user_signals_<period>, ingest_cadence_score (decayed weekly
    cadence over the last 4 weeks of the queried window).
    Normalisation across projects happens at the planner layer."""
    per_project: dict = defaultdict(lambda: {
        "ingests_current": 0,
        "ingests_archival": 0,
        "user_signals": 0,
        "ingest_cadence_score": 0.0,
    })
    weekly_ingests: dict = defaultdict(lambda: defaultdict(int))
    now = datetime.now(timezone.utc)
    for ev in events:
        projects = ev.get("projects") or []
        kind = ev.get("kind")
        if not projects:
            continue
        try:
            ts = _parse_iso(ev.get("ts", ""))
        except (ValueError, TypeError):
            continue
        week_idx = max(0, (now - ts).days // 7)
        for proj in projects:
            d = per_project[proj]
            if kind == "ingest":
                if ev.get("ingest_kind") == "archival":
                    d["ingests_archival"] += 1
                else:
                    d["ingests_current"] += 1
                    if week_idx < 4:
                        weekly_ingests[proj][week_idx] += 1
            elif kind == "user_signal":
                d["user_signals"] += 1

    # Decayed cadence: weight = exp(-w/2) for weeks 0..3.
    weights = [math.exp(-w / 2.0) for w in range(4)]
    total_w = sum(weights)
    for proj, weeks in weekly_ingests.items():
        weighted = sum(weeks.get(w, 0) * weights[w] for w in range(4))
        per_project[proj]["ingest_cadence_score"] = round(weighted / total_w, 3)

    return dict(per_project)


def cmd_query(args: argparse.Namespace) -> int:
    since = None
    if args.since:
        since = datetime.now(timezone.utc) - _parse_period(args.since)
    events = list(_read_events(since=since))

    if args.raw:
        print(json.dumps(events, indent=2))
        return 0

    if args.by_page:
        out = _aggregate_by_page(events)
        if args.page:
            out = {args.page: out.get(args.page, {})}
        print(json.dumps(out, indent=2))
        return 0

    if args.by_project:
        out = _aggregate_by_project(events)
        if args.project:
            out = {args.project: out.get(args.project, {})}
        print(json.dumps(out, indent=2))
        return 0

    by_kind: dict = defaultdict(int)
    for ev in events:
        by_kind[ev.get("kind", "?")] += 1
    print(json.dumps({"events": len(events), "by_kind": dict(by_kind)}, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Activity log writer + query helper for the recency planner."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_log = sub.add_parser("log", help="Append an event")
    log_sub = p_log.add_subparsers(dest="kind", required=True)

    p_ing = log_sub.add_parser("ingest", help="Record an ingest event")
    p_ing.add_argument("--page", required=True, help="Wiki-relative page stem (e.g. sources/foo-2023)")
    p_ing.add_argument("--source", required=True, help="Vault-relative source path")
    p_ing.add_argument("--projects", help="Comma-separated project names this ingest belongs to")
    p_ing.add_argument("--archival", action="store_true", help="Tag as archival (excluded from default-mode activity)")
    p_ing.set_defaults(func=cmd_log)

    p_us = log_sub.add_parser("user-signal", help="Record a user-driven action on a page")
    p_us.add_argument("--page", required=True, help="Wiki-relative page stem or path")
    p_us.add_argument("--action", help="Action label (e.g. edited, created, triggered-analysis)")
    p_us.add_argument("--projects", help="Comma-separated project names if known")
    p_us.set_defaults(func=cmd_log)

    p_q = sub.add_parser("query", help="Aggregate or list events")
    p_q.add_argument("--since", help="Limit to events newer than period (e.g. 7d, 24h, 2w)")
    g = p_q.add_mutually_exclusive_group()
    g.add_argument("--by-page", action="store_true", help="Per-page summary")
    g.add_argument("--by-project", action="store_true", help="Per-project summary with cadence score")
    g.add_argument("--raw", action="store_true", help="Emit raw JSON event list")
    p_q.add_argument("--page", help="Filter --by-page output to a single page")
    p_q.add_argument("--project", help="Filter --by-project output to a single project")
    p_q.set_defaults(func=cmd_query)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
