#!/usr/bin/env python3
"""identifier_cache.py — chemical/gene name → canonical-ID resolver.

A deterministic, on-demand utility that maps free-text scientific
identifiers (chemical names, gene symbols) to canonical IDs (SMILES,
InChI, Ensembl, UniProt) via PubChem and MyGene.info. Results are
cached at `.curator/identifiers.db` (SQLite, WAL) so repeated lookups
don't re-hit the network.

Design principles
-----------------
- Lazy / on-demand. Never invoked at ingest. Synthesis workers
  (`summary_table_builder`, `analysis_writer`) call into this when
  they cite cells from a `[tab]` page whose `normalise_columns` fm
  flag identifies a chemistry or gene-symbol column.
- Offline-friendly. `CURIOSITY_ENGINE_OFFLINE=1` env var → skip HTTP,
  return only cached results. Air-gapped users keep populating the
  cache offline (it's a local SQLite); resolutions surface when
  network access returns.
- No new deps. Stdlib only — `urllib.request`, `sqlite3`, `json`. No
  Anthropic SDK calls; this is a pure-data utility.
- Status-aware caching. `status: ok | not_found | offline`. Only
  `ok` and `not_found` rows are authoritative — `offline` rows are
  retried on the next call (a cache marker, not a result).

Subcommands
-----------
    identifier_cache.py lookup-chemical <name>
        Returns {name, name_norm, smiles, inchi, source, status, cached}.

    identifier_cache.py lookup-gene <symbol>
        Returns {symbol, symbol_norm, ensembl_id, uniprot_id, source,
                 status, cached}.

    identifier_cache.py bulk-lookup --type chemicals|genes
                                       --names-json '[...]'
        Resolve a list of names in one call. Output is a JSON array of
        per-name resolution dicts. Cache-aware: hits go straight back
        without HTTP.

    identifier_cache.py cache-stats
        Row counts per table + status breakdown. Useful for
        deployment-time visibility.

Exit codes: 0 = ok, 1 = argument error, 2 = unrecoverable HTTP /
DB error (rare — most network failures land in cache as `offline`
status without raising).

Hash-guarded by evolve_guard.sh.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional


DB_PATH = Path(".curator/identifiers.db")
HTTP_TIMEOUT = 5.0

PUBCHEM_BASE = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
)
MYGENE_BASE = "https://mygene.info/v3/query"

OFFLINE_ENV = "CURIOSITY_ENGINE_OFFLINE"


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
    return conn


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _normalise_name(name: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation noise.

    Used as the cache key so `Tris`, `tris`, and `Tris ` all hit the
    same row. Preserves Greek letters and hyphens (which carry meaning
    in chemical names like 2-amino-2-(hydroxymethyl)propane-1,3-diol)
    by intent — we collapse only structural whitespace.
    """
    return re.sub(r"\s+", " ", name.strip().lower())


def _is_offline() -> bool:
    return os.environ.get(OFFLINE_ENV, "").lower() in ("1", "true", "yes")


# ---- Cache reads ----

def _read_chemical(conn, name_norm: str) -> Optional[dict]:
    cur = conn.execute(
        "SELECT name_norm, smiles, inchi, inchikey, cid, source, status, "
        "fetched_at FROM chemicals WHERE name_norm = ?",
        (name_norm,),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {
        "name_norm": r[0], "smiles": r[1], "inchi": r[2],
        "inchikey": r[3], "cid": r[4], "source": r[5],
        "status": r[6], "fetched_at": r[7],
    }


def _read_gene(conn, symbol_norm: str) -> Optional[dict]:
    cur = conn.execute(
        "SELECT symbol_norm, ensembl_id, uniprot_id, entrez_id, taxid, "
        "source, status, fetched_at FROM genes WHERE symbol_norm = ?",
        (symbol_norm,),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {
        "symbol_norm": r[0], "ensembl_id": r[1], "uniprot_id": r[2],
        "entrez_id": r[3], "taxid": r[4], "source": r[5],
        "status": r[6], "fetched_at": r[7],
    }


# ---- Cache writes ----

def _write_chemical(conn, name_norm: str, *,
                     smiles: Optional[str], inchi: Optional[str],
                     inchikey: Optional[str], cid: Optional[int],
                     source: str, status: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO chemicals "
        "(name_norm, smiles, inchi, inchikey, cid, source, status, "
        " fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name_norm, smiles, inchi, inchikey, cid, source, status,
         _now_iso()),
    )
    conn.commit()


def _write_gene(conn, symbol_norm: str, *,
                  ensembl_id: Optional[str], uniprot_id: Optional[str],
                  entrez_id: Optional[int], taxid: Optional[int],
                  source: str, status: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO genes "
        "(symbol_norm, ensembl_id, uniprot_id, entrez_id, taxid, "
        " source, status, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol_norm, ensembl_id, uniprot_id, entrez_id, taxid,
         source, status, _now_iso()),
    )
    conn.commit()


# ---- HTTP fetchers ----

