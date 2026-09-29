"""Phase 2b+++++: CE workspace registry persistence + CM export dry-run."""
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

import cm_export  # noqa: E402
import viewer_server  # noqa: E402
import wiki_partition  # noqa: E402
import workspace_registry  # noqa: E402

# Real CM checkout available in the umbrella workspace (optional).
CM_SIBLING = ROOT.parent / "curiosity-merge"


def _mk_workspace(root: Path) -> Path:
    wiki = root / "wiki" / "notes"
    vault = root / "vault" / "raw"
    wiki.mkdir(parents=True)
    vault.mkdir(parents=True)
    (wiki / "alpha.md").write_text(
        "# Alpha\n\nSee (vault:raw/src.pdf)\n",
        encoding="utf-8",
    )
    (wiki / "beta.md").write_text("# Beta\n", encoding="utf-8")
    (vault / "src.pdf").write_bytes(b"%PDF-1.4")
    return root


class RegistryPersistence(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(dir=str(Path.home()))
        self.home = Path(self._td.name)
        self.reg = self.home / "reg.json"
        os.environ["CE_WORKSPACE_HOME"] = str(self.home)
        os.environ["CE_WORKSPACE_REGISTRY"] = str(self.reg)

    def tearDown(self):
        os.environ.pop("CE_WORKSPACE_HOME", None)
        os.environ.pop("CE_WORKSPACE_REGISTRY", None)
        self._td.cleanup()

    def test_register_persists_and_records_split(self):
        src = _mk_workspace(self.home / "src")
        dest = self.home / "child"
        dest.mkdir()
        data = workspace_registry.register(
            dest,
            set_active=True,
            split={
                "name": "child",
                "source": str(src),
                "stamp": "20260101-000000",
            },
        )
        self.assertEqual(data["active"], str(dest.resolve()))
        self.assertIn(str(dest.resolve()), data["paths"])
        self.assertEqual(len(data["splits"]), 1)
        self.assertEqual(data["splits"][0]["name"], "child")
        # Reload from disk
        again = workspace_registry.load()
        self.assertEqual(again["paths"], data["paths"])
        self.assertEqual(again["splits"][0]["target"], str(dest.resolve()))

    def test_refuses_outside_sandbox(self):
        with self.assertRaises(workspace_registry.RegistryError):
            workspace_registry.register(Path("/tmp/outside-ce-registry"))


class CmExportDryRun(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(dir=str(Path.home()))
        self.home = Path(self._td.name)
        os.environ["CE_WORKSPACE_HOME"] = str(self.home)
        os.environ["CE_WORKSPACE_REGISTRY"] = str(self.home / "reg.json")
        # Prefer real sibling CM; else plant a stub script.
        if (CM_SIBLING / "scripts" / "subgraph_export.py").is_file():
            os.environ["CE_CM_ROOT"] = str(CM_SIBLING)
            self._stub = False
        else:
            stub = self.home / "cm-stub"
            (stub / "scripts").mkdir(parents=True)
            (stub / "scripts" / "subgraph_export.py").write_text(
                "#!/usr/bin/env python3\nprint('stub')\n", encoding="utf-8"
            )
            os.environ["CE_CM_ROOT"] = str(stub)
            self._stub = True

    def tearDown(self):
        for k in (
            "CE_WORKSPACE_HOME",
            "CE_WORKSPACE_REGISTRY",
            "CE_CM_ROOT",
            "CURIOSITY_MERGE_ROOT",
            "SWITCHBAY_CM_ROOT",
        ):
            os.environ.pop(k, None)
        self._td.cleanup()

    def test_plan_export_dry_run(self):
        src = _mk_workspace(self.home / "ws")
        dest = self.home / "export-out"
        plan = cm_export.run_export(
            src,
            dest,
            ["notes/alpha", "notes/beta"],
            include_vault="owned",
            dry_run=True,
            register=False,
        )
        self.assertTrue(plan["ok"])
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["pages"], ["notes/alpha", "notes/beta"])
        self.assertEqual(plan["include_vault"], "owned")
        self.assertIn("--pages-file", plan["argv"])
        self.assertIn("--include-vault", plan["argv"])
        self.assertIn("owned", plan["argv"])
        self.assertIn("--no-preflight", plan["argv"])
        self.assertIn("--force", plan["argv"])
        # Must not create the destination on dry-run
        self.assertFalse(dest.exists())
        self.assertFalse(self.home.joinpath("reg.json").exists())

    def test_plan_refuses_inside_workspace(self):
        src = _mk_workspace(self.home / "ws")
        with self.assertRaises(cm_export.CmExportError):
            cm_export.run_export(
                src, src / "nested", ["notes/alpha"], dry_run=True
            )


class SplitRegistersWorkspace(unittest.TestCase):
    def tearDown(self):
        for k in (
            "CE_PUBLIC_BASE",
            "CE_SPLIT_HOME",
            "CE_WORKSPACE_HOME",
            "CE_WORKSPACE_REGISTRY",
            "CE_CM_ROOT",
        ):
            os.environ.pop(k, None)
        viewer_server.SPLIT_LAST = None

    def test_api_split_registers_and_cm_export_dry_run(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as td:
            root = Path(td)
            src = _mk_workspace(root / "ws")
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "index.html").write_text("<html></html>", encoding="utf-8")
            split_home = root / "homes"
            split_home.mkdir()
            reg = root / "reg.json"
            os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
            os.environ["CE_SPLIT_HOME"] = str(split_home)
            os.environ["CE_WORKSPACE_HOME"] = str(root)
            os.environ["CE_WORKSPACE_REGISTRY"] = str(reg)
            if (CM_SIBLING / "scripts" / "subgraph_export.py").is_file():
                os.environ["CE_CM_ROOT"] = str(CM_SIBLING)
            else:
                stub = root / "cm"
                (stub / "scripts").mkdir(parents=True)
                (stub / "scripts" / "subgraph_export.py").write_text(
                    "print('ok')\n", encoding="utf-8"
                )
                os.environ["CE_CM_ROOT"] = str(stub)

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
                        "name": "part-b",
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
                self.assertTrue(body.get("registered"))
                self.assertTrue((split_home / "part-b" / "wiki" / "notes" / "alpha.md").is_file())
                self.assertTrue(reg.is_file())
                reg_body = json.loads(reg.read_text())
                self.assertTrue(
                    any(str(split_home / "part-b") in p for p in reg_body["paths"])
                )

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/embed/ce/api/workspaces", timeout=2
                ) as resp:
                    ws = json.loads(resp.read().decode())
                self.assertTrue(ws["ok"])
                self.assertGreaterEqual(len(ws["paths"]), 1)
                self.assertGreaterEqual(len(ws["splits"]), 1)

                export_payload = json.dumps(
                    {
                        "name": "cm-dry",
                        "pages": ["notes/beta"],
                        "dry_run": True,
                        "include_vault": "none",
                    }
                ).encode()
                req2 = urllib.request.Request(
                    f"http://127.0.0.1:{port}/embed/ce/api/cm-export",
                    data=export_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req2, timeout=5) as resp:
                    dry = json.loads(resp.read().decode())
                self.assertTrue(dry["ok"])
                self.assertTrue(dry["dry_run"])
                self.assertEqual(dry["pages"], ["notes/beta"])
                self.assertIn("--include-vault", dry["argv"])
                self.assertFalse((split_home / "cm-dry").exists())
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
