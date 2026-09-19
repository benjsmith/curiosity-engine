"""workspace_registry.py — CE-owned workspace registry persistence.

Charter: shells (Switchbay/okbay) still own tab chrome and may keep their
own XDG registries. CE persists a sibling registry so split targets are
recorded even when no shell is seated, and so shells can sync from
``GET /api/workspaces``.

Storage (first hit wins for reads; writes go to the resolved path):
  1. ``$CE_WORKSPACE_REGISTRY`` — explicit file path
  2. ``$XDG_CONFIG_HOME/curiosity-engine/workspaces.json``
  3. ``~/.config/curiosity-engine/workspaces.json``

Shape (``ce-workspace-registry/1``)::

    {
      "schema": "ce-workspace-registry/1",
      "paths": ["/abs/a", "/abs/b"],
      "active": "/abs/a" | null,
      "splits": [
        {"name": "child", "source": "/abs/a", "target": "/abs/b",
         "stamp": "...", "registered_at": "..."}
      ]
    }

Sandbox: paths must resolve under ``$HOME`` (or ``$CE_WORKSPACE_HOME`` when
set). Entries outside the sandbox are dropped on load.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


SCHEMA = "ce-workspace-registry/1"


class RegistryError(Exception):
    """User-visible registry failure."""


def _home_root() -> Path:
    override = (os.environ.get("CE_WORKSPACE_HOME") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home().resolve()


def is_within_sandbox(path: Path) -> bool:
    try:
        target = Path(path).expanduser().resolve()
        target.relative_to(_home_root())
        return True
    except (ValueError, OSError):
        return False


def registry_path() -> Path:
    override = (os.environ.get("CE_WORKSPACE_REGISTRY") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "curiosity-engine" / "workspaces.json").resolve()


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "paths": [], "active": None, "splits": []}


def load() -> dict[str, Any]:
    p = registry_path()
    if not p.is_file():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    raw_paths = data.get("paths") or []
    if not isinstance(raw_paths, list):
        raw_paths = []
    safe_paths: list[str] = []
    for entry in raw_paths:
        s = str(entry)
        if is_within_sandbox(Path(s)):
            safe_paths.append(str(Path(s).expanduser().resolve()))
    # de-dupe preserving order
    seen: set[str] = set()
    paths: list[str] = []
    for s in safe_paths:
        if s not in seen:
            seen.add(s)
            paths.append(s)
    active = data.get("active")
    active_s = str(active) if active else None
    if active_s and not is_within_sandbox(Path(active_s)):
        active_s = paths[0] if paths else None
    elif active_s:
        active_s = str(Path(active_s).expanduser().resolve())
    splits_raw = data.get("splits") or []
    splits: list[dict[str, Any]] = []
    if isinstance(splits_raw, list):
        for item in splits_raw:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or "")
            if not target or not is_within_sandbox(Path(target)):
                continue
            splits.append(
                {
                    "name": str(item.get("name") or Path(target).name),
                    "source": str(item.get("source") or ""),
                    "target": str(Path(target).expanduser().resolve()),
                    "stamp": str(item.get("stamp") or ""),
                    "registered_at": str(item.get("registered_at") or ""),
                }
            )
    return {
        "schema": SCHEMA,
        "paths": paths,
        "active": active_s,
        "splits": splits,
    }


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".workspaces-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(data: dict[str, Any]) -> None:
    body = {
        "schema": SCHEMA,
        "paths": list(data.get("paths") or []),
        "active": data.get("active"),
        "splits": list(data.get("splits") or []),
    }
    _atomic_write(registry_path(), body)


def register(
    path: Path,
    *,
    set_active: bool = False,
    split: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add ``path`` to the registry. Optionally record a split provenance row."""
    if not is_within_sandbox(path):
        raise RegistryError(
            f"workspaces must live inside {_home_root()}; refusing {path}"
        )
    resolved = Path(path).expanduser().resolve()
    data = load()
    s = str(resolved)
    if s not in data["paths"]:
        data["paths"].append(s)
    if set_active:
        data["active"] = s
    if split:
        row = {
            "name": str(split.get("name") or resolved.name),
            "source": str(split.get("source") or ""),
            "target": s,
            "stamp": str(split.get("stamp") or ""),
            "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # Replace prior row for same target.
        data["splits"] = [
            r for r in data["splits"] if str(r.get("target")) != s
        ]
        data["splits"].append(row)
    save(data)
    return data


def unregister(path: Path) -> dict[str, Any]:
    resolved = str(Path(path).expanduser().resolve())
    data = load()
    data["paths"] = [p for p in data["paths"] if p != resolved]
    data["splits"] = [r for r in data["splits"] if str(r.get("target")) != resolved]
    if data.get("active") == resolved:
        data["active"] = data["paths"][0] if data["paths"] else None
    save(data)
    return data


def payload() -> dict[str, Any]:
    """JSON body for GET /api/workspaces."""
    data = load()
    return {
        "ok": True,
        "schema": data["schema"],
        "path": str(registry_path()),
        "paths": data["paths"],
        "active": data["active"],
        "splits": data["splits"],
    }
