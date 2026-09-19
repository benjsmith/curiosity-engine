"""Phase 2b++: CE filebrowser FS mutate sandbox + pack routes."""
from __future__ import annotations

import json
import os
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
import filebrowser_packs  # noqa: E402
import viewer_server  # noqa: E402


def _ws_tree() -> Path:
    td = tempfile.mkdtemp()
    ws = Path(td)
    (ws / "wiki" / "notes").mkdir(parents=True)
    (ws / "vault" / "raw").mkdir(parents=True)
    (ws / "wiki" / "notes" / "a.md").write_text("# A\n")
    (ws / "vault" / "raw" / "src.pdf").write_bytes(b"%PDF")
    return ws


class ResolveSandbox(unittest.TestCase):
    def test_allows_vault_wiki(self):
        ws = _ws_tree()
        p = filebrowser_fs.resolve(ws, "wiki/notes/a.md", must_exist=True)
        self.assertTrue(p.is_file())
        p2 = filebrowser_fs.resolve(ws, "vault/raw/src.pdf", must_exist=True)
        self.assertTrue(p2.is_file())

    def test_rejects_escape_dotdot(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, "wiki/../vault/raw/src.pdf")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, "wiki/notes/../../etc/passwd")

    def test_rejects_absolute_and_null(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, "/etc/passwd")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, "wiki/notes/\x00a.md")

    def test_rejects_outside_roots(self):
        ws = _ws_tree()
        (ws / "other.txt").write_text("x\n")
        (ws / ".curator").mkdir()
        (ws / ".curator" / "log.md").write_text("secret\n")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, "other.txt")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, ".curator/log.md")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, "wiki/.deleted/x.md")

    def test_rejects_symlink_escape(self):
        ws = _ws_tree()
        outside = Path(tempfile.mkdtemp()) / "secret.txt"
        outside.write_text("leak\n")
        link = ws / "wiki" / "notes" / "escape.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink not permitted")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.resolve(ws, "wiki/notes/escape.md", must_exist=True)


class MutateOps(unittest.TestCase):
    def test_create_rename_move_delete_duplicate(self):
        ws = _ws_tree()
        created = filebrowser_fs.create(
            ws, "wiki/notes/b.md", kind="file", content="# B"
        )
        self.assertEqual(created, "wiki/notes/b.md")
        self.assertTrue((ws / "wiki" / "notes" / "b.md").is_file())

        renamed = filebrowser_fs.rename(ws, "wiki/notes/b.md", "wiki/notes/c.md")
        self.assertEqual(renamed, "wiki/notes/c.md")
        self.assertFalse((ws / "wiki" / "notes" / "b.md").exists())

        filebrowser_fs.mkdir(ws, "wiki/notes/subdir")
        moved = filebrowser_fs.move(ws, "wiki/notes/c.md", "wiki/notes/subdir")
        self.assertEqual(moved, "wiki/notes/subdir/c.md")

        dup = filebrowser_fs.duplicate(ws, "wiki/notes/subdir/c.md")
        self.assertEqual(dup, "wiki/notes/subdir/c copy.md")
        self.assertTrue((ws / "wiki" / "notes" / "subdir" / "c copy.md").is_file())

        trash = filebrowser_fs.delete(ws, "wiki/notes/subdir/c copy.md")
        self.assertTrue("trash" in trash.lower() or trash == "the system Trash")
        self.assertFalse((ws / "wiki" / "notes" / "subdir" / "c copy.md").exists())

    def test_create_refuses_escape_dest(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.create(ws, "wiki/notes/../../outside.md")
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.rename(
                ws, "wiki/notes/a.md", "wiki/notes/../../outside.md"
            )

    def test_delete_empty_dir(self):
        ws = _ws_tree()
        filebrowser_fs.mkdir(ws, "vault/raw/empty")
        filebrowser_fs.delete(ws, "vault/raw/empty")
        self.assertFalse((ws / "vault" / "raw" / "empty").exists())

    def test_delete_nonempty_dir_refused(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_fs.FileOpError):
            filebrowser_fs.delete(ws, "vault/raw")


class PackRoutes(unittest.TestCase):
    def test_discovers_workbench_pack(self):
        ws = _ws_tree()
        pack = ws / ".workbench" / "packs" / "demo"
        pack.mkdir(parents=True)
        (pack / "pack.json").write_text(
            json.dumps(
                {
                    "name": "demo",
                    "version": "0.0.1",
                    "file_routes": [
                        {
                            "ext": "pptx",
                            "action": "open-deck",
                            "label": "Open deck",
                            "endpoint": "/api/demo/run",
                            "primary": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        payload = filebrowser_packs.file_routes_payload(ws)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        r = payload["routes"][0]
        self.assertEqual(r["ext"], ".pptx")
        self.assertEqual(r["pack"], "demo")
        self.assertEqual(r["action"], "open-deck")
        self.assertTrue(r.get("primary"))

    def test_empty_without_packs(self):
        ws = _ws_tree()
        payload = filebrowser_packs.file_routes_payload(ws)
        self.assertEqual(payload["routes"], [])


class FsApi(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)

    def _start(self, ws: Path):
        bundle = ws / "bundle"
        bundle.mkdir()
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

    def _post(self, port: int, path: str, body: dict, prefix: str = ""):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{prefix}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_api_create_and_sandbox_under_embed(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        ws = _ws_tree()
        httpd, port = self._start(ws)
        try:
            status, body = self._post(
                port,
                "/api/fs/create",
                {"path": "wiki/notes/new.md", "kind": "file", "content": "# N"},
                prefix="/embed/ce",
            )
            self.assertEqual(status, 200, body)
            self.assertTrue(body["ok"])
            self.assertEqual(body["path"], "wiki/notes/new.md")
            self.assertTrue((ws / "wiki" / "notes" / "new.md").is_file())

            # Escape attempt
            status2, body2 = self._post(
                port,
                "/api/fs/create",
                {"path": "wiki/../../etc/passwd", "kind": "file"},
                prefix="/embed/ce",
            )
            self.assertEqual(status2, 400)
            self.assertIn("error", body2)
            self.assertFalse(Path("/etc/passwd.ce-test").exists())

            # file-routes under prefix
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/embed/ce/api/file-routes", timeout=2
            ) as resp:
                routes = json.loads(resp.read().decode())
            self.assertTrue(routes["ok"])
            self.assertEqual(routes["routes"], [])

            # delete
            status3, body3 = self._post(
                port,
                "/api/fs/delete",
                {"path": "wiki/notes/new.md"},
                prefix="/embed/ce",
            )
            self.assertEqual(status3, 200, body3)
            self.assertTrue(body3["ok"])
            self.assertFalse((ws / "wiki" / "notes" / "new.md").exists())
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
