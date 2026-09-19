"""CE pack file-routes discovery (Phase 2b++ stub).

Switchbay aggregates Manifest.file_routes from installed packs via
``/api/file-routes``. CE does not own pack install / enable / action
dispatch (shell-side). This module only *reads* ``pack.json`` manifests
from well-known workspace locations so the CE filebrowser (and later
Switchbay embed clients) can list extension handlers.

Search order (first wins per pack name):
  1. ``<workspace>/.workbench/packs/<name>/pack.json`` (Switchbay workspace)
  2. ``<workspace>/packs/<name>/pack.json`` (optional CE-local)
  3. ``$CE_PACKS_DIR/<name>/pack.json`` when set

No network, no git clone, no enable toggles — discovery only.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PACK_FILE = "pack.json"


def _parse_file_routes(raw: dict[str, Any], *, pack: str, scope: str) -> list[dict[str, Any]]:
    routes_raw = raw.get("file_routes") or []
    out: list[dict[str, Any]] = []
    if not isinstance(routes_raw, list):
        return out
    for r in routes_raw:
        if not isinstance(r, dict):
            continue
        ext = str(r.get("ext") or "").strip().lower()
        action = str(r.get("action") or "").strip()
        if not ext or not action:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        entry: dict[str, Any] = {
            "ext": ext,
            "action": action,
            "pack": pack,
            "scope": scope,
        }
        for k in (
            "label",
            "description",
            "endpoint",
            "tab_kind",
            "selection_kind",
            "requires_binary",
        ):
            v = r.get(k)
            if isinstance(v, str) and v:
                entry[k] = v
        if r.get("primary") is True:
            entry["primary"] = True
        out.append(entry)
    return out


def _iter_pack_dirs(workspace: Path) -> list[tuple[Path, str]]:
    """Return (pack_dir, scope) candidates; workspace overrides CE_PACKS_DIR."""
    ws = Path(workspace).resolve()
    pairs: list[tuple[Path, str]] = [
        (ws / ".workbench" / "packs", "workspace"),
        (ws / "packs", "workspace-ce"),
    ]
    extra = (os.environ.get("CE_PACKS_DIR") or "").strip()
    if extra:
        pairs.append((Path(extra).expanduser().resolve(), "ce-packs"))
    return pairs


def file_routes_for(workspace: Path) -> list[dict[str, Any]]:
    """Flatten file_routes from discovered pack.json manifests."""
    seen: set[str] = set()
    routes: list[dict[str, Any]] = []
    for root, scope in _iter_pack_dirs(workspace):
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            if not _NAME_RE.match(name) or name in seen:
                continue
            pf = child / PACK_FILE
            if not pf.is_file():
                continue
            try:
                raw = json.loads(pf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            manifest_name = str(raw.get("name") or name).strip() or name
            if not _NAME_RE.match(manifest_name):
                continue
            seen.add(name)
            routes.extend(
                _parse_file_routes(raw, pack=manifest_name, scope=scope)
            )
    return routes


def file_routes_payload(workspace: Path) -> dict[str, Any]:
    routes = file_routes_for(workspace)
    return {"ok": True, "routes": routes, "count": len(routes)}
