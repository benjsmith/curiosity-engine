# Changelog

Human-curated record of what shipped, grouped thematically. For the authoritative log see `git log`; this file exists to surface reversals, upgrades, and multi-commit rollouts that aren't legible from individual commit messages.

## 2026-05-03 — sandbox-safe uv cache

- **Workspace-local uv cache via `uv.toml`** (`a17ff1c`). `setup.sh` writes `uv.toml` with `cache-dir = ".curator/uv-cache"` so uv auto-discovers a workspace-local cache from cwd. Fixes Codex CLI escalation prompts on every `uv run` (Codex's filesystem sandbox blocks `~/.cache/uv` access). Host-agnostic — same config works under Claude Code / Codex / Gemini / Copilot. Cache is seeded by APFS clone (`cp -c`) on macOS or GNU reflink (`cp --reflink=auto`) on Linux btrfs/XFS, falling back to plain recursive copy or an empty directory uv populates lazily. Clone path is near-zero extra disk via copy-on-write sharing with `~/.cache/uv` until divergence.

## 2026-04-12 → 2026-05-01 — post-Phase 1 architecture maturation

Window: 173 commits from `51113b8` (citation-style source naming) through `1f2cdd2` (Codex sandbox warning). Starting state was the Phase 1 baseline: ITERATE + EVOLVE two-loop, three lint dimensions, one-worker-one-page, premature stop conditions, haiku citation-merging failures. Ending state is a single CURATE loop with parallel multi-session curators, multiple page-type buckets, kuzu graph backend, semantic vault search, custom graph-first viewer, multi-host CLI support, and named-preset model routing.

### Core architecture
- ITERATE + EVOLVE collapsed into a single CURATE loop (`d99be75`).
- `batch_brief.py` and `compress.py` deleted; `score_diff.py` stripped; `epoch_summary.py` added (`b6360b0`, `6254e94`). Raw-token gate replaces compression.
- `.curator/` workspace layout for curator state, `config.json`, `prompts.md` (`745b48c`).
- lint reweighted to 4×0.25; `naming.py` extracted; `sweep.py` slimmed (`d9ef31d`).
- All lint dimensions activated + Phase 1 attempt-3 design fixes (`fd0d560`).

### Parallelism (Phase 1 #ITERATE-parallelism gap — closed)
- Parallel CURATE: claims coordination + spawn helper (`ee67116`).
- Spawn dispatcher fixes: workspace `/curate` registration (`cfa90df`); Edit/Write approval stalls (`ce2df45`).
- Live watch dashboard added (`1c6d607`), then made default with `--no-watch` for detach (`9cfa4d3`). **Upgrade.**

### Cross-page edits (Phase 1 #38 — closed)
- LINK operation: fast propose/classify/apply wikilink pass (`de50a39`).
- Cluster-scoped repair waves for large wikis via `wave_scope` (`a0e71ac`).
- Create-mode quotas + demand promotions split entity-vs-concept (`1cbd1e1`).
- CURATE can create concept pages (demand-driven + analysis-spawned) (`20adb26`).

### Stop conditions / saturation (Phase 1 #39 — closed)
- Saturation trigger + worker lockdown + citation verification (`7dddef2`).
- Default reviewer_model → opus (`2fa8e7f`).

### Model routing & multi-host (#37 — closed, expanded)
- Named-preset routing: `claude` / `codex` / `gemini` presets, agent-driven allowlist install (`c4a1691`).
- Allowlist plumbing hardened across several iterations: dual logical/physical paths (`3c2de51`, `9e0db1f`, `19f6930`), symlink resolution (`88154f8`), independent root derivation (`451ae7f`).
- Update flow: `scripts/update.sh` for in-session skill updates (`305b9bf`); npx-skills fallback (`aabfff5`); bare-name fix (`b016afa`); Copilot PTY hang removed (`f088a9b`); Codex sandbox warning + timeout (`1f2cdd2`).

### Knowledge graph & search
- kuzu graph DB as first-class knowledge graph (`b33e2f4`), wired into `connection_candidates` (`47e9f01`); inbound counts use kuzu (`e6a297b`); stale-graph surfacing fix (`933eadd`).
- Optional semantic vault search (MiniLM + sqlite-vec) + tiered-vault stubs (`7f798bf`); pysqlite3 fallback (`daf5547`).
- Graph-expand search stream + auto-file-as-analysis on synthesis queries (`97b1323`).
- Semantic dedup for sync-notes via sqlite-vec + MiniLM (`035f187`).
- FTS5 sanitization for hyphenated/reserved tokens (`e9e8749`); `%%` collapse (`977e9b4`).
- Identifier normalisation cache (lazy, offline) (`b720a17`).
- Incremental per-page score cache in `lint_scores.compute_all` (`215233e`).

### Page types (new buckets)
- **Facts/evidence** bucket (`8996763`); rebalance so paper findings → evidence not facts (`bdff91c`); facts gate-floor mismatch fixed (`baeb674`).
- **Notes + todos** scaffolding, types, floors, templates, slash commands (`0306e96`); sync-todos / sync-notes / Note graph node (`6882f75`); CURATE integration + `notes_curator` (`a2ff731`); todos consolidated onto `wiki/todos.md` (`08779ec` — **consolidation of earlier scaffolding**).
- **Figure pages** — 4-phase rollout: naming + score floors (`3a000c5`), `figures.py` extract/check/regen (`d87e19b`), wiring (`4b43a6a`), resync-prefixes migration (`ff92d57`), pages + render-all (`bfe197a`), figure_extractor worker + INGEST docs (`f7f20a8`), demand signal (`b57cdc1`), kuzu Depicts edge (`bec9c81`), pending-figures (`9407930`), `--purge` unreferenced (`460381d`). Figure assets relocated to `wiki/figures/_assets/` (`f8cf5a1` — **Option 3 migration; replaces earlier layout**). Inline figure rendering in viewer modal (`3eb610f`).
- **Class-entity tables** — 4-phase rollout: core mechanism (`bb2db83`), summary tables + graph (`6298c6f`), audit/risk telemetry/conversational capture (`2b50ece`), governance + schema evolution (`de9055a`). Canonical `[tbl]` title prefix + GFM renderer (`001d0b1`).

### Tabular ingest (newest, post-table-class)
- csv/xlsx/pptx ingest + pdfplumber + `[tab]` promotion (`5ef066e`).
- xlsx hierarchical headers + extracted-query (`67f8ff0`).
- Multimodal-table-extract wave (Sonnet worker) (`35f5b93`).
- Numeric-review wave + tab-page spot-check anchors (`002ee28`).
- Orphan-source priority-targets + tabular extraction baseline harness (`774c5fc`).
- PDF hybrid extraction with multimodal queue + evidence-demand trigger (`44af27e`).

### Viewer (full replacement late in cycle)
- **Quartz static-site viewer added** (`7e38d88`) → **Quartz removed** when graph-first custom viewer matured (`f3e10d3`). **Reversal.**
- `viewer.sh` graph-first viewer introduced (`ed1133a`), then ~15 iterations: subgraph navigator (`ee4b822`), inline figure rendering (`3eb610f`), label/source overhaul (`37cbe23`), drag/modal/sidebar groups, palette remap (`6ddc1dc`), label-type filter (`ee5f19e`), inline edit + vault upload from sidebar (`ce032a7`), favicon iterations (`b7a3fd8`, `c162b22`, `d266cb6`, `cad1aab`), perf settle drop (`c05d81d`).
- `wiki_viewer_mode` switch (`f86abc2`) — superseded once `viewer.sh` became single path.

### Naming / migration / hygiene
- Citation-style source naming + type-prefix display titles (`51113b8`); skill-update migration pass for citation_stem (`ab4d195`); resync-stems validates computed stem (`a2ef2d6`), tolerates binary-only stubs (`0464d98`); single-suffix binary naming + `normalize-vault-suffixes` migration (`6ebd31c`); naming rejects non-name authors + sanitizes stems (`f0d0533`); fragmented-stem rejection (`18f1895`).
- fix-frontmatter-quotes, dedupe-self-citations, smarter collision handling (`794919b`).
- Title-Case wikilink detection/rewrite/gating for Obsidian compat (`2383494`).

### Caveman integration
- Caveman ultra read, ultra/lite write by page type (`f884a9e`).
- Workers invoke caveman skill instead of following an inline spec (`ca9020b` — **simplification, removes earlier inline**).
- Caveman subagent + graph rebuild short-circuit (`1288b72`).

### Hardening / security
- Hash-guard `sweep.py` + remove agent-editable workspace copy (`82ae931`).
- Security + correctness hardening across scoring/ingest (`ad6664a`); harden scoring/naming/scrub/guard (`8d67f62`); vault dedup via sha256 + robust stub matching (`0e4ad32`).
- Sampled spot auditor in CURATE Phase 3 (`9cbc35f`).

### Setup / install
- uv canonical (`977e9b4`); pre-uv settings detection (`915a479`); template re-copy (`df69775`); `.venv` Python-version drift detect (`e41b4d8`); preflight + `.gitattributes` (`471e056`); `/tmp` scratch + skill-script reads auto-allowed (`e45bb0f`); merge new config keys from template (`ca4e679`); `naming.py` allowlist fix (`632f2c8`).

### Two larger refactors
- **Three-object mental model rewrite** — README + `docs/architecture.md` (`3ea62b0`). Current docs framing dates from here.
- **Five generality improvements from multi-domain study** (`877ead1`) — derived from the `curiosity-multidomain-test` workspace.

### Net summary
- Phase 1 architecture-fix gate (#37 model routing, #38 cross-page edits, #39 stop conditions) — **fully closed**.
- New capability shipped on top: figures, class-entity tables, tabular ingest, multimodal extraction, custom viewer, multi-host support.
- Major reversals: Quartz viewer dropped for in-house viewer; ITERATE/EVOLVE collapsed into CURATE; `compress.py` and `batch_brief.py` deleted; figure assets relocated; todos consolidated onto a single page; inline caveman spec replaced by skill invocation.
