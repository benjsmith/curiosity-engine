"""CE drop-ingest — stage uploads into vault/ + queue ingest runs.

Mirrors Switchbay ``/api/ingest/from-upload`` semantics at the API layer
so the filebrowser (and Switchbay drag UI via ``/embed/ce/``) can drop
files into the vault without inventing a parallel pipeline.

Staging lands under ``vault/raw/`` (CE drop-folder convention; also what
``local_ingest.py`` drains). Agent/LLM classification stays shell-side —
CE writes ``.workbench/ingest-runs/<run_id>.json`` with status ``queued``
(Switchbay pack-run drain shape) and returns ``{run_id, vault_path, …}``.

Allowlist: ``DEFAULT_EXTS`` from local_ingest (same set the CLI accepts).
Refuse hidden names, path components, null bytes, and payloads over 50 MB.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

# Keep in sync with local_ingest.DEFAULT_EXTS / DEFAULT_MAX_RAW_BYTES.
ALLOWED_EXTS = frozenset({
    ".md", ".txt", ".rst", ".html", ".htm", ".json", ".jsonl", ".yaml",
    ".yml", ".org", ".pdf", ".csv", ".xlsx", ".pptx",
})
MAX_BYTES = 50 * 1024 * 1024
_RUNS_DIR = "ingest-runs"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")


class IngestError(Exception):
    """User-visible ingest failure (bad name, ext, size, escape)."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


def safe_filename(name: str) -> str:
    """Basename-only sanitise; refuse hidden / empty / null."""
    raw = (name or "").strip().replace("\\", "/")
    if "\x00" in raw:
        raise IngestError("invalid filename")
    base = Path(raw).name
    base = re.sub(r"[^A-Za-z0-9._+-]+", "_", base)
    if not base or base.startswith(".") or not _SAFE_NAME.match(base):
        raise IngestError("invalid filename")
    if ".." in base:
        raise IngestError("invalid filename")
    return base


def assert_allowed_ext(filename: str) -> str:
    """Return lowercased suffix including leading ``.``; raise if not allowlisted."""
    suffix = Path(filename).suffix.lower()
    if not suffix or suffix not in ALLOWED_EXTS:
        raise IngestError(
            f"extension not allowlisted: {suffix or '(none)'} "
            f"(allowed: {', '.join(sorted(ALLOWED_EXTS))})",
            status=400,
        )
    return suffix


def _unique_raw_path(raw_dir: Path, filename: str) -> Path:
    candidate = raw_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 2
    while True:
        cand = raw_dir / f"{stem}-{n}{suffix}"
        if not cand.exists():
            return cand
        n += 1


def stage_bytes(workspace: Path, filename: str, data: bytes) -> dict[str, Any]:
    """Validate + write ``data`` under ``vault/raw/<safe>``; queue ingest run.

    Returns Switchbay-shaped ``{ok, run_id, vault_path, filename, size}``.
    """
    if data is None:
        raise IngestError("empty body")
    if len(data) == 0:
        raise IngestError("empty file")
    if len(data) > MAX_BYTES:
        raise IngestError("file too large (>50 MB)", status=413)

    safe = safe_filename(filename)
    assert_allowed_ext(safe)

    ws = Path(workspace).resolve()
    raw_dir = ws / "vault" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    # Belt-and-brace: dest must stay under vault/raw.
    dest = _unique_raw_path(raw_dir, safe)
    try:
        dest_res = dest.resolve()
        dest_res.relative_to((ws / "vault" / "raw").resolve())
    except ValueError as e:
        raise IngestError("path escapes vault/raw") from e
    if any(p.startswith(".") for p in dest.relative_to(ws).parts):
        raise IngestError("refusing hidden path")

    dest.write_bytes(data)
    rel = dest.relative_to(ws).as_posix()
    digest = hashlib.sha1(data).hexdigest()[:12]

    run_id = f"run-{uuid.uuid4().hex[:8]}"
    run_rec: dict[str, Any] = {
        "run_id": run_id,
        "status": "queued",
        "kind": "ingest-upload",
        "vault_path": rel,
        "filename": dest.name,
        "size": len(data),
        "sha1_12": digest,
        "mode": "staged",
        "note": (
            "CE staged the upload under vault/raw/; "
            "agent/LLM ingest classification remains shell-side "
            "(or run local_ingest.py on the drop folder)."
        ),
    }
    runs_dir = ws / ".workbench" / _RUNS_DIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.json").write_text(
        json.dumps(run_rec, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "ok": True,
        "run_id": run_id,
        "vault_path": rel,
        "filename": dest.name,
        "size": len(data),
        "status": "queued",
    }


def stage_path(workspace: Path, src: str | Path) -> dict[str, Any]:
    """Stage an already-on-disk file (absolute path) into vault/raw/.

    Used by ``POST /api/ingest/from-path``. Source must be a real file;
    contents are copied (not moved) so callers keep the original.
    """
    src_path = Path(src).expanduser()
    if not src_path.is_absolute():
        raise IngestError("source must be an absolute path")
    try:
        src_path = src_path.resolve()
    except (OSError, ValueError) as e:
        raise IngestError(f"resolve failed: {e}") from e
    if not src_path.is_file():
        raise IngestError("source must be a file", status=400)
    try:
        data = src_path.read_bytes()
    except OSError as e:
        raise IngestError(f"read failed: {e}") from e
    return stage_bytes(workspace, src_path.name, data)
