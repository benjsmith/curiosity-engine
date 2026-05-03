#!/usr/bin/env python3
"""
Recency-weighted slot allocator for project-aware CURATE waves.

Reads `epoch_summary.py` JSON (file path or stdin) plus a chosen
wave-mode and emits a slot allocation. Sits between mode selection
(unchanged, in epoch_summary.py + SKILL.md prose) and target picking
(unchanged inside each wave-mode's queue logic).

Single-project / no-project wikis get equivalent-to-global allocation
out of this script — no behaviour change vs. the pre-project flow.

Usage:
    planner.py allocate <epoch_summary.json> --wave-mode {repair,create,wire,...} \
                                              --mode {default,archival} \
                                              --slots N

Allocation rules per wave-mode (see docs/multi-project.md):

  repair:   70% project-by-activity (min 1 / max 4 per project),
            ~15% bridges (placeholder until wave 5 fills),
            ~10% unclassified bucket,
             ~5% ambient global worst-page.
  wire:     passthrough — wire stays global; orphans cross clusters
            by definition.
  create:   passthrough with ordering hint — bucket quotas (evidence,
            facts, demand promotions, summary tables, analyses) stay as
            today; candidates within each bucket get re-ordered by
            project activity so active-project items surface first.
  figure-extract / multimodal-table-extract / numeric-review / table-audit:
            passthrough with ordering hint — the candidate queue order
            is biased toward active projects (default mode) or dormant
            projects (archival mode).

`mode = default` weights active projects more heavily; `mode = archival`
inverts so dormant projects get the bigger slots (and the ordering hint
flips to ascending). Archival ingests flagged via `ingest_kind: archival`
are excluded from the default-mode activity score and counted at full
weight under archival mode.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Default mode allocation percentages for repair waves. Values are slot
# fractions that the allocator distributes after applying min_per_project /
# max_per_project caps. Bridges allocation is reserved but not yet filled
# (wave 5 will populate the bridge candidate list); when bridges has 0
# candidates the slot rolls to ambient so it isn't wasted.
PROJECT_FRACTION = 0.70
BRIDGE_FRACTION = 0.15
UNCLASSIFIED_FRACTION = 0.10
AMBIENT_FRACTION = 0.05

MIN_SLOTS_PER_PROJECT = 1
MAX_SLOTS_PER_PROJECT = 4

# Wave modes that get full per-project allocation.
ALLOCATING_MODES = {"repair"}

# Wave modes that pass through with an ordering hint only.
PASSTHROUGH_MODES = {
    "create",
    "figure-extract",
    "multimodal-table-extract",
    "numeric-review",
    "table-audit",
}

# Wave mode that stays global (no project-level reshaping).
GLOBAL_MODES = {"wire"}


def _activity_score(p: dict, max_ingests: int, max_signals: int,
                     max_cadence: float) -> float:
    """Activity score formula from docs/multi-project.md.

    activity = 0.55 × normalised_ingests_current_7d
             + 0.30 × normalised_user_signals_7d
             + 0.15 × normalised_ingest_cadence_score

    Cross-project max-normalisation on all three terms. Returns 0..1
    so the archival inversion (1 - score) also lives in 0..1.
    """
    ingest_norm = (p.get("ingests_current", 0) / max_ingests) if max_ingests else 0.0
    signal_norm = (p.get("user_signals", 0) / max_signals) if max_signals else 0.0
    cadence_raw = float(p.get("ingest_cadence_score", 0.0))
    cadence_norm = (cadence_raw / max_cadence) if max_cadence else 0.0
    return round(0.55 * ingest_norm + 0.30 * signal_norm + 0.15 * cadence_norm, 4)


def _compute_raw_activity_scores(project_activity: dict) -> dict:
    """Raw 0..1 activity score per project — unaffected by the archival
    flag. Used for bridge-pair classification (active / dormant labels
    must be stable regardless of mode) and as the input to the
    archival inversion."""
    projects = {k: v for k, v in project_activity.items() if not k.startswith("_")}
    if not projects:
        return {}
    max_ingests = max((p.get("ingests_current", 0) for p in projects.values()), default=0)
    max_signals = max((p.get("user_signals", 0) for p in projects.values()), default=0)
    max_cadence = max(
        (float(p.get("ingest_cadence_score", 0.0)) for p in projects.values()),
        default=0.0,
    )
    return {
        name: _activity_score(p, max_ingests, max_signals, max_cadence)
        for name, p in projects.items()
    }


def _compute_activity_scores(project_activity: dict, archival: bool) -> dict:
    """Returns {project_name: score in 0..1} for **slot allocation**.
    Default mode returns raw scores; archival inverts (1 - score) so
    dormant projects rank higher in the project-budget distribution.

    Pair classification (active vs dormant) should NOT use this function
    — it should use _compute_raw_activity_scores so the labels are
    stable across modes. The archival mode reshuffles WHICH pair types
    get more attention, but it doesn't redefine what "dormant" means."""
    raw = _compute_raw_activity_scores(project_activity)
    if archival:
        return {k: round(1.0 - v, 4) for k, v in raw.items()}
    return raw


