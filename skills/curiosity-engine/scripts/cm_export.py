"""cm_export.py — invoke curiosity-merge ``subgraph_export`` for a page set.

CE's native ``wiki_partition`` remains the default split path (no CM
dependency). This module is the Switchbay-parity *export* hook: when a
shell (or the CE viewer) wants CM license modes / preflight / manifest
shape, it calls ``POST /api/cm-export``.

Discovery order for the CM skill root:
  1. ``$CE_CM_ROOT`` / ``$CURIOSITY_MERGE_ROOT`` / ``$SWITCHBAY_CM_ROOT``
  2. Sibling checkout ``<ce-repo>/../curiosity-merge`` (dev)
  3. ``~/.claude/skills/curiosity-merge``
  4. ``~/.agents/skills/curiosity-merge`` (npx skills layout)

Sandbox:
  * ``--to`` must resolve **outside** the source workspace
  * ``--to`` must resolve under ``$HOME`` (or ``$CE_WORKSPACE_HOME``)
  * page refs validated via ``wiki_partition.validate_refs``

``dry_run=True`` validates + returns the planned argv **without** spawning
CM (and without requiring CM to be installed — discovery still runs; a
missing CM skill yields a clear error so callers can surface install
guidance).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import wiki_partition
import workspace_registry


class CmExportError(Exception):
    """User-visible CM export failure."""


_EXPORT_TIMEOUT = 900

_INCLUDE_VAULT = frozenset({"none", "owned", "all"})


def discover_cm_root() -> Path:
    candidates: list[Path] = []
    for key in ("CE_CM_ROOT", "CURIOSITY_MERGE_ROOT", "SWITCHBAY_CM_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())
    # Dev sibling: .../repos/curiosity-engine → .../repos/curiosity-merge
    here = Path(__file__).resolve()
    # skills/curiosity-engine/scripts → repo root is parents[3]
    try:
        repo_root = here.parents[3]
        candidates.append(repo_root.parent / "curiosity-merge")
    except IndexError:
        pass
    candidates.append(Path.home() / ".claude" / "skills" / "curiosity-merge")
    candidates.append(Path.home() / ".agents" / "skills" / "curiosity-merge")
    for c in candidates:
        script = c / "scripts" / "subgraph_export.py"
        if script.is_file():
            return c.resolve()
    raise CmExportError(
        "curiosity-merge skill not found — set CE_CM_ROOT to the skill "
        "root (must contain scripts/subgraph_export.py), or install via "
        "`npx skills add -g -y benjsmith/curiosity-merge`"
    )


def subgraph_export_script(cm_root: Path | None = None) -> Path:
    root = cm_root or discover_cm_root()
    script = root / "scripts" / "subgraph_export.py"
    if not script.is_file():
        raise CmExportError(f"subgraph_export.py missing under {root}")
    return script


def _ce_scripts_dir() -> Path:
    return Path(__file__).resolve().parent


def _safe_export_target(workspace: Path, target: Path) -> Path:
    """Refuse exports inside the source workspace or outside the sandbox."""
    ws = workspace.resolve()
    dest = target.expanduser().resolve()
    if ".." in Path(str(target)).parts:
        raise CmExportError(f"refusing path with .. segments: {target}")
    try:
        dest.relative_to(ws)
        raise CmExportError(f"refusing to write export inside workspace: {dest}")
    except ValueError:
        pass
    if dest == ws:
        raise CmExportError("target cannot be the source workspace")
    if not workspace_registry.is_within_sandbox(dest):
        raise CmExportError(
            f"export target must live inside {workspace_registry._home_root()}; "
            f"refusing {dest}"
        )
    return dest


def plan_export(
    workspace: Path,
    target: Path,
    pages: list[str],
    *,
    include_vault: str = "all",
    include_non_native: bool = True,
    no_preflight: bool = True,
    force: bool = True,
    label: str | None = None,
    cm_root: Path | None = None,
) -> dict[str, Any]:
    """Validate inputs and build the CM argv. Does not spawn.

    Returns a plan dict with ``argv``, ``cwd``, ``env`` extras, ``pages``,
    ``target``, ``cm_root``, ``script``.
    """
    workspace = workspace.resolve()
    if not (workspace / "wiki").is_dir():
        raise CmExportError(f"workspace has no wiki/: {workspace}")
    refs = list(dict.fromkeys(str(r).strip() for r in pages if str(r).strip()))
    if not refs:
        raise CmExportError("nothing selected")
    missing = wiki_partition.validate_refs(workspace, refs)
    if missing:
        raise CmExportError("not wiki pages: " + ", ".join(missing[:8]))
    vault_mode = (include_vault or "all").strip().lower()
    if vault_mode not in _INCLUDE_VAULT:
        raise CmExportError(
            f"include_vault must be one of {sorted(_INCLUDE_VAULT)}; got {vault_mode!r}"
        )
    dest = _safe_export_target(workspace, target)
    root = cm_root or discover_cm_root()
    script = subgraph_export_script(root)
    # pages-file path is filled in at execute time (temp file); plan uses a
    # placeholder token so dry-run JSON stays stable.
    argv = [
        sys.executable,
        str(script),
        "--pages-file",
        "{pages_file}",
        "--to",
        str(dest),
        "--workspace",
        str(workspace),
        "--include-vault",
        vault_mode,
    ]
    if include_non_native:
        argv.append("--include-non-native")
    if no_preflight:
        argv.append("--no-preflight")
    if force:
        argv.append("--force")
    if label:
        argv.extend(["--label", str(label)])
    return {
        "ok": True,
        "dry_run": True,
        "pages": refs,
        "target": str(dest),
        "cm_root": str(root),
        "script": str(script),
        "include_vault": vault_mode,
        "include_non_native": include_non_native,
        "argv": argv,
        "cwd": str(workspace),
        "ce_scripts": str(_ce_scripts_dir()),
    }


def run_export(
    workspace: Path,
    target: Path,
    pages: list[str],
    *,
    include_vault: str = "all",
    include_non_native: bool = True,
    no_preflight: bool = True,
    force: bool = True,
    label: str | None = None,
    cm_root: Path | None = None,
    dry_run: bool = False,
    register: bool = True,
    timeout: int = _EXPORT_TIMEOUT,
) -> dict[str, Any]:
    """Plan (and optionally execute) a CM subgraph export.

    When ``dry_run`` is True, returns the plan without spawning. When
    ``register`` is True and the export succeeds, the target is added to
    the CE workspace registry.
    """
    plan = plan_export(
        workspace,
        target,
        pages,
        include_vault=include_vault,
        include_non_native=include_non_native,
        no_preflight=no_preflight,
        force=force,
        label=label,
        cm_root=cm_root,
    )
    if dry_run:
        return plan

    dest = Path(plan["target"])
    if dest.exists() and any(dest.iterdir()) and not force:
        raise CmExportError(f"target already exists: {dest}")

    pages_file: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(plan["pages"], f)
            pages_file = f.name
        argv = [
            (pages_file if tok == "{pages_file}" else tok) for tok in plan["argv"]
        ]
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH"}
        }
        env["CURIOSITY_ENGINE_SCRIPTS_DIR"] = plan["ce_scripts"]
        try:
            proc = subprocess.run(
                argv,
                cwd=plan["cwd"],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise CmExportError(f"CM subgraph_export timed out after {timeout}s") from e
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            tail = "\n".join(out.strip().splitlines()[-12:])
            raise CmExportError(
                f"subgraph export failed (rc={proc.returncode}):\n{tail}"
            )
        result = {
            "ok": True,
            "dry_run": False,
            "pages": plan["pages"],
            "target": plan["target"],
            "cm_root": plan["cm_root"],
            "include_vault": plan["include_vault"],
            "exported": len(plan["pages"]),
            "log_tail": "\n".join(out.strip().splitlines()[-20:]),
            "stamp": time.strftime("%Y%m%d-%H%M%S"),
        }
        if register:
            try:
                workspace_registry.register(
                    dest,
                    set_active=False,
                    split={
                        "name": dest.name,
                        "source": str(workspace.resolve()),
                        "target": str(dest),
                        "stamp": result["stamp"],
                    },
                )
                result["registered"] = True
            except workspace_registry.RegistryError as e:
                result["registered"] = False
                result["register_error"] = str(e)
        return result
    finally:
        if pages_file:
            try:
                os.unlink(pages_file)
            except OSError:
                pass
