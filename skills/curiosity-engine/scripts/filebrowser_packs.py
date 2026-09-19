"""CE pack discovery + sandboxed action dispatch (Phase 2b++++).

Switchbay owns the full packstore (git install, pip extras, agent skill
dispatch via skillkit). CE mirrors the **same** ``pack.json`` Manifest
shape — ``file_routes``, enable state, local-path install — so the
filebrowser can list and dispatch named actions under the vault/wiki
sandbox without inventing a parallel pack format.

Search order (first wins per pack name):
  1. ``<workspace>/.workbench/packs/<name>/pack.json`` (Switchbay workspace)
  2. ``<workspace>/packs/<name>/pack.json`` (optional CE-local)
  3. ``$CE_PACKS_DIR/<name>/pack.json`` when set

Enable state: ``<workspace>/.workbench/packs-state.json`` (Switchbay-
compatible: name → bool; missing key = enabled).

Dispatch (``POST /api/packs/<pack>/action/<action>`` with ``{path}``):
  · path must resolve under vault/|wiki/ (filebrowser_fs sandbox)
  · pack must be discovered + enabled
  · action must appear in that pack's ``file_routes``
  · skill ``{pack}-{action}`` preferred at ``<pack>/skills/<skill>/SKILL.md``
    (Switchbay skillkit layout); falls back to ``skills/<action>/``
  · queues a run under ``.workbench/pack-runs/<run_id>.json`` and returns
    ``{run_id, pack, action}`` (Switchbay-compatible). Agent/LLM execution
    stays shell-side — CE accepts + sandboxes the request.

Install: local-path copy into ``.workbench/packs/`` only (no git clone).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import filebrowser_fs

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ACTION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
PACK_FILE = "pack.json"
_STATE_FILE = "packs-state.json"
_RUNS_DIR = "pack-runs"


class PackError(Exception):
    """User-visible pack failure (not found, disabled, bad path, etc.)."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


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


def _workspace_state_path(workspace: Path) -> Path:
    return Path(workspace).resolve() / ".workbench" / _STATE_FILE


def _load_state(workspace: Path) -> dict[str, Any]:
    p = _workspace_state_path(workspace)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(workspace: Path, state: dict[str, Any]) -> None:
    p = _workspace_state_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def _is_enabled(workspace: Path, name: str) -> bool:
    state = _load_state(workspace)
    if name in state:
        return bool(state[name])
    return True


