"""Phase 2a: CE_PUBLIC_BASE + hosted-shell contract."""
from __future__ import annotations

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

import public_base  # noqa: E402
import viewer_server  # noqa: E402


class PublicBaseNormalize(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)

    def test_empty_default(self):
        os.environ.pop("CE_PUBLIC_BASE", None)
        self.assertEqual(public_base.public_base(), "")

    def test_normalizes_slash(self):
        os.environ["CE_PUBLIC_BASE"] = "embed/ce/"
        self.assertEqual(public_base.public_base(), "/embed/ce")

    def test_strip_prefix(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        self.assertEqual(public_base.strip_public_base("/embed/ce"), "/")
        self.assertEqual(public_base.strip_public_base("/embed/ce/api/page"), "/api/page")
        self.assertEqual(public_base.strip_public_base("/api/page"), "/api/page")


class HostedShell(unittest.TestCase):
    def test_header_wins(self):
        h = public_base.hosted_shell_from_request(
            {"X-CE-Host": "switchbay"},
            {"host": ["okbay"]},
        )
        self.assertEqual(h, "switchbay")

    def test_query_fallback(self):
        self.assertEqual(
            public_base.hosted_shell_from_request(None, {"host": ["okbay"]}),
            "okbay",
        )

    def test_rejects_unknown(self):
        self.assertIsNone(public_base.normalize_hosted_shell("evil"))

    def test_settings_policy_stub(self):
        p = public_base.hosted_settings_policy("switchbay")
        self.assertEqual(p["hosted"], "switchbay")
        self.assertTrue(p["shell_owns_workspace_settings"])
        self.assertTrue(p["ce_viewer_knobs_enabled"])


class BootstrapInject(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)

    def test_injects_before_head_close(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        out = public_base.inject_viewer_bootstrap(
            b"<html><head><title>t</title></head><body></body></html>",
            hosted="okbay",
        ).decode()
        self.assertIn('window.CE_PUBLIC_BASE="/embed/ce"', out)
        self.assertIn('window.CE_HOSTED="okbay"', out)
        self.assertIn("window.ceApi=", out)
        self.assertLess(out.index("CE_PUBLIC_BASE"), out.index("</head>"))


class ViewerServerEmbed(unittest.TestCase):
    """Smoke: strip prefix + /api/hosted on loopback (not 8766 — avoid clashing)."""

    def tearDown(self):
        os.environ.pop("CE_PUBLIC_BASE", None)

    def test_hosted_and_prefixed_static(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = root / "bundle"
            wiki = root / "wiki"
            bundle.mkdir()
            wiki.mkdir()
            (wiki / "notes").mkdir()
            (bundle / "index.html").write_text(
                "<html><head><title>ce</title></head><body>hi</body></html>",
                encoding="utf-8",
            )
            (bundle / "data.json").write_text("{}", encoding="utf-8")

            viewer_server.BUNDLE_DIR = bundle.resolve()
            viewer_server.WORKSPACE_DIR = root.resolve()
            viewer_server.WIKI_DIR = wiki.resolve()
            viewer_server.VAULT_DIR = (root / "vault").resolve()
            viewer_server.VAULT_RAW_DIR = (root / "vault" / "raw").resolve()

            httpd = viewer_server.ReusableThreadingServer(
                ("127.0.0.1", 0), viewer_server.Handler
            )
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                # Prefixed HTML gets bootstrap
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/embed/ce/", timeout=2
                ) as resp:
                    body = resp.read().decode()
                self.assertIn("CE_PUBLIC_BASE", body)
                self.assertIn("hi", body)

                # Hosted probe
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/embed/ce/api/hosted?host=switchbay",
                    headers={"X-CE-Host": "okbay"},
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    payload = resp.read().decode()
                self.assertIn('"hosted": "okbay"', payload)

                # Unprefixed still works (proxy may strip)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/hosted", timeout=2
                ) as resp:
                    self.assertEqual(resp.status, 200)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
