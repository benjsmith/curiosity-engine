"""Public URL base path + hosted-shell detection (Phase 2a).

Switchbay/okbay same-origin reverse-proxy CE under a configurable prefix
(default empty; typical embed: ``/embed/ce``). See umbrella CHARTER locked
decisions #1 (no iframes) and the CE hosted-mode contract in
``docs/ADR-001-proxy-embed-and-hosted.md``.

Loopback serve stays ``127.0.0.1`` (bare viewer default port 8090; embed
hosts commonly use **8766**). Setting ``CE_PUBLIC_BASE`` does not change
the bind address.
"""

from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import parse_qs

HOSTED_SHELLS = frozenset({"switchbay", "okbay"})
HOST_HEADER = "X-CE-Host"
# Recommended public base when proxied (no trailing slash).
DEFAULT_EMBED_BASE = "/embed/ce"


def public_base() -> str:
    """Normalized public base path (no trailing slash), or \"\" when unset.

    Env: ``CE_PUBLIC_BASE`` — e.g. ``/embed/ce``.
    """
    raw = (os.environ.get("CE_PUBLIC_BASE") or "").strip()
    if not raw or raw == "/":
        return ""
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw.rstrip("/")


def strip_public_base(path: str) -> str:
    """Strip configured public base from an absolute request path."""
    base = public_base()
    if not base:
        return path or "/"
    p = path or "/"
    if p == base:
        return "/"
    if p.startswith(base + "/"):
        rest = p[len(base) :]
        return rest if rest else "/"
    return p


def normalize_hosted_shell(raw: str | None) -> str | None:
    """Return ``switchbay``|``okbay`` or None."""
    if not raw:
        return None
    h = str(raw).strip().lower()
    if h in HOSTED_SHELLS:
        return h
    return None


def hosted_shell_from_request(
    headers: Mapping[str, str] | None,
    query: Mapping[str, list[str]] | None = None,
) -> str | None:
    """Resolve hosted shell from ``X-CE-Host`` or ``?host=``.

    Header wins when both are present and valid.
    """
    if headers:
        for key, val in headers.items():
            if key.lower() == HOST_HEADER.lower():
                found = normalize_hosted_shell(val)
                if found:
                    return found
                break
    if query:
        vals = query.get("host") or []
        if vals:
            return normalize_hosted_shell(vals[0])
    return None


def hosted_shell_from_query_string(qs: str) -> str | None:
    if not qs:
        return None
    return hosted_shell_from_request(None, parse_qs(qs))


def viewer_bootstrap_js(*, hosted: str | None = None, api_base: str | None = None) -> str:
    """Inline script assigning window.CE_* for atlas/wiki/static under a proxy prefix."""
    base = public_base() if api_base is None else (api_base or "")
    base = base.rstrip("/")
    host = normalize_hosted_shell(hosted) or ""
    return (
        "window.CE_PUBLIC_BASE="
        + _js_str(base)
        + ";window.CE_HOSTED="
        + _js_str(host)
        + ";window.ceApi=function(p){var b=window.CE_PUBLIC_BASE||\"\";"
        "if(!p)p=\"/\";if(p.charAt(0)!==\"/\")p=\"/\"+p;return b+p;};"
    )


def _js_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def inject_viewer_bootstrap(html: bytes | str, *, hosted: str | None = None) -> bytes:
    """Inject bootstrap script before ``</head>`` (or start of ``<body>``)."""
    text = html.decode("utf-8") if isinstance(html, (bytes, bytearray)) else str(html)
    snippet = "<script>" + viewer_bootstrap_js(hosted=hosted) + "</script>\n"
    lower = text.lower()
    idx = lower.find("</head>")
    if idx >= 0:
        text = text[:idx] + snippet + text[idx:]
    else:
        bidx = lower.find("<body")
        if bidx >= 0:
            gt = text.find(">", bidx)
            if gt >= 0:
                text = text[: gt + 1] + "\n" + snippet + text[gt + 1 :]
            else:
                text = snippet + text
        else:
            text = snippet + text
    return text.encode("utf-8")


def hosted_settings_policy(hosted: str | None) -> dict:
    """Stub contract for shell-hosted CE (Phase 2a).

    Full settings UI split lands later. Today we only advertise intent:
    when ``host=switchbay|okbay``, shells own workspace/harness chrome;
    CE may later hide duplicate settings surfaces. Physics / viewer knobs
    remain available until a parity-gated follow-up.
    """
    shell = normalize_hosted_shell(hosted)
    return {
        "hosted": shell,
        "shell_owns_workspace_settings": shell is not None,
        "ce_viewer_knobs_enabled": True,  # stub: keep atlas/physics until 2b+
        "note": (
            "Hosted mode detected via X-CE-Host or ?host=. "
            "Settings chrome split is deferred; see ADR-001."
        ),
    }