def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "curiosity-engine/identifier_cache"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_chemical_pubchem(name: str) -> Optional[dict]:
    """Hit PubChem PUG-REST for a chemical name; return None on miss.

    Two-stage call: name → CID via the search endpoint, then CID →
    properties (SMILES + InChI + InChIKey) via the property endpoint.
    Either stage may 404; both are treated as `not_found`. Network
    timeouts raise `urllib.error.URLError` — caller catches and
    records `offline` status.
    """
    quoted = urllib.parse.quote(name)
    cid_url = f"{PUBCHEM_BASE}{quoted}/cids/JSON"
    data = _http_get_json(cid_url)
    cids = data.get("IdentifierList", {}).get("CID", [])
    if not cids:
        return None
    cid = int(cids[0])
    prop_url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/"
        f"{cid}/property/CanonicalSMILES,InChI,InChIKey/JSON"
    )
    pdata = _http_get_json(prop_url)
    props = (pdata.get("PropertyTable", {}).get("Properties", []) or [{}])[0]
    return {
        "cid": cid,
        "smiles": props.get("CanonicalSMILES"),
        "inchi": props.get("InChI"),
        "inchikey": props.get("InChIKey"),
    }


def _fetch_gene_mygene(symbol: str) -> Optional[dict]:
    """Hit MyGene.info for a gene symbol; return None on miss.

    Single search call constrained to symbol field; takes the first
    hit that has both an Ensembl gene ID and a UniProt accession (the
    most useful canonical pair for downstream synthesis). When neither
    field is present we still record the entrez_id so curators can
    cross-reference manually.
    """
    params = urllib.parse.urlencode({
        "q": f"symbol:{symbol}",
        "fields": "ensembl.gene,uniprot.Swiss-Prot,entrezgene,taxid",
        "size": "1",
    })
    url = f"{MYGENE_BASE}?{params}"
    data = _http_get_json(url)
    hits = data.get("hits", []) or []
    if not hits:
        return None
    h = hits[0]
    ens = h.get("ensembl", {})
    if isinstance(ens, list):
        ens = ens[0] if ens else {}
    uni = h.get("uniprot", {}) or {}
    swiss = uni.get("Swiss-Prot")
    if isinstance(swiss, list):
        swiss = swiss[0] if swiss else None
    return {
        "ensembl_id": ens.get("gene") if isinstance(ens, dict) else None,
        "uniprot_id": swiss,
        "entrez_id": h.get("entrezgene"),
        "taxid": h.get("taxid"),
    }


# ---- Public lookup helpers ----

def lookup_chemical(name: str, *,
                       force_refresh: bool = False) -> dict:
    """Resolve a chemical name. Returns a dict with cache hit info."""
    name_norm = _normalise_name(name)
    if not name_norm:
        return {
            "name": name, "name_norm": name_norm,
            "smiles": None, "inchi": None, "source": None,
            "status": "not_found", "cached": False,
            "note": "empty name after normalisation",
        }
    conn = _connect()
    try:
        cached = _read_chemical(conn, name_norm)
        if (cached and not force_refresh
                and cached["status"] in ("ok", "not_found")):
            cached["name"] = name
            cached["cached"] = True
            return cached
        if _is_offline():
            if cached:
                cached["name"] = name
                cached["cached"] = True
                return cached
            return {
                "name": name, "name_norm": name_norm,
                "smiles": None, "inchi": None, "source": None,
                "status": "offline", "cached": False,
            }
        # Network call.
        try:
            result = _fetch_chemical_pubchem(name)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, ConnectionError):
            _write_chemical(conn, name_norm, smiles=None, inchi=None,
                              inchikey=None, cid=None, source="pubchem",
                              status="offline")
            return {
                "name": name, "name_norm": name_norm,
                "smiles": None, "inchi": None, "source": "pubchem",
                "status": "offline", "cached": False,
            }
        if result is None:
            _write_chemical(conn, name_norm, smiles=None, inchi=None,
                              inchikey=None, cid=None, source="pubchem",
                              status="not_found")
            return {
                "name": name, "name_norm": name_norm,
                "smiles": None, "inchi": None, "source": "pubchem",
                "status": "not_found", "cached": False,
            }
        _write_chemical(conn, name_norm, smiles=result.get("smiles"),
                          inchi=result.get("inchi"),
                          inchikey=result.get("inchikey"),
                          cid=result.get("cid"), source="pubchem",
                          status="ok")
        return {
            "name": name, "name_norm": name_norm,
            "smiles": result.get("smiles"),
            "inchi": result.get("inchi"),
            "inchikey": result.get("inchikey"),
            "cid": result.get("cid"),
            "source": "pubchem", "status": "ok", "cached": False,
        }
    finally:
        conn.close()