def _distribute_slots(
    scores: dict,
    budget: int,
    min_per: int = MIN_SLOTS_PER_PROJECT,
    max_per: int = MAX_SLOTS_PER_PROJECT,
) -> dict:
    """Distribute `budget` slots across projects in proportion to scores,
    enforcing min_per (when score > 0) and max_per caps. Surplus or
    deficit after caps is corrected by adjusting the highest- or
    lowest-score project that still has room.

    With a single eligible project, the max_per cap is waived (the lone
    project absorbs the full budget). With zero eligible projects, the
    budget rolls to the caller (return empty dict)."""
    if budget <= 0 or not scores:
        return {}
    eligible = {k: v for k, v in scores.items() if v > 0}
    if not eligible:
        # All projects scored zero. Distribute uniformly so we still
        # exercise project-by-project allocation rather than dropping
        # everything to ambient. Skip caps when uniform.
        eligible = scores
        names = sorted(eligible)
        share = budget // len(names)
        rem = budget - share * len(names)
        out = {n: share for n in names}
        for n in names[:rem]:
            out[n] += 1
        return {k: v for k, v in out.items() if v > 0}

    if len(eligible) == 1:
        name = next(iter(eligible))
        return {name: budget}

    total = sum(eligible.values())
    raw = {k: budget * (v / total) for k, v in eligible.items()}
    # Initial round + clamp.
    out = {k: max(min_per, min(max_per, int(round(v)))) for k, v in raw.items()}
    diff = budget - sum(out.values())
    # Fix overruns / underruns by adjusting the most-eligible project
    # in the relevant direction. Bounded by len(eligible) iterations.
    iters = 0
    while diff != 0 and iters < 4 * len(eligible):
        if diff > 0:
            # Underrun: add to the highest-score project not at cap.
            cands = [k for k in eligible if out[k] < max_per]
            if not cands:
                break
            target = max(cands, key=lambda k: eligible[k])
            out[target] += 1
            diff -= 1
        else:
            # Overrun: subtract from the lowest-score project above min.
            cands = [k for k in eligible if out[k] > min_per]
            if not cands:
                break
            target = min(cands, key=lambda k: eligible[k])
            out[target] -= 1
            diff += 1
        iters += 1
    return {k: v for k, v in out.items() if v > 0}


def _slots_split(total: int) -> tuple[int, int, int, int]:
    """Carve `total` into (project, bridges, unclassified, ambient)
    using PROJECT/BRIDGE/UNCLASSIFIED/AMBIENT fractions, with floor() +
    a deterministic remainder rule that rolls leftover into the project
    bucket. Guarantees each non-project bucket gets at least 1 slot when
    `total >= 4`, else degrades gracefully."""
    if total <= 1:
        return total, 0, 0, 0
    if total <= 3:
        # Tiny waves: just project + ambient.
        return total - 1, 0, 0, 1
    project = int(total * PROJECT_FRACTION)
    bridges = max(1, int(total * BRIDGE_FRACTION))
    unclassified = max(1, int(total * UNCLASSIFIED_FRACTION))
    ambient = max(1, int(total * AMBIENT_FRACTION))
    project = total - bridges - unclassified - ambient
    return project, bridges, unclassified, ambient


def _pair_activity(projects: list, scores: dict) -> float:
    """Activity for a page given its project tags. A page belongs to
    multiple projects in general; treat it as 'as active as its most
    active project'. Returns 0.0 when the page has no tags or no scored
    projects."""
    return max((scores.get(p, 0.0) for p in projects), default=0.0)


def _classify_pair(act_a: float, act_b: float, active_threshold: float = 0.0) -> str:
    """Bridge-pair classification used by archival mode's stratified
    bridge budget (40% dormant-dormant, 40% dormant-active, 20%
    active-active per the design doc)."""
    a_active = act_a > active_threshold
    b_active = act_b > active_threshold
    if a_active and b_active:
        return "active-active"
    if a_active or b_active:
        return "dormant-active"
    return "dormant-dormant"


