#!/usr/bin/env python3
"""viewer_server.py — minimal HTTP server for the curiosity-engine wiki viewer.

Serves the static bundle from `~/.cache/curiosity-engine/wiki-view/<workspace>/`
and exposes three small write endpoints. Stdlib-only; uses
ThreadingHTTPServer so concurrent fetches (data.json + assets + a
write request) don't queue.

Endpoints
─────────
    GET  /                              static file (anything in the bundle)
    GET  /api/page?path=<path>          raw markdown of a wiki page
    GET  /api/vault/<name>              vault/*.extracted.md basenames only
    GET  /api/hosted                    hosted-mode stub (host=switchbay|okbay)
    GET  /api/tree                      vault/ + wiki/ relative paths (filebrowser)
    GET  /api/file-routes               pack Manifest.file_routes (enabled packs)
    GET  /api/packs                     list discovered packs + actions
    GET  /api/packs/<name>/actions      file_routes / actions for one pack
    POST /api/packs/<pack>/action/<act> dispatch named action {path} (sandbox queue)
    POST /api/packs/toggle              {name, enabled} workspace enable state
    POST /api/packs/install             {path|source|url} local or git → .workbench/packs/
    DELETE /api/packs?name=             uninstall workspace-scope pack
    DELETE /api/workspaces?path=        unregister workspace path
    GET  /api/fs/stat?path=             sandbox stat under vault/|wiki/
    POST /api/fs/reveal                 {path} reveal-in-OS (Finder/xdg/explorer)
    POST /api/fs/open-external          {path} open with OS default app
    POST /api/ingest/from-upload        multipart file → vault/raw/ + queue run
    POST /api/ingest/from-path          {path} absolute file → vault/raw/ + queue
    GET  /api/split                     last partition status (workspace split spike)
    GET  /api/workspaces                CE workspace registry (paths + split provenance)
    GET  /api/curation/history          HistoryDoc for graph replay UI
    POST /api/page                      JSON {path, content} → overwrite file
    POST /api/upload-vault              multipart form → save to vault/raw/
    POST /api/split                     partition wiki pages into a new workspace
    POST /api/workspaces                register path {path, set_active?, split?}
    POST /api/cm-export                 CM subgraph_export hook {pages, target, dry_run?}
    POST /api/fs/create                 {path, kind?, content?} create file/dir
    POST /api/fs/mkdir                  {path} create empty directory
    POST /api/fs/rename                 {path, to} rename within sandbox
    POST /api/fs/move                   {path, to} move within sandbox
    POST /api/fs/delete                 {path} trash (OS or .workbench/trash)
    POST /api/fs/duplicate              {path} sibling copy (Switchbay parity)

Proxy / embed (Phase 2a)
────────────────────────
    Optional ``CE_PUBLIC_BASE`` (e.g. ``/embed/ce``) strips that prefix from
    request paths and injects ``window.CE_PUBLIC_BASE`` / ``ceApi()`` into
    HTML so absolute ``/api/*`` fetches stay same-origin under a reverse
    proxy. Hosted shell: header ``X-CE-Host`` or ``?host=switchbay|okbay``.
    Always binds ``127.0.0.1`` (loopback). Bare ``viewer.sh`` default port
    remains 8090; embed hosts commonly use **8766**.

Writes are constrained:
    * /api/page only accepts paths that start with `notes/` or `todos/`
      and end in `.md`. The path is resolved + checked to ensure it
      stays inside `wiki/`.
    * /api/upload-vault sanitises the filename (strips directories,
      replaces non-alnum chars with `_`) before writing to vault/raw/.
    * /api/fs/* mutates only under `vault/` + `wiki/` (see filebrowser_fs.py);
      escapes, hidden components, and SKIP_DIRS are refused. Delete goes to
      OS trash when available, else `.workbench/trash/`.
    * /api/packs/*/action/* requires the target file under vault/|wiki/;
      install copies or `git clone --depth 1` into `.workbench/packs/`.
      Agent/LLM skill execution stays Switchbay-side — CE queues the run.
    * /api/fs/reveal|open-external only resolve vault/|wiki/ paths.
    * /api/ingest/from-upload|from-path allowlist DEFAULT_EXTS, stage under
      vault/raw/, queue `.workbench/ingest-runs/` (shell drains / local_ingest).

After any successful write the server invokes
`wiki_render.py build <wiki_dir> --output-dir <bundle_dir>` so the
served data.json reflects the change. Re-builds are typically <1s
for ~300-page wikis; the response only returns once the rebuild
completes, so the frontend can re-fetch data.json synchronously.

Localhost-only binding (single-user assumption — no auth).

Usage
─────
    viewer_server.py <bundle_dir> <workspace_dir> <port>

Invoked by viewer.sh; not intended to be called by hand.
"""

