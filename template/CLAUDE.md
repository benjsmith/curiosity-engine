# Curiosity Engine

A self-improving knowledge wiki. Uses the `curiosity-engine` skill.

This file mirrors the shared sections of the skill's SKILL.md so a subagent
spawned inside this workspace inherits the same discipline. If this file
drifts from SKILL.md's shared sections, SKILL.md wins — regenerate this
file from the template.

## Layout
- `vault/` — raw source files + FTS5 search index. Append-only.
  - `vault/raw/` — drop folder. User drops files here; `local_ingest.py`
    extracts, moves originals to `vault/`, removes from drop folder.
- `wiki/` — git-tracked markdown content. Subdirs: `sources/`, `entities/`,
  `concepts/`, `analyses/`, `evidence/`, `facts/`.
- `.curator/` — curator state, not git-tracked.
  - `schema.md`, `prompts.md`, `config.json` — human-edited.
  - `graph.kuzu` — kuzu property graph (WikiPage/VaultSource nodes,
    WikiLink/Cites edges). Rebuilt via `graph.py rebuild wiki`.
  - `log.md`, `index.md`, `.epoch_plan.md`, `.guard.snapshot` — auto.
    Improvement ideas for skill scripts land in `log.md` under
    `## improvement-suggestions` — prose only, no agent-generated code
    enters execution (all skill scripts are hash-guarded).

Read `.curator/schema.md` before any operation.

## Quick commands
- "Add <file> to the vault" — ingest a source
- "What do I know about X?" — query the wiki
- "Lint" — check wiki health
- "Sweep" / "clean up" — mechanical hygiene pass
- "Link" / "wire up" / "connect pages" — fast propose→classify→apply wikilink pass
- "Curate" / "run" / "improve" / "iterate" — autonomous CURATE loop

## Naming (naming.py)

All page-type prefixes and citation-style stems come from
`<skill_path>/scripts/naming.py`. Workers and reviewers that create or
rename pages must use `citation_stem`, `source_display_title`, and the
`TYPE_PREFIX` dict — never invent a new scheme. `naming.py` is
hash-guarded; the skill enforces consistency across workers.

## Bash discipline (hard rule)

This workspace is designed for uninterrupted autonomous loops. Approval
prompts break that, so the bash surface is deliberately tiny. The ONLY
bash commands allowed:

1. `git -C wiki <subcmd> ...` — never `cd wiki && git ...`, never flags
   before `-C`
2. `uv run python3 <skill_path>/scripts/<named_script>.py ...` — never bare
   `python3`, never `-c "..."`. The `uv run` prefix auto-discovers the
   workspace `.venv` (created by setup.sh) so imports like `kuzu` resolve.
   Covers sweep.py, graph.py, lint_scores.py, score_diff.py,
   epoch_summary.py, scrub_check.py, vault_index.py, vault_search.py,
   local_ingest.py — all hash-guarded skill scripts.
3. `bash <skill_path>/scripts/evolve_guard.sh ...`
4. `date ...`

**For everything else, use the tool layer:**
- Read (not `cat`/`head`/`tail`/`less`)
- Glob (not `ls`/`find`)
- Grep (not `grep`/`rg`)
- Edit / Write (not `sed`/`mv`/`cp`/`touch`/`rm`/`>>`/`>`)

**No compound shell:** no pipes (`|`), no `&&`, no `$(...)`, no backticks,
no heredocs, no inline scripts. One command per bash call. If you need two
things, make two calls.

**Why:** every other bash command either has a safe tool-layer equivalent
or cannot be scoped to the workspace via prefix matching without risking
the user's wider filesystem. Breaking this rule means approval prompts,
which means an autonomous loop stops.

Subagents spawned from this workspace inherit the same rule. Include the
discipline block verbatim in every Agent prompt.