def _select_bridges(
    candidates: list,
    raw_scores: dict,
    budget: int,
    archival: bool,
) -> list:
    """Pick `budget` bridge candidates from a list of cross_project=True
    pairs, with mode-specific selection rules.

    Pair classification (active vs dormant) always uses raw activity
    scores — the labels are properties of the projects' state, not of
    the wave mode. The archival flag controls which strata get larger
    quotas, not what "dormant" means.

    Default mode: rank by `min(activity_a, activity_b)` descending — two
    active projects bridging beats one active + one dormant, which beats
    two dormants. Matches the design doc's "two-active-projects bridges
    beat one-active-one-dormant" rule.

    Archival mode: stratified 40/40/20 across pair types so active↔active
    work isn't lost while dormant material gets attention. Within each
    stratum, sort by `min(activity)` ascending so the most-dormant
    pair surfaces first."""
    cross = [c for c in candidates if c.get("cross_project")]
    if budget <= 0 or not cross:
        return []

    annotated = []
    for c in cross:
        act_a = _pair_activity(c.get("projects_a", []), raw_scores)
        act_b = _pair_activity(c.get("projects_b", []), raw_scores)
        annotated.append({
            **c,
            "activity_a": round(act_a, 4),
            "activity_b": round(act_b, 4),
            "min_activity": round(min(act_a, act_b), 4),
            "max_activity": round(max(act_a, act_b), 4),
            "pair_type": _classify_pair(act_a, act_b),
        })

    if not archival:
        annotated.sort(key=lambda c: c["min_activity"], reverse=True)
        return annotated[:budget]

    # Archival mode: stratified 40/40/20 across pair types. Within each
    # stratum, sort by min_activity ascending (most dormant first).
    strata = {
        "dormant-dormant": [],
        "dormant-active": [],
        "active-active": [],
    }
    for c in annotated:
        strata[c["pair_type"]].append(c)
    for k in strata:
        strata[k].sort(key=lambda c: c["min_activity"])

    quotas = {
        "dormant-dormant": int(round(0.40 * budget)),
        "dormant-active": int(round(0.40 * budget)),
        "active-active": int(round(0.20 * budget)),
    }
    # Quota rounding may over/undershoot; reconcile.
    total = sum(quotas.values())
    diff = budget - total
    # Spread diff onto the largest non-empty strata first (deterministic
    # by sort_key tie-break: dormant-dormant > dormant-active > active-active).
    order = ["dormant-dormant", "dormant-active", "active-active"]
    i = 0
    while diff != 0 and i < 4 * len(order):
        target = order[i % len(order)]
        if diff > 0 and strata[target] and quotas[target] < len(strata[target]):
            quotas[target] += 1
            diff -= 1
        elif diff < 0 and quotas[target] > 0:
            quotas[target] -= 1
            diff += 1
        i += 1

    out: list = []
    for stratum in order:
        out.extend(strata[stratum][:quotas[stratum]])
    # If a stratum was empty and its quota went unused, backfill from
    # whichever stratum still has unselected items (preserve stratum
    # priority: dormant-dormant > dormant-active > active-active).
    if len(out) < budget:
        seen = {(c["page_a"], c["page_b"]) for c in out}
        for stratum in order:
            for c in strata[stratum]:
                if len(out) >= budget:
                    break
                key = (c["page_a"], c["page_b"])
                if key not in seen:
                    out.append(c)
                    seen.add(key)
            if len(out) >= budget:
                break
    return out[:budget]


def _allocate_repair(
    project_activity: dict,
    summary: dict,
    total_slots: int,
    archival: bool,
) -> dict:
    project_budget, bridge_budget, unclassified_budget, ambient_budget = _slots_split(total_slots)

    scores = _compute_activity_scores(project_activity, archival)
    project_alloc = _distribute_slots(scores, project_budget)

    # Surplus when no projects: project_budget rolls to ambient (today's
    # global behaviour). Surplus when only some projects took caps:
    # remainder rolls to ambient too — keeps wide-attention behaviour
    # while signalling project work.
    used = sum(project_alloc.values())
    surplus = project_budget - used
    if surplus > 0:
        ambient_budget += surplus

    # Build by_project candidate lists from project_activity.worst_within.
    by_project = []
    for proj, slots in sorted(project_alloc.items(), key=lambda kv: -kv[1]):
        meta = project_activity.get(proj, {})
        by_project.append({
            "project": proj,
            "slots": slots,
            "activity_score": scores.get(proj, 0.0),
            "candidates": meta.get("worst_within", [])[:slots],
        })

    unclassified_meta = project_activity.get("_unclassified", {})
    unclassified_block = {
        "slots": unclassified_budget,
        "candidates": unclassified_meta.get("worst_within", [])[:unclassified_budget],
    }

    # Bridge candidates: cross-project page pairs sharing vault sources
    # but not yet wikilinked. Selection rules differ per mode (default
    # ranks by min(activity), archival stratifies 40/40/20 across pair
    # types). Pair classification uses RAW activity scores so the
    # active/dormant labels are mode-stable.
    raw_scores = _compute_raw_activity_scores(project_activity)
    bridge_candidates = _select_bridges(
        summary.get("connection_candidates", []),
        raw_scores,
        bridge_budget,
        archival,
    )
    bridges_block: dict = {
        "slots": bridge_budget,
        "candidates": bridge_candidates,
    }
    if not bridge_candidates:
        bridges_block["note"] = (
            "no cross-project bridge candidates available — "
            "orchestrator may roll these slots into ambient"
        )

    # Ambient: pull from summary.worst_5 (current global worst). If
    # _unclassified.worst_within already covers it, dedupe.
    seen_pages = {c["page"] for c in unclassified_block["candidates"]}
    for p in by_project:
        seen_pages.update(c["page"] for c in p["candidates"])
    ambient_candidates = []
    for entry in summary.get("worst_5", []):
        if entry["page"] in seen_pages:
            continue
        ambient_candidates.append(entry)
        if len(ambient_candidates) >= ambient_budget:
            break
    ambient_block = {
        "slots": ambient_budget,
        "candidates": ambient_candidates,
    }

    note = None
    n_active = sum(1 for v in scores.values() if v > 0)
    if n_active == 0:
        note = ("no project activity in window — allocation collapses to "
                "ambient global worst-pages (equivalent to pre-project flow)")
    elif n_active == 1 and len(scores) <= 1:
        note = "single active project — equivalent to global allocation"

    return {
        "wave_mode": "repair",
        "project_mode": "archival" if archival else "default",
        "total_slots": total_slots,
        "allocations": {
            "by_project": by_project,
            "_unclassified": unclassified_block,
            "_bridges": bridges_block,
            "_ambient": ambient_block,
        },
        "activity_scores": scores,
        "notes": note,
    }


