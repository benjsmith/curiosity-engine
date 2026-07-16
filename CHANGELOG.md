# Changelog

Human-curated record of what shipped, grouped thematically. For the authoritative log see `git log`; this file exists to surface reversals, upgrades, and multi-commit rollouts that aren't legible from individual commit messages.

## 2026-07-16 — v0.8.3 — entity-resolution abstention gate

**Entity-resolution abstention gate on the synthesis path** — queries for non-existent / look-alike entities no longer answer from a proximity match to a similarly-named entity; known aliases still resolve.

Downstream benchmarking (switchyard alias-resolution harness) found a false-bridging bug: when a query names a look-alike that does not exist ("Project Onyxx" when only "Project Onyx" is curated), retrieval surfaces the real entity by lexical/embedding proximity and the model reports that entity's fact. The wrong-entity fact was present in every retrieval arm's context (curated wiki and raw vault) — so this is not a fusion problem. Abstention was left to the LLM noticing a name mismatch, and it degraded as corroborating wrong-entity evidence accumulated (~12% false-bridge rate on look-alike queries).

- **`entity_gate.py`** (new, hash-guarded): deterministic gate — extract entity mentions from the query, resolve each against the curated identity layer (page titles/stems, frontmatter `aliases` / `same_as` / `iri`, IRI registry, wikilink pipe-aliases). Exact and known-alias matches resolve; fuzzy proximity to a differently-named entity never resolves. Unresolved names with no verbatim occurrence anywhere in the workspace **abstain**. Mention extraction is capitalisation-first plus an identity-aware n-gram pass so all-lowercase queries are gated the same way as Title Case.
- **`graph.py retrieve`**: runs the gate before seeding; full abstain returns empty `pages`/`vault` (no wrong-entity context to dilute the signal); resolved mentions pin their curated page as the lead seed.
- **`query_router.py classify`**: embeds the same verdict on synthesis routes so the answering agent sees an explicit `ABSTAIN` directive.
- **`aliases` frontmatter** documented and allowlisted in `naming.py` / `template/schema.md` as the curated-synonym surface the gate resolves through.
- **Regression suite** at `tests/test_entity_gate.py` (fixture workspace, no network): canonical name and known aliases answer; "Project Onyxx" / "Project Marlon" abstain and never surface the real entity's fact.

Measured target on the same corpus: look-alike false-bridge 0.12 → 0.00 at zero accuracy cost on real entities and known aliases.

## 2026-07-16 — v0.8.2 — first-run todos.md fix + setup-guide corrections

- **Bug fix — `sweep.py purge-template-todo-artefacts`**: the migration op stripped everything after a literal `(todo:T<id>)` placeholder on *any* line, which truncated the seeded `wiki/todos.md` overview prose ("get a minted `(todo:T<id>)` suffix.") during the very first `setup.sh` run and left a fresh wiki dirty. The strip now applies only to checkbox lines — the only place the pre-fix sync-todos pollution it undoes ever landed. Verified: a fresh workspace now comes up with a clean wiki; simulated pollution on a checkbox placeholder line is still stripped.
- **docs/setup-advanced.md**: removed the OpenClaude section (and the README's mention of it). The GitHub Copilot Chat (VS Code) instructions now describe the recommended install path: open your repo in VS Code and install from the integrated terminal with `npx skills add benjsmith/curiosity-engine` (`-g` for global, or as-is for the open project folder), keeping the GitHub Copilot target ticked in the CLI's agent picker (it is by default) — instead of pasting SKILL.md into workspace instructions.

## 2026-07-16 — v0.8.1 — documentation accuracy pass

A full docs-vs-code audit. Every user-facing doc was checked claim-by-claim against the scripts; no behavior changes except one bug fix that aligns code with its own documentation.

- **README**: page-type count corrected — eleven, not eight (`notes`, `todos`, `projects` were missing everywhere the types were enumerated, including the architecture diagram). The "Three stores, three verbs" framing was misleading and is now the accurate inventory: two content stores (vault, wiki), two derived databases (SQLite, kuzu graph), one curator, three commands (`ingest`/`query`/`curate`). Dropped the nonexistent `claude skill install` channel (the two real channels: `npx skills add`, git clone + symlink). uv is *not* auto-installed by setup.sh — it prints install commands and exits.
- **docs/architecture.md**: eleven subdirectories; the wave-mode table now lists all **seven** modes (`figure-extract`, `multimodal-table-extract`, `numeric-review` were missing) and the create-mode summary-tables 10% bucket; the new-page floor list covers all per-directory relaxations; the hash-guard list defers to the 21-entry `GUARDED` array instead of naming 8 scripts; stale "sweep copy" dropped from `.curator/` contents (same fix in `template/schema.md`); the single-user limitation now points at `curiosity-merge` for asynchronous extract-and-merge sharing.
- **docs/code-knowledge.md**: aligned to the shipped implementation — `--init-workspace` takes no path (pair it with `--ce-workspace-path`); the session drainer has two modes (one-shot, per-session `/distill`); the session brief is built by filename-stem matching against `wiki/entities/` (not a `graph.kuzu` lookup) and its sample now shows the real section set; allowlist example shows per-script entries (no `scripts/*` wildcards); `(code:...)` citations are a prose convention (graph tracking is roadmap); citation qualifier resolves via `code_citation_root`; detached `/curate` spawns `claude -p` with the workspace as cwd (no `--workspace` flag); Codex falls back in-session; workspace detection uses the real markers (`.curator/config.json` / `wiki/.git`); re-running register rewrites the pointer/allowlist with defaults (not a diff-based no-op); `code_capture.py pr` takes the workspace root; illustrative vault filenames noted as such (`<base>-<sha12>.extracted.md`).
- **docs/setup-advanced.md**: the update slug is hardcoded in `update.sh` and `update_source_slug` is intentionally not read (fork users pass `--source`); setup.sh never curl-pipes uv; the `CURIOSITY_ENGINE_OFFLINE` env flag never existed — identifier resolution detects offline per lookup (same fix in `template/schema.md`); OpenClaude is not in the host registry and takes the manual allowlist path.
- **docs/viewers.md**: D3 + Fuse ship inside the skill payload and are copied at build time — nothing is downloaded into `~/.cache/.../wiki-view-vendor/`.
- **docs/testing.md**: historical disclaimer now also covers the retired `evolve_guard.sh verify` stdin mode (replaced by `snapshot`/`check`).
- **templates**: `template/CLAUDE.md` and `template/schema.md` now list all eleven wiki subdirs; `schema.md` documents `projects/`.
- **RELEASE_CHECKLIST.md / SECURITY.md**: paths updated for the post-v0.7.0 `skills/curiosity-engine/` layout.
- **Bug fix — `epoch_summary.py`**: `cluster_scope_threshold: 0` now disables cluster scoping, as SKILL.md and architecture.md have always documented; previously 0 made scoping unconditionally active on any wiki with a scored page.

## 2026-07-13 — v0.8.0 — richer neighbors verb + embedder.py as a stable library surface

Two additions that let downstream tools (switchyard first) delete their parallel implementations and consume CE directly:

**`graph.py neighbors` grows `--direction out|in|both` and per-neighbor detail.** Output entries now carry `distance`, `title`, and `type`; `--direction both` gives the undirected wikilink neighbourhood that `retrieve`'s traversal uses (previously only reachable through switchyard's own text-derived `_wiki_neighbors`). Backward-compatible: default direction stays `out` and the old fields are unchanged — the verb is now BFS-in-Python over the kuzu edge list, since variable-length Cypher can't emit per-node distance or mix directions.

**`embedder.py` is declared a stable library surface.** `load_embedder(config_dict)`, `predict_model_id`, and the `Embedder` API (`embed_passages` / `embed_query` / `model_id` / `dim`) are now covered by the versioning policy (breaking changes only on a major bump), so tools that vendor CE can import it instead of shipping their own fastembed/sentence-transformers stack — the config is a plain dict, nothing reads the filesystem. A small diagnostics CLI (`embedder.py probe | embed-query | embed-passages`) serves one-off and non-Python callers (per-call model load; import the module for hot loops).

