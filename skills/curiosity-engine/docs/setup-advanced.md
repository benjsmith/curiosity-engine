# Setup — advanced

Edge cases, alternative CLIs, model presets (incl. fully local via
Ollama), deployment notes. For first-time install see the main
README's Quick Start.

## Backing up the wiki

The `wiki/` folder is its own git repository, independent of the
workspace. Push it to GitHub / GitLab / internal to back it up and
sync across machines:

```bash
cd my-research/wiki
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

## Updating the skill without exiting the session

Ask the agent to "update the skill". It runs `scripts/update.sh`,
which detects the install channel automatically — `git pull --ff-only`
for git installs (detected by walking up from the skill dir to a work
tree that is actually this skill's repo; since v0.7.0 the skill lives
at `skills/curiosity-engine/` inside the repo, and the documented git
install is a clone plus a symlink from the agent's skills dir into
that subfolder), or a version-pinned `npx skills@<pinned> update -g
<slug>` for npx-skills installs (pinned defensively — skills
1.5.13–1.5.16 had a root-layout regression that bricked the
pre-v0.7.0 layout; the subdirectory layout installs correctly on all
versions, and the pin is `SKILLS_CLI_VERSION` in update.sh) — prints
a preview (commit log for git, update plan for npx), and waits for
you to confirm. Once confirmed, it auto-commits any in-progress wiki
edits with a canned `wip: auto-commit before skill update` message,
snapshots the skill dir, applies the update, verifies the install is
still complete (rolling back to the snapshot if the CLI left a
partial tree), and runs `setup.sh` to apply any migrations. The
upstream npx-skills slug is hardcoded in `update.sh`; fork users
pass `--source <owner>/<repo>` on each update. (The
`update_source_slug` key in `.curator/config.json` is intentionally
not consulted — keeping update sources out of editable config
closes the slug-flip vector.)

## Running in other coding-agent CLIs

Same `setup.sh` works; `.claude/settings.json` is read by Claude
Code only. The first time you drive the workspace from a non-Claude-
Code host, the orchestrator detects it (env-var fingerprint) and
offers a **one-time approval-gated install** of the bash allowlist
into the host's own config — single Y/n prompt with a diff preview,
then it backs up the host file and writes the translated patterns.
After that the host treats curiosity-engine bash calls as pre-
approved and autonomous loops run uninterrupted. The marker
`.curator/.allowlist-installed-<host>` records the install so the
proposal doesn't repeat; delete it to re-trigger.

If the host isn't recognised or its allowlist schema has moved, the
orchestrator falls back to printing the patterns and asking you to
paste them in manually rather than guessing.

- **OpenClaude** — drop the skill into `~/.openclaude/skills/`;
  skill-path substitution works. Not in the host registry, so the
  allowlist install takes the manual-paste fallback above.
- **Codex CLI** — clone into a known scripts directory and export
  `CURIOSITY_ENGINE_SCRIPTS_DIR=<path>/scripts` so prompts without
  `<skill_path>` substitution still resolve. The auto-install writes
  to `~/.codex/config.toml`.
- **GitHub Copilot Chat (VS Code)** — clone anywhere, open the
  workspace folder in VS Code, and paste the contents of `SKILL.md`
  into the chat's workspace instructions. The single-chat-window
  flow works: Copilot runs as the orchestrator, dispatches subagents
  where supported, and falls back to sequential in-session workers
  with explicit role-reset prompts where not (see
  `SKILL.md#single-session-fallback`). The auto-install writes to
  your VS Code user `settings.json` (or workspace
  `.vscode/settings.json` if you prefer per-project scope — pick at
  the prompt).
- **Gemini CLI** — clone anywhere, export
  `CURIOSITY_ENGINE_SCRIPTS_DIR`. The auto-install writes to
  `~/.gemini/settings.json`.
- **Cursor** — clone anywhere; auto-install writes to Cursor's user
  `settings.json` (path varies per OS, listed in `SKILL.md`'s host
  registry).

## Running with different models (incl. fully local via Ollama)

Models are picked per-session, not per-machine. `.curator/config.json`
carries a named-preset map plus an `active_preset` default; the
orchestrator resolves which preset is active by checking the
`CURATOR_PRESET` env var first, then falling back to `active_preset`.
So one workspace can be driven from Claude Code one minute and Codex
CLI the next without editing the file:

```bash
# Default — uses active_preset from config.json
claude

# Per-session override — same workspace, different backend
CURATOR_PRESET=codex codex

CURATOR_PRESET=gemini gemini
```

The shipped config seeds three presets:

```json
{
  "active_preset": "claude",
  "presets": {
    "claude": { "worker_model": "claude-sonnet-4-6", "reviewer_model": "claude-opus-4-6" },
    "codex":  { "worker_model": "gpt-5",             "reviewer_model": "gpt-5" },
    "gemini": { "worker_model": "gemini-2.5-pro",    "reviewer_model": "gemini-2.5-pro" }
  }
}
```

