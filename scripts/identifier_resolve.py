#!/usr/bin/env python3
"""identifier_resolve.py — explicit, gated network resolver for the
chemical/gene identifier cache.

This is the ONLY script in the skill that sends user-derived
identifier strings (chemical names, gene symbols) to external
endpoints. It is:

- **Off by default.** Refuses to run unless
  `.curator/config.json` has `identifier_resolution.enabled = true`.
  The config block ships disabled in the template; user must
  explicitly opt in.
- **Two-step.** `review` shows the queued payload + endpoints
  without making any network call. `run --yes` is required to
  actually hit the network.
- **Endpoint-configurable.** `chemicals_endpoint` and
  `genes_endpoint` in the config block can be overridden — point
  them at an internal API mirror in enterprise settings, or a
  custom resolver. Defaults match the public PubChem and
  MyGene.info endpoints.
- **Cache-first.** Before any network call the script checks
  `.curator/identifiers.db` (managed by `identifier_cache.py`).
  Only true misses go to the network.

Why this exists: chemical structures and gene lists can be
sensitive (proprietary compounds, novel target screens). Sending
them to a public database without explicit user gesture is a
data-exfiltration concern in some workflows. Splitting the network
out of `identifier_cache.py` makes the egress visible, opt-in,
configurable, and removable.

Subcommands
-----------
    identifier_resolve.py review
        Print the pending queue + the endpoints that would receive
        each kind of name. No network call. Always safe to run.

    identifier_resolve.py run [--yes]
        Drain the queue, hit endpoints, write results to the cache.
        Without --yes: prints the plan and exits without acting.
        With --yes: runs the resolutions; processed queue is
        archived under `.curator/identifier-requests.history/`.

    identifier_resolve.py status
        Quick health check: is the resolver enabled, what endpoints
        are configured, queue size.

Hash-guarded by evolve_guard.sh.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identifier_cache import (  # noqa: E402
    _connect as cache_connect,
    _normalise_name,
    archive_queue,
    read_chemical,
    read_gene,
    read_queue,
    write_chemical,
    write_gene,
)

CONFIG_PATH = Path(".curator/config.json")
HTTP_TIMEOUT = 5.0

DEFAULT_ENDPOINTS = {
    "chemicals_endpoint": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/",
    "genes_endpoint":     "https://mygene.info/v3/query",
}


def _load_config() -> dict:
    """Returns {enabled, chemicals_endpoint, genes_endpoint} with
    defaults applied. Refuses silently when config is missing — the
    caller's `enabled` check handles the off-by-default behaviour."""
    cfg_block = {}
    if CONFIG_PATH.exists():
        try:
            full = json.loads(CONFIG_PATH.read_text())
            cfg_block = full.get("identifier_resolution") or {}
        except Exception:
            cfg_block = {}
    out = {
        "enabled": bool(cfg_block.get("enabled", False)),
        "chemicals_endpoint": cfg_block.get("chemicals_endpoint")
                                or DEFAULT_ENDPOINTS["chemicals_endpoint"],
        "genes_endpoint": cfg_block.get("genes_endpoint")
                            or DEFAULT_ENDPOINTS["genes_endpoint"],
    }
    return out


