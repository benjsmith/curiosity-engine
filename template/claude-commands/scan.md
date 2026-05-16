---
description: Scan every registered project-dir, ingest new/changed files into the vault.
---

Walks every project-dir registered against this workspace (via `setup.sh --register-project-dir`), ingests new and changed files via the standard untrusted-content envelope, and marks orphaned extractions whose originals were deleted or moved.

Only `.extracted.md` files land in the vault — the originals stay where the user keeps them on the filesystem. See SKILL.md § SCAN for the full mechanics.

**Workspace context.** Apply the workspace-resolution rule from SKILL.md § Code-repo mode. SCAN operates on the named workspace.

**Action.** Run:

```bash
uv run python3 <skill_path>/scripts/scan.py all --workspace "$WORKSPACE"
```

Read the JSON output. Each `per_project` entry reports `to_ingest`, `changed`, `unchanged`, `orphans`, and `ingested_ok`/`ingested_failed` counts. Surface a one-line summary per project-dir to the user (e.g., "research: 5 new, 2 changed, 1 orphan").

If `totals.ingested_failed > 0`, show the failed paths and any error strings. Common causes: permission errors, files modified mid-scan, scrub_check quarantine.

If no project-dirs are registered (empty `per_project` list), tell the user: "no project-dirs registered against this workspace. Register one via `bash <skill_path>/scripts/setup.sh --register-project-dir` from the directory you want to track."

For a single project-dir without scanning the others, pass `--pointer <path/to/.curiosity/config.toml>` and use `scan.py one` instead. Useful when re-validating after edits to a specific pointer.

Optional argument:

$ARGUMENTS