from __future__ import annotations

import http.server
import os
import json
import re
import socketserver
import subprocess
import sys
import urllib.parse
from email.parser import BytesParser
from email.policy import default as default_email_policy
from pathlib import Path

import public_base
import filebrowser_tree
import filebrowser_fs
import filebrowser_packs
import filebrowser_ingest
import wiki_partition
import workspace_registry
import cm_export
import curation_history

SCRIPT_DIR = Path(__file__).resolve().parent

BUNDLE_DIR: Path | None = None
WORKSPACE_DIR: Path | None = None
WIKI_DIR: Path | None = None
VAULT_DIR: Path | None = None
VAULT_RAW_DIR: Path | None = None
SPLIT_LAST: dict | None = None


def _safe_wiki_path(rel: str) -> Path:
    """Resolve a wiki-relative path and refuse anything outside wiki/.
    Only `notes/` and `todos/` subtrees may be edited via the server."""
    if not rel or rel.endswith("/") or "\\" in rel:
        raise ValueError("invalid path")
    # Reject .. directly — Path.resolve() would also catch escapes via the
    # relative_to check, but cheaper and clearer to refuse upfront.
    if ".." in Path(rel).parts:
        raise ValueError("path may not contain ..")
    candidate = (WIKI_DIR / rel).resolve()
    try:
        rel_to_wiki = candidate.relative_to(WIKI_DIR.resolve())
    except ValueError:
        raise ValueError("path escapes wiki/")
    parts = rel_to_wiki.parts
    if not parts or parts[0] not in ("notes", "todos"):
        raise ValueError("only notes/ and todos/ pages are editable")
    if candidate.suffix != ".md":
        raise ValueError("only .md files are editable")
    return candidate


def _safe_vault_filename(name: str) -> str:
    """Drop directory components and sanitise unusual chars for vault/raw/."""
    name = Path(name).name
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    if not name or name.startswith("."):
        raise ValueError("invalid filename")
    return name


_EXTRACTED_RE = re.compile(r"^[A-Za-z0-9._-]+\.extracted\.md$")


def _safe_extracted_basename(name: str) -> str:
    """Only allow vault/*.extracted.md basenames (no path components)."""
    name = Path(name).name
    if not _EXTRACTED_RE.match(name):
        raise ValueError("only *.extracted.md basenames are served")
    return name


