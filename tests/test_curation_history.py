"""Phase 2b+++: curation history builder + /api/curation/history under embed prefix."""
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

import curation_history  # noqa: E402
import viewer_server  # noqa: E402


class BuildFromData(unittest.TestCase):
    def test_sources_first_and_edges_after_nodes(self):
        data = {
            "nodes": [
                {"id": "b", "title": "B", "type": "concept", "degree": 2},
                {"id": "a", "title": "A", "type": "source", "degree": 0},
            ],
            "edges": [{"source": "a", "target": "b", "type": "wikilink"}],
        }
        doc = curation_history.build_from_data(data, duration_s=10, source="test")
        self.assertEqual(doc["source"], "test")
        node_ids = [e["id"] for e in doc["events"] if e["op"] == "node"]
        self.assertEqual(node_ids, ["a", "b"])
        edges = [e for e in doc["events"] if e["op"] == "edge"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["source"], "a")
        self.assertEqual(doc["degree"]["a"], 1)
        self.assertEqual(doc["degree"]["b"], 1)


    def test_created_orders_before_type(self):
        data = {
            "nodes": [
                {"id": "late", "title": "Late", "type": "source", "degree": 9},
                {"id": "early", "title": "Early", "type": "note", "degree": 0},
            ],
            "edges": [],
            "pages": {
                "late": {"properties": {"created": "2024-06-01"}},
                "early": {"properties": {"created": "2020-01-01"}},
            },
        }
        doc = curation_history.build_from_data(data, duration_s=10, source="test")
        node_ids = [e["id"] for e in doc["events"] if e["op"] == "node"]
        self.assertEqual(node_ids, ["early", "late"])

    def test_empty(self):
        doc = curation_history.build_from_data({})
        self.assertEqual(doc["events"], [])
        self.assertEqual(doc["source"], "empty")

    def test_prefers_workbench_cache(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cache = ws / ".workbench"
            cache.mkdir()
            payload = {
                "duration": 5,
                "events": [
                    {"t": 0, "op": "node", "id": "x", "title": "X", "type": "note"}
                ],
                "source": "prewarm",
            }
            (cache / "curation-history.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            bundle = ws / "bundle"
            bundle.mkdir()
            (bundle / "data.json").write_text(
                json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
            )
            doc = curation_history.read_or_build(ws, bundle)
            self.assertEqual(doc["source"], "prewarm")
            self.assertEqual(len(doc["events"]), 1)


class HistoryApi(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)

    def test_api_under_embed_prefix(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "index.html").write_text(
                "<html><head></head><body>ce</body></html>", encoding="utf-8"
            )
            (bundle / "data.json").write_text(
                json.dumps(
                    {
                        "nodes": [
                            {
                                "id": "n1",
                                "title": "One",
                                "type": "entity",
                                "degree": 0,
                            }
                        ],
                        "edges": [],
                    }
                ),
                encoding="utf-8",
            )
            (root / "wiki").mkdir()
            (root / "vault").mkdir()

            viewer_server.BUNDLE_DIR = bundle.resolve()
            viewer_server.WORKSPACE_DIR = root.resolve()
            viewer_server.WIKI_DIR = (root / "wiki").resolve()
            viewer_server.VAULT_DIR = (root / "vault").resolve()
            viewer_server.VAULT_RAW_DIR = (root / "vault" / "raw").resolve()

            httpd = viewer_server.ReusableThreadingServer(
                ("127.0.0.1", 0), viewer_server.Handler
            )
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/embed/ce/api/curation/history",
                    timeout=2,
                ) as resp:
                    body = json.loads(resp.read().decode())
                self.assertIn("events", body)
                self.assertEqual(body["events"][0]["id"], "n1")
                self.assertEqual(body["source"], "ce-data-json")

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/curation/history", timeout=2
                ) as resp:
                    body2 = json.loads(resp.read().decode())
                self.assertEqual(len(body2["events"]), len(body["events"]))
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
