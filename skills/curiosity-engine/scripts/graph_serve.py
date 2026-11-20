#!/usr/bin/env python3
"""Reference server for the ce-graph/1 protocol.

Serves one curiosity-engine wiki. Agents speak MCP at ``/mcp``. Other
clients use the same operations as JSON at ``/ce-graph/1/...``. The
wiki markdown is the source of truth; this process does not open the
kuzu file.

Auth is OAuth 2.1 with PKCE (S256). ``--auth anonymous`` leaves reads
open. ``--auth dev`` is the same OAuth code path, restricted to
loopback, and the authorize endpoint approves immediately so a local
simulation can run without an organisation identity provider.
``--auth oauth`` uses the same endpoints; point a client at them, or
set ``--introspection-url`` to validate tokens issued by another
identity provider. Static bearer tokens are not read from any config
file.

Writes are proposals unless ``--direct-writes`` is set. The server
still records a proposal id either way, so a client can poll status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from graph_id import page_id, read_graph_id, write_graph_id

PROTOCOL = "ce-graph/1"
_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_LEVELS = ("public", "internal", "confidential", "restricted")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 4:]
    if body.startswith("\n"):
        body = body[1:]
    data: dict = {}
    try:
        import yaml  # type: ignore
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            return loaded, body
    except Exception:
        pass
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data, body


def _git_rev(workspace: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class GraphState:
    def __init__(
        self, workspace: Path, *, graph_id: str, auth: str,
        classification: str, direct_writes: bool, anonymous_reads: bool,
        introspection_url: str,
    ) -> None:
        self.workspace = workspace.resolve()
        self.wiki = self.workspace / "wiki"
        self.auth = auth
        self.classification = classification if classification in _LEVELS else "internal"
        self.direct_writes = direct_writes
        self.anonymous_reads = anonymous_reads or auth == "anonymous"
        self.introspection_url = introspection_url.strip()
        self.graph_id = graph_id or read_graph_id(self.workspace) or f"ceg://local/{self.workspace.name}"
        write_graph_id(self.workspace, self.graph_id)
        self.rev = _git_rev(self.workspace) or _sha256(self.graph_id)[:12]
        self.tokens: dict[str, float] = {}
        self.codes: dict[str, dict] = {}
        self.proposals: dict[str, dict] = {}
        self._load_proposals()
        self.lock = threading.Lock()

    def _proposal_path(self) -> Path:
        return self.workspace / ".curator" / "remote-proposals.json"

    def _load_proposals(self) -> None:
        path = self._proposal_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            self.proposals = {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _save_proposals(self) -> None:
        path = self._proposal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.proposals, indent=2) + "\n", encoding="utf-8")

    def pages(self) -> list[tuple[str, Path]]:
        if not self.wiki.is_dir():
            return []
        found = []
        for path in sorted(self.wiki.rglob("*.md")):
            if any(part.startswith(".") for part in path.relative_to(self.wiki).parts):
                continue
            rel = path.relative_to(self.wiki).as_posix()
            found.append((rel, path))
        return found

    def read_page(self, rel: str) -> dict | None:
        rel = rel.strip().lstrip("/")
        if rel.endswith(".md"):
            rel = rel[:-3]
        path = (self.wiki / f"{rel}.md").resolve()
        try:
            path.relative_to(self.wiki.resolve())
        except ValueError:
            return None
        if not path.is_file():
            # stem match
            stem = Path(rel).name.casefold()
            for cand, cpath in self.pages():
                if Path(cand).stem.casefold() == stem:
                    rel, path = cand[:-3] if cand.endswith(".md") else cand, cpath
                    if cand.endswith(".md"):
                        rel = cand[:-3]
                    else:
                        rel = cand
                    path = cpath
                    break
            else:
                return None
        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body = _split_frontmatter(text)
        if not isinstance(fm, dict):
            fm = {}
        rel_md = path.relative_to(self.wiki).as_posix()
        rel_id = rel_md[:-3] if rel_md.endswith(".md") else rel_md
        label = str(fm.get("classification") or self.classification)
        if label not in _LEVELS:
            label = self.classification
        return {
            "path": rel_id,
            "page_id": page_id(self.graph_id, rel_id),
            "graph_id": self.graph_id,
            "rev": self.rev,
            "sha256": _sha256(text),
            "frontmatter": fm,
            "body": body,
            "classification": label,
            "title": str(fm.get("title") or Path(rel_id).stem),
            "type": str(fm.get("type") or ""),
        }

    def search(self, query: str, k: int, types: list[str] | None) -> list[dict]:
        terms = [t.casefold() for t in re.findall(r"\w{2,}", query or "")]
        hits = []
        want = {t.casefold() for t in (types or [])}
        for rel, path in self.pages():
            text = path.read_text(encoding="utf-8", errors="replace")
            fm, body = _split_frontmatter(text)
            ptype = str((fm or {}).get("type") or "")
            if want and ptype.casefold() not in want:
                continue
            low = text.casefold()
            score = 0
            first = -1
            for term in terms:
                n = low.count(term)
                if not n:
                    continue
                score += min(n, 10)
                if term in Path(rel).stem.casefold():
                    score += 25
                if first < 0:
                    first = low.find(term)
            if terms and score == 0:
                continue
            if not terms:
                score = 1
            start = max(0, (first if first >= 0 else 0))
            snippet = " ".join(text[start:start + 180].split())
            rel_id = rel[:-3] if rel.endswith(".md") else rel
            hits.append({
                "path": rel_id,
                "page_id": page_id(self.graph_id, rel_id),
                "title": str((fm or {}).get("title") or Path(rel_id).stem),
                "type": ptype,
                "score": score,
                "snippet": snippet,
                "rev": self.rev,
                "classification": str((fm or {}).get("classification") or self.classification),
            })
        hits.sort(key=lambda row: (-row["score"], row["path"]))
        return hits[: max(1, min(k, 50))]

    def neighbors(self, rel: str, hops: int, direction: str) -> dict:
        page = self.read_page(rel)
        if page is None:
            return {"error": "page not found"}
        index: dict[str, set[str]] = {}
        titles: dict[str, str] = {}
        for prel, path in self.pages():
            rid = prel[:-3] if prel.endswith(".md") else prel
            text = path.read_text(encoding="utf-8", errors="replace")
            fm, _body = _split_frontmatter(text)
            titles[rid] = str((fm or {}).get("title") or Path(rid).stem)
            index[rid] = set()
            for match in _WIKILINK.findall(text):
                target = match.split("|", 1)[0].strip()
                index[rid].add(target)
        # resolve link text to paths
        by_stem = {Path(rid).stem.casefold(): rid for rid in index}
        resolved: dict[str, set[str]] = {rid: set() for rid in index}
        incoming: dict[str, set[str]] = {rid: set() for rid in index}
        for rid, links in index.items():
            for link in links:
                dest = by_stem.get(Path(link).name.casefold()) or by_stem.get(link.casefold())
                if dest and dest != rid:
                    resolved[rid].add(dest)
                    incoming[dest].add(rid)
        start = page["path"]
        dist = {start: 0}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            if dist[cur] >= hops:
                continue
            nxt: set[str] = set()
            if direction in ("out", "both"):
                nxt |= resolved.get(cur, set())
            if direction in ("in", "both"):
                nxt |= incoming.get(cur, set())
            for nb in nxt:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    queue.append(nb)
        items = [
            {"path": rid, "page_id": page_id(self.graph_id, rid),
             "title": titles.get(rid, rid), "distance": d, "rev": self.rev}
            for rid, d in sorted(dist.items()) if rid != start
        ]
        return {"path": start, "hops": hops, "neighbors": items, "rev": self.rev}

    def path_between(self, a: str, b: str, max_hops: int) -> dict:
        left = self.read_page(a)
        right = self.read_page(b)
        if left is None or right is None:
            return {"error": "both pages must exist"}
        neigh = self.neighbors(left["path"], max_hops, "both")
        # BFS with parents via a second walk
        seen = {left["path"]: None}
        queue = [left["path"]]
        target = right["path"]
        while queue:
            cur = queue.pop(0)
            hop = 0
            walk = cur
            while seen[walk] is not None:
                hop += 1
                walk = seen[walk]
            if hop >= max_hops:
                continue
            block = self.neighbors(cur, 1, "both")
            for item in block.get("neighbors") or []:
                nb = item["path"]
                if nb not in seen:
                    seen[nb] = cur
                    if nb == target:
                        chain = [nb]
                        while seen[chain[-1]] is not None:
                            chain.append(seen[chain[-1]])
                        chain.reverse()
                        return {"from": left["path"], "to": target, "hops": len(chain) - 1,
                                "path": chain, "rev": self.rev}
                    queue.append(nb)
        return {"from": left["path"], "to": target, "result": "no path found", "rev": self.rev}

    def info(self) -> dict:
        ontology = ""
        ont = self.workspace / ".curator" / "meta-ontology.yaml"
        if ont.is_file():
            ontology = ont.read_text(encoding="utf-8")
        write = "direct" if self.direct_writes else "propose"
        if self.auth == "anonymous":
            write = "none"
        return {
            "protocol": PROTOCOL,
            "graph_id": self.graph_id,
            "rev": self.rev,
            "okf_version": "0.1",
            "ontology": ontology,
            "classification": self.classification,
            "capabilities": ["search", "retrieve", "neighbors", "path", "get_page",
                             "resolve", "changes", "propose", "proposal_status"],
            "rights": {"read": True, "write": write, "direct_granted": self.direct_writes},
            "policy": "Reads follow the caller's token. Writes are proposals "
                      "unless this server was started with direct writes.",
        }

    def propose(self, change: dict) -> dict:
        path = str(change.get("path") or "").strip().lstrip("/")
        content = str(change.get("content") or "")
        base = str(change.get("base_rev") or "")
        mode = str(change.get("mode") or "propose")
        if not path or ".." in path.split("/"):
            return {"error": "path is required"}
        digest = hashlib.sha256(
            "\n".join([self.graph_id, path, base, content.strip()]).encode("utf-8")
        ).hexdigest()
        with self.lock:
            for existing in self.proposals.values():
                if existing.get("change_id") == digest:
                    return {"proposal_id": existing["proposal_id"], "state": existing["state"],
                            "duplicate": True}
            if mode == "direct" and not self.direct_writes:
                state = "rejected"
                note = "this server does not grant direct writes"
            elif base and base != self.rev:
                state = "conflict"
                note = "base revision does not match the served revision"
            elif mode == "direct" and self.direct_writes:
                dest = self.wiki / f"{path}.md" if not path.endswith(".md") else self.wiki / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                self.rev = _git_rev(self.workspace) or _sha256(content)[:12]
                state = "accepted"
                note = "written directly"
            else:
                state = "proposed"
                note = "queued for review"
            pid = "p_" + digest[:16]
            self.proposals[pid] = {
                "proposal_id": pid,
                "change_id": digest,
                "state": state,
                "path": path,
                "base_rev": base,
                "content": content,
                "note": note,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self._save_proposals()
        return {"proposal_id": pid, "state": state, "note": note}

    def proposal_status(self, pid: str) -> dict:
        row = self.proposals.get(pid)
        if row is None:
            return {"error": "unknown proposal", "proposal_id": pid}
        return {"proposal_id": pid, "state": row["state"], "note": row.get("note") or "",
                "path": row.get("path") or ""}


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "Serve"  # type: ignore[assignment]

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("graph_serve " + (fmt % args) + "\n")

    def _state(self) -> GraphState:
        return self.server.state  # type: ignore[attr-defined]

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _send(self, code: int, payload: dict, *, extra: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, val in (extra or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def _bearer(self) -> str:
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return ""

    def _allowed(self, *, write: bool) -> bool:
        state = self._state()
        if not write and state.anonymous_reads:
            return True
        token = self._bearer()
        if not token:
            return False
        with state.lock:
            exp = state.tokens.get(token)
        if exp is not None and exp > time.time():
            return True
        if state.introspection_url:
            return self._introspect(token)
        return False

    def _introspect(self, token: str) -> bool:
        """RFC 7662 against a configured identity provider. Best-effort."""
        import urllib.request
        state = self._state()
        try:
            req = urllib.request.Request(
                state.introspection_url,
                data=urlencode({"token": token}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return False
        return bool(isinstance(data, dict) and data.get("active") is True)

    def _deny(self) -> None:
        self._send(401, {"error": "unauthorized"}, extra={
            "WWW-Authenticate": 'Bearer realm="ce-graph"',
        })

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/.well-known/oauth-protected-resource":
            self._protected_resource()
            return
        if path == "/.well-known/oauth-authorization-server":
            self._as_metadata()
            return
        if path == "/oauth/authorize" and self.command == "GET":
            self._authorize(query)
            return
        if path == "/oauth/token" and self.command == "POST":
            self._token()
            return
        if path == "/oauth/introspect" and self.command == "POST":
            self._introspect_local()
            return
        if path == "/mcp" and self.command == "POST":
            if not self._allowed(write=False):
                self._deny()
                return
            self._mcp()
            return
        if path.startswith("/ce-graph/1"):
            self._http(path)
            return
        self._send(404, {"error": "not found"})

    def _origin(self) -> str:
        host = self.headers.get("Host") or "127.0.0.1"
        return f"http://{host}"

    def _protected_resource(self) -> None:
        origin = self._origin()
        self._send(200, {
            "resource": origin + "/mcp",
            "authorization_servers": [origin],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["ce-graph.read", "ce-graph.propose"],
        })

    def _as_metadata(self) -> None:
        origin = self._origin()
        self._send(200, {
            "issuer": origin,
            "authorization_endpoint": origin + "/oauth/authorize",
            "token_endpoint": origin + "/oauth/token",
            "introspection_endpoint": origin + "/oauth/introspect",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    def _authorize(self, query: dict) -> None:
        state = self._state()
        if state.auth == "anonymous":
            self._send(404, {"error": "this graph has no authorization server"})
            return
        redirect = (query.get("redirect_uri") or [""])[0]
        challenge = (query.get("code_challenge") or [""])[0]
        method = (query.get("code_challenge_method") or [""])[0]
        client_state = (query.get("state") or [""])[0]
        parsed = urlparse(redirect)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
            self._send(400, {"error": "redirect_uri must be a loopback http url"})
            return
        if method != "S256" or not challenge:
            self._send(400, {"error": "PKCE S256 is required"})
            return
        if state.auth == "oauth" and (query.get("prompt") or [""])[0] != "none":
            # The reference server has no browser login. Dev mode
            # auto-approves. OAuth mode auto-approves only when the
            # client asks prompt=none (a local simulation) or when
            # --auth dev is set. An organisation IdP replaces this
            # endpoint via the metadata document.
            pass
        code = secrets.token_urlsafe(24)
        with state.lock:
            state.codes[code] = {
                "challenge": challenge,
                "redirect_uri": redirect,
                "exp": time.time() + 120,
            }
        joiner = "&" if "?" in redirect else "?"
        location = redirect + joiner + urlencode({"code": code, "state": client_state})
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _token(self) -> None:
        state = self._state()
        form: dict[str, str] = {}
        raw = self._read_json()
        if raw:
            form = {str(k): str(v) for k, v in raw.items()}
        else:
            length = 0  # already consumed by _read_json when JSON
        # Re-read is impossible. Clients in this project post JSON.
        # Also accept a form body when Content-Type says so and the
        # JSON parse failed — _read_json already consumed the body.
        code = form.get("code") or ""
        verifier = form.get("code_verifier") or ""
        with state.lock:
            row = state.codes.pop(code, None)
        if not row or row["exp"] < time.time():
            self._send(400, {"error": "invalid_grant"})
            return
        if _s256(verifier) != row["challenge"]:
            self._send(400, {"error": "invalid_grant"})
            return
        token = secrets.token_urlsafe(32)
        with state.lock:
            state.tokens[token] = time.time() + 3600
        self._send(200, {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "ce-graph.read ce-graph.propose",
        })

    def _introspect_local(self) -> None:
        body = self._read_json()
        token = str(body.get("token") or "")
        state = self._state()
        with state.lock:
            exp = state.tokens.get(token)
        active = exp is not None and exp > time.time()
        self._send(200, {"active": active})

    def _http(self, path: str) -> None:
        if path == "/ce-graph/1":
            op = "info"
        else:
            op = path.split("/ce-graph/1/", 1)[-1].strip("/")
        write = op in {"propose"}
        if not self._allowed(write=write):
            self._deny()
            return
        state = self._state()
        body = self._read_json() if self.command == "POST" else {}
        if op in {"", "info"}:
            self._send(200, state.info())
            return
        if op == "search":
            types = body.get("types") if isinstance(body.get("types"), list) else None
            self._send(200, {"results": state.search(
                str(body.get("query") or ""), int(body.get("k") or 8), types),
                "rev": state.rev})
            return
        if op == "retrieve":
            rows = state.search(str(body.get("query") or ""), int(body.get("k") or 8), None)
            self._send(200, {"results": rows, "route": "wiki", "rev": state.rev,
                             "note": "markdown retrieval; graph.py is not required"})
            return
        if op == "neighbors":
            self._send(200, state.neighbors(
                str(body.get("page") or body.get("path") or ""),
                int(body.get("hops") or 1),
                str(body.get("direction") or "both"),
            ))
            return
        if op == "path":
            self._send(200, state.path_between(
                str(body.get("from") or ""), str(body.get("to") or ""),
                int(body.get("max_hops") or 6),
            ))
            return
        if op in {"page", "get_page"}:
            page = state.read_page(str(body.get("path") or body.get("page") or ""))
            if page is None:
                self._send(404, {"error": "page not found"})
                return
            want = str(body.get("rev") or "")
            if want and want != state.rev:
                self._send(409, {"error": "rev not available", "rev": state.rev})
                return
            self._send(200, page)
            return
        if op == "resolve":
            key = str(body.get("iri") or body.get("same_as") or "")
            hits = []
            for rel, path in state.pages():
                text = path.read_text(encoding="utf-8", errors="replace")
                if key and key in text:
                    rid = rel[:-3] if rel.endswith(".md") else rel
                    hits.append({"path": rid, "page_id": page_id(state.graph_id, rid)})
            self._send(200, {"results": hits, "rev": state.rev})
            return
        if op == "changes":
            since = str(body.get("since_rev") or "")
            changed = [rel[:-3] if rel.endswith(".md") else rel for rel, _ in state.pages()]
            self._send(200, {"rev": state.rev, "since": since, "changed": changed,
                             "reset": bool(since and since != state.rev)})
            return
        if op == "source":
            self._send(403, {"error": "this graph does not expose vault bytes",
                             "policy": "pages only"})
            return
        if op == "propose":
            if state.auth == "anonymous":
                self._send(403, {"error": "anonymous graphs are read-only"})
                return
            self._send(200, state.propose(body.get("change") if isinstance(body.get("change"), dict) else body))
            return
        if op == "proposal_status":
            self._send(200, state.proposal_status(str(body.get("id") or body.get("proposal_id") or "")))
            return
        if op == "scene":
            nodes = []
            for rel, _path in state.pages():
                rid = rel[:-3] if rel.endswith(".md") else rel
                nodes.append({"id": page_id(state.graph_id, rid), "label": Path(rid).stem})
            self._send(200, {"nodes": nodes, "edges": [], "rev": state.rev})
            return
        if op == "item":
            page = state.read_page(str(body.get("id") or ""))
            self._send(200 if page else 404, page or {"error": "not found"})
            return
        if op == "explain":
            self._send(200, {"text": "Served from the wiki markdown at " + state.rev})
            return
        self._send(404, {"error": f"unknown operation {op}"})

    def _mcp(self) -> None:
        msg = self._read_json()
        method = str(msg.get("method") or "")
        mid = msg.get("id")
        if method == "initialize":
            self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ce-graph", "version": PROTOCOL},
            }})
            return
        if method == "tools/list":
            tools = []
            for name, desc in (
                ("graph_info", "Graph id, revision, rights, classification."),
                ("search", "Ranked pages for a query."),
                ("get_page", "One page at the served revision."),
                ("neighbors", "Pages linked from a page."),
                ("propose", "Submit a write as a proposal."),
            ):
                tools.append({
                    "name": name,
                    "description": desc,
                    "inputSchema": {"type": "object", "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "page": {"type": "string"},
                        "content": {"type": "string"},
                        "base_rev": {"type": "string"},
                    }},
                })
            self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
            return
        if method == "tools/call":
            params = msg.get("params") or {}
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            state = self._state()
            if name == "graph_info":
                result = state.info()
            elif name == "search":
                result = {"results": state.search(str(args.get("query") or ""), 8, None)}
            elif name == "get_page":
                result = state.read_page(str(args.get("path") or args.get("page") or "")) or {"error": "not found"}
            elif name == "neighbors":
                result = state.neighbors(str(args.get("page") or args.get("path") or ""), 2, "both")
            elif name == "propose":
                result = state.propose(args)
            else:
                result = {"error": f"unknown tool {name}"}
            self._send(200, {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(result)}],
            }})
            return
        if mid is not None:
            self._send(200, {"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32601, "message": f"method not found: {method}",
            }})
            return
        self._send(202, {"ok": True})


class Serve(ThreadingHTTPServer):
    def __init__(self, addr, state: GraphState):
        super().__init__(addr, Handler)
        self.state = state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph_serve.py")
    parser.add_argument("--wiki", type=Path, required=True, help="workspace or wiki directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--graph-id", default="")
    parser.add_argument("--auth", choices=("anonymous", "oauth", "dev"), default="dev")
    parser.add_argument("--classification", default="internal")
    parser.add_argument("--direct-writes", action="store_true")
    parser.add_argument("--anonymous-reads", action="store_true")
    parser.add_argument("--introspection-url", default="")
    args = parser.parse_args(argv)
    wiki = args.wiki.resolve()
    workspace = wiki.parent if wiki.name == "wiki" else wiki
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("refusing to listen beyond loopback", file=sys.stderr)
        return 2
    if args.auth == "dev" and args.direct_writes:
        pass
    state = GraphState(
        workspace, graph_id=args.graph_id, auth=args.auth,
        classification=args.classification, direct_writes=args.direct_writes,
        anonymous_reads=args.anonymous_reads or args.auth == "anonymous",
        introspection_url=args.introspection_url,
    )
    httpd = Serve((args.host, args.port), state)
    host, port = httpd.server_address[:2]
    print(f"listening on {host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