## 2026-07-13 — v0.7.0 — repo restructure: skill moves to skills/curiosity-engine/

Completes the response to the skills-CLI root-layout regression (see v0.6.1). The skill — SKILL.md, `scripts/`, `template/`, `docs/`, plus a LICENSE copy — now lives at **`skills/curiosity-engine/`** inside the repo; repo-level files (README, CHANGELOG, SECURITY, release checklist) stay at the root. This is the layout Anthropic's own [anthropics/skills](https://github.com/anthropics/skills) repo uses (`skills/<name>/SKILL.md`, no root SKILL.md).

*(Follow-up, same day: v0.7.0 initially also shipped a frontmatterless root SKILL.md pointer stub. It was unnecessary — discovery needs no root file — and a second SKILL.md confused humans more than it helped anyone, so it was removed. The README documents where the skill lives.)*

Why: skills-CLI 1.5.13–1.5.16 installs a repo-root SKILL.md as a single-file skill, dropping every supporting file. The subdirectory layout is handled correctly by **all** CLI versions — and better, it is **retroactively self-healing**: an update run by any CLI version against a stale pre-v0.7.0 lock entry (`skillPath: "SKILL.md"`) re-discovers the moved skill, installs the full tree, and rewrites the lock. Users bricked by the old layout are repaired by a plain `npx skills update` / `npx skills add` — no pinned command needed anymore. Verified empirically on a live test branch across the full matrix: fresh add and stale-lock update, each with CLI 1.5.12 and 1.5.16, plus the root-stub variants.

**Install-shape invariant:** the installed directory still contains SKILL.md, `scripts/`, `template/` at its top level (the CLI copies the *contents* of the skill folder), so `<skill_path>/scripts/...` references, workspace allowlists, and external callers (e.g. switchyard) need no changes.

**Changes riding along:**
- `update.sh` git-channel detection now walks up from the skill dir (`git rev-parse --show-toplevel`) and verifies the work tree is actually this repo — supporting the new clone+symlink git install, still supporting pre-v0.7.0 clones, and refusing to `git pull` an unrelated repo (e.g. an npx install under a git-managed `$HOME`). The npx-channel pin + snapshot/rollback from v0.6.1 stay as defence-in-depth.
- Documented git install is now: clone anywhere, then `ln -s <clone>/skills/curiosity-engine ~/.claude/skills/curiosity-engine`.
- README/docs links updated for the new layout; the unpinned `npx skills add` command is recommended again.

## 2026-07-13 — v0.6.1 — defend installs against the skills-CLI root-layout regression

