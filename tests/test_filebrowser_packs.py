"""Phase 2b++++: CE pack list / enable / install / sandboxed action dispatch."""
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

import filebrowser_packs  # noqa: E402
import viewer_server  # noqa: E402


def _ws_tree() -> Path:
    td = tempfile.mkdtemp()
    ws = Path(td)
    (ws / "wiki" / "notes").mkdir(parents=True)
    (ws / "vault" / "raw").mkdir(parents=True)
    (ws / "wiki" / "notes" / "a.md").write_text("# A\n")
    (ws / "vault" / "raw" / "deck.pptx").write_bytes(b"PK\x03\x04fake")
    return ws


def _write_demo_pack(pack_dir: Path, *, with_skill: bool = True) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pack.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "0.0.1",
                "description": "test pack",
                "skills": ["demo-open-deck"],
                "file_routes": [
                    {
                        "ext": "pptx",
                        "action": "open-deck",
                        "label": "Open deck",
                        "endpoint": "/api/packs/demo/action/open-deck",
                        "primary": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    if with_skill:
        skill = pack_dir / "skills" / "demo-open-deck"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo-open-deck\n---\nHandle the pptx.\n",
            encoding="utf-8",
        )


class PackListEnable(unittest.TestCase):
    def test_list_and_toggle(self):
        ws = _ws_tree()
        _write_demo_pack(ws / ".workbench" / "packs" / "demo")
        packs = filebrowser_packs.list_packs(ws)
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["name"], "demo")
        self.assertTrue(packs[0]["enabled"])
        self.assertIn("open-deck", packs[0]["actions"])

        routes = filebrowser_packs.file_routes_for(ws)
        self.assertEqual(len(routes), 1)

        filebrowser_packs.set_enabled(ws, "demo", False)
        self.assertFalse(filebrowser_packs.get_pack(ws, "demo")["enabled"])
        self.assertEqual(filebrowser_packs.file_routes_for(ws), [])

        filebrowser_packs.set_enabled(ws, "demo", True)
        self.assertEqual(len(filebrowser_packs.file_routes_for(ws)), 1)

    def test_actions_for_pack(self):
        ws = _ws_tree()
        _write_demo_pack(ws / ".workbench" / "packs" / "demo")
        payload = filebrowser_packs.actions_for_pack(ws, "demo")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["actions"], ["open-deck"])
        with self.assertRaises(filebrowser_packs.PackError) as ctx:
            filebrowser_packs.actions_for_pack(ws, "missing")
        self.assertEqual(ctx.exception.status, 404)


class PackInstall(unittest.TestCase):
    def test_install_from_path_and_uninstall(self):
        ws = _ws_tree()
        src = Path(tempfile.mkdtemp()) / "demo"
        _write_demo_pack(src)
        result = filebrowser_packs.install_from_path(ws, src)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pack"]["name"], "demo")
        self.assertTrue((ws / ".workbench" / "packs" / "demo" / "pack.json").is_file())

        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.install_from_path(ws, src)  # already installed

        un = filebrowser_packs.uninstall_pack(ws, "demo")
        self.assertTrue(un["ok"])
        self.assertFalse((ws / ".workbench" / "packs" / "demo").exists())

    def test_install_rejects_relative_and_nested(self):
        ws = _ws_tree()
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.install_from_path(ws, "relative/pack")
        _write_demo_pack(ws / ".workbench" / "packs" / "demo")
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.install_from_path(
                ws, ws / ".workbench" / "packs" / "demo"
            )


