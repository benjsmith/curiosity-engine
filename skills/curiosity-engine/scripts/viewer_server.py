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
    GET  /api/split                     last partition status (workspace split spike)
    POST /api/page                      JSON {path, content} → overwrite file
    POST /api/upload-vault              multipart form → save to vault/raw/
    POST /api/split                     partition wiki pages into a new workspace

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
import wiki_partition

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
        if url.path == "/api/split":
            return self._handle_get_split()
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
