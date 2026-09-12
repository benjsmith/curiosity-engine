#!/usr/bin/env python3
"""pack_vault_shards.py — pack vault/*.extracted.md into zip shards for the viewer.

Large wikis served as static Pages (or offline bundles) cannot expose the
live `GET /api/vault/<name>` endpoint. The viewer chrome (vault.js) then
falls back to `vault/manifest.json.gz` + `vault/shard-NN.zip`.

This script builds those artifacts under the viewer bundle's `vault/`
directory (or a chosen --output-dir). It is optional — local
`viewer.sh serve` prefers the API and does not need shards.

Usage
─────
    # From a workspace root (writes into the cache wiki-view bundle):
    uv run python3 <skill>/scripts/pack_vault_shards.py

    # Explicit paths:
    uv run python3 <skill>/scripts/pack_vault_shards.py \\
        --vault vault --output-dir ~/.cache/curiosity-engine/wiki-view/<ws>/vault

    # Invoke after viewer.sh build for large vaults:
    bash <skill>/scripts/viewer.sh build
    uv run python3 <skill>/scripts/pack_vault_shards.py

Manifest schema
───────────────
    {
      "version": 1,
      "files": { "<basename>.extracted.md": <shard_id:int>, ... }
    }

Shard files are named shard-00.zip, shard-01.zip, … (zero-padded).
Each zip contains the extracted markdown files at the archive root
(basename only). Default ~32 MiB uncompressed per shard.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import zipfile
from pathlib import Path

DEFAULT_SHARD_BYTES = 32 * 1024 * 1024


def pack(vault_dir: Path, output_dir: Path, shard_bytes: int) -> dict:
    files = sorted(vault_dir.glob("*.extracted.md"))
    if not files:
        print(f"no *.extracted.md under {vault_dir}", file=sys.stderr)
        return {"version": 1, "files": {}}

    output_dir.mkdir(parents=True, exist_ok=True)
    # Clear previous shards so a shrink does not leave orphans.
    for old in output_dir.glob("shard-*.zip"):
        old.unlink()
    man_path = output_dir / "manifest.json.gz"
    if man_path.exists():
        man_path.unlink()

    mapping: dict[str, int] = {}
    shard_id = 0
    current: list[Path] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal shard_id, current, current_bytes
        if not current:
            return
        name = f"shard-{shard_id:02d}.zip"
        zpath = output_dir / name
        with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in current:
                zf.write(f, arcname=f.name)
                mapping[f.name] = shard_id
        print(f"  wrote {name} ({len(current)} files, ~{current_bytes // 1024} KiB raw)")
        shard_id += 1
        current = []
        current_bytes = 0

    print(f"packing {len(files)} extractions from {vault_dir} → {output_dir}")
    for f in files:
        size = f.stat().st_size
        if current and current_bytes + size > shard_bytes:
            flush()
        current.append(f)
        current_bytes += size
    flush()

    manifest = {"version": 1, "files": mapping}
    raw = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    with gzip.open(man_path, "wb") as gz:
        gz.write(raw)
    print(f"  wrote manifest.json.gz ({len(mapping)} entries, {shard_id} shards)")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--vault",
        type=Path,
        default=Path("vault"),
        help="workspace vault/ directory (default: ./vault)",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="where to write shard-*.zip + manifest.json.gz "
             "(default: ~/.cache/curiosity-engine/wiki-view/<cwd-name>/vault)",
    )
    ap.add_argument(
        "--shard-bytes",
        type=int,
        default=DEFAULT_SHARD_BYTES,
        help=f"approx uncompressed bytes per shard (default {DEFAULT_SHARD_BYTES})",
    )
    args = ap.parse_args()
    vault = args.vault.resolve()
    if not vault.is_dir():
        print(f"vault dir missing: {vault}", file=sys.stderr)
        sys.exit(1)
    out = args.output_dir
    if out is None:
        ws = Path.cwd().name
        out = Path.home() / ".cache" / "curiosity-engine" / "wiki-view" / ws / "vault"
    else:
        out = out.resolve()
    pack(vault, out, args.shard_bytes)


if __name__ == "__main__":
    main()