class PackDispatchSandbox(unittest.TestCase):
    def test_dispatch_queues_run(self):
        ws = _ws_tree()
        _write_demo_pack(ws / ".workbench" / "packs" / "demo")
        result = filebrowser_packs.dispatch_action(
            ws, "demo", "open-deck", "vault/raw/deck.pptx"
        )
        self.assertTrue(result["run_id"].startswith("run-"))
        self.assertEqual(result["pack"], "demo")
        self.assertEqual(result["action"], "open-deck")
        self.assertEqual(result["skill"], "demo-open-deck")
        self.assertEqual(result["status"], "queued")
        run_file = ws / ".workbench" / "pack-runs" / f"{result['run_id']}.json"
        self.assertTrue(run_file.is_file())
        rec = json.loads(run_file.read_text())
        self.assertEqual(rec["path"], "vault/raw/deck.pptx")

    def test_dispatch_refuses_escape(self):
        ws = _ws_tree()
        _write_demo_pack(ws / ".workbench" / "packs" / "demo")
        (ws / "outside.pptx").write_bytes(b"x")
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.dispatch_action(
                ws, "demo", "open-deck", "wiki/../outside.pptx"
            )
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.dispatch_action(
                ws, "demo", "open-deck", "../../etc/passwd"
            )
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.dispatch_action(
                ws, "demo", "open-deck", "/etc/passwd"
            )

    def test_dispatch_refuses_disabled_and_unknown(self):
        ws = _ws_tree()
        _write_demo_pack(ws / ".workbench" / "packs" / "demo")
        filebrowser_packs.set_enabled(ws, "demo", False)
        with self.assertRaises(filebrowser_packs.PackError) as ctx:
            filebrowser_packs.dispatch_action(
                ws, "demo", "open-deck", "vault/raw/deck.pptx"
            )
        self.assertEqual(ctx.exception.status, 409)

        filebrowser_packs.set_enabled(ws, "demo", True)
        with self.assertRaises(filebrowser_packs.PackError) as ctx2:
            filebrowser_packs.dispatch_action(
                ws, "demo", "not-a-route", "vault/raw/deck.pptx"
            )
        self.assertEqual(ctx2.exception.status, 404)

    def test_dispatch_refuses_outside_vault_wiki(self):
        ws = _ws_tree()
        _write_demo_pack(ws / ".workbench" / "packs" / "demo")
        # Drop a file under .workbench — not in sandbox roots.
        sneak = ws / ".workbench" / "secret.pptx"
        sneak.parent.mkdir(parents=True, exist_ok=True)
        sneak.write_bytes(b"x")
        with self.assertRaises(filebrowser_packs.PackError):
            filebrowser_packs.dispatch_action(
                ws, "demo", "open-deck", ".workbench/secret.pptx"
            )


class PackApi(unittest.TestCase):
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

    def _json(self, port: int, method: str, path: str, body=None, prefix: str = ""):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{prefix}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_api_dispatch_toggle_install_under_embed(self):
        os.environ["CE_PUBLIC_BASE"] = "/embed/ce"
        ws = _ws_tree()
        src = Path(tempfile.mkdtemp()) / "demo"
        _write_demo_pack(src)
        httpd, port = self._start(ws)
        prefix = "/embed/ce"
        try:
            # install
            st, body = self._json(
                port, "POST", "/api/packs/install", {"path": str(src)}, prefix=prefix
            )
            self.assertEqual(st, 200, body)
            self.assertEqual(body["pack"]["name"], "demo")

            # list
            st, body = self._json(port, "GET", "/api/packs", prefix=prefix)
            self.assertEqual(st, 200, body)
            self.assertEqual(body["count"], 1)

            # actions
            st, body = self._json(
                port, "GET", "/api/packs/demo/actions", prefix=prefix
            )
            self.assertEqual(st, 200, body)
            self.assertEqual(body["actions"], ["open-deck"])

            # dispatch
            st, body = self._json(
                port,
                "POST",
                "/api/packs/demo/action/open-deck",
                {"path": "vault/raw/deck.pptx"},
                prefix=prefix,
            )
            self.assertEqual(st, 200, body)
            self.assertTrue(body["run_id"].startswith("run-"))

            # sandbox escape via API
            st, body = self._json(
                port,
                "POST",
                "/api/packs/demo/action/open-deck",
                {"path": "vault/../../etc/passwd"},
                prefix=prefix,
            )
            self.assertEqual(st, 400, body)
            self.assertIn("error", body)

            # disable → 409
            st, body = self._json(
                port,
                "POST",
                "/api/packs/toggle",
                {"name": "demo", "enabled": False},
                prefix=prefix,
            )
            self.assertEqual(st, 200, body)
            st, body = self._json(
                port,
                "POST",
                "/api/packs/demo/action/open-deck",
                {"path": "vault/raw/deck.pptx"},
                prefix=prefix,
            )
            self.assertEqual(st, 409, body)

            # file-routes empty when disabled
            st, body = self._json(port, "GET", "/api/file-routes", prefix=prefix)
            self.assertEqual(st, 200, body)
            self.assertEqual(body["routes"], [])

            # uninstall
            st, body = self._json(
                port, "DELETE", "/api/packs?name=demo", prefix=prefix
            )
            self.assertEqual(st, 200, body)
            self.assertFalse((ws / ".workbench" / "packs" / "demo").exists())
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
