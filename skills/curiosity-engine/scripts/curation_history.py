"""Curation-history timeline for the wiki-view replay UI (Phase 2b+++).

Produces a HistoryDoc compatible with knowledge-atlas ``playReplayTimeline``
and Switchbay's ``/api/curation/history`` shape.

CE spike source of truth: the viewer ``data.json`` graph (nodes + edges).
Git first-seen chronology (Switchbay ``curation_history.py``) remains a
parity gap — optional cache hit if ``.workbench/curation-history.json``
already exists from a shell pre-warm.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_DURATION_S = 12.0
MAX_EVENTS = 2500
CACHE_REL = Path(".workbench") / "curation-history.json"


def _edge_id(ref: Any) -> str:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        v = ref.get("id")
        return str(v) if isinstance(v, str) else ""
    return ""


def build_from_data(
    data: dict[str, Any] | None,
    *,
    duration_s: float = DEFAULT_DURATION_S,
    max_events: int = MAX_EVENTS,
    source: str = "ce-data-json",
) -> dict[str, Any]:
    """Synthesize HistoryDoc from CE data.json (deterministic)."""
    nodes_raw = (data or {}).get("nodes") or []
    edges_raw = (data or {}).get("edges") or []
    nodes: list[dict[str, Any]] = []
    for n in nodes_raw:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip()
        if not nid:
            continue
        nodes.append(
            {
                "id": nid,
                "title": str(n.get("title") or nid),
                "type": str(n.get("type") or "unclassified"),
                "degree": int(n.get("degree") or 0),
            }
        )
    if not nodes:
        return {
            "duration": duration_s,
            "events": [],
            "source": "empty",
            "node_count": 0,
            "degree": {},
            "version": SCHEMA_VERSION,
        }

    def rank(n: dict[str, Any]) -> tuple[int, int, str]:
        is_source = 0 if n["type"] in ("source", "sources") else 1
        return (is_source, -(n["degree"] or 0), n["id"])

    nodes.sort(key=rank)
    by_id = {n["id"]: n for n in nodes}
    index_of = {n["id"]: i for i, n in enumerate(nodes)}
    n_count = len(nodes)

    def t_of(i: int) -> float:
        if n_count <= 1:
            return 0.0
        return round((i / (n_count - 1)) * duration_s, 3)

    events: list[dict[str, Any]] = []
    for i, n in enumerate(nodes):
        events.append(
            {
                "t": t_of(i),
                "op": "node",
                "id": n["id"],
                "title": n["title"],
                "type": n["type"],
            }
        )

    seen_pairs: set[tuple[str, str]] = set()
    edge_events: list[dict[str, Any]] = []
    for e in edges_raw:
        if not isinstance(e, dict):
            continue
        s = _edge_id(e.get("source"))
        t = _edge_id(e.get("target"))
        if not s or not t or s == t or s not in by_id or t not in by_id:
            continue
        pair = (s, t) if s < t else (t, s)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        later = max(index_of[s], index_of[t])
        edge_events.append(
            {
                "t": t_of(later),
                "op": "edge",
                "source": s,
                "target": t,
            }
        )

    events.extend(edge_events)
    events.sort(key=lambda e: (e["t"], 0 if e["op"] == "node" else 1))

    if len(events) > max_events:
        node_events = [e for e in events if e["op"] == "node"]
        edge_ev = [e for e in events if e["op"] == "edge"]
        keep_n = max(0, max_events - len(node_events))
        if keep_n < len(edge_ev):
            step = len(edge_ev) / max(1, keep_n)
            edge_ev = [edge_ev[int(i * step)] for i in range(keep_n)]
        events = sorted(
            node_events + edge_ev,
            key=lambda e: (e["t"], 0 if e["op"] == "node" else 1),
        )

    degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    for e in events:
        if e["op"] != "edge":
            continue
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1

    return {
        "duration": duration_s,
        "events": events,
        "source": source,
        "generated_at": time.time(),
        "node_count": len(nodes),
        "degree": degree,
        "version": SCHEMA_VERSION,
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _cache_usable(doc: dict[str, Any]) -> bool:
    events = doc.get("events")
    return isinstance(events, list) and "duration" in doc


def read_or_build(
    workspace: Path,
    bundle_dir: Path | None = None,
    *,
    duration_s: float = DEFAULT_DURATION_S,
) -> dict[str, Any]:
    """Prefer shell cache; else build from bundle data.json."""
    ws = Path(workspace)
    cache = ws / CACHE_REL
    if cache.is_file():
        cached = _read_json(cache)
        if cached and _cache_usable(cached):
            out = dict(cached)
            out.setdefault("source", "workbench-cache")
            out["version"] = SCHEMA_VERSION
            return out

    data: dict[str, Any] | None = None
    if bundle_dir is not None:
        data = _read_json(Path(bundle_dir) / "data.json")
    if data is None:
        # Fallback: wiki_render cache under ~/.cache is caller-provided via bundle.
        data = {}
    return build_from_data(data, duration_s=duration_s)


def history_payload(
    workspace: Path,
    bundle_dir: Path | None = None,
) -> dict[str, Any]:
    """API envelope: HistoryDoc fields at top level (Switchbay-compatible)."""
    doc = read_or_build(workspace, bundle_dir)
    # Keep Switchbay clients happy: they expect the doc itself, not {ok, …}.
    return doc
