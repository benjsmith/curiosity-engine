"""Phase 2b: CE filebrowser tree API + path matchers."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import filebrowser_match  # noqa: E402
import filebrowser_tree  # noqa: E402
import viewer_server  # noqa: E402


class Matchers(unittest.TestCase):
    def test_empty_matches_all(self):
        m = filebrowser_match.build_matcher("")
        self.assertTrue(m("wiki/a.md"))

    def test_substring(self):
        m = filebrowser_match.build_matcher("Enti")
        self.assertTrue(m("wiki/entities/Foo.md"))
        self.assertFalse(m("vault/raw/x.pdf"))

    def test_glob_ext(self):
        m = filebrowser_match.build_matcher("*.md")
        self.assertTrue(m("wiki/notes/a.md"))
        self.assertFalse(m("vault/raw/a.pdf"))

    def test_regex(self):
        m = filebrowser_match.build_matcher(r"/vault\/raw\/.*\.pdf$/i")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertTrue(m("vault/raw/Paper.PDF"))
        self.assertFalse(m("wiki/notes/a.md"))

    def test_bad_regex(self):
        self.assertIsNone(filebrowser_match.build_matcher("/(/"))

    def test_filter_paths(self):
        paths = ["wiki/a.md", "vault/raw/b.pdf", "wiki/b.md"]
        got, ok = filebrowser_match.filter_paths(paths, "*.md")
        self.assertTrue(ok)
        self.assertEqual(got, ["wiki/a.md", "wiki/b.md"])


class TreeWalk(unittest.TestCase):
    def test_vault_wiki_only(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "wiki" / "entities").mkdir(parents=True)
            (ws / "wiki" / "entities" / "Foo.md").write_text("# Foo\n")
            (ws / "vault" / "raw").mkdir(parents=True)
            (ws / "vault" / "raw" / "src.pdf").write_bytes(b"%PDF")
            (ws / "vault" / "src.extracted.md").write_text("extracted\n")
            # Noise that must NOT appear
            (ws / "other.txt").write_text("nope\n")
            (ws / ".curator").mkdir()
            (ws / ".curator" / "log.md").write_text("secret\n")
            (ws / "wiki" / ".git").mkdir()
            (ws / "wiki" / ".git" / "config").write_text("x\n")
            (ws / "wiki" / "node_modules" / "pkg").mkdir(parents=True)
            (ws / "wiki" / "node_modules" / "pkg" / "x.js").write_text("1\n")

            files = filebrowser_tree.walk_ce_tree(ws)
            self.assertIn("wiki/entities/Foo.md", files)
            self.assertIn("vault/raw/src.pdf", files)
            self.assertIn("vault/src.extracted.md", files)
            self.assertNotIn("other.txt", files)
            self.assertTrue(all(f.startswith(("vault/", "wiki/")) for f in files))
            self.assertFalse(any(".git" in f or "node_modules" in f or ".curator" in f for f in files))

            payload = filebrowser_tree.tree_payload(ws)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["count"], len(files))
            self.assertEqual(payload["roots"], ["vault", "wiki"])


class TreeApi(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)

    def test_api_tree_under_embed_prefix(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            wiki = root / "wiki" / "notes"
            vault = root / "vault" / "raw"
            bundle.mkdir()
            wiki.mkdir(parents=True)
            vault.mkdir(parents=True)
            (wiki / "hello.md").write_text("# hi\n")
            (vault / "doc.pdf").write_bytes(b"%PDF")
            (bundle / "index.html").write_text(
                "<html><head></head><body>ce</body></html>", encoding="utf-8"
            )

            viewer_server.BUNDLE_DIR = bundle.resolve()
            viewer_server.WORKSPACE_DIR = root.resolve()
            viewer_server.WIKI_DIR = (root / "wiki").resolve()
            viewer_server.VAULT_DIR = (root / "vault").resolve()
            viewer_server.VAULT_RAW_DIR = vault.resolve()

            httpd = viewer_server.ReusableThreadingServer(
                ("127.0.0.1", 0), viewer_server.Handler
            )
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/embed/ce/api/tree", timeout=2
                ) as resp:
                    body = json.loads(resp.read().decode())
                self.assertTrue(body["ok"])
                self.assertIn("wiki/notes/hello.md", body["files"])
                self.assertIn("vault/raw/doc.pdf", body["files"])

                # Unprefixed (proxy may strip) still works
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/tree", timeout=2
                ) as resp:
                    body2 = json.loads(resp.read().decode())
                self.assertEqual(body2["count"], body["count"])
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