class Handler(http.server.SimpleHTTPRequestHandler):
    # Quiet down — default httpd logging is noisy.
    def log_message(self, fmt, *args):
        pass

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BUNDLE_DIR), **kwargs)

    def _rewrite_path(self) -> urllib.parse.ParseResult:
        """Strip CE_PUBLIC_BASE so routing + static map to the bundle root."""
        url = urllib.parse.urlparse(self.path)
        stripped = public_base.strip_public_base(url.path)
        if stripped != url.path:
            self.path = urllib.parse.urlunparse(
                (url.scheme, url.netloc, stripped, url.params, url.query, url.fragment)
            )
            url = urllib.parse.urlparse(self.path)
        return url

    def _hosted(self, url: urllib.parse.ParseResult) -> str | None:
        return public_base.hosted_shell_from_request(
            self.headers, urllib.parse.parse_qs(url.query)
        )

    # ── routing ────────────────────────────────────────────────────
    def do_GET(self):
        url = self._rewrite_path()
        if url.path == "/api/page":
            return self._handle_get_page(urllib.parse.parse_qs(url.query))
        if url.path.startswith("/api/vault/"):
            return self._handle_get_vault(url.path[len("/api/vault/"):])
        if url.path == "/api/hosted":
            return self._json(200, public_base.hosted_settings_policy(self._hosted(url)))
        if url.path == "/api/tree":
            return self._handle_get_tree()
        if url.path == "/api/file-routes":
            return self._handle_get_file_routes()
        if url.path == "/api/packs":
            return self._handle_get_packs()
        m_actions = self._match_pack_actions(url.path)
        if m_actions is not None:
            return self._handle_get_pack_actions(m_actions)
        if url.path == "/api/fs/stat":
            return self._handle_fs_stat(urllib.parse.parse_qs(url.query))
        if url.path == "/api/split":
            return self._handle_get_split()
        if url.path == "/api/workspaces":
            return self._handle_get_workspaces()
        if url.path == "/api/curation/history":
            return self._handle_get_curation_history()
        # Inject embed bootstrap into HTML so /api fetches honor CE_PUBLIC_BASE.
        if self._looks_like_html(url.path):
            return self._serve_html_with_bootstrap(url)
        return super().do_GET()

    def do_POST(self):
        url = self._rewrite_path()
        if url.path == "/api/page":
            return self._handle_post_page()
        if url.path == "/api/upload-vault":
            return self._handle_upload()
        if url.path == "/api/split":
            return self._handle_post_split()
        if url.path == "/api/workspaces":
            return self._handle_post_workspaces()
        if url.path == "/api/cm-export":
            return self._handle_post_cm_export()
        if url.path == "/api/fs/create":
            return self._handle_fs_create()
        if url.path == "/api/fs/mkdir":
            return self._handle_fs_mkdir()
        if url.path == "/api/fs/rename":
            return self._handle_fs_rename()
        if url.path == "/api/fs/move":
            return self._handle_fs_move()
        if url.path == "/api/fs/delete":
            return self._handle_fs_delete()
        if url.path == "/api/fs/duplicate":
            return self._handle_fs_duplicate()
        if url.path == "/api/fs/reveal":
            return self._handle_fs_reveal()
        if url.path == "/api/fs/open-external":
            return self._handle_fs_open_external()
        if url.path == "/api/ingest/from-upload":
            return self._handle_ingest_from_upload()
        if url.path == "/api/ingest/from-path":
            return self._handle_ingest_from_path()
        if url.path == "/api/packs/toggle":
            return self._handle_packs_toggle()
        if url.path == "/api/packs/install":
            return self._handle_packs_install()
        m_action = self._match_pack_action(url.path)
        if m_action is not None:
            return self._handle_pack_action(m_action[0], m_action[1])
        return self._json(404, {"error": "not found"})

    def do_DELETE(self):
        url = self._rewrite_path()
        if url.path == "/api/workspaces":
            return self._handle_delete_workspaces(urllib.parse.parse_qs(url.query))
        if url.path == "/api/packs":
            return self._handle_packs_uninstall(urllib.parse.parse_qs(url.query))
        return self._json(404, {"error": "not found"})

    def _looks_like_html(self, path: str) -> bool:
        p = path.rstrip("/") or "/"
        if p == "/" or p.endswith(".html") or p.endswith(".htm"):
            return True
        return False

    def _serve_html_with_bootstrap(self, url: urllib.parse.ParseResult) -> None:
        # Resolve like SimpleHTTPRequestHandler: "" / "/" → index.html
        rel = url.path.lstrip("/")
        if not rel or rel.endswith("/"):
            candidate = BUNDLE_DIR / rel / "index.html"
        else:
            candidate = BUNDLE_DIR / rel
            if candidate.is_dir():
                candidate = candidate / "index.html"
        try:
            candidate = candidate.resolve()
            candidate.relative_to(BUNDLE_DIR.resolve())
        except Exception:
            return self.send_error(404, "File not found")
        if not candidate.is_file():
            return self.send_error(404, "File not found")
        raw = candidate.read_bytes()
        body = public_base.inject_viewer_bootstrap(raw, hosted=self._hosted(url))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── helpers ────────────────────────────────────────────────────
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            raise ValueError(f"bad json: {e}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _rebuild(self) -> None:
        """Re-render the bundle so data.json reflects the latest write.
        Failures are logged but don't fail the request — the file write
        already succeeded.

        subprocess.run is called with a list-form argv (no shell=True)
        and every argument is hardcoded or derived from
        `Path(__file__).parent` — no command-injection vector. Static
        analyzers flag subprocess.run on sight; this comment documents
        why this call site is safe.
        """
        try:
            subprocess.run(
                [
                    "uv", "run", "python3",
                    str(SCRIPT_DIR / "wiki_render.py"),
                    "build", str(WIKI_DIR),
                    "--output-dir", str(BUNDLE_DIR),
                ],
                check=False, capture_output=True, timeout=30,
            )
        except Exception as e:
            sys.stderr.write(f"viewer-server: rebuild failed: {e}\n")

    # ── handlers ───────────────────────────────────────────────────
    def _handle_get_tree(self) -> None:
        """List vault/ + wiki/ files for the CE filebrowser (Phase 2b)."""
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        return self._json(200, filebrowser_tree.tree_payload(WORKSPACE_DIR))

    def _handle_get_file_routes(self) -> None:
        """Pack file_routes from enabled packs (Phase 2b++++)."""
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        return self._json(200, filebrowser_packs.file_routes_payload(WORKSPACE_DIR))

    def _match_pack_actions(self, path: str) -> str | None:
        """GET /api/packs/<name>/actions → pack name."""
        prefix = "/api/packs/"
        suffix = "/actions"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        mid = path[len(prefix):-len(suffix)]
        if not mid or "/" in mid:
            return None
        return mid

    def _match_pack_action(self, path: str) -> tuple[str, str] | None:
        """POST /api/packs/<pack>/action/<action> → (pack, action)."""
        prefix = "/api/packs/"
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix):]
        parts = rest.split("/")
        if len(parts) != 3 or parts[1] != "action":
            return None
        return parts[0], parts[2]

    def _handle_get_packs(self) -> None:
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        return self._json(200, filebrowser_packs.packs_payload(WORKSPACE_DIR))

    def _handle_get_pack_actions(self, name: str) -> None:
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        try:
            return self._json(200, filebrowser_packs.actions_for_pack(WORKSPACE_DIR, name))
        except filebrowser_packs.PackError as e:
            return self._json(e.status, {"error": str(e)})

    def _handle_pack_action(self, pack: str, action: str) -> None:
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        rel = str(body.get("path") or "").strip()
        try:
            result = filebrowser_packs.dispatch_action(
                WORKSPACE_DIR, pack, action, rel
            )
        except filebrowser_packs.PackError as e:
            return self._json(e.status, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        return self._json(200, result)

    def _handle_packs_toggle(self) -> None:
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        name = str(body.get("name") or "").strip()
        if "enabled" not in body:
            return self._json(400, {"error": "enabled required"})
        enabled = bool(body.get("enabled"))
        try:
            return self._json(
                200, filebrowser_packs.set_enabled(WORKSPACE_DIR, name, enabled)
            )
        except filebrowser_packs.PackError as e:
            return self._json(e.status, {"error": str(e)})

    def _handle_packs_install(self) -> None:
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        src = str(
            body.get("source") or body.get("url") or body.get("path") or ""
        ).strip()
        if not src:
            return self._json(400, {"error": "path or source/url required"})
        try:
            if filebrowser_packs.looks_like_git_url(src):
                result = filebrowser_packs.install_from_git(WORKSPACE_DIR, src)
            else:
                result = filebrowser_packs.install_from_path(WORKSPACE_DIR, src)
            return self._json(200, result)
        except filebrowser_packs.PackError as e:
            return self._json(e.status, {"error": str(e)})

    def _handle_packs_uninstall(self, qs: dict) -> None:
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        name = (qs.get("name") or [""])[0].strip()
        if not name:
            return self._json(400, {"error": "name required"})
        try:
            return self._json(
                200, filebrowser_packs.uninstall_pack(WORKSPACE_DIR, name)
            )
        except filebrowser_packs.PackError as e:
            return self._json(e.status, {"error": str(e)})

    def _fs_workspace(self):
        if WORKSPACE_DIR is None:
            self._json(500, {"error": "workspace unset"})
            return None
        return WORKSPACE_DIR

    def _handle_fs_stat(self, qs: dict) -> None:
        ws = self._fs_workspace()
        if ws is None:
            return
        rel = (qs.get("path") or [""])[0]
        try:
            return self._json(200, filebrowser_fs.stat(ws, rel))
        except filebrowser_fs.FileOpError as e:
            return self._json(400, {"error": str(e)})

    def _fs_mutate_result(self, op: str, fn) -> None:
        """Run a filebrowser_fs mutation; rebuild viewer if wiki/ changed."""
        ws = self._fs_workspace()
        if ws is None:
            return
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        try:
            result = fn(ws, body)
        except filebrowser_fs.FileOpError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        # Rebuild when wiki content may have changed.
        touched = []
        if isinstance(result, dict):
            for key in ("path", "trashed_from"):
                v = result.get(key)
                if isinstance(v, str):
                    touched.append(v)
            src = body.get("path")
            if isinstance(src, str):
                touched.append(src)
        if any(t.replace("\\", "/").startswith("wiki/") for t in touched):
            self._rebuild()
        payload = {"ok": True, **result} if isinstance(result, dict) else {"ok": True}
        return self._json(200, payload)

    def _handle_fs_create(self) -> None:
        def _op(ws, body):
            rel = str(body.get("path") or "")
            kind = str(body.get("kind") or "file")
            content = body.get("content", "")
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise filebrowser_fs.FileOpError("content must be a string")
            path = filebrowser_fs.create(ws, rel, kind=kind, content=content)
            return {"path": path, "op": "create"}
        return self._fs_mutate_result("create", _op)

    def _handle_fs_mkdir(self) -> None:
        def _op(ws, body):
            path = filebrowser_fs.mkdir(ws, str(body.get("path") or ""))
            return {"path": path, "op": "mkdir"}
        return self._fs_mutate_result("mkdir", _op)

    def _handle_fs_rename(self) -> None:
        def _op(ws, body):
            path = filebrowser_fs.rename(
                ws, str(body.get("path") or ""), str(body.get("to") or "")
            )
            return {"path": path, "op": "rename"}
        return self._fs_mutate_result("rename", _op)

    def _handle_fs_move(self) -> None:
        def _op(ws, body):
            path = filebrowser_fs.move(
                ws, str(body.get("path") or ""), str(body.get("to") or "")
            )
            return {"path": path, "op": "move"}
        return self._fs_mutate_result("move", _op)

    def _handle_fs_delete(self) -> None:
        def _op(ws, body):
            rel = str(body.get("path") or "")
            trashed_to = filebrowser_fs.delete(ws, rel)
            return {"trashed_to": trashed_to, "trashed_from": rel, "op": "delete"}
        return self._fs_mutate_result("delete", _op)

    def _handle_fs_duplicate(self) -> None:
        def _op(ws, body):
            path = filebrowser_fs.duplicate(ws, str(body.get("path") or ""))
            return {"path": path, "op": "duplicate"}
        return self._fs_mutate_result("duplicate", _op)


    def _handle_get_curation_history(self) -> None:
        """HistoryDoc for wiki-view replay (CE_PUBLIC_BASE-aware via rewrite)."""
        if WORKSPACE_DIR is None:
            return self._json(
                500,
                {
                    "error": "workspace not configured",
                    "events": [],
                    "duration": 0,
                    "source": "error",
                },
            )
        try:
            doc = curation_history.history_payload(WORKSPACE_DIR, BUNDLE_DIR)
        except Exception as e:
            return self._json(
                500,
                {"error": str(e), "events": [], "duration": 0, "source": "error"},
            )
        return self._json(200, doc)

    def _handle_get_split(self) -> None:
        """Last workspace-partition status (Phase 2b+ split spike)."""
        return self._json(200, {"last": SPLIT_LAST})

    def _handle_post_split(self) -> None:
        """Partition selected wiki pages into a new workspace outside source.

        Body: {target|name, move: [ref], copy: [ref], name?}.
        If ``target`` is omitted, ``CE_SPLIT_HOME/<name>`` is used when set.
        Honors CE_PUBLIC_BASE via path rewrite. Sync for the spike; shells
        may wrap async + workspace registry themselves.
        """
        global SPLIT_LAST
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        if isinstance(SPLIT_LAST, dict) and SPLIT_LAST.get("state") == "running":
            return self._json(409, {"error": "a split is already running"})
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        move = [str(r).strip() for r in (body.get("move") or []) if str(r).strip()]
        copy = [str(r).strip() for r in (body.get("copy") or []) if str(r).strip()]
        if not move and not copy:
            return self._json(400, {"error": "nothing selected"})
        name = wiki_partition.sanitize_name(str(body.get("name") or ""))
        target_raw = str(body.get("target") or "").strip()
        if not target_raw:
            home = (os.environ.get("CE_SPLIT_HOME") or "").strip()
            if not home or not name:
                return self._json(
                    400,
                    {"error": "target required (or set CE_SPLIT_HOME + name)"},
                )
            target_raw = str(Path(home).expanduser() / name)
        elif not name:
            name = wiki_partition.sanitize_name(Path(target_raw).name)
        if not name:
            return self._json(400, {"error": "name required"})
        target = Path(target_raw)
        rec: dict = {
            "state": "running",
            "step": "starting",
            "target": str(target),
            "name": name,
            "error": None,
        }
        SPLIT_LAST = rec

        def _progress(step: str) -> None:
            rec["step"] = step

        try:
            stats = wiki_partition.partition_workspace(
                WORKSPACE_DIR,
                target,
                move,
                copy,
                name=name,
                progress=_progress,
            )
            rec.update(state="done", step="done", **stats)
            # Persist split target in CE workspace registry (shells may also register).
            try:
                workspace_registry.register(
                    Path(stats["target"]),
                    set_active=False,
                    split={
                        "name": stats.get("name") or name,
                        "source": str(WORKSPACE_DIR),
                        "target": stats["target"],
                        "stamp": stats.get("stamp") or "",
                    },
                )
                rec["registered"] = True
                stats = {**stats, "registered": True}
            except workspace_registry.RegistryError as reg_err:
                rec["registered"] = False
                rec["register_error"] = str(reg_err)
                stats = {
                    **stats,
                    "registered": False,
                    "register_error": str(reg_err),
                }
            # Rebuild source viewer if pages were moved.
            if stats.get("moved"):
                self._rebuild()
            return self._json(200, {"ok": True, "started": False, **stats})
        except wiki_partition.PartitionError as e:
            rec.update(state="error", error=str(e), step="error")
            return self._json(400, {"error": str(e)})
        except Exception as e:
            rec.update(state="error", error=str(e), step="error")
            return self._json(500, {"error": str(e)})


    def _handle_get_workspaces(self) -> None:
        """CE workspace registry snapshot (Phase 2b+++++ registry slice)."""
        return self._json(200, workspace_registry.payload())

    def _handle_post_workspaces(self) -> None:
        """Register a workspace path. Body: {path, set_active?, split?}."""
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        raw = str(body.get("path") or body.get("target") or "").strip()
        if not raw:
            return self._json(400, {"error": "path required"})
        set_active = bool(body.get("set_active") or body.get("active"))
        split = body.get("split") if isinstance(body.get("split"), dict) else None
        try:
            data = workspace_registry.register(
                Path(raw), set_active=set_active, split=split
            )
        except workspace_registry.RegistryError as e:
            return self._json(400, {"error": str(e)})
        return self._json(200, {"ok": True, **workspace_registry.payload(), "registered": data["paths"]})

    def _handle_delete_workspaces(self, qs: dict) -> None:
        raw = (qs.get("path") or [""])[0].strip()
        if not raw:
            return self._json(400, {"error": "path required"})
        try:
            workspace_registry.unregister(Path(raw))
        except Exception as e:
            return self._json(400, {"error": str(e)})
        return self._json(200, workspace_registry.payload())

    def _handle_post_cm_export(self) -> None:
        """Invoke curiosity-merge subgraph_export for selected pages.

        Body: {
          pages|move|copy: [ref], target|name, include_vault?, dry_run?,
          include_non_native?, label?, register?
        }.
        When ``target`` is omitted, ``CE_SPLIT_HOME/<name>`` is used.
        ``dry_run: true`` validates + returns planned argv without spawning CM.
        """
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        pages = [str(r).strip() for r in (body.get("pages") or []) if str(r).strip()]
        move = [str(r).strip() for r in (body.get("move") or []) if str(r).strip()]
        copy = [str(r).strip() for r in (body.get("copy") or []) if str(r).strip()]
        pages = list(dict.fromkeys([*pages, *move, *copy]))
        if not pages:
            return self._json(400, {"error": "nothing selected"})
        name = wiki_partition.sanitize_name(str(body.get("name") or ""))
        target_raw = str(body.get("target") or "").strip()
        if not target_raw:
            home = (os.environ.get("CE_SPLIT_HOME") or "").strip()
            if not home or not name:
                return self._json(
                    400,
                    {"error": "target required (or set CE_SPLIT_HOME + name)"},
                )
            target_raw = str(Path(home).expanduser() / name)
        dry_run = bool(body.get("dry_run"))
        include_vault = str(body.get("include_vault") or "all")
        include_non_native = body.get("include_non_native")
        if include_non_native is None:
            include_non_native = True
        register = body.get("register")
        if register is None:
            register = not dry_run
        label = body.get("label")
        label_s = str(label).strip() if label else None
        try:
            result = cm_export.run_export(
                WORKSPACE_DIR,
                Path(target_raw),
                pages,
                include_vault=include_vault,
                include_non_native=bool(include_non_native),
                dry_run=dry_run,
                register=bool(register),
                label=label_s,
            )
        except cm_export.CmExportError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": str(e)})
        return self._json(200, result)

    def _handle_fs_reveal(self) -> None:
        ws = self._fs_workspace()
        if ws is None:
            return
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        rel = str(body.get("path") or "").strip()
        if not rel:
            return self._json(400, {"error": "path required"})
        try:
            result = filebrowser_fs.reveal(ws, rel)
        except filebrowser_fs.FileOpError as e:
            return self._json(400, {"error": str(e)})
        return self._json(200, {"ok": True, **result})

    def _handle_fs_open_external(self) -> None:
        ws = self._fs_workspace()
        if ws is None:
            return
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        rel = str(body.get("path") or "").strip()
        if not rel:
            return self._json(400, {"error": "path required"})
        try:
            result = filebrowser_fs.open_external(ws, rel)
        except filebrowser_fs.FileOpError as e:
            return self._json(400, {"error": str(e)})
        return self._json(200, {"ok": True, **result})

    def _handle_ingest_from_upload(self) -> None:
        """Multipart ``file`` → vault/raw/ + ingest-run queue (Switchbay shape)."""
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "multipart/form-data required"})
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return self._json(400, {"error": "empty body"})
        if length > filebrowser_ingest.MAX_BYTES + 64 * 1024:
            return self._json(413, {"error": "file too large (>50 MB)"})
        body = self.rfile.read(length)
        head = (f"Content-Type: {ctype}\r\n\r\n").encode("ascii")
        msg = BytesParser(policy=default_email_policy).parsebytes(head + body)
        if not msg.is_multipart():
            return self._json(400, {"error": "not multipart"})
        filename = None
        data = None
        for part in msg.iter_parts():
            cd = part.get("Content-Disposition", "")
            if "filename=" not in cd:
                continue
            filename = part.get_filename() or ""
            data = part.get_payload(decode=True)
            if filename and data is not None:
                break
        if not filename or data is None:
            return self._json(400, {"error": "no `file` field"})
        try:
            result = filebrowser_ingest.stage_bytes(WORKSPACE_DIR, filename, data)
        except filebrowser_ingest.IngestError as e:
            return self._json(e.status, {"error": str(e)})
        return self._json(200, result)

    def _handle_ingest_from_path(self) -> None:
        if WORKSPACE_DIR is None:
            return self._json(500, {"error": "workspace unset"})
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        src = str(body.get("path") or "").strip()
        if not src:
            return self._json(400, {"error": "path required"})
        try:
            result = filebrowser_ingest.stage_path(WORKSPACE_DIR, src)
        except filebrowser_ingest.IngestError as e:
            return self._json(e.status, {"error": str(e)})
        return self._json(200, result)

    def _handle_get_page(self, qs: dict) -> None:
        rel = (qs.get("path") or [""])[0]
        try:
            p = _safe_wiki_path(rel)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        if not p.exists():
            return self._json(404, {"error": "page missing"})
        return self._json(200, {"path": rel, "content": p.read_text()})

    def _handle_get_vault(self, raw_name: str) -> None:
        """Serve a single vault/*.extracted.md by basename.

        Used by vault.js when the local viewer_server is up. Static Pages
        bundles fall back to shard zips instead.
        """
        try:
            name = _safe_extracted_basename(urllib.parse.unquote(raw_name))
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        path = (VAULT_DIR / name).resolve()
        try:
            path.relative_to(VAULT_DIR.resolve())
        except ValueError:
            return self._json(404, {"error": "not found"})
        if not path.is_file():
            return self._json(404, {"error": "not found"})
        data = path.read_bytes()
        # Prefer markdown content-type; text/plain is also fine for <pre>.
        ctype = "text/markdown; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_post_page(self) -> None:
        try:
            body = self._read_json_body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        rel = body.get("path", "")
        content = body.get("content", "")
        if not isinstance(content, str):
            return self._json(400, {"error": "content must be a string"})
        try:
            p = _safe_wiki_path(rel)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        if content and not content.endswith("\n"):
            content += "\n"
        p.write_text(content)
        self._rebuild()
        return self._json(200, {"ok": True, "path": rel})

    def _handle_upload(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "multipart/form-data required"})
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return self._json(400, {"error": "empty body"})
        body = self.rfile.read(length)
        head = (f"Content-Type: {ctype}\r\n\r\n").encode("ascii")
        msg = BytesParser(policy=default_email_policy).parsebytes(head + body)
        if not msg.is_multipart():
            return self._json(400, {"error": "not multipart"})
        saved = []
        for part in msg.iter_parts():
            cd = part.get("Content-Disposition", "")
            if "filename=" not in cd:
                continue
            filename = part.get_filename() or ""
            if not filename:
                continue
            try:
                safe = _safe_vault_filename(filename)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            data = part.get_payload(decode=True)
            if data is None:
                continue
            VAULT_RAW_DIR.mkdir(parents=True, exist_ok=True)
            (VAULT_RAW_DIR / safe).write_bytes(data)
            saved.append(safe)
        if not saved:
            return self._json(400, {"error": "no file part with filename"})
        return self._json(200, {"ok": True, "saved": saved})


class ReusableThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    global BUNDLE_DIR, WORKSPACE_DIR, WIKI_DIR, VAULT_DIR, VAULT_RAW_DIR
    if len(sys.argv) < 4:
        print("usage: viewer_server.py <bundle_dir> <workspace_dir> <port>",
              file=sys.stderr)
        sys.exit(2)
    BUNDLE_DIR = Path(sys.argv[1]).resolve()
    WORKSPACE_DIR = Path(sys.argv[2]).resolve()
    WIKI_DIR = WORKSPACE_DIR / "wiki"
    VAULT_DIR = WORKSPACE_DIR / "vault"
    VAULT_RAW_DIR = VAULT_DIR / "raw"
    try:
        port = int(sys.argv[3])
    except ValueError:
        print("port must be an integer", file=sys.stderr)
        sys.exit(2)

    if not BUNDLE_DIR.is_dir():
        print(f"bundle dir missing: {BUNDLE_DIR}", file=sys.stderr)
        sys.exit(1)
    if not WIKI_DIR.is_dir():
        print(f"wiki dir missing: {WIKI_DIR}", file=sys.stderr)
        sys.exit(1)

    base = public_base.public_base()
    sys.stderr.write(
        f"viewer-server: serving {BUNDLE_DIR} on http://127.0.0.1:{port}\n"
    )
    if base:
        sys.stderr.write(
            f"viewer-server: CE_PUBLIC_BASE={base} "
            f"(public URLs under http://127.0.0.1:{port}{base}/)\n"
        )
    sys.stderr.write(
        f"viewer-server: edits go to {WIKI_DIR}, uploads to {VAULT_RAW_DIR}\n"
    )
    httpd = ReusableThreadingServer(("127.0.0.1", port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n")


if __name__ == "__main__":
    main()
