"""Reveal-in-OS + drop-ingest + git pack install (Switchbay parity slice)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import filebrowser_fs  # noqa: E402
import filebrowser_ingest  # noqa: E402
import filebrowser_packs  # noqa: E402
import viewer_server  # noqa: E402


def _ws_tree() -> Path:
    td = tempfile.mkdtemp()
    ws = Path(td)
    (ws / "wiki" / "notes").mkdir(parents=True)
    (ws / "vault" / "raw").mkdir(parents=True)
    (ws / "wiki" / "notes" / "a.md").write_text("# A\n")
    (ws / "vault" / "raw" / "src.pdf").write_bytes(b"%PDF-1.4")
    return ws


class RevealSandbox(unittest.TestCase):
    def test_reveal_allows_vault_wiki(self):
        ws = _ws_tree()
        # Do not require a real desktop opener — patch subprocess.run.
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))

            class R:
                returncode = 0

            return R()

        real = filebrowser_fs.subprocess.run
        filebrowser_fs.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            out = filebrowser_fs.reveal(ws, "wiki/notes/a.md")
            self.assertTrue(out["revealed"])
            self.assertTrue(calls)
            out2 = filebrowser_fs.open_external(ws, "vault/raw/src.pdf")
            self.assertTrue(out2["opened"])
        finally:
            filebrowser_fs.subprocess.run = real  # type: ignore[assignment]

    def test_reveal_rejects_escape(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.reveal(ws, "wiki/../vault/raw/src.pdf")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.reveal(ws, "../../etc/passwd")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.reveal(ws, "/etc/passwd")
        (ws / "other.txt").write_text("x\n")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.reveal(ws, "other.txt")


class IngestAllowlist(unittest.TestCase):
    def test_stage_happy_path(self):
        ws = _ws_tree()
        result = filebrowser_ingest.stage_bytes(ws, "paper.pdf", b"%PDF-ok")
        self.assertTrue(result["ok"])
        self.assertTrue(result["run_id"].startswith("run-"))
        self.assertEqual(result["vault_path"], "vault/raw/paper.pdf")
        self.assertTrue((ws / "vault" / "raw" / "paper.pdf").is_file())
        run = ws / ".workbench" / "ingest-runs" / f"{result['run_id']}.json"
        self.assertTrue(run.is_file())
        rec = json.loads(run.read_text())
        self.assertEqual(rec["kind"], "ingest-upload")
        self.assertEqual(rec["status"], "queued")

    def test_stage_unique_collision(self):
        ws = _ws_tree()
        filebrowser_ingest.stage_bytes(ws, "note.md", b"# one\n")
        r2 = filebrowser_ingest.stage_bytes(ws, "note.md", b"# two\n")
        self.assertEqual(r2["filename"], "note-2.md")
        self.assertTrue((ws / "vault" / "raw" / "note-2.md").is_file())

    def test_rejects_extension_outside_allowlist(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_ingest.IngestError) as ctx:
            filebrowser_ingest.stage_bytes(ws, "evil.exe", b"MZ")
        self.assertIn("allowlisted", str(ctx.exception))
        with self.assertRaises(filebrowser_ingest.IngestError):
            filebrowser_ingest.stage_bytes(ws, "noext", b"x")
        self.assertFalse((ws / "vault" / "raw" / "evil.exe").exists())

    def test_rejects_hidden_and_pathful_names(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_ingest.IngestError):
            filebrowser_ingest.stage_bytes(ws, ".hidden.md", b"x")
        # Path components are stripped to basename (upload parity).
        r = filebrowser_ingest.stage_bytes(ws, "subdir/ok.md", b"# ok\n")
        self.assertEqual(r["filename"], "ok.md")
        with self.assertRaises(filebrowser_ingest.IngestError):
            filebrowser_ingest.stage_bytes(ws, "bad\x00.md", b"x")

    def test_rejects_oversized(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_ingest.IngestError) as ctx:
            filebrowser_ingest.stage_bytes(
                ws, "big.pdf", b"x" * (filebrowser_ingest.MAX_BYTES + 1)
            )
        self.assertEqual(ctx.exception.status, 413)


class GitPackInstall(unittest.TestCase):
    def test_looks_like_git_url(self):
        self.assertTrue(filebrowser_packs.looks_like_git_url("https://github.com/a/b.git"))
        self.assertTrue(filebrowser_packs.looks_like_git_url("git@github.com:a/b.git"))
        self.assertFalse(filebrowser_packs.looks_like_git_url("/abs/path/pack"))
        self.assertFalse(filebrowser_packs.looks_like_git_url("relative/pack"))

    def test_install_from_git_local_repo(self):
        """Refuse file:// and absolute paths; happy path via stubbed git clone."""
        ws = _ws_tree()
        src = Path(tempfile.mkdtemp()) / "demo"
        src.mkdir()
        (src / "pack.json").write_text(
            json.dumps(
                {
                    "name": "demo",
                    "version": "0.0.1",
                    "file_routes": [
                        {"ext": "pptx", "action": "open-deck", "label": "Open"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        # Refuse file:// and absolute paths through git entrypoint.
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.install_from_git(ws, f"file://{src}")
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.install_from_git(ws, str(src))

        # Happy path: stub git clone → copytree.
        real_run = filebrowser_packs.subprocess.run

        def fake_run(argv, **kwargs):
            # argv: git clone --depth 1 <url> <target>
            target = Path(argv[-1])
            import shutil

            shutil.copytree(src, target)

            class R:
                returncode = 0
                stderr = b""

            return R()

        filebrowser_packs.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            result = filebrowser_packs.install_from_git(
                ws, "https://example.com/org/demo.git"
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["pack"]["name"], "demo")
            self.assertEqual(result.get("source"), "git")
            self.assertTrue(
                (ws / ".workbench" / "packs" / "demo" / "pack.json").is_file()
            )
        finally:
            filebrowser_packs.subprocess.run = real_run  # type: ignore[assignment]

    def test_install_from_git_real_local_clone(self):
        """If git is available, clone a real on-disk repo via file-protocol
        is refused — use a local path install instead. Here we create a
        git repo and clone it using a ``git`` URL that points at the
        directory: many gits accept absolute paths as clone sources, but
        our API refuses them; so we only assert the stubbed path above
        unless we can use a remote. Skip if no git.
        """
        if not subprocess.run(
            ["git", "--version"], capture_output=True
        ).returncode == 0:
            self.skipTest("git not available")
        ws = _ws_tree()
        src = Path(tempfile.mkdtemp()) / "demopack"
        src.mkdir()
        (src / "pack.json").write_text(
            json.dumps({"name": "demopack", "version": "0.1.0", "file_routes": []}),
            encoding="utf-8",
        )
        subprocess.run(["git", "init"], cwd=src, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=src, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True, capture_output=True)
        subprocess.run(["git", "add", "pack.json"], cwd=src, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=src, check=True, capture_output=True
        )
        # Absolute path must go through install_from_path, not git.
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.install_from_git(ws, str(src))
        # Path install still works.
        result = filebrowser_packs.install_from_path(ws, src)
        self.assertEqual(result["pack"]["name"], "demopack")


class ApiRevealIngest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)

    def _start(self, ws: Path):
        bundle = ws / "bundle"
        bundle.mkdir(exist_ok=True)
        (bundle / "index.html").write_text(
            "<html><head></head><body>ce</body></html>", encoding="utf-8"
        )
        viewer_server.BUNDLE_DIR = bundle.resolve()
        viewer_server.WORKSPACE_DIR = ws.resolve()
        viewer_server.WIKI_DIR = (ws / "wiki").resolve()
        viewer_server.VAULT_DIR = (ws / "vault").resolve()
        viewer_server.VAULT_RAW_DIR = (ws / "vault" / "raw").resolve()
        httpd = viewer_server.ReusableThreadingServer(
            ("127.0.0.1", 0), viewer_server.Handler
        )
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, port

    def _post_json(self, port, path, body, prefix=""):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{prefix}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def _post_multipart(self, port, path, filename, payload, prefix=""):
        boundary = "----CeTestBoundary7a3"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{prefix}{path}",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_reveal_api_sandbox(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        ws = _ws_tree()
        httpd, port = self._start(ws)
        # Stub OS opener for the server process path.
        real = filebrowser_fs.subprocess.run

        def fake_run(argv, **kwargs):
            class R:
                returncode = 0

            return R()

        filebrowser_fs.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            status, body = self._post_json(
                port,
                "/api/fs/reveal",
                {"path": "wiki/notes/a.md"},
                prefix="/embed/ce",
            )
            self.assertEqual(status, 200, body)
            self.assertTrue(body.get("ok"))
            status2, body2 = self._post_json(
                port,
                "/api/fs/reveal",
                {"path": "wiki/../../etc/passwd"},
                prefix="/embed/ce",
            )
            self.assertEqual(status2, 400)
            self.assertIn("error", body2)
        finally:
            filebrowser_fs.subprocess.run = real  # type: ignore[assignment]
            httpd.shutdown()
            httpd.server_close()

    def test_ingest_upload_api(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        ws = _ws_tree()
        httpd, port = self._start(ws)
        try:
            status, body = self._post_multipart(
                port,
                "/api/ingest/from-upload",
                "clip.md",
                b"# hello vault\n",
                prefix="/embed/ce",
            )
            self.assertEqual(status, 200, body)
            self.assertTrue(body["ok"])
            self.assertEqual(body["vault_path"], "vault/raw/clip.md")
            self.assertTrue((ws / "vault" / "raw" / "clip.md").is_file())

            status2, body2 = self._post_multipart(
                port,
                "/api/ingest/from-upload",
                "malware.exe",
                b"MZ",
                prefix="/embed/ce",
            )
            self.assertEqual(status2, 400)
            self.assertIn("allowlisted", body2["error"])
            self.assertFalse((ws / "vault" / "raw" / "malware.exe").exists())
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
