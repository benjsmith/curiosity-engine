"""Phase 2b+: CE wiki partition (workspace split) spike."""
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

import viewer_server  # noqa: E402
import wiki_partition  # noqa: E402


def _mk_workspace(root: Path) -> Path:
    wiki = root / "wiki" / "notes"
    vault = root / "vault" / "raw"
    wiki.mkdir(parents=True)
    vault.mkdir(parents=True)
    (wiki / "alpha.md").write_text(
        "# Alpha\n\nSee (vault:raw/src.pdf) and ![[figures/_assets/a.png]]\n",
        encoding="utf-8",
    )
    (wiki / "beta.md").write_text("# Beta\n\nCopy me.\n", encoding="utf-8")
    (wiki / "gamma.md").write_text("# Gamma\n\nStay.\n", encoding="utf-8")
    figs = root / "wiki" / "figures" / "_assets"
    figs.mkdir(parents=True)
    (figs / "a.png").write_bytes(b"\x89PNG\r\n")
    (vault / "src.pdf").write_bytes(b"%PDF-1.4")
    return root


class PartitionCore(unittest.TestCase):
    def test_copy_and_move_with_vault_and_figure(self):
        with tempfile.TemporaryDirectory() as td:
            src = _mk_workspace(Path(td) / "src")
            dest = Path(td) / "out" / "child"
            stats = wiki_partition.partition_workspace(
                src,
                dest,
                move=["notes/alpha"],
                copy=["notes/beta"],
                name="child",
            )
            self.assertTrue(stats["ok"])
            self.assertEqual(stats["exported"], 2)
            self.assertEqual(stats["moved"], 1)
            self.assertEqual(stats["copied"], 1)
            self.assertEqual(stats["vault"], 1)
            self.assertEqual(stats["figures"], 1)
            self.assertTrue((dest / "wiki" / "notes" / "alpha.md").is_file())
            self.assertTrue((dest / "wiki" / "notes" / "beta.md").is_file())
            self.assertTrue((dest / "vault" / "raw" / "src.pdf").is_file())
            self.assertTrue((dest / "wiki" / "figures" / "_assets" / "a.png").is_file())
            # MOVE pruned from source into .deleted
            self.assertFalse((src / "wiki" / "notes" / "alpha.md").is_file())
            deleted = list((src / "wiki" / ".deleted").rglob("alpha.md"))
            self.assertEqual(len(deleted), 1)
            self.assertTrue((src / "wiki" / "notes" / "beta.md").is_file())
            self.assertTrue((src / "wiki" / "notes" / "gamma.md").is_file())
            # Manifests both sides
            src_m = list((src / ".curator" / "splits").glob("*.json"))
            dst_m = list((dest / ".curator" / "splits").glob("*.json"))
            self.assertEqual(len(src_m), 1)
            self.assertEqual(len(dst_m), 1)
            body = json.loads(src_m[0].read_text())
            self.assertEqual(body["role"], "source")
            self.assertIn("notes/alpha", body["moved"])

    def test_refuses_inside_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            src = _mk_workspace(Path(td) / "src")
            with self.assertRaises(wiki_partition.PartitionError):
                wiki_partition.partition_workspace(
                    src, src / "nested", move=["notes/beta"], copy=[]
                )

    def test_validate_refs(self):
        with tempfile.TemporaryDirectory() as td:
            src = _mk_workspace(Path(td) / "src")
            bad = wiki_partition.validate_refs(src, ["notes/beta", "missing"])
            self.assertEqual(bad, ["missing"])


class SplitApi(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)
        os.environ.pop("CE_SPLIT_HOME", None)
        viewer_server.SPLIT_LAST = None

    def test_api_split_under_embed_prefix(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _mk_workspace(root / "ws")
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "index.html").write_text(
                "<html><head></head><body>ce</body></html>", encoding="utf-8"
            )
            split_home = root / "homes"
            split_home.mkdir()
            os.environ["CE_SPLIT_HOME"] = str(split_home)

            viewer_server.BUNDLE_DIR = bundle.resolve()
            viewer_server.WORKSPACE_DIR = src.resolve()
            viewer_server.WIKI_DIR = (src / "wiki").resolve()
            viewer_server.VAULT_DIR = (src / "vault").resolve()
            viewer_server.VAULT_RAW_DIR = (src / "vault" / "raw").resolve()
            viewer_server.SPLIT_LAST = None

            httpd = viewer_server.ReusableThreadingServer(
                ("127.0.0.1", 0), viewer_server.Handler
            )
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                payload = json.dumps(
                    {
                        "name": "part-a",
                        "move": ["notes/alpha"],
                        "copy": ["notes/beta"],
                    }
                ).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/embed/ce/api/split",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    body = json.loads(resp.read().decode())
                self.assertTrue(body["ok"])
                self.assertEqual(body["moved"], 1)
                self.assertTrue((split_home / "part-a" / "wiki" / "notes" / "alpha.md").is_file())

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/embed/ce/api/split", timeout=2
                ) as resp:
                    status = json.loads(resp.read().decode())
                self.assertEqual(status["last"]["state"], "done")

                # filebrowser still works after split
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/embed/ce/api/tree", timeout=2
                ) as resp:
                    tree = json.loads(resp.read().decode())
                self.assertTrue(tree["ok"])
                self.assertIn("wiki/notes/beta.md", tree["files"])
                self.assertNotIn("wiki/notes/alpha.md", tree["files"])
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_api_split_rejects_empty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = _mk_workspace(root / "ws")
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "index.html").write_text("<html></html>", encoding="utf-8")
            viewer_server.BUNDLE_DIR = bundle.resolve()
            viewer_server.WORKSPACE_DIR = src.resolve()
            viewer_server.WIKI_DIR = (src / "wiki").resolve()
            viewer_server.VAULT_DIR = (src / "vault").resolve()
            viewer_server.VAULT_RAW_DIR = (src / "vault" / "raw").resolve()
            viewer_server.SPLIT_LAST = None
            httpd = viewer_server.ReusableThreadingServer(
                ("127.0.0.1", 0), viewer_server.Handler
            )
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/split",
                    data=b'{"move":[],"copy":[]}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req, timeout=2)
                self.assertEqual(cm.exception.code, 400)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
