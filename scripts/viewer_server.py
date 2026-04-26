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
    POST /api/page                      JSON {path, content} → overwrite file
    POST /api/upload-vault              multipart form → save to vault/raw/

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
import json
import re
import socketserver
import subprocess
import sys
import urllib.parse
from email.parser import BytesParser
from email.policy import default as default_email_policy
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

BUNDLE_DIR: Path | None = None
WORKSPACE_DIR: Path | None = None
WIKI_DIR: Path | None = None
VAULT_RAW_DIR: Path | None = None


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


class Handler(http.server.SimpleHTTPRequestHandler):
    # Quiet down — default httpd logging is noisy.
    def log_message(self, fmt, *args):
        pass

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BUNDLE_DIR), **kwargs)

    # ── routing ────────────────────────────────────────────────────
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/page":
            return self._handle_get_page(urllib.parse.parse_qs(url.query))
        return super().do_GET()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/page":
            return self._handle_post_page()
        if url.path == "/api/upload-vault":
            return self._handle_upload()
        return self._json(404, {"error": "not found"})

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
    def _handle_get_page(self, qs: dict) -> None:
        rel = (qs.get("path") or [""])[0]
        try:
            p = _safe_wiki_path(rel)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        if not p.exists():
            return self._json(404, {"error": "page missing"})
        return self._json(200, {"path": rel, "content": p.read_text()})

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
    global BUNDLE_DIR, WORKSPACE_DIR, WIKI_DIR, VAULT_RAW_DIR
    if len(sys.argv) < 4:
        print("usage: viewer_server.py <bundle_dir> <workspace_dir> <port>",
              file=sys.stderr)
        sys.exit(2)
    BUNDLE_DIR = Path(sys.argv[1]).resolve()
    WORKSPACE_DIR = Path(sys.argv[2]).resolve()
    WIKI_DIR = WORKSPACE_DIR / "wiki"
    VAULT_RAW_DIR = WORKSPACE_DIR / "vault" / "raw"
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

    sys.stderr.write(
        f"viewer-server: serving {BUNDLE_DIR} on http://127.0.0.1:{port}\n"
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
