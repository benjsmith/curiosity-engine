#!/usr/bin/env python3
"""scrub_check.py — prompt-injection tripwire for curator writes.

Scans a markdown file for known injection markers. Exit 1 if any match.

Three modes:
  --mode wiki    Agent-authored wiki pages (before commit). Checks the
                 authored body AND any FETCHED CONTENT blocks. Also bans
                 raw URLs in the authored body.
  --mode vault   Vault extractions at ingest time. Checks FETCHED CONTENT
                 blocks for injection markers. Advisory: hits are logged
                 but the file is still ingested (quarantine is the caller's
                 job).
  --mode ingest  Alias for vault — used in the INGEST pipeline.

Frontmatter is always skipped (structured metadata, not prose).

Usage:
    scrub_check.py --mode wiki <path.md>
    scrub_check.py --mode vault <path.md>
"""

import argparse
import json
import re
import sys
from pathlib import Path

MARKERS = [
    (r"ignore\s+(all\s+)?(the\s+)?previous\s+instructions", "ignore-previous-instructions"),
    (r"disregard\s+(all\s+|the\s+)?(previous|above)", "disregard-previous"),
    (r"forget\s+(all\s+|the\s+)?(previous|above)\s+instructions", "forget-previous"),
    (r"new\s+instructions:\s*", "new-instructions"),
    (r"override\s+(your|the)\s+(rules|instructions|schema|policy)", "override-rules"),
    (r"system\s+prompt", "system-prompt-ref"),
    (r"\bSYSTEM:\s", "system-directive"),
    (r"\[INST\]", "chatml-inst"),
    (r"\[/INST\]", "chatml-inst-close"),
    (r"###\s*Instruction", "alpaca-instruction"),
    (r"<\|im_start\|>", "chatml-start"),
    (r"<\|im_end\|>", "chatml-end"),
    (r"<\|system\|>", "chatml-system"),
    (r"<\|assistant\|>", "chatml-assistant"),
    (r"you\s+are\s+now\s+(a|an|the)\s+", "persona-hijack"),
    (r"act\s+as\s+if\s+you\s+are", "persona-hijack-act"),
    (r"pretend\s+(to\s+be|you\s+are)", "persona-hijack-pretend"),
    (r"reveal\s+(your|the)\s+(system\s+)?prompt", "reveal-prompt"),
    (r"exfiltrate|base64\s+encode|curl\s+[^\n]*\|", "exfil-pattern"),
]

URL_IN_BODY = re.compile(r"https?://[^\s)\"'`<>]+")

FETCHED_BLOCK = re.compile(
    r"<!--\s*BEGIN FETCHED CONTENT.*?END FETCHED CONTENT\s*-->",
    flags=re.DOTALL,
)


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            return text[end + 5:]
        end = text.find("\n---", 4)
        if end > 0:
            return text[end + 4:]
    return text


def _scan_markers(text: str) -> list:
    low = text.lower()
    return [name for pattern, name in MARKERS if re.search(pattern, low)]


def scan(text: str, mode: str) -> list:
    body = strip_frontmatter(text)
    hits = []

    if mode == "wiki":
        hits.extend(_scan_markers(body))
        authored = FETCHED_BLOCK.sub("", body)
        if URL_IN_BODY.search(authored):
            hits.append("url-in-body")
    else:
        fetched_blocks = FETCHED_BLOCK.findall(body)
        for block in fetched_blocks:
            hits.extend(_scan_markers(block))
        if not fetched_blocks:
            hits.extend(_scan_markers(body))

    return list(dict.fromkeys(hits))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--mode", choices=["wiki", "vault", "ingest"], default="wiki")
    args = ap.parse_args()
    mode = "vault" if args.mode == "ingest" else args.mode
    if not args.path.exists():
        print(json.dumps({"path": str(args.path), "error": "missing"}))
        return 2
    text = args.path.read_text()
    hits = scan(text, mode)
    print(json.dumps({"path": str(args.path), "mode": mode, "hits": hits}))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