A preset block may carry per-preset overrides for `parallel_workers`,
`wallclock_max_hours`, etc. — useful when a backend wants different
concurrency or wallclock limits (the Ollama example below halves
both). Edit `active_preset` for a per-project default; export
`CURATOR_PRESET` for a per-session swap. See
`template/config.example.json` for copy-paste-ready Ollama and
mixed-vendor blocks.

**Fully local via Ollama.** Requires an Ollama-compatible coding-
agent CLI (Continue.dev, Cody, or Claude Code routed through an
OpenAI-compatible proxy). `ollama serve` locally, `ollama pull` your
chosen models, then add an `ollama` preset to `.curator/config.json`
(see `config.example.json`). Caveats: open-weight models will drop
citations more often than frontier Sonnet/Opus — tune
`parallel_workers` down inside the preset block and expect more
`score_diff` rejections. Semantic search still works locally (the
embedding model runs offline via fastembed/ONNX, or
sentence-transformers as fallback). The deterministic table-
extraction tier (`local_ingest.py` + `sweep.py promote-extracted-
tables`) runs purely on local Python libraries (pdfplumber / openpyxl
/ python-pptx) and is unaffected by model choice; if you later add a
worker-model pass to interpret extracted scientific tables, that
pass benefits from frontier models per the design principles cited
in `docs/citation.md`.

**Enterprise notes.** No code sends wiki/vault content anywhere
except to the model API your CLI drives; swap to Ollama for fully
on-prem. PyPI access is required at setup time; HuggingFace egress
is required only if you opt into semantic search (can be pre-staged
via `HF_HOME`).

## Deployment notes

- **Disk footprint.** Rough guide: `vault/` ≈ the size of your
  source PDFs (~50 MB per 100 academic papers). `vault.db` adds
  ~10–30% for FTS5 indexing. Semantic embeddings (opt-in) add ~0.5
  MB per indexed line — ~200 MB for a 100-source vault.
  `wiki/figures/_assets/` at 150 DPI is ~0.3–0.6 MB per rendered
  page; figure extraction typically renders 5–20 pages per source.
  Budget a few GB for a 100-source knowledge base with semantic
  search + figures on.
- **Backup & restore.** `wiki/` is a git repo — push it wherever you
  back up code. `vault/` holds your raw sources — back it up like
  any data folder; re-ingest is expensive (it's what you pay the
  curator to do). `vault.db`, `graph.kuzu`, and
  `wiki/figures/_assets/` are all derived and auto-regenerate from
  vault + wiki on the next `setup.sh` / `graph.py rebuild` /
  `figures.py regen` run (the asset folder is gitignored inside the
  wiki repo for the same reason). The one non-regeneratable store is
  `.curator/tables.db` (class-entity row data is source-of-truth in
  SQLite, not derivable from git-tracked files) — back it up
  separately if you've used class tables.
- **Rendering on GitHub and raw markdown viewers.** By default, wiki
  figure and summary-table pages use Obsidian's `![[asset.png]]`
  transclusion syntax. The built-in graph viewer and Obsidian both
  render these inline; GitHub and generic markdown viewers show
  them as literal text. Set `wiki_viewer_mode: "vscode"` in
  `.curator/config.json` and re-run setup.sh to convert embeds to
  standard `![](path)` syntax for VS Code / Foam / GitHub renderers
  — the underlying PNGs are unchanged.
- **No-network / air-gapped install.** `setup.sh` never pipes
  installer scripts; if `uv` is missing it prints platform-specific
  install commands and exits. Pre-install uv (brew, `pip install
  --user uv`, or an inspected copy of the official install script)
  and re-run `setup.sh`. For pypdfium2 / Pillow / kuzu / pyyaml
  — pre-populate a PyPI mirror and `pip install` them; setup.sh
  uses `uv pip install` which respects `UV_INDEX_URL` /
  `PIP_INDEX_URL` for internal mirrors.
- **Identifier resolution (chemicals + genes).** When a `[tab]`
  page has a chemistry or gene-symbol column, synthesis workers can
  resolve names to canonical IDs (SMILES, InChI, Ensembl, UniProt)
  on demand via `identifier_cache.py`. PubChem PUG-REST handles
  chemicals; MyGene.info handles genes. Both are free and require
  no API keys. Resolutions cache to `.curator/identifiers.db`
  (SQLite, WAL) so repeated lookups don't re-hit the network.
  Air-gapped use needs no flag — offline is detected per lookup:
  cached entries are served when present, failed network calls
  return `status: offline` markers and are re-tried on later
  lookups.
  Lazy: never invoked at ingest, only at synthesis time when a
  worker cites a row.

## Wiring orphan sources after a bulk ingest

After a large `local_ingest.py` run, most newly-created
`wiki/sources/*.md` stubs have zero inbound wikilinks. Say "wire up
orphan sources" or just "link" — both map to the LINK pass, which
now pre-ranks orphan stubs as `priority_targets` and instructs the
proposer to spend ≥60% of its proposal budget on them. If a weaker
reviewer model still misses them, run

```
uv run python3 <skill_path>/scripts/sweep.py orphan-sources wiki --limit 30
```

and paste the output into chat. It returns the worst-orphaned source
stubs alongside up to 3 best-fit concept/entity pages each, so the
agent can wire them directly without inferring the frontier from
prose.