def _http_get_json(url: str) -> dict:
    """The only outbound network call site in the skill. Wrapped in
    a single helper so reviewers have one place to audit."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "curiosity-engine/identifier_resolve"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:  # noqa: S310 — endpoint is config-gated
        return json.loads(r.read().decode("utf-8"))


# ---- Resolvers (one per kind, endpoint-driven) ----

def _resolve_chemical(name: str, endpoint: str) -> tuple:
    """Returns (status, result_dict). Endpoint is treated as a
    PubChem-PUG-REST-shaped service; if you're pointing at an
    internal mirror it must speak the same JSON shape."""
    try:
        quoted = urllib.parse.quote(name)
        cid_url = f"{endpoint.rstrip('/')}/{quoted}/cids/JSON"
        data = _http_get_json(cid_url)
        cids = data.get("IdentifierList", {}).get("CID", [])
        if not cids:
            return "not_found", {}
        cid = int(cids[0])
        # Property fetch path — derive from endpoint root by replacing the
        # trailing /name/ segment with /cid/<id>/property/...
        # If user has configured a non-default endpoint, the same path
        # convention applies (PUG-REST shape). Internal mirrors should
        # mirror the URL grammar.
        prop_root = re.sub(r"/name/?$", "/cid/", endpoint.rstrip("/") + "/")
        prop_url = (
            f"{prop_root}{cid}/property/CanonicalSMILES,InChI,InChIKey/JSON"
        )
        pdata = _http_get_json(prop_url)
        props = (pdata.get("PropertyTable", {}).get("Properties", []) or [{}])[0]
        return "ok", {
            "cid": cid,
            "smiles": props.get("CanonicalSMILES"),
            "inchi": props.get("InChI"),
            "inchikey": props.get("InChIKey"),
        }
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ConnectionError):
        return "offline", {}


def _resolve_gene(symbol: str, endpoint: str) -> tuple:
    """Returns (status, result_dict). Endpoint is treated as a
    MyGene.info-shaped service."""
    try:
        params = urllib.parse.urlencode({
            "q": f"symbol:{symbol}",
            "fields": "ensembl.gene,uniprot.Swiss-Prot,entrezgene,taxid",
            "size": "1",
        })
        url = f"{endpoint.rstrip('/')}?{params}"
        data = _http_get_json(url)
        hits = data.get("hits", []) or []
        if not hits:
            return "not_found", {}
        h = hits[0]
        ens = h.get("ensembl", {})
        if isinstance(ens, list):
            ens = ens[0] if ens else {}
        uni = h.get("uniprot", {}) or {}
        swiss = uni.get("Swiss-Prot")
        if isinstance(swiss, list):
            swiss = swiss[0] if swiss else None
        return "ok", {
            "ensembl_id": ens.get("gene") if isinstance(ens, dict) else None,
            "uniprot_id": swiss,
            "entrez_id": h.get("entrezgene"),
            "taxid": h.get("taxid"),
        }
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ConnectionError):
        return "offline", {}


# ---- Queue traversal ----

def _aggregate_queue() -> dict:
    """Collapse the JSONL queue into deduplicated name lists per kind,
    plus a mapping name → source pages that requested it."""
    out: dict = {"chemicals": {}, "genes": {}}
    for ev in read_queue():
        kind = ev.get("kind")
        if kind not in out:
            continue
        sp = ev.get("source_page")
        for n in ev.get("names") or []:
            nn = _normalise_name(n)
            if not nn:
                continue
            entry = out[kind].setdefault(n, {"name": n, "source_pages": set()})
            if sp:
                entry["source_pages"].add(sp)
    # Convert sets to sorted lists for stable output.
    for kind in out:
        for entry in out[kind].values():
            entry["source_pages"] = sorted(entry["source_pages"])
    return out


# ---- Subcommands ----

def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load_config()
    queue = read_queue()
    print(json.dumps({
        "enabled": cfg["enabled"],
        "chemicals_endpoint": cfg["chemicals_endpoint"],
        "genes_endpoint": cfg["genes_endpoint"],
        "queue_events": len(queue),
        "config_path": str(CONFIG_PATH),
        "note": (
            "Resolver is OFF. Set "
            "`identifier_resolution.enabled = true` in "
            f"{CONFIG_PATH} to enable. Inspect the queue first via "
            "`identifier_resolve.py review`."
        ) if not cfg["enabled"] else None,
    }, indent=2))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    cfg = _load_config()
    agg = _aggregate_queue()
    chem_names = sorted(agg["chemicals"].keys())
    gene_names = sorted(agg["genes"].keys())

    plan = {
        "enabled": cfg["enabled"],
        "endpoints": {
            "chemicals": cfg["chemicals_endpoint"],
            "genes":     cfg["genes_endpoint"],
        },
        "would_send": {
            "chemicals": {
                "count": len(chem_names),
                "names": chem_names,
                "endpoint": cfg["chemicals_endpoint"],
            },
            "genes": {
                "count": len(gene_names),
                "symbols": gene_names,
                "endpoint": cfg["genes_endpoint"],
            },
        },
        "next_step": (
            "Resolver is currently DISABLED. To proceed: set "
            f"`identifier_resolution.enabled = true` in {CONFIG_PATH}, "
            "then re-run `identifier_resolve.py run --yes`."
        ) if not cfg["enabled"] else (
            "Run `identifier_resolve.py run --yes` to drain the queue."
        ),
    }
    print(json.dumps(plan, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load_config()
    if not cfg["enabled"]:
        print(json.dumps({
            "ok": False,
            "error": "identifier_resolution.enabled is false in "
                     f"{CONFIG_PATH}; refusing to run.",
            "fix": "Set `identifier_resolution.enabled = true` in the "
                   "config, then re-run.",
        }, indent=2))
        return 1
    if not args.yes:
        # Print the would-do plan and exit. Same shape as review so
        # the user can read either.
        return cmd_review(args)

    agg = _aggregate_queue()
    if not agg["chemicals"] and not agg["genes"]:
        print(json.dumps({"ok": True, "drained": 0, "note": "queue empty"}))
        return 0

    conn = cache_connect()
    chem_results = []
    gene_results = []
    try:
        # Chemicals
        for name in sorted(agg["chemicals"]):
            nn = _normalise_name(name)
            existing = read_chemical(conn, nn)
            if existing and existing["status"] in ("ok", "not_found"):
                chem_results.append({"name": name, "status": existing["status"], "from_cache": True})
                continue
            status, data = _resolve_chemical(name, cfg["chemicals_endpoint"])
            write_chemical(
                conn, nn,
                smiles=data.get("smiles"), inchi=data.get("inchi"),
                inchikey=data.get("inchikey"), cid=data.get("cid"),
                source="pubchem", status=status,
            )
            chem_results.append({"name": name, "status": status, "from_cache": False})

        # Genes
        for symbol in sorted(agg["genes"]):
            sn = _normalise_name(symbol)
            existing = read_gene(conn, sn)
            if existing and existing["status"] in ("ok", "not_found"):
                gene_results.append({"symbol": symbol, "status": existing["status"], "from_cache": True})
                continue
            status, data = _resolve_gene(symbol, cfg["genes_endpoint"])
            write_gene(
                conn, sn,
                ensembl_id=data.get("ensembl_id"),
                uniprot_id=data.get("uniprot_id"),
                entrez_id=data.get("entrez_id"),
                taxid=data.get("taxid"),
                source="mygene", status=status,
            )
            gene_results.append({"symbol": symbol, "status": status, "from_cache": False})
    finally:
        conn.close()

    # Archive the queue so the next run starts fresh.
    archive_queue(processed_marker="resolved")

    print(json.dumps({
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "chemicals": chem_results,
        "genes": gene_results,
        "endpoints": {
            "chemicals": cfg["chemicals_endpoint"],
            "genes":     cfg["genes_endpoint"],
        },
        "queue_archived": True,
    }, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_st = sub.add_parser("status", help="show enabled flag, endpoints, queue size")
    p_st.set_defaults(func=cmd_status)

    p_rv = sub.add_parser("review",
                           help="print queue + endpoints; no network call")
    p_rv.set_defaults(func=cmd_review)

    p_rn = sub.add_parser("run",
                           help="drain queue against endpoints (requires --yes)")
    p_rn.add_argument("--yes", action="store_true",
                       help="actually hit the network; without this flag run is review-only")
    p_rn.set_defaults(func=cmd_run)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
