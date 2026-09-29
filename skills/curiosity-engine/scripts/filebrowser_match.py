"""Filebrowser path matchers — parity with Switchbay FileBrowser buildMatcher.

Empty query → match all. Supports:
  - /pattern/flags  (invalid regex → None, so UI can show feedback)
  - *.ext           filetype-only (case-insensitive)
  - plain substring (case-insensitive against the full relative path)
"""
from __future__ import annotations

import re
from collections.abc import Callable

Matcher = Callable[[str], bool]


def file_ext(path: str) -> str:
    """Match Switchbay FileBrowser.fileExt (last dot after last slash)."""
    slash = path.rfind("/")
    dot = path.rfind(".")
    if dot <= slash:
        return ""
    return path[dot + 1 :].lower()


def build_matcher(query: str) -> Matcher | None:
    """Return a matcher, or None when the regex form is invalid."""
    q = (query or "").strip()
    if not q:
        return lambda _p: True

    m = re.fullmatch(r"/(.+)/([gimsuy]*)", q)
    if m:
        pattern, flags_s = m.group(1), m.group(2) or "i"
        flag = 0
        if "i" in flags_s:
            flag |= re.IGNORECASE
        if "m" in flags_s:
            flag |= re.MULTILINE
        if "s" in flags_s:
            flag |= re.DOTALL
        try:
            rx = re.compile(pattern, flag)
        except re.error:
            return None
        return lambda p, _rx=rx: _rx.search(p) is not None

    glob = re.fullmatch(r"\*\.([A-Za-z0-9_]+)", q)
    if glob:
        ext = glob.group(1).lower()
        return lambda p, _e=ext: file_ext(p) == _e

    lower = q.lower()
    return lambda p, _l=lower: _l in p.lower()


def filter_paths(paths: list[str], query: str) -> tuple[list[str] | None, bool]:
    """Filter paths. Returns (matches_or_None_on_bad_regex, ok)."""
    matcher = build_matcher(query)
    if matcher is None:
        return None, False
    return [p for p in paths if matcher(p)], True