def _read_manifest(pack_dir: Path) -> dict[str, Any] | None:
    pf = pack_dir / PACK_FILE
    if not pf.is_file():
        return None
    try:
        raw = json.loads(pf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _pack_record(pack_dir: Path, scope: str, workspace: Path) -> dict[str, Any] | None:
    raw = _read_manifest(pack_dir)
    if raw is None:
        return None
    name = str(raw.get("name") or pack_dir.name).strip() or pack_dir.name
    if not _NAME_RE.match(name):
        return None
    routes = _parse_file_routes(raw, pack=name, scope=scope)
    skills_raw = raw.get("skills") or []
    skills = [str(s) for s in skills_raw if isinstance(s, str)]
    return {
        "name": name,
        "version": str(raw.get("version") or "").strip() or "0.0.0",
        "description": str(raw.get("description") or ""),
        "skills": skills,
        "file_routes": routes,
        "actions": sorted({r["action"] for r in routes}),
        "scope": scope,
        "path": str(pack_dir.resolve()),
        "enabled": _is_enabled(workspace, name),
        "requires_extra": [
            str(e) for e in (raw.get("requires_extra") or []) if isinstance(e, str)
        ],
    }


def list_packs(workspace: Path) -> list[dict[str, Any]]:
    """Every discovered pack (workspace first; first name wins)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
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
            rec = _pack_record(child, scope, workspace)
            if rec is None:
                continue
            seen.add(name)
            out.append(rec)
    return out


def get_pack(workspace: Path, name: str) -> dict[str, Any] | None:
    if not _NAME_RE.match(name):
        return None
    for root, scope in _iter_pack_dirs(workspace):
        d = root / name
        if d.is_dir():
            rec = _pack_record(d, scope, workspace)
            if rec is not None:
                return rec
    return None


def packs_payload(workspace: Path) -> dict[str, Any]:
    packs = list_packs(workspace)
    return {"ok": True, "packs": packs, "count": len(packs)}


def actions_for_pack(workspace: Path, name: str) -> dict[str, Any]:
    rec = get_pack(workspace, name)
    if rec is None:
        raise PackError(f"pack not found: {name}", status=404)
    routes = list(rec.get("file_routes") or [])
    return {
        "ok": True,
        "pack": rec["name"],
        "enabled": bool(rec.get("enabled", True)),
        "actions": list(rec.get("actions") or []),
        "routes": routes,
        "count": len(routes),
    }


def file_routes_for(workspace: Path) -> list[dict[str, Any]]:
    """Flatten file_routes from enabled packs only (Switchbay parity)."""
    routes: list[dict[str, Any]] = []
    for pack in list_packs(workspace):
        if not pack.get("enabled", True):
            continue
        routes.extend(pack.get("file_routes") or [])
    return routes


def file_routes_payload(workspace: Path) -> dict[str, Any]:
    routes = file_routes_for(workspace)
    return {"ok": True, "routes": routes, "count": len(routes)}


def set_enabled(workspace: Path, name: str, enabled: bool) -> dict[str, Any]:
    if not _NAME_RE.match(name):
        raise PackError(f"invalid pack name: {name!r}")
    rec = get_pack(workspace, name)
    if rec is None:
        raise PackError(f"pack not found: {name}", status=404)
    state = _load_state(workspace)
    state[name] = bool(enabled)
    _save_state(workspace, state)
    updated = get_pack(workspace, name)
    return {"ok": True, "pack": updated}


def install_from_path(workspace: Path, src: str | Path) -> dict[str, Any]:
    """Copy a local pack directory into ``.workbench/packs/<name>/``.

    Source must be an absolute path to a directory with a valid pack.json.
    Refuses to install from inside the destination tree (escape / nest).
    """
    ws = Path(workspace).resolve()
    src_path = Path(src).expanduser()
    if not src_path.is_absolute():
        raise PackError("install source must be an absolute path")
    try:
        src_path = src_path.resolve()
    except (OSError, ValueError) as e:
        raise PackError(f"resolve failed: {e}") from e
    if not src_path.is_dir():
        raise PackError(f"not a directory: {src_path}")
    raw = _read_manifest(src_path)
    if raw is None:
        raise PackError(f"{src_path} has no valid {PACK_FILE}")
    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise PackError(f"manifest name invalid: {name!r}")
    dest_root = ws / ".workbench" / "packs"
    dest_root.mkdir(parents=True, exist_ok=True)
    dest_root_res = dest_root.resolve()
    # Refuse installing from inside dest (or the workspace packs tree).
    try:
        src_path.relative_to(dest_root_res)
        raise PackError("refusing to install from inside the packs destination")
    except ValueError:
        pass
    target = dest_root / name
    if target.exists():
        raise PackError(f"pack {name!r} already installed in workspace")
    # Ensure target stays under dest_root after resolve.
    if dest_root_res not in target.resolve().parents and target.resolve() != dest_root_res / name:
        # name is validated by _NAME_RE so this is belt-and-brace
        if not str(target.resolve()).startswith(str(dest_root_res) + os.sep):
            raise PackError("refusing to install outside packs dir")
    shutil.copytree(src_path, target)
    rec = get_pack(ws, name)
    if rec is None:
        raise PackError("install succeeded but pack not discoverable")
    return {"ok": True, "pack": rec}


def uninstall_pack(workspace: Path, name: str) -> dict[str, Any]:
    if not _NAME_RE.match(name):
        raise PackError(f"invalid pack name: {name!r}")
    ws = Path(workspace).resolve()
    dest_root = (ws / ".workbench" / "packs").resolve()
    target = dest_root / name
    if not target.exists():
        raise PackError(f"pack not found in workspace scope: {name}", status=404)
    if dest_root not in target.resolve().parents:
        raise PackError("refusing to delete outside the pack scope dir")
    shutil.rmtree(target)
    return {"ok": True, "uninstalled": name}


def _find_skill(pack_dir: Path, pack: str, action: str) -> dict[str, Any] | None:
    """Locate ``{pack}-{action}`` skill (Switchbay layout), then ``{action}``."""
    skills_root = pack_dir / "skills"
    if not skills_root.is_dir():
        return None
    candidates = [f"{pack}-{action}", action]
    for skill_name in candidates:
        skill_dir = skills_root / skill_name
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            try:
                body = skill_md.read_text(encoding="utf-8")
            except OSError:
                body = ""
            return {
                "name": skill_name,
                "path": str(skill_dir.resolve()),
                "body_chars": len(body),
            }
    return None


def _route_for_action(rec: dict[str, Any], action: str) -> dict[str, Any] | None:
    for r in rec.get("file_routes") or []:
        if isinstance(r, dict) and r.get("action") == action:
            return r
    return None


def dispatch_action(
    workspace: Path,
    pack: str,
    action: str,
    path: str,
) -> dict[str, Any]:
    """Sandbox-validate and queue a pack file-route action.

    Returns Switchbay-shaped ``{run_id, pack, action}`` plus CE fields
    (``status``, ``skill``, ``path``). Does **not** spawn an LLM agent.
    """
    if not _NAME_RE.match(pack):
        raise PackError(f"invalid pack name: {pack!r}")
    if not _ACTION_RE.match(action):
        raise PackError(f"invalid action name: {action!r}")
    rel = (path or "").strip()
    if not rel:
        raise PackError("path required")

    rec = get_pack(workspace, pack)
    if rec is None:
        raise PackError(f"pack not found: {pack}", status=404)
    if not rec.get("enabled", True):
        raise PackError(
            f"pack '{pack}' is not active — enable it via POST /api/packs/toggle",
            status=409,
        )

    route = _route_for_action(rec, action)
    if route is None:
        raise PackError(
            f"action {action!r} not declared in pack {pack!r} file_routes",
            status=404,
        )

    # Sandbox: vault/wiki only; must be an existing file.
    try:
        target = filebrowser_fs.resolve(workspace, rel, must_exist=True)
    except filebrowser_fs.FileOpError as e:
        raise PackError(str(e), status=400) from e
    if not target.is_file():
        raise PackError("path must be a file", status=400)

    pack_dir = Path(str(rec.get("path") or ""))
    skill = _find_skill(pack_dir, pack, action) if pack_dir.is_dir() else None

    run_id = f"run-{uuid.uuid4().hex[:8]}"
    norm_path = rel.replace("\\", "/").lstrip("./").strip("/")
    while norm_path.startswith("./"):
        norm_path = norm_path[2:]
    run_rec: dict[str, Any] = {
        "run_id": run_id,
        "pack": pack,
        "action": action,
        "path": norm_path,
        "status": "queued",
        "kind": f"pack:{pack}:{action}",
        "skill": skill,
        "route": {
            k: route[k]
            for k in ("ext", "action", "label", "endpoint", "tab_kind")
            if k in route
        },
        "mode": "skill_queue" if skill else "accepted",
        "note": (
            "CE queued the pack action under workspace sandbox; "
            "agent/LLM skill execution remains Switchbay-side."
        ),
    }

    runs_dir = Path(workspace).resolve() / ".workbench" / _RUNS_DIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = runs_dir / f"{run_id}.json"
    run_path.write_text(json.dumps(run_rec, indent=2) + "\n", encoding="utf-8")

    return {
        "run_id": run_id,
        "pack": pack,
        "action": action,
        "status": run_rec["status"],
        "path": run_rec["path"],
        "skill": skill["name"] if skill else None,
        "mode": run_rec["mode"],
    }
