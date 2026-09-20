"""Obsidian vault import: path sandbox, zip extract, wikilink preservation."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import obsidian_import  # noqa: E402
import viewer_server  # noqa: E402


def _make_vault(root: Path) -> Path:
    vault = root / "obsidian-vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text(
        '{"legacyEditor": false, "baseFontSize": 16}\n', encoding="utf-8"
    )
    (vault / "Home.md").write_text(
        "# Home\n\nWelcome. See [[Projects]] and [[Daily/2026-09-20|today]].\n",
        encoding="utf-8",
    )
    (vault / "Projects.md").write_text(
        "# Projects\n\nBack to [[Home]].\n",
        encoding="utf-8",
    )
    daily = vault / "Daily"
    daily.mkdir()
    (daily / "2026-09-20.md").write_text(
        "# 2026-09-20\n\nLinked from [[Home]].\n",
        encoding="utf-8",
    )
    (vault / ".trash").mkdir()
    (vault / ".trash" / "gone.md").write_text("nope\n", encoding="utf-8")
    return vault


def _ws(root: Path) -> Path:
    ws = root / "workspace"
    ws.mkdir()
    (ws / "wiki").mkdir()
    (ws / "vault" / "raw").mkdir(parents=True)
    return ws


class PathSandbox(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        os.environ["CE_WORKSPACE_HOME"] = str(self.root)
        import importlib
        import workspace_registry
        importlib.reload(workspace_registry)
        importlib.reload(obsidian_import)

    def tearDown(self):
        self._td.cleanup()
        os.environ.pop("CE_WORKSPACE_HOME", None)

    def test_absolute_under_sandbox_ok(self):
        vault = _make_vault(self.root)
        p = obsidian_import.resolve_source_path(str(vault))
        self.assertEqual(p, vault.resolve())

    def test_relative_rejected(self):
        with self.assertRaises(obsidian_import.ObsidianImportError):
            obsidian_import.resolve_source_path("relative/vault")

    def test_escape_outside_sandbox_rejected(self):
        with self.assertRaises(obsidian_import.ObsidianImportError) as ctx:
            obsidian_import.resolve_source_path("/etc/passwd")
        self.assertEqual(ctx.exception.status, 403)


class ZipExtract(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        os.environ["CE_WORKSPACE_HOME"] = str(self.root)
        import importlib
        import workspace_registry
        importlib.reload(workspace_registry)
        importlib.reload(obsidian_import)

    def tearDown(self):
        self._td.cleanup()
        os.environ.pop("CE_WORKSPACE_HOME", None)

    def test_happy_zip_extract(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("vault/Note.md", "# Note\n[[Other]]\n")
            zf.writestr("vault/Other.md", "# Other\n")
            zf.writestr("vault/.obsidian/app.json", "{}\n")
        dest = self.root / "extract"
        root = obsidian_import.extract_zip_bytes(buf.getvalue(), dest)
        self.assertTrue((root / "Note.md").is_file() or (root / "vault" / "Note.md").is_file())

    def test_zip_slip_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.md", "x")
        with self.assertRaises(obsidian_import.ObsidianImportError) as ctx:
            obsidian_import.extract_zip_bytes(buf.getvalue(), self.root / "slip")
        self.assertIn("slip", str(ctx.exception).lower())


class WikilinkPresence(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        os.environ["CE_WORKSPACE_HOME"] = str(self.root)
        import importlib
        import workspace_registry
        importlib.reload(workspace_registry)
        importlib.reload(obsidian_import)

    def tearDown(self):
        self._td.cleanup()
        os.environ.pop("CE_WORKSPACE_HOME", None)

    def test_wikilinks_survive_folder_import(self):
        vault = _make_vault(self.root)
        ws = _ws(self.root)
        rec = obsidian_import.import_vault(str(vault), workspace=ws)
        self.assertTrue(rec["ok"])
        self.assertTrue(rec["wikilinks_preserved"])
        self.assertGreaterEqual(rec["wikilink_occurrences"], 3)
        home = (ws / "wiki" / "Home.md").read_text(encoding="utf-8")
        self.assertIn("[[Projects]]", home)
        self.assertIn("[[Daily/2026-09-20|today]]", home)
        self.assertFalse((ws / "wiki" / ".trash").exists())
        self.assertTrue((ws / "wiki" / ".obsidian" / "app.json").is_file())
        self.assertEqual(rec["mapping"], "obsidian-vault-root → wiki/")

    def test_wikilinks_survive_zip_import(self):
        vault = _make_vault(self.root)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for f in vault.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(vault).as_posix())
        ws = _ws(self.root)
        rec = obsidian_import.import_vault(
            workspace=ws,
            zip_bytes=buf.getvalue(),
            zip_filename="dummy.zip",
        )
        self.assertTrue(rec["ok"])
        self.assertIn("[[Home]]", (ws / "wiki" / "Projects.md").read_text())


class ApiImport(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        os.environ["CE_WORKSPACE_HOME"] = str(self.root)
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        import importlib
        import workspace_registry
        importlib.reload(workspace_registry)
        importlib.reload(obsidian_import)
        importlib.reload(viewer_server)
        self.vault = _make_vault(self.root)
        self.ws = _ws(self.root)
        bundle = self.ws / "bundle"
        bundle.mkdir()
        (bundle / "index.html").write_text(
            "<html><head></head><body>ce</body></html>", encoding="utf-8"
        )
        viewer_server.BUNDLE_DIR = bundle.resolve()
        viewer_server.WORKSPACE_DIR = self.ws.resolve()
        viewer_server.WIKI_DIR = (self.ws / "wiki").resolve()
        viewer_server.VAULT_DIR = (self.ws / "vault").resolve()
        viewer_server.VAULT_RAW_DIR = (self.ws / "vault" / "raw").resolve()
        self.httpd = viewer_server.ReusableThreadingServer(
            ("127.0.0.1", 0), viewer_server.Handler
        )
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._td.cleanup()
        os.environ.pop("CE_WORKSPACE_HOME", None)
        os.environ.pop("CE_PUBLIC_BASE", None)

    def _post_json(self, path, body, prefix="/embed/ce"):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{prefix}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_api_path_import_under_public_base(self):
        status, body = self._post_json(
            "/api/import/obsidian",
            {"path": str(self.vault)},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertIn("[[Projects]]", (self.ws / "wiki" / "Home.md").read_text())

    def test_api_rejects_escape(self):
        status, body = self._post_json(
            "/api/import/obsidian",
            {"path": "/etc/passwd"},
        )
        self.assertEqual(status, 403)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
