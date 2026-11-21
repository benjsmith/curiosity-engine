"""ce-graph/1 reference server, OKF import, and namespaced page ids.

No kuzu, no network beyond loopback, no model calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "curiosity-engine" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from graph_id import page_id, read_graph_id  # noqa: E402
from okf_import import import_cache, import_vault  # noqa: E402


def _page(path: Path, body: str, **fm) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, val in fm.items():
        lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class AccessTests(unittest.TestCase):
    def test_source_ref_fallback_and_denial(self):
        from graph_serve import _source_visible
        self.assertTrue(_source_visible("", {}))
        self.assertTrue(_source_visible("sharepoint:open", {}))
        self.assertFalse(_source_visible("deny:sharepoint:hidden", {}))
        self.assertFalse(_source_visible("uc:sales", {"uc:sales": False}))


class ImportTests(unittest.TestCase):
    def test_cache_stamps_graph_id_and_does_not_touch_caller_wiki(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            _page(bundle / "concepts" / "widget.md", "A widget.", title="Widget", type="concept")
            caller = root / "caller"
            (caller / "wiki").mkdir(parents=True)
            cache = root / "cache"
            result = import_cache(bundle, cache, "ceg://git/acme/eng")
            self.assertEqual(result["pages"], ["concepts/widget"])
            self.assertEqual(read_graph_id(cache), "ceg://git/acme/eng")
            self.assertTrue((cache / "wiki" / "concepts" / "widget.md").is_file())
            self.assertEqual(list((caller / "wiki").rglob("*.md")), [])
            ids = (cache / ".curator" / "page-ids.txt").read_text(encoding="utf-8")
            self.assertIn(page_id("ceg://git/acme/eng", "concepts/widget"), ids)

    def test_vault_import_is_untrusted_and_not_a_wiki_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            _page(bundle / "concepts" / "widget.md", "Secret-looking prose.", title="Widget")
            ws = root / "ws"
            (ws / "wiki").mkdir(parents=True)
            result = import_vault(bundle, ws, "ceg://git/acme/eng")
            self.assertEqual(result["vault"], ["vault/ext/okf/concepts__widget.md"])
            text = (ws / "vault/ext/okf/concepts__widget.md").read_text(encoding="utf-8")
            self.assertIn("untrusted: true", text)
            self.assertIn("BEGIN FETCHED CONTENT", text)
            self.assertFalse((ws / "wiki" / "concepts").exists())


class ServerTests(unittest.TestCase):
    def test_oauth_pkce_then_read_and_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wiki = root / "wiki"
            _page(
                wiki / "concepts" / "widget.md",
                "The widget uses a sealed bearing. See [[concepts/bearing]].",
                title="Widget", type="concept", classification="confidential",
            )
            _page(
                wiki / "concepts" / "bearing.md",
                "The bearing is sealed.",
                title="Bearing", type="concept", classification="confidential",
            )
            subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "add", "wiki"], check=True, capture_output=True)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
            subprocess.run(
                ["git", "-C", str(root), "commit", "-m", "init"],
                check=True, capture_output=True, env=env,
            )
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPTS / "graph_serve.py"),
                 "--wiki", str(root), "--auth", "dev", "--port", "0",
                 "--graph-id", "ceg://kg.example/eng",
                 "--classification", "confidential"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                line = proc.stdout.readline() if proc.stdout else ""
                self.assertIn("listening on", line)
                port = int(line.rsplit(":", 1)[-1])
                base = f"http://127.0.0.1:{port}"
                meta = json.load(urllib.request.urlopen(base + "/.well-known/oauth-authorization-server"))
                self.assertIn("S256", meta["code_challenge_methods_supported"])
                verifier = "a" * 50
                import base64
                import hashlib
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()
                ).decode().rstrip("=")
                redirect = f"http://127.0.0.1:{port}/cb"
                # The server redirects to redirect_uri. Catch it by using
                # a loopback path on the same server that 404s after the
                # query string is visible... easier to not follow redirects.
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None
                opener = urllib.request.build_opener(NoRedirect)
                auth_url = (
                    base + "/oauth/authorize?"
                    + urllib.parse.urlencode({
                        "response_type": "code",
                        "client_id": "switchbay",
                        "redirect_uri": "http://127.0.0.1:9/cb",
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "state": "xyz",
                    })
                )
                try:
                    opener.open(auth_url)
                    self.fail("expected redirect")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 302)
                    location = exc.headers["Location"]
                parsed = urllib.parse.urlparse(location)
                code = urllib.parse.parse_qs(parsed.query)["code"][0]
                self.assertEqual(urllib.parse.parse_qs(parsed.query)["state"][0], "xyz")
                token_req = urllib.request.Request(
                    base + "/oauth/token",
                    data=json.dumps({
                        "grant_type": "authorization_code",
                        "code": code,
                        "code_verifier": verifier,
                        "redirect_uri": "http://127.0.0.1:9/cb",
                    }).encode(),
                    headers={"Content-Type": "application/json"},
                )
                token = json.load(urllib.request.urlopen(token_req))
                access = token["access_token"]
                bare = urllib.request.Request(base + "/ce-graph/1/info")
                try:
                    urllib.request.urlopen(bare)
                    self.fail("anonymous read should fail")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 401)
                info_req = urllib.request.Request(
                    base + "/ce-graph/1/info",
                    headers={"Authorization": f"Bearer {access}"},
                )
                info = json.load(urllib.request.urlopen(info_req))
                self.assertEqual(info["graph_id"], "ceg://kg.example/eng")
                self.assertEqual(info["rights"]["write"], "propose")
                self.assertTrue(info["rev"])
                search_req = urllib.request.Request(
                    base + "/ce-graph/1/search",
                    data=json.dumps({"query": "sealed bearing", "k": 5}).encode(),
                    headers={"Authorization": f"Bearer {access}",
                             "Content-Type": "application/json"},
                )
                found = json.load(urllib.request.urlopen(search_req))
                paths = [row["path"] for row in found["results"]]
                self.assertIn("concepts/widget", paths)
                page_req = urllib.request.Request(
                    base + "/ce-graph/1/page",
                    data=json.dumps({"path": "concepts/widget", "rev": info["rev"]}).encode(),
                    headers={"Authorization": f"Bearer {access}",
                             "Content-Type": "application/json"},
                )
                page = json.load(urllib.request.urlopen(page_req))
                self.assertEqual(page["rev"], info["rev"])
                self.assertEqual(page["classification"], "confidential")
                self.assertIn("concepts/widget", page["page_id"])
                prop_req = urllib.request.Request(
                    base + "/ce-graph/1/propose",
                    data=json.dumps({"path": "concepts/note", "content": "hello\n",
                                     "base_rev": info["rev"], "mode": "direct"}).encode(),
                    headers={"Authorization": f"Bearer {access}",
                             "Content-Type": "application/json"},
                )
                prop = json.load(urllib.request.urlopen(prop_req))
                self.assertEqual(prop["state"], "rejected")
                mcp_req = urllib.request.Request(
                    base + "/mcp",
                    data=json.dumps({
                        "jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}},
                    }).encode(),
                    headers={"Authorization": f"Bearer {access}",
                             "Content-Type": "application/json",
                             "Accept": "application/json"},
                )
                mcp = json.load(urllib.request.urlopen(mcp_req))
                self.assertEqual(mcp["result"]["serverInfo"]["name"], "ce-graph")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
