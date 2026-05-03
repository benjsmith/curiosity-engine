# Multi-project model

**Status: shipped.** Single-wiki verbs (waves 1–5) live in this skill. Cross-wiki verbs marked ‡ live in the [`curiosity-merge`](https://github.com/benjsmith/curiosity-merge) companion skill — installable from `setup.sh`'s optional-install menu. See `CHANGELOG.md` for the per-wave history.

This document captures the design for organizing knowledge across many projects in a single wiki. The user-facing model is "drop things in, run `curate`, occasionally `archive`." Project membership is derived from the citation graph, not declared by the user. The planner allocates worker effort across projects by recent activity, with an inverted-weight `archival` mode for working on neglected material.

**No-projects default.** Plain literature wikis that don't use the multi-project model see no behaviour change. Until the user runs `projects.py create <name>` for the first time, `epoch_summary.project_activity` is suppressed (returns `{}`), `connection_candidates` ships without project-tag enrichment, the viewer's sidebar omits the `Projects` group (no records → no group), and no command auto-suggests projects. The orchestrator sees the same JSON shape it saw pre-multi-project. The first `projects.py create` is what activates the rest of the machinery.

## Verb cheatsheet

| Verb | Scope | Purpose |
|------|-------|---------|
| `add` | item or folder | Ingest a file or folder. Counts as current activity. Can specify `to Project A` to pre-tag. |
| `import` | folder | Bulk-import a folder, current activity. Idempotent (skips by sha256). |
| `archive` | item or folder | Ingest as archival activity — does not inflate the default-mode planner score for the target project. |
| `curate` | wave | Default mode. Recency-weighted parallel fanout across active projects + cross-project bridges + unclassified bucket + ambient global slot. |
| `curate archival` | wave | Archival mode. Inverted activity weighting; per-pair-type bridge budget so active↔active links aren't lost. Raised classifier confidence threshold. |
| `rename` | project (within one wiki) | Absorb project B into project A. Mechanical: re-tag pages, delete B's home page, rewrite wikilinks, rebuild graph. No deleted-table writes. |
| `delete` | project | Soft-delete. Single-tagged pages move to `wiki/.deleted/<name>/`; multi-tagged pages just drop the tag. A manifest at `_manifest.json` records both lists so `restore` can reverse it. (Vault file handling deferred to a later wave; pages move, vault files stay.) |
| `restore` | project | Inverse of `delete` (replays the manifest). |
| `purge` | project | Hard-delete from `.deleted/` and drop the registry entry. Separate, deliberate command. |
| `merge` ‡ | wiki + wiki | Cross-wiki operation. Vault sha256 reconciliation, source-stub stem reconciliation, page-name collision queue, graph union with `origin:` audit tags, bridge discovery across origins. |
| `subgraph-export` ‡ | extract | Write a self-contained mini-wiki for a project / page / origin scope, suitable for `git push` to GitHub. |
| `discover-bridges` ‡ | within or across wikis | Semantic-similarity sweep that surfaces high-similarity page pairs that aren't yet wikilinked. Output is a review queue. |

`rename` (project) and `merge` (wiki) are deliberately distinct verbs to avoid confusion. `rename` is mechanical and contained; `merge` is heavy and cross-origin.

## Project home pages

Each project has a home page at `projects/<name>.md`. It serves three purposes: human-facing project summary, anchor for the semantic-similarity classifier, and the page deleted/renamed when the project is removed/absorbed.

**Manual mode**: user creates the project home first, then refers to it: `add ~/papers/foo.pdf to Project A` imports against an existing project home.

**Automatic mode**: when a command names a project that doesn't exist:

> "Project A doesn't exist yet. Create it now? Optionally, add a brief description to seed the project home page (or skip and let the curator generate one from imported content)."

**Missing project name**: when a command imports without naming a project:

> "Which project? Existing: a, b, c. You can name an existing one, give a new project name, or say 'none' to leave classification to the curator."

If the user gives an unknown name, the conversation falls through to the create-with-description prompt above.

`add docs in raw to Project A` is shorthand for "import everything currently sitting in `vault/raw/`, tagged as Project A."

## Auto-classification

The `classify-projects` SWEEP op runs on every CURATE wave, using these signals in order:

1. **Citation in-degree by project**. Source stub cited from project A's pages → tag A. Cited from multiple → tag all. Uncited → unclassified.
2. **Wikilink in-degree by project**. Same logic for concepts/entities.
3. **Semantic similarity to project home pages**. Cosine similarity of the unclassified item's embedding against project home embeddings. Confidence threshold gates the assignment; below threshold, the item stays unclassified.
4. **Inheritance from synthesis**. New analysis pages inherit project tags from their cited pages; concept/entity pages spawned by the analysis inherit those tags.

**Cold-start guard**: when fewer than `min_home_pages_for_classifier = 5` projects have non-stub home pages, the semantic step is skipped entirely; only citation-graph signals are used. Avoids garbage classifications when there's nothing to anchor against.

**Audit trail**: every project-tag mutation logs to `.curator/log.md`:

```
[classify-projects] entities/transformer.md gained tag "speech-recognition"
  (now cited from analyses/whisper-arch.md, project: speech-recognition)
[classify-projects] sources/vaswani-2017.md tag changed from {nlp} to {nlp, vision}
  (new citer: concepts/vision-transformer.md, project: vision)
```

The user can override any tag in conversation; the override is recorded and the classifier won't re-tag without new evidence.

**`unclassified` bucket**: items with no signal stay in a pseudo-project named `unclassified`. The default planner gives this bucket a 10% slot per wave so it doesn't accumulate. Surfaced as a `unclassified_pct` lint dimension at wiki-level for visibility.

## Activity score

User signals only. Agent edits never count.

```
activity = 0.55 × normalized_ingests_last_7d        (excludes archival ingests)
         + 0.30 × normalized_user_signals_last_7d
         + 0.15 × ingest_cadence_score              (last 30d, decayed)
```

**User vs. agent edit detection**: the curator commits with `Co-Authored-By: Claude...` trailers. Filtering `git log -- wiki/` to commits *without* the trailer gives the user-edit signal cleanly.

**Per-page timestamps**: `.curator/activity.log` records two timestamps per page:

- `user_signal_at` — last user-driven action involving this page (ingest of its source, user-triggered analysis creation, manual edit, conversational request that touched it). Counted in activity score.
- `agent_modified_at` — last CURATE-wave touch. Not counted; useful for telemetry and for ranking `discover-bridges` candidates.

**The user-signal-outside-CURATE rule**: any edit made *outside* a CURATE wave counts as user-signaled, regardless of whether the agent's hands typed it. The intent originated with the user, so the page deserves activity credit.

**Archival ingests**: items ingested via `archive` get `ingest_kind: archival` in their frontmatter. The default-mode activity formula filters these out so a 50-paper archival dump doesn't make a dormant project look "active." Archival mode counts them at full weight.

## Default-mode planner

Wave budget allocation (assuming 10 worker slots per wave):

- **70% — project allocation by activity**, with `min_share = 1 slot` per project that has any non-zero activity (active projects always get at least one worker) and `max_share = 4 slots` per project (one runaway project never starves the others).
- **15% — cross-project bridge candidates**, distributed by `min(activity_A, activity_B)` over candidate pairs, so two-active-projects bridges beat one-active-one-dormant.
- **10% — unclassified bucket**, keeps loose ends draining.
- **5% — ambient global** worst-composite page across all projects. Prevents complete neglect of any region.

Within each project's allocation, existing CURATE machinery does its job — worst-page targeting, demand promotions, wire-mode if cluster-scoping triggers. No change there.

**Saturation pivot is per-project**. Each project has its own ratchet history; one project hitting saturation pivots to create-mode for that project alone, while others continue in repair-mode.

## Archival mode

Same machinery, inverted weighting. Invoked by phrases that map to `--mode=archival`:

- `curate archival` / `curate in archival mode`
- `archive for 30 mins` (mode + time budget)
- `archive these docs in raw` (compound: ingest+classify+archival-curate, scoped to the just-imported items)
- `curate <project>` (rare explicit override)

**Inversions and adjustments:**

- Activity score is inverted (low-activity projects get the biggest slots), but archival-tagged ingests count at full weight so just-archived material gets attention.
- Classifier confidence threshold raised — don't mass-tag old material based on shaky semantic similarity.
- Worst-page targeting bounded by `orphan_rate` and `unsourced_density`, not `crossref_sparsity` (concept pages in dormant projects already have saturated crossref; the leverage is in evidence/source connections that decayed).

**Bridge slots split across all three pair types** to preserve current cross-project work even when archival weighting is on:

- 40% dormant-dormant pairs (pure archival cross-linking)
- 40% dormant-active pairs (connecting old work to current threads)
- 20% active-active pairs (preserves current cross-project work)

## Soft-delete data model

`projects.py delete <name>`:

1. Iterate pages with `<name>` in their `projects:` set.
   - **Single-tagged** (`projects: [<name>]`): move file to `wiki/.deleted/<name>/<original-path>`. Snapshot graph nodes + edges to `deleted_nodes` / `deleted_edges` tables. Snapshot relevant `tables.db` rows.
   - **Multi-tagged** (`projects: [<name>, other]`): drop `<name>` from the set. Page stays in place. No `.deleted/` move.
2. For each vault file *exclusively* used by deleted pages: move to `vault/.deleted/<name>/`. Files cited by any non-deleted page stay where they are. (Important guardrail — vault is usually shared.)
3. Update `.curator/projects.json`: mark project as `deleted_at: <timestamp>` but keep the entry so `restore` works.
4. Rebuild graph (now without the deleted nodes/edges).

`projects.py restore <name>`: inverse — moves files back from `.deleted/`, replays the deleted-table rows into the live tables, removes the `deleted_at` flag.

`projects.py purge <name>`: hard-delete from `.deleted/` and the deleted-table snapshots. Separate, deliberate command.

## Project rename (absorb)

`projects.py rename <from> <to>`:

- For all pages with `projects:` containing `<from>`, replace with `<to>` (deduping if `<to>` already in the set).
- If `projects/<from>.md` exists and `projects/<to>.md` doesn't, rename the file.
- If both exist, delete `projects/<from>.md` and rewrite all wikilinks pointing at it to point at `projects/<to>.md`.
- Update `.curator/projects.json`.
- Rebuild graph.

Mechanical, contained. No deleted-table writes (use `delete` if you want recoverability).

## Wiki merge

**Ships as a separate skill: [`curiosity-merge`](https://github.com/benjsmith/curiosity-merge).** The merge / subgraph-export / discover-bridges verbs operate on external data (someone else's wiki) and have a different trust model than daily curation, a smaller audience, and an independent release cadence. Installable from curiosity-engine's `setup.sh` optional-install menu alongside caveman and semantic-search. Public sub-wikis use the GitHub topic tag [`curiosity-wiki`](https://github.com/topics/curiosity-wiki) for discovery.

`curiosity-merge merge ../wiki-b --as-origin <name>`:

1. **Vault reconciliation**: walk `wiki-b/vault/`, sha256 each file, compare to `wiki-a/vault/` index. Identical content (different filename) → dedupe to one canonical name; rewrite citation stems via `naming.py resync-stems`. Different content (same filename) → rename one with discriminator.
2. **Source-stub reconciliation**: stems collapse naturally after vault dedup. Two stubs pointing at the same vault file → merge into one (union the citing-page sets, preserve both wikis' notes/annotations).
3. **Page-name collision handling**: same stem, both wikis have it. Three sub-cases:
   - **Identical content** → keep one, drop the other.
   - **Both substantive, semantically same topic** → flag for human merge; keep both as `transformer.md` and `transformer-from-<origin>.md` with a manual-reconciliation queue page listing all such pairs.
   - **Different topics that happen to share the stem** → rename one with origin discriminator (e.g. `transformer-electrical.md`).
4. **Graph union**: rebuild kuzu graph across the merged wiki. Citations and wikilinks unify automatically because they reference stems.
5. **Project tag propagation**: every page from wiki-b inherits an `origin: <name>` audit tag in addition to its `projects:` set.
6. **Bridge discovery pass** (`discover-bridges --across-origins`): semantic similarity across pages from different origins, surfaces high-similarity pairs as cross-wikilink candidates. Review queue file; accepted bridges become wikilinks, rejected pairs go to a `dismissed` list.
7. **Audit report**: `.curator/merge-<timestamp>.md` summarizes all reconciliations, collisions, and bridge candidates.

**Friend's wiki / sub-wiki incorporation**: identical operation, with `--as-origin <friend-name>`. The origin tag preserves attribution. `subgraph-export --by-origin <friend-name>` is the inverse op.

**Bridge discovery as a standalone op**: also useful within a single wiki — periodically run `discover-bridges` to find high-similarity page pairs that aren't yet wikilinked. The merge case is just bridge-discovery scoped to cross-origin pairs.

## Bootstrap from existing project folders

Selective bulk import via `import` (or `archive` for archival material):

```
curiosity import-folder ~/Documents/projects/foo \
  --as-project foo \
  --filter "*.pdf,*.md,*.docx" \
  [--dry-run]
```

- Copies (does not move) matching files into `vault/inbox/foo/`. Originals untouched.
- Pre-tags each ingested item with `projects: [foo]`, short-circuiting the classifier.
- Idempotent: skips files already imported by sha256, so re-running picks up only new additions.
- Honors `.curiosity-ignore` in the source dir.

The `--filter` default is "things curiosity can ingest" — PDFs, markdown, docx, csv, xlsx, pptx, images. Code and binaries are skipped.

Friction is intentional. Aim the command at subdirectories; use `--filter` aggressively. The act of running the command forces selectivity.

## Implementation history

Shipped 2026-05-03 across six waves; see `CHANGELOG.md` for per-wave detail and the corresponding commits.

1. **Foundation** — `projects.py` (create/list/exists/rename/delete/restore/purge), `projects/<name>.md` home-page convention, `projects:` frontmatter, `classify-projects` SWEEP op (citation-graph signals).
2. **Activity tracking** — `.curator/activity.log` (JSONL); `local_ingest.py` `--archival` and `--projects` flags; `activity_log.py` library + CLI.
3. **Recency-weighted planner** — `scripts/planner.py allocate` with default + archival modes; `epoch_summary.py` gains `project_activity` field. Single-project / no-project wikis collapse to today's behaviour.
4. **Semantic classifier step** — cosine similarity to project home embeddings, cold-start-guarded at 5 substantive home pages, opt-in via `embedding_enabled`.
5. **Cross-project bridge candidates** — `connection_candidates` enriched with project tags; planner fills the bridge slot per default/archival rules.
6. **Cross-wiki operations** (`merge`, `subgraph-export`, `discover-bridges`) — separate skill, [`curiosity-merge`](https://github.com/benjsmith/curiosity-merge). Different trust model (external data ingestion); installable from `setup.sh`.