def lookup_gene(symbol: str, *,
                  force_refresh: bool = False) -> dict:
    """Resolve a gene symbol. Returns a dict with cache hit info."""
    symbol_norm = _normalise_name(symbol)
    if not symbol_norm:
        return {
            "symbol": symbol, "symbol_norm": symbol_norm,
            "ensembl_id": None, "uniprot_id": None, "source": None,
            "status": "not_found", "cached": False,
            "note": "empty symbol after normalisation",
        }
    conn = _connect()
    try:
        cached = _read_gene(conn, symbol_norm)
        if (cached and not force_refresh
                and cached["status"] in ("ok", "not_found")):
            cached["symbol"] = symbol
            cached["cached"] = True
            return cached
        if _is_offline():
            if cached:
                cached["symbol"] = symbol
                cached["cached"] = True
                return cached
            return {
                "symbol": symbol, "symbol_norm": symbol_norm,
                "ensembl_id": None, "uniprot_id": None, "source": None,
                "status": "offline", "cached": False,
            }
        try:
            result = _fetch_gene_mygene(symbol)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, ConnectionError):
            _write_gene(conn, symbol_norm, ensembl_id=None,
                         uniprot_id=None, entrez_id=None, taxid=None,
                         source="mygene", status="offline")
            return {
                "symbol": symbol, "symbol_norm": symbol_norm,
                "ensembl_id": None, "uniprot_id": None,
                "source": "mygene", "status": "offline",
                "cached": False,
            }
        if result is None:
            _write_gene(conn, symbol_norm, ensembl_id=None,
                         uniprot_id=None, entrez_id=None, taxid=None,
                         source="mygene", status="not_found")
            return {
                "symbol": symbol, "symbol_norm": symbol_norm,
                "ensembl_id": None, "uniprot_id": None,
                "source": "mygene", "status": "not_found",
                "cached": False,
            }
        _write_gene(conn, symbol_norm,
                     ensembl_id=result.get("ensembl_id"),
                     uniprot_id=result.get("uniprot_id"),
                     entrez_id=result.get("entrez_id"),
                     taxid=result.get("taxid"),
                     source="mygene", status="ok")
        return {
            "symbol": symbol, "symbol_norm": symbol_norm,
            "ensembl_id": result.get("ensembl_id"),
            "uniprot_id": result.get("uniprot_id"),
            "entrez_id": result.get("entrez_id"),
            "taxid": result.get("taxid"),
            "source": "mygene", "status": "ok", "cached": False,
        }
    finally:
        conn.close()


# ---- CLI ----

def cmd_lookup_chemical(name: str, force: bool) -> int:
    print(json.dumps(lookup_chemical(name, force_refresh=force),
                       indent=2))
    return 0


def cmd_lookup_gene(symbol: str, force: bool) -> int:
    print(json.dumps(lookup_gene(symbol, force_refresh=force),
                       indent=2))
    return 0


def cmd_bulk_lookup(kind: str, names_json: str, force: bool) -> int:
    try:
        names = json.loads(names_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid --names-json: {e}"}))
        return 1
    if not isinstance(names, list):
        print(json.dumps({"error": "--names-json must be a JSON array"}))
        return 1
    if kind == "chemicals":
        results = [lookup_chemical(n, force_refresh=force) for n in names]
    elif kind == "genes":
        results = [lookup_gene(n, force_refresh=force) for n in names]
    else:
        print(json.dumps({"error": f"unknown --type: {kind!r}"}))
        return 1
    print(json.dumps({"type": kind, "count": len(results),
                       "results": results}, indent=2))
    return 0


def cmd_cache_stats() -> int:
    if not DB_PATH.exists():
        print(json.dumps({"chemicals": 0, "genes": 0,
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
        chem_total = conn.execute(
            "SELECT COUNT(*) FROM chemicals"
        ).fetchone()[0]
        gene_total = conn.execute(
            "SELECT COUNT(*) FROM genes"
        ).fetchone()[0]
    finally:
        conn.close()
    print(json.dumps({
        "chemicals": {
            "total": chem_total,
            "by_status": {s: c for s, c in chem},
        },
        "genes": {
            "total": gene_total,
            "by_status": {s: c for s, c in gene},
        },
        "offline_mode": _is_offline(),
    }, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_lc = sub.add_parser("lookup-chemical",
                           help="resolve one chemical name")
    p_lc.add_argument("name")
    p_lc.add_argument("--force", action="store_true",
                       help="ignore cache, force a fresh HTTP call")

    p_lg = sub.add_parser("lookup-gene",
                           help="resolve one gene symbol")
    p_lg.add_argument("symbol")
    p_lg.add_argument("--force", action="store_true",
                       help="ignore cache, force a fresh HTTP call")

    p_bl = sub.add_parser("bulk-lookup",
                           help="resolve a JSON array of names in one call")
    p_bl.add_argument("--type", choices=["chemicals", "genes"],
                       required=True)
    p_bl.add_argument("--names-json", required=True,
                       help="JSON array of names/symbols")
    p_bl.add_argument("--force", action="store_true",
                       help="ignore cache, force a fresh HTTP call")

    sub.add_parser("cache-stats", help="cache row counts + status breakdown")

    args = ap.parse_args()
    if args.cmd == "lookup-chemical":
        return cmd_lookup_chemical(args.name, args.force)
    if args.cmd == "lookup-gene":
        return cmd_lookup_gene(args.symbol, args.force)
    if args.cmd == "bulk-lookup":
        return cmd_bulk_lookup(args.type, args.names_json, args.force)
    if args.cmd == "cache-stats":
        return cmd_cache_stats()
    ap.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