def _allocate_passthrough(
    wave_mode: str,
    project_activity: dict,
    total_slots: int,
    archival: bool,
) -> dict:
    """Specialty + create modes: project layer only re-orders the
    existing candidate queue. Emits an ordering hint the orchestrator
    applies when it picks from its own (unchanged) queues."""
    scores = _compute_activity_scores(project_activity, archival)
    direction = "ascending" if archival else "descending"
    return {
        "wave_mode": wave_mode,
        "project_mode": "archival" if archival else "default",
        "total_slots": total_slots,
        "passthrough": True,
        "ordering_hint": {
            "by": "candidate_project_activity",
            "direction": direction,
            "scores": scores,
        },
        "notes": (
            f"{wave_mode} wave — orchestrator queue order should be "
            f"{direction} by source-page project activity score"
        ),
    }


def _allocate_global(wave_mode: str, total_slots: int, archival: bool) -> dict:
    """Wire mode and other inherently-global modes: project layer is
    inert. Returned for symmetry so the orchestrator's call site is
    uniform."""
    return {
        "wave_mode": wave_mode,
        "project_mode": "archival" if archival else "default",
        "total_slots": total_slots,
        "passthrough": True,
        "global": True,
        "notes": (f"{wave_mode} stays global — orphans cross clusters "
                  "by definition; project layer does not reshape this wave"),
    }


def cmd_allocate(args: argparse.Namespace) -> int:
    if args.epoch_summary == "-":
        summary = json.load(sys.stdin)
    else:
        summary = json.loads(Path(args.epoch_summary).read_text())

    project_activity = summary.get("project_activity", {})
    archival = (args.mode == "archival")

    if args.wave_mode in GLOBAL_MODES:
        out = _allocate_global(args.wave_mode, args.slots, archival)
    elif args.wave_mode in PASSTHROUGH_MODES:
        out = _allocate_passthrough(args.wave_mode, project_activity, args.slots, archival)
    elif args.wave_mode in ALLOCATING_MODES:
        out = _allocate_repair(project_activity, summary, args.slots, archival)
    else:
        print(f"ERROR: unknown wave-mode {args.wave_mode!r}", file=sys.stderr)
        return 2

    print(json.dumps(out, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Recency-weighted slot allocator for project-aware CURATE waves."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_alloc = sub.add_parser("allocate", help="Compute slot allocation for a wave")
    p_alloc.add_argument(
        "epoch_summary",
        help="Path to epoch_summary.py JSON output, or '-' to read from stdin",
    )
    p_alloc.add_argument(
        "--wave-mode",
        required=True,
        choices=sorted(ALLOCATING_MODES | PASSTHROUGH_MODES | GLOBAL_MODES),
        help="The wave mode chosen by epoch_summary's mode-selection cascade",
    )
    p_alloc.add_argument(
        "--mode",
        choices=["default", "archival"],
        default="default",
        help="Project-layer mode: default (active-biased) or archival (dormant-biased)",
    )
    p_alloc.add_argument(
        "--slots",
        type=int,
        default=10,
        help="Total worker slots for the wave (default 10, matches parallel_workers)",
    )
    p_alloc.set_defaults(func=cmd_allocate)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
