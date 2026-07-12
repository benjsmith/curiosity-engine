#!/usr/bin/env python3
"""identifier_cache.py — chemical/gene identifier cache + request queue.

Cache-only layer for the identifier-resolution flow. NO NETWORK
calls happen in this script — `urllib` is not imported. The network
layer lives in `identifier_resolve.py` and runs only when the user
explicitly approves a batch via `identifier_resolve.py run --yes`.

The split exists for security:

- Workers (synthesis Agents) call this script's `queue` subcommand to
  record what they'd like resolved. The request lands in
  `.curator/identifier-requests.jsonl` — a local JSONL file the user
  can inspect before any network call happens.
- Workers also call `lookup-chemical` / `lookup-gene` / `bulk-lookup`
  to read CACHED resolutions from `.curator/identifiers.db`. Cache
  hits return immediately; cache misses return `status: pending`
  (the request is queued for the next manual resolve pass).
- The user periodically inspects the queue (`identifier_resolve.py
  review`) and explicitly drains it (`identifier_resolve.py run --yes`).
  That's the only path that hits the network, gated by
  `identifier_resolution.enabled = true` in `.curator/config.json`.

Why this matters: chemical and gene identifiers are sometimes
sensitive (proprietary structures, novel target lists). Sending them
to a public database without explicit user gesture is a
data-exfiltration risk for some users. The split makes the network
hop visible, opt-in, and configurable (point at an internal API
mirror in enterprise settings).

Subcommands
-----------
    identifier_cache.py lookup-chemical <name>
        Read-only cache hit by chemical name. Returns the cached row
        or `{status: pending}` for misses (and queues the lookup).

    identifier_cache.py lookup-gene <symbol>
        Read-only cache hit by gene symbol. Same semantics.

    identifier_cache.py bulk-lookup --type chemicals|genes --names-json '[...]'
        Cache-aware batch read. Hits go straight back; misses are
        appended to the request queue and surfaced as
        `{status: pending}` in the result list.

    identifier_cache.py queue --type chemicals|genes --names-json '[...]'
                              [--source-page <wiki/path.md>]
        Append a resolution request to the queue without checking
        cache. Used by orchestrators that want to record
        future-needed resolutions explicitly.

    identifier_cache.py pending
        List queued requests waiting for the next resolve pass.

    identifier_cache.py cache-stats
        Row counts per table + status breakdown.

Hash-guarded by evolve_guard.sh.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional


DB_PATH = Path(".curator/identifiers.db")
QUEUE_PATH = Path(".curator/identifier-requests.jsonl")


# ---- DB lifecycle ----

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chemicals (
            name_norm TEXT PRIMARY KEY,
            smiles TEXT,
            inchi TEXT,
            inchikey TEXT,
            cid INTEGER,
            source TEXT,
            status TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS genes (
            symbol_norm TEXT PRIMARY KEY,
            ensembl_id TEXT,
            uniprot_id TEXT,
            entrez_id INTEGER,
            taxid INTEGER,
            source TEXT,
            status TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    # U1 — domain-agnostic entity identity. `chemicals`/`genes` above are
    # per-authority resolution caches; `entities` is the generalised IRI
    # registry that any entity page can mint into, regardless of whether
    # an external authority exists. The IRI is workspace-stable and minted
    # deterministically (see mint_iri); external canonical ids live in
    # same_as_json as an owl:sameAs-style map and never gate identity.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            iri TEXT PRIMARY KEY,
            entity_class TEXT NOT NULL,
            page_path TEXT,
            same_as_json TEXT,
            status TEXT NOT NULL,
            resolved_at TEXT NOT NULL
        )
    """)
    return conn


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _normalise_name(name: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation noise."""
    return re.sub(r"\s+", " ", name.strip().lower())


# ---- Cache reads ----

def read_chemical(conn, name_norm: str) -> Optional[dict]:
    """Public reader — used by identifier_resolve.py to check cache
    before hitting the network."""
    cur = conn.execute(
        "SELECT name_norm, smiles, inchi, inchikey, cid, source, status, "
        "fetched_at FROM chemicals WHERE name_norm = ?",
        (name_norm,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "name_norm": row[0], "smiles": row[1], "inchi": row[2],
        "inchikey": row[3], "cid": row[4], "source": row[5],
        "status": row[6], "fetched_at": row[7],
    }


def read_gene(conn, symbol_norm: str) -> Optional[dict]:
    cur = conn.execute(
        "SELECT symbol_norm, ensembl_id, uniprot_id, entrez_id, taxid, "
        "source, status, fetched_at FROM genes WHERE symbol_norm = ?",
        (symbol_norm,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "symbol_norm": row[0], "ensembl_id": row[1], "uniprot_id": row[2],
        "entrez_id": row[3], "taxid": row[4], "source": row[5],
        "status": row[6], "fetched_at": row[7],
    }


# ---- Cache writes ----

def write_chemical(conn, name_norm: str, *,
                    smiles: Optional[str] = None,
                    inchi: Optional[str] = None,
                    inchikey: Optional[str] = None,
                    cid: Optional[int] = None,
                    source: str, status: str) -> None:
    """Public writer — used by identifier_resolve.py to record results."""
    conn.execute(
        "INSERT OR REPLACE INTO chemicals(name_norm, smiles, inchi, inchikey, "
        "cid, source, status, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name_norm, smiles, inchi, inchikey, cid, source, status, _now_iso()),
    )
    conn.commit()


def write_gene(conn, symbol_norm: str, *,
                ensembl_id: Optional[str] = None,
                uniprot_id: Optional[str] = None,
                entrez_id: Optional[int] = None,
                taxid: Optional[int] = None,
                source: str, status: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO genes(symbol_norm, ensembl_id, uniprot_id, "
        "entrez_id, taxid, source, status, fetched_at) VALUES (?, ?, ?, ?, "
        "?, ?, ?, ?)",
        (symbol_norm, ensembl_id, uniprot_id, entrez_id, taxid, source,
         status, _now_iso()),
    )
    conn.commit()


# ---- Entity identity (U1) ----

import hashlib  # noqa: E402 — local to the identity section


def _slug(text: str) -> str:
    """Filename/IRI-safe slug: lowercase, non-alphanumeric runs → single
    hyphen, stripped. Deterministic; no network, no randomness."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower())
    return s.strip("-")


def workspace_id(explicit: Optional[str] = None) -> str:
    """The workspace token embedded in minted IRIs.

    Resolution order: explicit arg → `workspace_id` in .curator/config.json
    → basename of the current working directory. Workspace-stable (it does
    not change as pages are added); documented as such. Renaming the
    workspace directory changes it, which is why identity is keyed on the
    minted IRI, not on the directory."""
    if explicit:
        return _slug(explicit)
    cfg = Path(".curator/config.json")
    if cfg.exists():
        try:
            ws = json.loads(cfg.read_text()).get("workspace_id")
            if ws:
                return _slug(str(ws))
        except Exception:
            pass
    return _slug(Path.cwd().name) or "workspace"


def read_entity(conn, iri: str) -> Optional[dict]:
    cur = conn.execute(
        "SELECT iri, entity_class, page_path, same_as_json, status, "
        "resolved_at FROM entities WHERE iri = ?",
        (iri,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    try:
        same_as = json.loads(row[3]) if row[3] else {}
    except (json.JSONDecodeError, TypeError):
        same_as = {}
    return {
        "iri": row[0], "entity_class": row[1], "page_path": row[2],
        "same_as": same_as, "status": row[4], "resolved_at": row[5],
    }


def write_entity(conn, iri: str, *, entity_class: str,
                 page_path: Optional[str] = None,
                 same_as: Optional[dict] = None,
                 status: str = "ok") -> None:
    """Upsert an entity IRI. same_as is a {authority: id} map serialised to
    JSON. Merges into any existing same_as rather than clobbering it."""
    existing = read_entity(conn, iri)
    merged = dict(existing["same_as"]) if existing else {}
    if same_as:
        merged.update({k: v for k, v in same_as.items() if v is not None})
    if page_path is None and existing:
        page_path = existing["page_path"]
    conn.execute(
        "INSERT OR REPLACE INTO entities(iri, entity_class, page_path, "
        "same_as_json, status, resolved_at) VALUES (?, ?, ?, ?, ?, ?)",
        (iri, entity_class, page_path,
         json.dumps(merged, separators=(",", ":"), sort_keys=True),
         status, _now_iso()),
    )
    conn.commit()


def mint_iri(entity_class: str, title: str, *,
             workspace: Optional[str] = None,
             same_as: Optional[dict] = None,
             page_path: Optional[str] = None) -> str:
    """Deterministically mint a workspace-stable IRI for an entity.

    Form: ``ce:<entity_class>:<workspace>:<slug>``. Identity never keys on
    an external id — those live in same_as — so re-resolution upstream can
    never orphan a citation that pinned the IRI.

    Collision handling is deterministic (no UUID/random): if the base IRI is
    already held by a *different* identity (different same_as or page_path),
    a short stable hash of a disambiguator is appended. Same identity →
    same IRI returned (idempotent)."""
    ws = workspace_id(workspace)
    cls = _slug(entity_class) or "entity"
    base_slug = _slug(title) or "unnamed"
    base = f"ce:{cls}:{ws}:{base_slug}"

    conn = _connect()
    try:
        existing = read_entity(conn, base)
        if existing is None:
            return base
        # Same identity? Reuse the base IRI. A page is one entity, so a
        # matching page_path reconciles even when the two mints contribute
        # disjoint authorities (pubchem resolved now, wikidata later).
        # Failing that, any shared same_as pair means the same real-world
        # entity under different page paths.
        same_identity = False
        if page_path and existing["page_path"]:
            same_identity = page_path == existing["page_path"]
        if not same_identity and same_as and existing["same_as"]:
            shared = set(same_as.items()) & set(existing["same_as"].items())
            same_identity = bool(shared)
        if same_identity:
            return base
        # Genuine collision between distinct entities → deterministic suffix.
        disambig = ""
        if same_as:
            disambig = next(iter(sorted(f"{k}:{v}" for k, v in same_as.items())), "")
        disambig = disambig or page_path or title
        suffix = hashlib.sha1(disambig.encode("utf-8")).hexdigest()[:6]
        return f"{base}-{suffix}"
    finally:
        conn.close()


def lookup_cached_entity(iri: str) -> dict:
    """Read-only IRI lookup. Returns the stored row or {status: unminted}."""
    conn = _connect()
    try:
        ent = read_entity(conn, iri)
    finally:
        conn.close()
    if ent is None:
        return {"iri": iri, "status": "unminted", "cached": False}
    ent["cached"] = True
    return ent


# ---- Queue ----

def queue_request(kind: str, names: list, source_page: Optional[str] = None) -> dict:
    """Append a resolution request to the queue. No network. Idempotent
    per (kind, name): identical requests already in the queue are
    deduped. Returns counts."""
    if kind not in ("chemicals", "genes"):
        raise ValueError(f"unknown kind {kind!r}")
    if not isinstance(names, list):
        raise ValueError("names must be a list")
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)

    seen: set = set()
    if QUEUE_PATH.exists():
        for line in QUEUE_PATH.read_text().splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("kind") == kind:
                for n in ev.get("names") or []:
                    seen.add(_normalise_name(n))

    new_names = []
    for n in names:
        nn = _normalise_name(n)
        if not nn or nn in seen:
            continue
        new_names.append(n)
        seen.add(nn)
    if not new_names:
        return {"queued": 0, "skipped": len(names), "reason": "all already queued or empty"}

    event = {
        "ts": _now_iso(),
        "kind": kind,
        "names": new_names,
    }
    if source_page:
        event["source_page"] = source_page
    with QUEUE_PATH.open("a") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")
    return {"queued": len(new_names), "skipped": len(names) - len(new_names)}


def read_queue() -> list:
    """Return the current queue as a list of events. Library entry
    point used by identifier_resolve.py."""
    if not QUEUE_PATH.exists():
        return []
    out = []
    for line in QUEUE_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def archive_queue(processed_marker: str = "processed") -> None:
    """Move the current queue file aside after a resolve pass. Library
    entry point used by identifier_resolve.py."""
    if not QUEUE_PATH.exists():
        return
    archive_dir = QUEUE_PATH.parent / "identifier-requests.history"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    QUEUE_PATH.rename(archive_dir / f"{ts}-{processed_marker}.jsonl")


# ---- Lookup helpers (cache-only) ----

def lookup_cached_chemical(name: str, queue_on_miss: bool = True) -> dict:
    """Return the cached resolution for `name`, or `{status: pending}`
    if it isn't cached. Optionally queue the miss for the next
    resolve pass."""
    name_norm = _normalise_name(name)
    if not name_norm:
        return {
            "name": name, "name_norm": name_norm,
            "status": "not_found", "cached": False,
            "note": "empty name after normalisation",
        }
    conn = _connect()
    try:
        cached = read_chemical(conn, name_norm)
    finally:
        conn.close()
    if cached and cached["status"] in ("ok", "not_found"):
        cached["name"] = name
        cached["cached"] = True
        return cached
    if queue_on_miss:
        try:
            queue_request("chemicals", [name])
        except Exception:
            pass
    return {
        "name": name, "name_norm": name_norm,
        "status": "pending", "cached": False,
        "note": "not in cache; queued for next resolve pass — run "
                "`identifier_resolve.py review` to inspect, then "
                "`identifier_resolve.py run --yes`.",
    }


def lookup_cached_gene(symbol: str, queue_on_miss: bool = True) -> dict:
    symbol_norm = _normalise_name(symbol)
    if not symbol_norm:
        return {
            "symbol": symbol, "symbol_norm": symbol_norm,
            "status": "not_found", "cached": False,
            "note": "empty symbol after normalisation",
        }
    conn = _connect()
    try:
        cached = read_gene(conn, symbol_norm)
    finally:
        conn.close()
    if cached and cached["status"] in ("ok", "not_found"):
        cached["symbol"] = symbol
        cached["cached"] = True
        return cached
    if queue_on_miss:
        try:
            queue_request("genes", [symbol])
        except Exception:
            pass
    return {
        "symbol": symbol, "symbol_norm": symbol_norm,
        "status": "pending", "cached": False,
        "note": "not in cache; queued for next resolve pass — run "
                "`identifier_resolve.py review` to inspect, then "
                "`identifier_resolve.py run --yes`.",
    }


# ---- CLI ----

def cmd_lookup_chemical(name: str) -> int:
    print(json.dumps(lookup_cached_chemical(name), indent=2))
    return 0


def cmd_lookup_gene(symbol: str) -> int:
    print(json.dumps(lookup_cached_gene(symbol), indent=2))
    return 0


def cmd_bulk_lookup(kind: str, names_json: str) -> int:
    try:
        names = json.loads(names_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid --names-json: {e}"}))
        return 1
    if not isinstance(names, list):
        print(json.dumps({"error": "--names-json must be a JSON array"}))
        return 1
    if kind == "chemicals":
        results = [lookup_cached_chemical(n) for n in names]
    elif kind == "genes":
        results = [lookup_cached_gene(n) for n in names]
    else:
        print(json.dumps({"error": f"unknown --type: {kind!r}"}))
        return 1
    pending = sum(1 for r in results if r["status"] == "pending")
    print(json.dumps({
        "type": kind, "count": len(results),
        "pending": pending,
        "note": (
            f"{pending} name(s) not in cache and queued for the next "
            "resolve pass — run `identifier_resolve.py review` then "
            "`identifier_resolve.py run --yes` (after enabling in config)."
        ) if pending else None,
        "results": results,
    }, indent=2))
    return 0


def cmd_queue(kind: str, names_json: str, source_page: Optional[str]) -> int:
    try:
        names = json.loads(names_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid --names-json: {e}"}))
        return 1
    if not isinstance(names, list):
        print(json.dumps({"error": "--names-json must be a JSON array"}))
        return 1
    try:
        result = queue_request(kind, names, source_page=source_page)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_pending() -> int:
    events = read_queue()
    by_kind: dict = {"chemicals": set(), "genes": set()}
    sources: set = set()
    for ev in events:
        kind = ev.get("kind")
        if kind not in by_kind:
            continue
        for n in ev.get("names") or []:
            by_kind[kind].add(n)
        if ev.get("source_page"):
            sources.add(ev["source_page"])
    print(json.dumps({
        "total_events": len(events),
        "chemicals": sorted(by_kind["chemicals"]),
        "genes": sorted(by_kind["genes"]),
        "source_pages": sorted(sources),
        "queue_path": str(QUEUE_PATH),
        "next_step": (
            "Run `identifier_resolve.py review` to see endpoints, then "
            "`identifier_resolve.py run --yes` to drain. The resolve script "
            "is gated by `identifier_resolution.enabled = true` in "
            ".curator/config.json."
        ) if events else None,
    }, indent=2))
    return 0


def _parse_same_as(raw: Optional[str]) -> dict:
    """Accept same_as as a JSON object (`{"pubchem":"CID2244"}`) or a JSON
    array of `authority:id` strings (`["pubchem:CID2244"]`) — the latter
    matches the frontmatter bracket-list form. Returns {} for None/empty."""
    if not raw:
        return {}
    val = json.loads(raw)
    if isinstance(val, dict):
        return {str(k): v for k, v in val.items()}
    if isinstance(val, list):
        out = {}
        for item in val:
            k, _, v = str(item).partition(":")
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
        return out
    raise ValueError("--same-as must be a JSON object or array of 'auth:id'")


def cmd_mint_entity(entity_class: str, title: str, same_as_raw: Optional[str],
                    page_path: Optional[str], workspace: Optional[str],
                    write: bool) -> int:
    try:
        same_as = _parse_same_as(same_as_raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(json.dumps({"error": f"invalid --same-as: {e}"}))
        return 1
    iri = mint_iri(entity_class, title, workspace=workspace,
                   same_as=same_as, page_path=page_path)
    status = "ok" if same_as else "local-only"
    if write:
        conn = _connect()
        try:
            write_entity(conn, iri, entity_class=entity_class,
                         page_path=page_path, same_as=same_as, status=status)
        finally:
            conn.close()
    print(json.dumps({
        "iri": iri,
        "entity_class": entity_class,
        "page_path": page_path,
        "same_as": same_as,
        "status": status,
        "written": write,
    }, indent=2))
    return 0


def cmd_lookup_entity(iri: str) -> int:
    print(json.dumps(lookup_cached_entity(iri), indent=2))
    return 0


def cmd_cache_stats() -> int:
    if not DB_PATH.exists():
        print(json.dumps({"chemicals": 0, "genes": 0, "entities": 0,
                          "note": "no cache yet"}))
        return 0
    conn = _connect()
    try:
        chem = conn.execute(
            "SELECT status, COUNT(*) FROM chemicals GROUP BY status"
        ).fetchall()
        gene = conn.execute(
            "SELECT status, COUNT(*) FROM genes GROUP BY status"
        ).fetchall()
        ent = conn.execute(
            "SELECT status, COUNT(*) FROM entities GROUP BY status"
        ).fetchall()
        chem_total = conn.execute("SELECT COUNT(*) FROM chemicals").fetchone()[0]
        gene_total = conn.execute("SELECT COUNT(*) FROM genes").fetchone()[0]
        ent_total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        ent_classes = conn.execute(
            "SELECT entity_class, COUNT(*) FROM entities GROUP BY entity_class"
        ).fetchall()
    finally:
        conn.close()
    queue_size = len(read_queue())
    print(json.dumps({
        "chemicals": {"total": chem_total, "by_status": dict(chem)},
        "genes":     {"total": gene_total, "by_status": dict(gene)},
        "entities":  {"total": ent_total, "by_status": dict(ent),
                      "by_class": dict(ent_classes)},
        "queue":     {"events_pending": queue_size},
    }, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_lc = sub.add_parser("lookup-chemical",
                           help="cache-only chemical lookup; queue on miss")
    p_lc.add_argument("name")

    p_lg = sub.add_parser("lookup-gene",
                           help="cache-only gene lookup; queue on miss")
    p_lg.add_argument("symbol")

    p_bl = sub.add_parser("bulk-lookup",
                           help="cache-only batch lookup; queue misses")
    p_bl.add_argument("--type", choices=["chemicals", "genes"], required=True)
    p_bl.add_argument("--names-json", required=True,
                       help="JSON array of names/symbols")

    p_q = sub.add_parser("queue",
                          help="append a resolution request to the queue (no cache check)")
    p_q.add_argument("--type", choices=["chemicals", "genes"], required=True)
    p_q.add_argument("--names-json", required=True,
                      help="JSON array of names/symbols")
    p_q.add_argument("--source-page",
                      help="optional wiki/-relative path of the page that triggered the request")

    p_me = sub.add_parser("mint-entity",
                           help="mint a workspace-stable IRI for an entity (U1)")
    p_me.add_argument("--entity-class", required=True,
                       help="chemical|gene|protein|person|org|concept|...")
    p_me.add_argument("--title", required=True,
                       help="entity display title (drives the IRI slug)")
    p_me.add_argument("--same-as",
                       help="JSON object {\"auth\":\"id\"} or array [\"auth:id\"]")
    p_me.add_argument("--page-path",
                       help="wiki/-relative path of the entity page")
    p_me.add_argument("--workspace",
                       help="override workspace token (default: config or cwd name)")
    p_me.add_argument("--no-write", action="store_true",
                       help="compute the IRI without persisting to the cache")

    p_le = sub.add_parser("lookup-entity",
                           help="read-only IRI lookup (U1)")
    p_le.add_argument("iri")

    sub.add_parser("pending",
                    help="list queued requests waiting for the next resolve pass")

    sub.add_parser("cache-stats",
                    help="cache row counts + queue size")

    args = ap.parse_args()
    if args.cmd == "mint-entity":
        return cmd_mint_entity(args.entity_class, args.title, args.same_as,
                               args.page_path, args.workspace, not args.no_write)
    if args.cmd == "lookup-entity":
        return cmd_lookup_entity(args.iri)
    if args.cmd == "lookup-chemical":
        return cmd_lookup_chemical(args.name)
    if args.cmd == "lookup-gene":
        return cmd_lookup_gene(args.symbol)
    if args.cmd == "bulk-lookup":
        return cmd_bulk_lookup(args.type, args.names_json)
    if args.cmd == "queue":
        return cmd_queue(args.type, args.names_json, args.source_page)
    if args.cmd == "pending":
        return cmd_pending()
    if args.cmd == "cache-stats":
        return cmd_cache_stats()
    ap.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
