#!/usr/bin/env python3
"""scrub_check.py — prompt-injection tripwire for curator writes.

Scans one or more markdown files for known injection markers. Exit 1 if
any hit, exit 2 if any path is missing, else exit 0.

Three modes:
  --mode wiki    Agent-authored wiki pages (before commit). Applies
                 STRONG imperative markers to authored prose and bans
                 raw URLs. LLM subject-vocabulary markers ("system
                 prompt", ChatML tokens) are skipped — they false-
                 positive on wikis about LLMs. Any FETCHED CONTENT
                 blocks quoted inside the page are scanned with the
                 full ruleset (third-party material, treat as suspect).
  --mode vault   Vault extractions at ingest time. Applies the full
                 ruleset inside FETCHED CONTENT blocks. Advisory:
                 hits are logged but the file is still ingested
                 (quarantine is the caller's job).
  --mode ingest  Alias for vault — used in the INGEST pipeline.

Frontmatter is always skipped (structured metadata, not prose).

Usage:
    scrub_check.py --mode wiki <path.md> [<path.md> ...]
    scrub_check.py --mode vault <path.md>
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Strong imperative injection markers. Direct commands addressed to
# the reading agent; rare in any legitimate prose; applied in every
# mode.
STRONG_MARKERS = [
    # Direct command-override patterns — extremely rare in any
    # legitimate prose.
    (r"ignore\s+(all\s+)?(the\s+)?(previous|above|prior|earlier)\s+(instructions|rules|prompts?)", "ignore-previous-instructions"),
    (r"disregard\s+(all\s+|the\s+)?(previous|above|prior|earlier)", "disregard-previous"),
    (r"forget\s+(all\s+|the\s+)?(previous|above|prior|earlier)\s+(instructions|rules|prompts?)", "forget-previous"),
    (r"new\s+instructions:\s*", "new-instructions"),
    (r"updated\s+instructions:\s*", "updated-instructions"),
    (r"override\s+(your|the)\s+(rules|instructions|schema|policy|prompt|safety|guidelines)", "override-rules"),
    (r"bypass\s+(your|the|all|any)\s+(rules|instructions|safety|guidelines|filters?)", "bypass-rules"),
    # Persona hijack — telling the agent it has a different identity.
    (r"you\s+are\s+now\s+(a|an|the)\s+", "persona-hijack"),
    (r"act\s+as\s+if\s+you\s+are", "persona-hijack-act"),
    (r"pretend\s+(to\s+be|you\s+are)", "persona-hijack-pretend"),
    # Note: input is lowercased before matching, so all literal
    # tokens here must be lowercase. `dan` (lowercase) matches both
    # `DAN` and `Dan` in the original input.
    (r"\bdan\b\s+mode|\bdeveloper\s+mode\b|\bjailbreak\s+mode\b", "jailbreak-persona"),
    (r"\b(do\s+anything\s+now)\b", "dan-spelt-out"),
    # Prompt-extraction attacks.
    (r"reveal\s+(your|the)\s+(system\s+)?prompt", "reveal-prompt"),
    (r"(show|print|output|display|repeat|echo)\s+(your|the)\s+(system\s+)?(prompt|instructions|rules)", "leak-prompt"),
    (r"what\s+(are|were)\s+your\s+(system\s+)?(prompt|instructions|rules)", "ask-prompt"),
    (r"\brepeat\s+(verbatim|exactly|word\s+for\s+word)\b", "repeat-verbatim-jailbreak"),
    # Exfiltration / shell-execution prompts.
    (r"exfiltrate|base64\s+encode|curl\s+[^\n]*\|", "exfil-pattern"),
    (r"(execute|run|eval)\s+the\s+following\s+(code|command|script)", "execute-following"),
    (r"\b(rm\s+-rf|chmod\s+\+x|wget\s+\S+\s*\|\s*(sh|bash))", "shell-exec-pattern"),
    # Browser-side script injection (rendered HTML viewers can be
    # tricked into executing inline JS).
    (r"<\s*script\b", "script-tag"),
    (r"\bjavascript:", "javascript-uri"),
    (r"\bdata:text/html", "data-uri-html"),
    (r"\bonerror\s*=", "html-onerror-handler"),
    (r"\bonclick\s*=", "html-onclick-handler"),
]

# LLM subject-vocabulary markers. Legitimate in prose about LLMs and
# prompting ("system prompt", ChatML tokens in a ChatML analysis,
# etc.), so they false-positive heavily on authored wiki content. We
# apply them in vault mode (raw sources from outside, where these
# tokens are more suspicious) and skip them in wiki-authored prose.
LLM_VOCAB_MARKERS = [
    (r"system\s+prompt", "system-prompt-ref"),
    (r"\bSYSTEM:\s", "system-directive"),
    (r"\[INST\]", "chatml-inst"),
    (r"\[/INST\]", "chatml-inst-close"),
    (r"###\s*Instruction", "alpaca-instruction"),
    (r"<\|im_start\|>", "chatml-start"),
    (r"<\|im_end\|>", "chatml-end"),
    (r"<\|system\|>", "chatml-system"),
    (r"<\|assistant\|>", "chatml-assistant"),
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


def _scan_markers(text: str, ruleset: str) -> list:
    """Scan with STRONG markers; add LLM_VOCAB when ruleset != 'authored'."""
    low = text.lower()
    markers = STRONG_MARKERS
    if ruleset != "authored":
        markers = STRONG_MARKERS + LLM_VOCAB_MARKERS
    return [name for pattern, name in markers if re.search(pattern, low)]


def scan(text: str, mode: str) -> list:
    body = strip_frontmatter(text)
    hits = []

    if mode == "wiki":
        # Authored wiki prose outside any FETCHED CONTENT block: strict
        # imperative markers only. LLM subject vocabulary is allowed.
        authored = FETCHED_BLOCK.sub("", body)
        hits.extend(_scan_markers(authored, "authored"))
        if URL_IN_BODY.search(authored):
            hits.append("url-in-body")
        # Any quoted FETCHED CONTENT is third-party material — scan
        # with the full ruleset.
        for block in FETCHED_BLOCK.findall(body):
            hits.extend(_scan_markers(block, "full"))
    else:
        fetched_blocks = FETCHED_BLOCK.findall(body)
        for block in fetched_blocks:
            hits.extend(_scan_markers(block, "full"))
        if not fetched_blocks:
            hits.extend(_scan_markers(body, "full"))

    return list(dict.fromkeys(hits))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path,
                    help="one or more markdown file paths to scan")
    ap.add_argument("--mode", choices=["wiki", "vault", "ingest"], default="wiki")
    args = ap.parse_args()
    mode = "vault" if args.mode == "ingest" else args.mode

    exit_code = 0
    for path in args.paths:
        if not path.exists():
            print(json.dumps({"path": str(path), "error": "missing"}))
            exit_code = max(exit_code, 2)
            continue
        text = path.read_text()
        hits = scan(text, mode)
        print(json.dumps({"path": str(path), "mode": mode, "hits": hits}))
        if hits:
            exit_code = max(exit_code, 1)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