**Incident.** The `skills` CLI ([vercel-labs/skills](https://github.com/vercel-labs/skills)) regressed in **1.5.13** (2026-06-23, verified by bisect: 1.5.12 good → 1.5.13 broken, still broken in 1.5.16): for repos whose SKILL.md sits at the **repo root** — this one — `add` and `update` install **only SKILL.md**, deleting `scripts/`, `template/`, and `docs/` from the install directory. Fresh installs since 2026-06-23 arrived broken; updates were harmless no-ops until v0.6.0 shipped and gave the updater something to fetch, at which point `npx skills update` (including the path inside `update.sh`) replaced working installs with a single file. **No workspace data is affected in any scenario** — wikis (git-versioned, and auto-committed by update.sh before any update), vaults, and `.curator/` all live outside the skill install; the blast radius is the skill's own code. Repair a bricked install with `npx skills@1.5.12 add -g -y benjsmith/curiosity-engine` (or a git clone) — verified working.

**Defences shipped in this release:**
- **`update.sh`**: the npx channel is version-pinned (`SKILLS_CLI_VERSION=1.5.12`, the last known-good CLI), and the update is wrapped in snapshot → apply → integrity-check → rollback. Any update that leaves a partial tree (this bug, a timeout, any future regression) is rolled back automatically and the skill keeps working; the check runs on every post-update path including CLI failure and timeout.
- **SKILL.md self-heal preamble** (§Install integrity check): SKILL.md is by construction the one file that survives a bricking, so the recovery procedure lives there — the agent verifies `scripts/setup.sh` exists before any operation, and on a partial install stops (no improvised curation without the hash-guarded ratchet), reassures the user their data is intact, and gives the pinned repair command.
- **README / setup-advanced**: install and update instructions pin `skills@1.5.12` and document the regression.

Not yet done (tracked): repo-restructure to the subdirectory skill layout (verified immune on CLI 1.5.16) — needs an update-path test from a `skillPath: "SKILL.md"` lock entry first; upstream bug report to vercel-labs/skills.

## 2026-07-12 — v0.6.0 — benchmark-validated graph retrieval, two-tier graph, fastembed/ONNX embedder

Upstreams the retrieval wins from a controlled CE-vs-RAG benchmark (n=50/task across single-hop / multi-hop / global, 4-judge panel scored in both orderings, bootstrap-paired CIs) into the substrate. Headline findings the release encodes: vector-seeded **BFS graph retrieval reaches vector-RAG parity on factoids and wins multi-hop + global/sensemaking**; the optimal policy is **query routing** (graph-only for sensemaking — vault chunks dilute comprehensiveness there; graph+vault blend elsewhere); Personalized PageRank was tested and **rejected** at wiki scale (significantly worse on global, Δ −0.079, CI excludes 0 — its teleport mass concentrates on hub pages and loses the diversity sensemaking rewards). All features are backward-compatible; everything soft-falls-back when kuzu or embedding deps are absent.

**First-class graph retrieval (`graph.py retrieve`).** New primary retrieval verb: semantic seed (chunked wiki-page embedding index, lexical fallback) → multi-hop BFS over the graph → pages ranked by (graph distance asc, query-term overlap desc), with per-page provenance (`seed_mode`, `via` seed, edge `tier`). `--route auto` classifies the query and applies the winning routing policy; `--route graph|blend` forces an arm. Blend mode appends a `vault_search --mode hybrid` recall stream (`vault_search.py` gained a library entry point `search_results()` for this; CLI unchanged). BFS by design — no PPR.

**Wiki-page embedding index (`.curator/wiki.db`, `graph.py embed`).** Chunked (900/150) sqlite-vec index over wiki pages, same opt-in config (`embedding_enabled` / `embedding_model`) and deps as the vault layer. Content-hash incremental; model change triggers auto-rebuild; `rebuild` refreshes it opportunistically. Closes the keyword-seeded graph-entry gap the benchmark identified (0.60 vs 0.92 single-hop correctness).

**Provisional two-tier graph.** `rebuild` now derives a second edge tier mechanically (no LLM): `ProvisionalLink(origin, score)` edges from **co-citation** (unlinked pairs sharing ≥2 vault sources) and **embedding-neighbor** (cosine ≥0.60, top-5 per page), tunable via the new `provisional` config block. Kept as a separate rel table so every existing `WikiLink` consumer (query_router cypher, viewer, path/neighbors, `--graph-expand`) keeps curated-only semantics untouched. Provisional edges live only in kuzu — never in wiki markdown. `retrieve` traverses both tiers weighted (typed edge = 1 hop, provisional = 2). A fresh ingest gets a warm graph before any curation runs.

**LINK closes the loop.** New `graph.py link-candidates` exposes the provisional tier as the LINK proposer's candidate queue (new `<BRIDGE_CANDIDATES_JSON>` slot in `link_proposer`; bridge-candidates was previously computed but never fed to LINK). Promotion is automatic — an applied `[[wikilink]]` becomes a typed edge at the next rebuild, retiring the provisional edge; classifier-rejected pairs are recorded in `.curator/link-rejects.json` and pruned from the tier at every rebuild.

**QUERY defaults changed.** SKILL.md's QUERY workflow now leads with `graph.py retrieve` (routing included) instead of raw `vault_search`, which remains the fallback for vault-only / FTS5-operator searches.

**Shared local embedder — fastembed/ONNX preferred, PyTorch optional (`scripts/embedder.py`).** Ported from switchyard: every embedding consumer (vault_index, vault_search, graph.py, sweep.py sync-notes + project classifier) now goes through one loader with two local backends — **fastembed** (ONNX runtime, no PyTorch, ~50MB of deps; default model `BAAI/bge-small-en-v1.5`, 384-dim, stronger retrieval than MiniLM at the same size, asymmetric query embedding) and **sentence-transformers** (fallback; MiniLM; loaded cache-first via `local_files_only` so a warm cache never re-validates against the HF hub or hangs offline). New `embedding_backend` config key (`auto` default). Vectors are labeled with the backend+model that produced them: wiki.db auto-re-embeds on mismatch, vault_search warns and points at `--reembed`. Backwards compatible by construction: under `auto`, a configured model name starting with `sentence-transformers/` pins the ST backend, so pre-v0.6 workspaces keep their MiniLM vector space untouched. `setup.sh` now offers `fastembed + sqlite-vec` (~115MB total vs ~200MB+ for the torch stack). Local backends only — embedding text never leaves the machine.

**Fixes.** `graph.py` query verbs open kuzu **read-only** — a read-write open bumps graph.kuzu's mtime and masked wiki-newer-than-graph staleness for subsequent commands. `retrieve`/`link-candidates` are exempt from the hard stale gate (they return objects and degrade gracefully) and flag `graph_stale` instead. A `.curator/.graph-meta.json` schema-version marker defeats rebuild's mtime short-circuit exactly once after upgrading, so existing workspaces get the provisional tier + wiki embeddings on their next plain `rebuild` (no `--force` needed); the short-circuit also yields when link-rejects.json or wiki.db changed after the last rebuild. Graphs built by pre-v0.6 skills (no ProvisionalLink table) are tolerated by retrieve rather than crashing. `vault_index.py --reembed` no longer fails with "not authorized" on gated sqlite builds (the sqlite-vec extension load was missing its `enable_load_extension` gate).

Verified end-to-end against a 391-page real wiki: rebuild (349 co-citation + 492/626 embedding provisional edges under MiniLM/bge respectively), both routes, provenance tagging, reject-pruning, incremental re-embed (0.2s no-op — the model only loads when something changed), model-switch auto re-embed, lexical fallback with embeddings off, seeds-only degrade without kuzu, stale flagging, and legacy-command regression.

## 2026-06-07 — v0.5.1 — finish dropping caveman from setup; non-interactive-safe setup

Cleanup follow-up to the v0.5.0 line. The caveman companion-skill **install prompt** was already removed in v0.5.0 (`c09634a`); this release removes the last two remaining caveman references in `setup.sh` — the legacy `caveman`→`compression` config-key migration block and the lineage comment. `grep -i caveman scripts/setup.sh` is now empty.

**Non-interactive-safe setup.** Confirmed every `read -r` prompt in `setup.sh` is guarded by `_is_interactive()` (`[ -t 0 ] && [ -t 1 ]`, plus the `CURIOSITY_ENGINE_NONINTERACTIVE` override), so an automated agent running the script with stdin not a terminal never hangs or fails on a prompt — each falls through to its default. Verified end-to-end: a full workspace bootstrap exits 0 both interactively (driven through a real pty) and under `bash setup.sh < /dev/null`.

Anyone who still sees the caveman install prompt is on a pre-v0.5.0 install; re-install latest (`npx skills add -g -y benjsmith/curiosity-engine`). *(Correction 2026-07-13: pin the CLI — `npx skills@1.5.12 add -g -y benjsmith/curiosity-engine` — see v0.6.1: skills ≥ 1.5.13 installs only SKILL.md.)*

## 2026-06-01 — v0.5.0 — U1–U5 empiricist-EDM upgrades

Implements the five additive upgrades from [`docs/ce-as-edm.md`](docs/ce-as-edm.md), scoped to CE-as-research-wiki (no EDM platform, no maplib/RDF). Each deepens a capability CE already had half-built; all are backward-compatible and optional. Full design + verification log in [`docs/u1-u5-implementation-plan.md`](docs/u1-u5-implementation-plan.md). Verified against a real 382-page wiki and controlled fixtures.

**U1 — Domain-agnostic identity layer.** Generalises the chemical/gene identifier cache into an entity IRI service. New `entities` table in `.curator/identifiers.db`; `identifier_cache.py mint-entity` / `lookup-entity` mint workspace-stable IRIs (`ce:<class>:<workspace>:<slug>`) deterministically — idempotent, stable hash suffix on collision, `same_as` merges across mints from one page. Resolver registry in `identifier_resolve.py` (chemical/gene wired; other classes local-only). New `iri` / `same_as` / `entity_class` frontmatter keys (`naming.py`). The IRI never keys on an external id, so upstream re-resolution can't orphan a citation.

**U2 — Deterministic query substrate.** New `query_router.py` promotes `tables.db` (SQLite) and `graph.kuzu` (Cypher) from curator scratch to a first-class query verb: `sql` / `cypher` / `introspect` / `classify`. Both engines opened **read-only, doubly enforced** (SQLite `PRAGMA query_only`; kuzu `read_only=True`; plus a statement allowlist — SELECT/WITH only, no Cypher writes). Structured/structural questions hit the engine; only synthesis spends tokens. `epoch_summary.py` gains a `table_aggregates` planner hook. Note: `tables.db` is SQLite, not DuckDB — scoped accordingly.

**U3 — Declared shapes (curate-time).** `table:` columns gain optional `units` / `constraint` / `source_required`. A `units` column is a measurement: every row must carry a value *and* a vault-tier source. New hash-guarded `shape_check.py`; enforced at insert time (`tables.py`) **and** at the citation ratchet (`verify_table_shapes` in `score_diff.py` rejects a page newly citing a shape-violating row). Constraints: `>x`, `>=x`, `<x`, `<=x`, `==x`, `!=x`, ranges `[lo,hi]`/`(lo,hi)`. Pages declaring no shape keys are unaffected.

**U4 — Federation by identity.** `epoch_summary.py --shard <seed>` repurposes the 2-hop `wave_scope` neighborhood as a sharding boundary: emits a bounded candidate sub-wiki plus its **seam entities** (IRI-bearing pages inside the shard linked from outside it) — the join keys `curiosity-merge` reconciles on. Never auto-splits. Federation-by-identity contract documented in [`docs/multi-project.md`](docs/multi-project.md).

**U5 — Incremental materialisation.** New hash-guarded `derived_cache.py` generalises the per-page score cache into a dependency-fingerprint cache for any derived fact (aggregates, closures). `table_fingerprint` / `graph_fingerprint` / `memoize`; O(changed) invalidation. Demonstrated consumer: `cached-aggregate` (memoised read-only aggregate, busts on row churn).

**Incidental fixes.** Read-only SQLite opens use `PRAGMA query_only` instead of the `mode=ro` URI, which hangs on a live WAL-mode db whose `-shm` needs write access. `shape_check.py` and `derived_cache.py` added to the `evolve_guard.sh` hash manifest.

**Cross-repo follow-up.** IRI-keyed reconciliation and shard ingestion land in the [`curiosity-merge`](https://github.com/benjsmith/curiosity-merge) companion skill; this release ships the stable `entities`-table contract it consumes.

## 2026-05-16 — v0.4.0 — project-directory ingest + drop ci-mode Action template

**Two threads in one release.**

### 1. Project-directory ingest (the new feature)

Extends the v0.2.0 code-repo pointer-file pattern to non-code project directories. A user with research projects, due-diligence folders, contracts, design decks, etc., on their filesystem can now register those directories against a CE workspace without copying anything into the vault. Only `.extracted.md` files land in the vault; the originals stay where the user keeps them. A scanner walks the registered directories on demand and on three auto-trigger paths (start of CURATE, viewer rebuild, skill update) so the user rarely has to remember to ingest manually.

**Surface.** From inside any directory:

```bash
bash <skill_path>/scripts/setup.sh --register-project-dir \
  [--ce-workspace-path PATH]      # default: $CURIOSITY_WORKSPACE or ~/Documents/curiosity-workspace
  [--ce-project NAME]             # default: directory basename
  [--ingest-paths LIST]           # default: "."
  [--ingest-extensions LIST]      # default: .pdf,.md,.txt,.docx,.pptx,.csv,.xlsx,.html,.rst
  [--no-initial-scan]             # skip the scan at end of setup
  [--init-workspace]              # bootstrap workspace if absent
  [--yes]
```

Writes `.curiosity/config.toml` with `project_kind = "documents"`, registers the directory against the workspace's project-dir registry (`<workspace>/.curator/project-dirs.json`), validates pointer paths for safety, and optionally runs an initial scan. Never copies originals to vault.

**Vault layout (Path A — source-path-only).** Each extraction's frontmatter carries:

```yaml
source_path: /absolute/path/to/original.pdf
sha256: <hash-of-original>
source_in_place: true
```

scan.py uses `sha256` to detect changes; on change, the stale extraction moves to `vault/_stale/` and the new one takes the canonical name. On delete, the orphaned extraction gets `orphan: true` in frontmatter and is excluded from the default planner.

**Scan triggers.** Three automatic, one manual:

- **Start of CURATE** (phase 1, step 2 in SKILL.md): `scan.py all` runs once at the start of a CURATE session before the first wave. No-op when no project-dirs are registered. Subsequent waves in the same session skip — directory state changes infrequently relative to wave cadence.
- **Viewer rebuild**: `viewer.sh build` runs `scan.py check-stale` (cheap mtime walk; no ingestion) and `wiki_render.py` reads the resulting `.curator/scan-staleness.json` into `data.json`. `main.js` emits a dismissible banner at the top of the viewer if any project-dir has stale files: *"N unscanned change(s) in project-dirs: research: 5 · contracts: 2+1 orphan. Run `curate` or `/scan` to ingest."*
- **Skill update**: `update.sh` runs `scan.py all` at the end of the update flow, catching up on filesystem changes the user made between sessions. One-line summary printed.
- **Manual**: `/scan` slash command or natural language ("scan project dirs", "ingest the new files"). Invokes `scan.py all` directly.

**New script** `scripts/scan.py` (hash-guarded, stdlib only) with three subcommands:

- `one --workspace W --pointer P [--dry-run]` — scan a single project-dir's pointer.
- `all --workspace W [--dry-run]` — iterate the workspace's project-dir registry and scan each. Writes the staleness sidecar.
- `check-stale --workspace W` — cheap mtime-only walk; reports unscanned-file counts without reading any bytes.

**code_repo.py extensions** (additive, back-compat preserved):

- `validate-paths <pointer>` — runs the path-traversal guard (no `..`, no absolute paths, all resolve inside pointer-dir; null bytes rejected; symlink-walk escapes caught via canonical resolution).
- `register-project-dir`, `unregister-project-dir`, `list-project-dirs` — registry IO.
- Pointer schema gains `project_kind` (default `"code"` for back-compat) + `[ingest] extensions`, `exclude`, `follow_symlinks`.

**local_ingest.py extensions** (additive):

- `--source-path-only` flag — skip the copy to `vault/<base>.<ext>`; only the `.extracted.md` is written, with the frontmatter recording the original's filesystem location.
- `--file <path>` flag — ingest a single file rather than rglob a directory. Used by scan.py to ingest one specific candidate without rglob picking up its siblings.

**Security (new threat T8 in SECURITY.md).** A malicious pointer file attempts to direct scan.py at filesystem paths outside the pointer's own subtree to exfiltrate sensitive content. Mitigations:

- `validate_pointer_paths()` runs before any scan; refuses `..`, absolute paths, null bytes, and paths whose canonical resolution escapes the pointer-dir.
- Symlinks not followed by default. Even with `follow_symlinks: true`, scan.py rechecks every resolved path with `relative_to(pointer_dir)` and skips escapes.
- Workspace itself cannot be registered as a project-dir (`setup.sh` refuses to avoid ingestion loops).
- Extension whitelist enforced (no `*` glob); `.env`, dotfiles, binaries, etc., not on the default list.
- Standard collateral excluded at scan time regardless of pointer entries (`.git/`, `node_modules/`, `__pycache__/`, `.venv/`, etc.).
- Every extraction still wraps in the standard `untrusted: true` + `<!-- BEGIN FETCHED CONTENT -->` envelope and runs through `scrub_check.py --mode vault` before being indexed.

### 2. Drop --ci-mode + Action template (security cleanup)

Retired the `--ci-mode` flag and the `template/coderepo-workflows/ce-capture.yml` workflow template shipped since v0.2.0. Reason: even after v0.2.2's hardening (pinned `CE_SKILL_REF`, `pip install` instead of `curl|sh`, least-privilege `permissions:`, `persist-credentials: false`), Socket/Snyk scanners kept flagging the workflow's *shape* (external-repo checkout + write-capable deploy key + push to a second repo) as a supply-chain anomaly regardless of the specific hardening. The pattern matched their template heuristics.

The capture script (`scripts/code_capture.py pr/commits/changelog`) remains the stable API. Teams that want centralised capture build their own CI job (GitHub Action / GitLab CI / Jenkins) calling that script with their own auth surface, their own pinning policy, and their own audit trail. `docs/code-knowledge.md` documents the integration shape and the required steps; the workflow file is no longer shipped by the skill.

**Breaking note:** the `--ci-mode` flag is removed. Workspaces that were using the shipped template (none in production that we know of, given v0.2.0 was 12 days ago) need to copy `template/coderepo-workflows/ce-capture.yml` from before the v0.4.0 commit and maintain it themselves. The capture script signatures are unchanged.

### Files

New: `scripts/scan.py`, `template/claude-commands/scan.md`. Modified: `scripts/code_repo.py` (validate-paths + registry + project_kind), `scripts/local_ingest.py` (--source-path-only, --file), `scripts/setup.sh` (--register-project-dir branch + workspace-cwd guard + ci-mode removal), `scripts/viewer.sh` (pre-build check-stale), `scripts/wiki_render.py` (data.json carries scan_staleness), `scripts/update.sh` (post-update scan), `scripts/evolve_guard.sh` (hash-guard scan.py), `template/wiki-view/static/main.js` (staleness banner), `SKILL.md` (### SCAN section + CURATE phase 1 scan step + bash-discipline script list), `SECURITY.md` (T8 threat + mitigations), `README.md` (drop --ci-mode example), `docs/code-knowledge.md` (Action template retirement note + centralised-capture rewrite). Deleted: `template/coderepo-workflows/` directory.

## 2026-05-16 — v0.3.1 — docs: deferred-design note for wiki translation

Adds `docs/translation-design.md` capturing the design space for a future `/translate <target-language>` operation analogous to v0.3.0's RESTYLE. No code change — the operation is not implemented and no SKILL.md, prompts.md, or script touches it. The doc exists so the design thinking is recorded while it's fresh; a future implementer doesn't have to re-derive the load-bearing decisions.

Key recorded decisions:

- **CE doc-class ontology stays English** under any translation. Type names (`source`, `entity`, ...), directory names, bracket prefixes (`[src]`, `[con]`, ...), frontmatter keys, citation DSL, wikilink targets, and filenames are infrastructure that every skill script pattern-matches on. Translation operates on body prose + wikilink display labels (`[[stem|target-language-display]]`) + title body after the bracket prefix. Same separation `style:` already uses.
- **`lang:` frontmatter marker** (per-page, parallel to `style:`) drives idempotency + resumability.
- **Per-language bloat-cap map** in the eventual `translate.py` because expansion ratios vary materially (EN→DE ~1.3×, EN→ZH ~0.4-0.5×). `score_diff.py --bloat-mult` from v0.3.0 covers this already.
- **CURATE feedback loop** is the architectural decision: Path A (one-shot translate, CURATE stays English — ship first) vs Path B (`workspace_language` config + CURATE prompt fill — eventual end-state). Doc recommends Path A v1, Path B v3.1.
- **Numbers, dates, units, decimal separators, quoted-source text, code, citations, wikilink stems** all stay byte-for-byte unchanged. The numbers/units rule is the new genuinely-translation-specific gotcha.
- **Vault stays in original language by design** — provenance, not a quote requirement. Translated wiki page citing English vault source is fine.

Implementation deferred until a user actually asks. Estimated v1 effort if/when un-deferred: ~250-350 lines, parallel surface to v0.3.0 RESTYLE.

## 2026-05-13 — v0.3.0 — RESTYLE wave: hydrate caveman wikis to prose (and the inverse)

Caveman compression is a one-way door today: a workspace that ran with `caveman.enabled = true` accumulates telegraphic pages that CURATE will never re-style because well-cited, well-linked, unbloated pages score fine and never enter the worst-page queue. The new RESTYLE operation inverts the selection — every page is in scope — so a one-time-style-flip terminates.

**Surface.** `/restyle <target>` slash command, or natural language ("restyle the wiki to readable prose", "compress everything to caveman"). Targets: `prose-v1` (succinct readable English — the default schema rule), `caveman-lite-v1` (terse, full sentences with articles), `caveman-ultra-v1` (telegraphic). Bidirectional — hydrate caveman to prose for readability, or compress prose to caveman if the denser register fits the team better.

**Resumable + idempotent.** Each restyle'd page gains a `style: <target-id>` frontmatter marker (new optional schema key). Re-runs filter out pages already at the target, so an interrupted wave (rate limit, manual stop, model error) resumes cleanly with no duplicated work and no separate progress file.

**New script `scripts/restyle.py`** (hash-guarded, stdlib-only) with four subcommands:

- `plan wiki --target <id> [--types ...] [--limit N]` — enumerate + filter + cost estimate. Returns the candidate list, count of pages already at target, count in other styles, rough USD cost range (input tokens × Sonnet rate + 1-in-5 reviewer overhead).
- `mark <page> --style <id>` — set the `style:` and `updated:` frontmatter keys atomically; orchestrator calls this once per accepted rewrite.
- `progress wiki` — print counts by style state for end-of-wave reporting.
- `score-check <page> --target <id> --new-text-stdin` — `score_diff.py` wrapper with target-specific bloat cap baked in (2.0× for `prose-v1` because hydration legitimately expands the body ~1.5–1.65×; 1.5× for caveman targets — compression direction). Citations still gated unconditionally.

**Worker / reviewer prompts** added to `.curator/prompts.md`: `restyle_worker` (voice-only transform; preserves every citation, wikilink, number, heading, and frontmatter byte-for-byte — no new claims, no new citations) and `restyle_reviewer` (1-in-5 spot-audit at reviewer-model with fresh context, narrow scope: information preservation + citation attachment + wikilink targets + style match).

**Mechanical gate change.** `score_diff.py` gains a `--bloat-mult <float>` flag (default 1.5, preserves existing behaviour). Restyle waves pass 2.0; CURATE waves do not touch the flag. Pure-additive — existing callers see no change.

**Orchestration.** SKILL.md's new `### RESTYLE` section walks the agent through the loop: config check (warn if `caveman.enabled` conflicts with target), plan, per-page worker dispatch with the restyle_worker template, score-check ratchet, write+mark+commit per page, spot-audit every 5th accepted page with the restyle_reviewer template (revert verdict triggers `git -C wiki revert` on the page's single-page commit). Per-page commits keep individual rewrites git-revertable; rejections log to `.curator/log.md` under `## restyle-rejections` and skip without commit.

**Cost discipline.** Restyle hits every page, not just worst-scoring ones — a 200-page wiki is roughly $5–$20 at Sonnet rates. The plan subcommand prints a cost-range estimate before any worker fires; `--limit 20` is recommended for a small validation batch first if you're unsure how the rewrite reads in your domain.

**Coexistence with caveman.** Caveman stays an option. If `caveman.enabled = true` and the target is `prose-v1`, the agent warns and offers to flip the config before the wave runs — otherwise new CURATE edits would re-cavemanise pages restyle just hydrated, leaving the wiki in a fighting state.

Files: new `scripts/restyle.py`, new `template/claude-commands/restyle.md`, modified `scripts/score_diff.py` (additive `--bloat-mult` flag), modified `scripts/evolve_guard.sh` (hash-guard entry for restyle.py), modified `scripts/setup.sh` (allowlist entries for both workspace and code-repo modes + a canary so existing workspaces refresh `.claude/settings.json`), modified `template/prompts.md` (two new prompt blocks), modified `template/schema.md` (documents the `style:` key), modified `SKILL.md` (RESTYLE operation + restyle.py in bash discipline script list).

## 2026-05-13 — v0.2.2 — harden ce-capture.yml workflow (Socket supply-chain warning)

Tightens the GitHub Action template shipped by `setup.sh --register-code-repo --ci-mode` to close a Socket scan warning about supply-chain risk. Four changes, all surface-level — no behavioural change to capture:

- **Default `CE_SKILL_REF` pinned to `v0.2.1`** (was `main`). The mutable-default-branch checkout is replaced with a pinned release tag. Teams can still override the repo variable with a different tag or a 40-char commit SHA for stronger immutability guarantees.
- **`curl|sh` uv install replaced with `pip install --user uv`** via PyPI (sha256-verified install path). Adds a small `actions/setup-python@v5` step to ensure a known interpreter; uses `python3 -m pip install --user` so we don't fight the runner's externally-managed Python.
- **Least-privilege `permissions:` block at workflow level**: `contents: read`, `pull-requests: read`, `issues: read`. `GITHUB_TOKEN` can no longer write through any step. Pushes to the wiki repo continue to use the SSH deploy key on a separate auth path.
- **`persist-credentials: false`** on the code-repo and skill checkouts so `GITHUB_TOKEN` isn't baked into `.git/config` for subsequent steps to reuse implicitly. The wiki checkout deliberately keeps the default (true) — its SSH deploy key needs to remain wired in for the later `git push` step, and the deploy key's scope is already narrowly bounded to the wiki repo by GitHub's deploy-key model.

**Drive-by fix.** The v0.2.0 workflow template had an indentation bug: the `Co-Authored-By:` trailer line in the wiki-push commit message was at column 0, which terminated the YAML `|` block scalar early and made the YAML parser treat `Co-Authored-By:` as a stray top-level mapping key. GitHub Actions would have either rejected the workflow or run it with the trailer missing from the commit message. Re-indented to stay inside the run block.

Workspaces that already wired `--ci-mode` should re-run `setup.sh --register-code-repo --ci-mode` to refresh the workflow file. Setup refuses to overwrite a customised workflow; the previous file (with the bugs) must be deleted manually first if you want the new one.

## 2026-05-12 — v0.2.1 — viewer: new categorical palette + white-bordered Unclassified

Replaces the Tableau-10-derived viewer palette with a categorical 12-colour
set, mapped to the 11 canonical types in sidebar `TYPE_ORDER` plus a
distinct treatment for Unclassified. The same colours now appear in the
graph view, the wiki-browser sidebar group dots, and the label-picker
swatches (driven by a single source of truth in `wiki_render.py PALETTE`
and mirrored CSS `--type-*` vars).

**Type → colour:** project `#4d1ae8`, analysis `#1d6996`, concept `#38a6a5`,
entity `#0f8554`, evidence `#73af48`, fact `#edad08`, figure `#e17c05`,
table `#cc503e`, source `#94346e`, note `#6f4070`, todo-list `#9656a2`.

**Unclassified** renders as a white fill with a thicker black border —
SVG `stroke: #000; stroke-width: 2px` on graph + subgraph circles,
`box-shadow: inset 0 0 0 1px #000` on sidebar dots (keeps the 7×7 dot
size without a layout shift). Distinguishes loose-ends pages at a
glance without competing chromatically with the named types.

**Fallback** (`default` slot, for any type that escapes the canonical
set) becomes a neutral `#bbbbbb` grey — rarely visible since
`KNOWN_TYPES` buckets unrecognised frontmatter values to `unclassified`
at render time.

No data-contract changes; the on-disk frontmatter is untouched. The
viewer rebuild on next `viewer.sh build` re-emits the bundle with the
new palette and no further action is needed in existing workspaces.

## 2026-05-12 — v0.2.0 — code-repo mode: capturing the knowledge that escapes the codebase

Curiosity Engine now treats engineering codebases as first-class projects. A code repo registers against an existing CE workspace via a small `.curiosity/config.toml` pointer file; capture flows from PR descriptions, commits, the changelog, and agent session transcripts into the workspace's vault; a per-(repo, branch) session brief gives a fresh agent yesterday's context for files in the current diff; `/curate` from inside a code repo detaches into the workspace so the engineer's coding-session transcript stays clean. The wiki itself stays unchanged — code-repo content lands as `sources`, `entities`, `concepts`, `analyses`, `evidence` per the existing 8-type taxonomy, distinguished only by the project tag and a new `(code:project:path:line)` reference form.

**Full design.** [`docs/code-knowledge.md`](docs/code-knowledge.md) (referenced from the README's new "Using Curiosity Engine for codebases" section). The doc covers the problem framing, the page-type mapping, the pointer-file format, the capture surfaces in v1, and what's deferred.

**Idempotency for existing CE users.** Zero change to the workspace flow. The code-repo branch in `setup.sh` only fires when (a) cwd lacks workspace markers AND (b) the heuristic recognises cwd as a code repo (`.git/` plus a source-marker file like `pyproject.toml`, `package.json`, `Cargo.toml`, etc.). Researchers re-running setup in their workspace cwd see exactly today's behaviour. `--in-repo` is the explicit override for solo / OSS / monorepo users who want the legacy "create the workspace right here" behaviour.

**New setup flow.** From inside a code repo:

```bash
bash <skill_path>/scripts/setup.sh --register-code-repo \
  [--ce-workspace-path PATH]   # default: $CURIOSITY_WORKSPACE or ~/Documents/curiosity-workspace
  [--ce-project NAME]          # default: repo basename
  [--init-workspace]           # bootstrap a workspace at the path if absent
  [--yes]                      # accept default-Y prompts non-interactively
  [--ci-mode]                  # also drop a GitHub Action workflow template
  [--install-drainer]          # accepted but no-op in v0.2.0; daemon ships in v0.2.x
```

Writes the committed pointer (`.curiosity/config.toml`), a per-machine allowlist (`.claude/settings.local.json`) rooted at the absolute workspace path, a per-machine capture hook (`.git/hooks/post-merge`), a `.gitignore` block for the per-machine files, slash-command templates into `.claude/commands/`, and (with `--ci-mode`) `.github/workflows/ce-capture.yml`. Cross-platform default path resolution uses `xdg-user-dir DOCUMENTS` on Linux when available, falls back to `~/Documents/curiosity-workspace`, then `~/curiosity-workspace`.

**Capture surfaces (v1).**

- **Commits + PRs + changelog** via a local `post-merge` git hook. Triggers on `git pull` / `git merge`. Writes one vault entry per commit (with diff stat), and if `gh` is on PATH and authenticated, captures the associated PR's thread + reviews. Silently no-ops if the workspace isn't cloned on this engineer's machine, so `git pull` never breaks.
- **GitHub Action** (`--ci-mode`) as an alternative for teams that don't want every engineer to have the workspace cloned locally. Template ships with secret-wiring instructions; setup.sh writes the file but does not auto-configure secrets (deploy key + `CE_WIKI_DEPLOY_KEY` + `CE_WIKI_REPO` are the team owner's one-time task).
- **Agent session transcripts** via `session_drainer.py`. Walks `~/.claude/projects/<flatpath>/*.jsonl`, drains completed sessions (mtime > 5min) into vault entries, with a hard skip rule for the workspace's own flatpath — prevents detached curate sessions from being re-ingested as engineer work. On-demand for v0.2.0; daemon/launchd unit deferred.

Every vault entry is sha256-content-addressed, so the local hook and the Action can both fire without producing duplicates.

**Slash commands (route to workspace via pointer-file walk-up):**
- `/note`, `/decision`, `/gotcha`, `/constraint` — append to topic notes files in the workspace with project autotag. CURATE drains them into the appropriate page types over time.
- `/distill` — proposes wiki edits from the current coding session transcript; awaits confirmation.
- `/brief` — regenerates the per-(repo, branch) session brief.
- `/curate` — when invoked from a code-repo cwd, spawns a detached `claude -p` against the workspace and returns immediately with the session ID. The engineer's coding transcript stays untouched. Hosts without headless support (Gemini, Copilot today) fall back to in-session curate with a banner warning.

**Session brief.** `scripts/session_brief.py` generates `<code-repo>/.curiosity/session-brief.md` (per-machine, gitignored) from the workspace's project-tagged content + the current branch's diff vs. main + recent activity log entries. SKILL.md instructs agents to read this at session start, so a fresh coding agent picks up yesterday's context without grep-and-rediscover. Optional `[brief] regenerate_on_pull = true` in the pointer file wires it into the `post-merge` hook.

**New scripts (all stdlib-only, all hash-guarded by `evolve_guard.sh`):**
- `scripts/code_repo.py` — detection, pointer-file IO, workspace resolution (with git-bounded walk-up)
- `scripts/code_capture.py` — `commits` / `pr` / `changelog` / `session` subcommands
- `scripts/session_drainer.py` — one-shot session-transcript ingestion with workspace-flatpath skip rule
- `scripts/session_brief.py` — per-(repo, branch) digest generator
- `scripts/curate_launch.py` — host-aware detached `claude -p` spawn with status file
- `scripts/curate_status.py` — alive check + log scrape (waves, accepts, rejects)

**Safety properties preserved.**
- Walk-up for the pointer file is **bounded by git root**: a code repo nested inside an unrelated workspace's directory tree can never be silently mis-routed to that workspace. Workspace discovery by directory proximity is deliberately not supported.
- `setup.sh` never creates `vault/`, `wiki/`, or `.curator/` inside a code repo. `--in-repo` is the only path that produces the legacy in-repo layout, and it's explicit.
- Captured content carries the standard `untrusted: true` envelope and runs through `scrub_check.py --mode vault` before being indexed; injection-marker hits land in `vault/_suspect/`.
- Detached curate sessions write their transcripts to the workspace's project dir in `~/.claude/projects/`, separate from the code repo's project dir — the drainer's skip rule prevents recursion (curate sessions don't re-ingest themselves).

**Bundled bugfix — viewer TYPE_CANONICAL.** `template/wiki-view/static/sidebar.js` and `template/wiki-view/static/graph.js` were missing entries for the `extracted-table` and `summary-table` page subtypes in their `TYPE_CANONICAL` maps. Result: pages of those subtypes (the `wiki/tables/tab-*.md` and `wiki/tables/tbl-*.md` outputs of the existing multimodal-table-extract and summary-table-builder waves) did not collapse under the **Tables** sidebar bucket or share the table colour in the graph view — they got their own unrecognised sections. Fix is two lines per file mapping both subtypes to `table`. Display layer only; the on-disk frontmatter values stay unchanged so `tables.py extracted-query`, the numeric-review pipeline, and graph indexing — all of which key off the subtype values — are unaffected. Viewer template stays outside the hash-guard set per its blast-radius rule.

**Out of scope (deferred to v0.2.x and v1.5+):**
- Session-drainer daemon unit (launchd / systemd). On-demand drain via `/distill` works today.
- Code-comment scraping (`WHY:` / `GOTCHA:` / `INVARIANT:` patterns).
- Slack / Teams / email / Confluence / Notion / Linear connectors.
- IDE extensions (VS Code / Cursor "Send to CE" command).
- Native Windows PowerShell setup (bash via Git Bash / WSL still works).
- Automatic deploy-key wiring for the GitHub Action (template ships, secrets manual).
- Drift audit for `(code:project:path:line)` citations — extend the existing `table_citation_risk` pattern to code ranges.
- A `wiki/.recent.md` workspace-wide rolling digest (generalisation of the per-repo session brief).

## 2026-05-06 — v0.1.4 — fix silent embedding-load failure in vault_index / vault_search

`vault_index._init_embed_tables` and `vault_search._semantic_search`
called `sqlite_vec.load(conn)` without first calling
`conn.enable_load_extension(True)`. Modern stdlib sqlite3 (and
pysqlite3) ship extension support compiled in but disabled at
runtime, so the call raised `OperationalError: not authorized`. The
chained call from `local_ingest.py` caught the exception but only
forwarded `indexed.get("status")`, dropping the error message — so
the visible symptom was just `"indexed": "error"` with no detail,
and any new vault entries silently lost their embedding while the
FTS5 row was never even written.

**Fix.** Mirror the gate `sweep.py` already uses for its
notes-embeddings DB (`enable_load_extension(True)` → `load()` →
`enable_load_extension(False)`) in both vault_index call sites and
in vault_search. Update `local_ingest.py` to forward the full
`indexed` dict so future failures surface their `error` field
instead of silently degrading. Drive-by: rename the deprecated
`SentenceTransformer.get_sentence_embedding_dimension()` call to
`get_embedding_dimension()` so `vault_index.py` stops emitting a
`FutureWarning` on every model load.

**Operational note.** Workspaces with `embedding_enabled: true`
that ingested anything since the regression have FTS5 rows but no
matching vectors for those new sources. Run
`scripts/vault_index.py --reembed` after upgrading to backfill
embeddings for existing rows; new ingests pick up embeddings
automatically.

## 2026-05-05 — v0.1.3 — narrow identifier_resolve.py allowlist + DPI doc fix

Closes a gap discovered while reviewing v0.1.2: the
`identifier_resolution.enabled` config flag was the only gate
between an orchestrator agent and a network call to PubChem /
MyGene.info, but `.curator/config.json` is **agent-editable** (the
workspace allowlist permits `Edit(./.curator/**)`). An agent could
flip the flag, then invoke the previously broad
`Bash(... identifier_resolve.py:*)` allowlist rule with `run --yes`,
firing the network call without user approval. The off-by-default
config was a convenience default, not a security boundary.

**Fix: subcommand-level allowlist entries.**

- `scripts/setup.sh` allowlist for `identifier_resolve.py` is
  narrowed from the broad `:*` to two specific subcommand prefixes:
  `review:*` and `status:*`. Both are read-only / visibility-only.
  Invoking `identifier_resolve.py run --yes` no longer matches any
  allowlist rule — Claude Code prompts the user for approval on
  every invocation. Setup gains a canary that forces regen on
  v0.1.2 workspaces so the new rule lands automatically.

- `SECURITY.md` adds a new section, "Bash-allowlist as the boundary
  (general principle)", clarifying that `.curator/config.json`
  flags are convenience tunables and the bash allowlist is the
  load-bearing security gate. Future sensitive operations should
  follow the same pattern: subcommand-level allowlist for safe
  ops, no allowlist for state-changing ops, config flag on top as
  a default-off convenience.

- The T7 (data exfiltration) mitigation summary is updated to
  reflect the new gate ordering.

**DPI tunability — doc-only fix.**

The curator agent flagged that re-extracting suspect `[tab]` pages
would benefit from higher DPI but figures.py is hash-guarded.
Investigation showed the existing `--dpi` CLI flag is already
allowlist-passable (the bash rule is `figures.py:*`, anything-after) —
the curator just didn't know the flag exists. Earlier draft of
this release added a `figure_render_dpi` config key; reverted on
review (same agent-editable-config concern as above) in favour of
the simpler doc fix.

- `SKILL.md` multimodal-table-extract pre-render step now
  references the existing `--dpi` CLI flag with `--force` to
  re-render at higher resolution. No config plumbing.

## 2026-05-05 — v0.1.2 — security pass

Addresses six findings from the Gen Agent Trust Hub / Socket scan of `v0.1.0`. No breaking changes for default users; the only behaviour shift is that `identifier_cache.py` is now cache-only and external identifier resolution requires explicit user opt-in via `identifier_resolve.py`.

- **PROMPT_INJECTION** — `scripts/scrub_check.py` STRONG_MARKERS broadened to catch updated/new instruction headers, bypass language, jailbreak personas (DAN / developer mode), prompt-extraction templates, shell-execution prompts, and browser-side script injection (`<script` tags, `javascript:` URIs, `onerror=`, `onclick=`). Orchestrator prompts in `SKILL.md` and `template/CLAUDE.md` reinforced with explicit attack-shape lists, nested-quote handling, and worker-output-is-also-data discipline.

- **EXTERNAL_DOWNLOADS** — `scripts/setup.sh` no longer auto-installs `uv` via `curl … | sh`. Refuses with platform-specific install instructions instead. Vendor JS (D3 7.9.0, Fuse.js 7.0.0) committed in-tree at `template/wiki-view/static/vendor/`; viewer build does not call any CDN. SHA-256 hashes recorded in `RELEASE_CHECKLIST.md` for vendor-bundle review on every release.

- **DATA_EXFILTRATION** — `scripts/identifier_cache.py` is refactored to cache-only: `urllib` import removed entirely, `queue` subcommand records resolution requests to `.curator/identifier-requests.jsonl`. New `scripts/identifier_resolve.py` is the only outbound-network script in the skill: off by default (`identifier_resolution.enabled: false` in template config), endpoint-configurable (point at internal API mirrors in enterprise settings), two-step `review` → `run --yes` ceremony. Workflow preserves convenience while making egress visible and gated.

- **REMOTE_CODE_EXECUTION / update.sh anomaly** — `scripts/update.sh` slug is now hardcoded to upstream; `.curator/config.json`'s `update_source_slug` is no longer read. Fork users override per-invocation with `--source <owner>/<repo>`, validated against a strict GitHub-slug regex. Non-default slugs print a prominent ⚠ warning banner in the preview.

- **COMMAND_EXECUTION** — `scripts/tables.py` `__import__("re").compile(...)` replaced with plain `re.compile(...)` (cosmetic; same behaviour). `scripts/viewer_server.py` `subprocess.run` call site annotated with a safety comment documenting why it's safe (list-form argv, no `shell=True`, hardcoded args).

- **`SECURITY.md` declared** — new top-level doc captures the threat model, trust boundaries, mitigation per threat, and a complete catalog of outbound network surfaces + subprocess/dynamic-execution sites. Reviewers can verify what's intentional without source archaeology.

- **`RELEASE_CHECKLIST.md` declared** — pre-release checklist for vendor-bundle review, Socket re-scan, CHANGELOG, smoke-test on a real workspace; tagging and versioning policy.

Verified by re-running the smoke tests on the test workspaces.

## 2026-05-04 — v0.1.1 — silent on plain wikis with no projects

First patch release. One bugfix-only commit on top of `v0.1.0`; no behaviour change for wikis that use the multi-project model.

- **`epoch_summary` suppresses `project_activity` when no projects exist** (`5f167fe`). On plain literature wikis (no `wiki/projects/<name>.md` home pages on disk), `project_activity` was always emitted with an `_unclassified` bucket counting every page in the wiki — exactly the kind of signal an orchestrator might pick up to suggest projects unprompted. Now: when zero project home pages exist, `project_activity` returns `{}` and `connection_candidates` ships without project-tag enrichment. JSON shape becomes identical to pre-multi-project (pre-`v0.1.0`). The first `projects.py create` is what activates the rest of the multi-project plumbing. Documented as the "No-projects default" in `docs/multi-project.md`.

## 2026-05-03 — cross-project bridge candidates (wave 5)

- **Bridge slot fills with cross-project candidates** (`d377b3c`). The kuzu `connection_candidates` query (page pairs sharing vault sources but unlinked) now enriches each candidate with `projects_a`, `projects_b`, and a `cross_project` flag (true when both pages carry tags and the sets differ). Default candidate limit bumped from 5 to 20 so the planner has a pool to filter from. `planner.py` splits raw vs mode-adjusted scoring (`_compute_raw_activity_scores`) so pair classification (`active-active` / `dormant-active` / `dormant-dormant`) is mode-stable — labels reflect project state, not wave mode. Default mode ranks bridges by `min(activity_a, activity_b)` descending (two-active-projects bridges beat one-active-one-dormant per design). Archival mode stratifies 40/40/20 across pair types with within-stratum ordering by `min_activity` ascending; rounding is reconciled and empty strata are backfilled from neighbours so the bridge budget gets used. Page activity for multi-tagged pages = max over its project scores. Empty candidate pools degrade with an informational note.

## 2026-05-03 — semantic classifier step (wave 4)

- **Semantic similarity layer in `classify-projects`** (`eb83043`). Runs after the citation-graph fixed-point pass — fills gaps for pages with no inbound wikilinks yet (the citation step can't reach them). Cold-start guard: when fewer than `project_classifier_min_home_pages` (default 5) projects have substantive home pages (≥ `project_classifier_home_min_words`, default 30, after stripping the curator's stub line), the step is skipped without loading any model. Past cold-start: respects `embedding_enabled`, degrades cleanly when `sentence-transformers` / `numpy` are missing. Encodes target pages and project home bodies with `all-MiniLM-L6-v2`, computes cosine similarity (dot product on unit-normalised vectors), assigns the single best match above `project_classifier_confidence_threshold` (default 0.5). Audit log distinguishes citation vs semantic source per assignment with similarity score. New keys in `template/config.json`; setup's additive merge brings them into existing workspaces. Citation step (wave 1b) unchanged — regression-checked.

## 2026-05-03 — recency planner (wave 3)

- **Soft-deleted pages excluded from page scans** (`0dd955f`). Independently useful bug fix: `lint_scores.wiki_pages_in` and `sweep.wiki_pages` were filtering only filename-level dotfiles, so paths inside `wiki/.deleted/<project>/` (created by `projects.py delete`) were still scored, classified, and surfaced as worst-page candidates. The fix walks `relative_to(wiki_dir).parts` and rejects any path where any directory segment starts with a dot — same predicate `projects.py` already uses.
- **Recency-weighted slot allocator** (`f0bdd42`). New `scripts/planner.py allocate <epoch_summary.json> --wave-mode <m> --mode {default,archival} --slots N`. Sits between mode selection (unchanged in `epoch_summary.py` + SKILL.md prose) and target picking (unchanged inside each mode's queue logic). For repair mode: 70% project-by-activity (min 1 / max 4 per project, waived on single-project), 15% bridges (placeholder until wave 5), 10% unclassified, 5% ambient global worst-page; surplus rolls to ambient. For wire: passthrough global. For create + specialty modes (figure-extract / multimodal-table-extract / numeric-review / table-audit): passthrough with ordering hint that re-orders existing candidate queues by candidate-project activity score (descending in default mode, ascending in archival). Activity score formula is cross-project max-normalised on all three terms (`0.55 × ingests_current` + `0.30 × user_signals` + `0.15 × cadence`) so it stays bounded 0..1 and the archival `1 - x` inversion stays in range. `epoch_summary.py` gains a `project_activity` field (additive — existing consumers ignore it). `activity_log.py` exposes public `query_by_project()` / `query_by_page()` so `epoch_summary` imports without shelling out. **No SKILL.md change in this wave** — `planner.py` is callable but not yet wired into the documented Plan flow; you can review actual allocation outputs against existing wave plans before flipping the curator over. Bridges placeholder reserved per the locked design — wave 5 fills it. Single-project / no-project wikis behave equivalently to the pre-project flow.

## 2026-05-03 — activity tracking (wave 2)

- **Activity log + archival/projects ingest flags** (`a75b25c`). New `scripts/activity_log.py` records two event kinds (`ingest`, `user_signal`) as one-event-per-line JSON in `.curator/activity.log`. Library API (`from activity_log import log_event`) for in-process callers, CLI (`log` / `query`) for orchestrator + scripts. `query --by-project` emits the exact shape the wave-3 planner will consume: `ingests_current`, `ingests_archival`, `user_signals`, and a decayed cadence score (exp(-w/2) over the last 4 weeks). `local_ingest.py` gains `--archival` (sets `ingest_kind: archival` in the extraction frontmatter and on the activity event) and `--projects a,b,c` (pre-tags the extraction so the citation-graph classifier doesn't have to guess on user-stated project ingests). Activity-log writes are best-effort — a log failure cannot break ingest. Wave 3 (recency-weighted planner) and the orchestrator's conversational `add` / `import` / `archive` flow consume what wave 2 writes.

## 2026-05-03 — multi-project foundation (wave 1)

Closes wave 1 of the multi-project rollout (see `docs/multi-project.md`). The substrate is in place; the recency-weighted planner and archival mode (waves 2–3) consume what wave 1 writes.

- **Design + roadmap landed** (`4b11dc3`) — `docs/multi-project.md` is the durable spec; README has a verb cheatsheet pointing at it. Verbs locked: `add` / `import` / `archive` / `curate` / `curate archival` / `rename` (project) / `delete` / `restore` / `purge` / `merge` (wiki) / `discover-bridges`. `rename` and `merge` are deliberately distinct — `rename` is single-wiki and mechanical; `merge` is cross-wiki and heavy.
- **Wave 1a — registry + home pages** (`776804b`). New `scripts/projects.py` with `create` / `list` / `exists`. Strict-slug name discipline; orchestrator handles conversational slugification. `create` is dual-mode: writes a templated `wiki/projects/<name>.md` if absent, or registers an existing user-authored home page (recovering the description from frontmatter). `setup.sh` adds `wiki/projects/` to the layout and the new script to the allowlist with a canary entry. `naming.py` adds `[proj]` to TYPE_PREFIX and `projects` / `description` / `ingest_kind` to ALLOWED_FM_KEYS.
- **Wave 1b — classify-projects sweep op** (`c62b31e`). Citation-graph-only classifier (semantic step deferred to wave 4). For each non-home page, `projects:` becomes the union of its current set and the project sets of pages that wikilink to it. Iterates to fixed point (max 5 passes, bails early). Monotonic-additive — never removes a tag, so user overrides survive. Project home pages are seeded with their own slug and frozen. Logs every change to `.curator/log.md` under `## classify-projects <ts>` with before/after diff. `--dry-run` flag for review. Frontmatter rewrite via the new public `naming.set_frontmatter_field` helper, which handles single-line and multi-line YAML list forms.
- **Wave 1c — rename / delete / restore / purge** (`77e17a0`). Project-lifecycle commands. `rename` is mechanical: rewrites tags, moves home page (or deletes source home in absorption), rewrites `[proj]` title prefix, rewrites `[[from]]` / `[[from|alias]]` wikilinks across the wiki, updates registry. `delete` is soft: single-tagged pages move to `wiki/.deleted/<name>/`, multi-tagged pages drop just the tag, a `_manifest.json` records both lists. `restore` reverses it via the manifest, archiving the manifest under `.deleted/.history/`. `purge` is hard: removes `.deleted/<name>/` entirely and drops the registry entry. Vault file handling (which vault files are exclusively cited by deleted-scope pages) deferred to a follow-up wave.

**Deferred to wave 2+**: activity tracking (`.curator/activity.log` with user-vs-agent timestamp split, archival ingest flag), recency-weighted planner, archival mode, semantic classifier step (with cold-start guard), cross-project bridge candidates, wiki `merge`, standalone `discover-bridges`.

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
