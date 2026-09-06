# Dataset pipeline follow-up

Approved scope: correction artifacts + recovery (1B), stable source-record
identity (2B), aggregate limits + streamed JSONL (3C), exact JSON Pointer
selectors (4B), and a separate record FTS index (5C).

Use the existing wiki Git repository for artifact history. Do not introduce a
second version-control system or make automatic commits from data commands.
Correction artifacts and import manifests belong under `wiki/_data/` and are
committed with accepted wiki changes through the existing workflow.

Implementation contracts:

- Build recovery in a temporary workspace/database, validate all replay inputs,
  and publish only on success. Refuse to discard class data without manifests.
- New identity policy is explicit and versioned; preserve old plan/ID behavior.
  New IDs use dataset identity, source hash, collection, and source locator.
- Stage JSONL records in temporary SQLite; keep exact literals, full-collection
  uniqueness, atomic import, and bounded previews. Bound aggregate rows, fields,
  cells, input bytes and staging disk use; clean up failures.
- Selectors locate records and metadata only. Persist them in extraction options
  and validate pointer syntax, presence and selected shapes before publication.
- Record FTS is derived and rebuildable, separate from source-preview FTS.
  Results retain source/table citations and locators; no new citation syntax or
  changes to CURATE's default ranking/planner.

## Commands and operational behavior

Run commands from the workspace root with the existing `uv run python3
<skill_path>/scripts/` prefix.

### Git-backed correction recovery (1B)

`sweep.py apply-numeric-review` now writes a sparse correction recipe under
`wiki/_data/corrections/<hash>.json` and puts its path in the accepted table
page's `correction_manifest` frontmatter. A recipe contains source/extraction
hashes, table headers, row/column locators, original and corrected literal cells,
the resulting full-table hash, and review provenance. It covers corrections
outside the ten-row snapshot. `tables.py restore-backup` records the resulting
state in the same way.

**Git remains the sole history store.** Commit accepted pages and referenced
recipes together in the existing wiki repository. There is no new repository,
branching system, or automatic commit inside these commands. Content-addressed
files prevent a crash from overwriting an older page's replay recipe; an
unreferenced recipe is inert. To undo a review after losing SQLite backups,
restore the accepted page from the desired Git revision and run recovery. Keep
the recipe it references and the original source available.

```text
tables.py checkpoint-reviews --wiki wiki
tables.py recover --wiki wiki --dry-run
tables.py recover --wiki wiki
```

`checkpoint-reviews` is a one-time bridge for existing reviewed pages: while
their current database is still available, record their reviewed rows against
the original extraction. It skips pages that already have a recipe. Commit the
result through the normal wiki workflow.

Recovery builds temporary copies and a fresh database, promotes original tables,
replays only recipes referenced by accepted pages, syncs entity schemas, replays
import manifests, and rebuilds record FTS. It validates before publishing; the
live wiki and its Git repository are unchanged. SQLite backup transactions
publish each derived database. Run recovery with ingest/curation writers stopped.
If tables publish successfully but the separate record cache cannot be published,
the report says tables were recovered and gives the cache-rebuild retry command;
it does not claim that nothing was applied. Publication waits are bounded.

Recovery refuses changed source anchors, missing recipes, incompatible schemas,
and existing class rows that its manifests cannot reproduce. A prose-only edit
to an entity is allowed during recovery when its schema still matches the
manifest. Ordinary imports retain their stricter whole-page hash guard.

This reconstructs manifest-backed imports; legacy manually inserted class rows
still require a database backup or a reviewed import plan. A missing database
cannot reveal records for which no replay evidence was ever retained. A Git
clone alone also does not supply originals kept outside the wiki repository.

### Stable source-record identity (2B)

New proposals carry `identity_policy: source-record-v2` and `dataset_id` (defaults
to the explicitly declared class-table name; set it explicitly for long-lived
datasets). IDs hash dataset ID, source-content hash, collection pointer, and
source locator. File location, extraction filename, preview cap, and Markdown
rendering version do not affect them. Re-importing identical records from a
relocated/re-rendered source reports unchanged rows and adds source lineage.

Changed export bytes are a new source version and get new IDs. This does not
deduplicate overlapping exports by natural/domain keys. Old manifests without
an identity policy retain their original algorithm and IDs. Existing IDs are
never silently migrated; reusing a legacy table with the new policy requires a
deliberate identity transition to avoid mixing both ID populations.

### Staged JSONL and aggregate limits (3C)

Actual JSONL ingest and replay read one line at a time, retaining an exact
temporary original and staging flattened values in temporary SQLite. Promotion,
profiles, and imports iterate staged rows. Unique-value and duplicate-record
counts cover all records, using SQLite rather than unbounded Python sets.
Malformed late lines reject the file and remove the temporary stage; drop-folder
originals remain intact. The in-memory `extract(bytes, ...)` helper remains for
existing callers, while file-based operations use `extract_path`.

Single JSON documents remain bounded in memory. Limits are still enforced on
individual files. Proposal specs can add a `limits` object; it is persisted in
the plan and additionally bounds selected files' combined raw bytes, records,
union of fields, and rows × union-field count. `max_stage_bytes` defaults to
512 MiB and bounds temporary SQLite/original staging, including overlapping
source and combined-record stages. Import payloads are consumed incrementally
inside the existing atomic transaction. Temporary staging is always disposable.
Staging budgets below 64 KiB are rejected because SQLite needs space for its
schema and indexes before any records can be processed.

### Exact nested selectors (4B)

```text
local_ingest.py --file export.json --record-pointer /response/data/items \
  --metadata-pointer /response/metadata
```

Use one records pointer and repeat `--metadata-pointer` for disjoint metadata
views. RFC 6901 escapes are supported (`~1` for `/`, `~0` for `~`); an empty
records pointer selects the root. The records target must be an array of
objects, including an empty array. Missing pointers, malformed escapes,
overlapping selectors, and incompatible shapes fail explicitly. Selectors apply
to JSON, not JSONL, and are persisted in `structured_options` for replay.
Unselected fields remain in the retained original. No filtering, joining,
renaming, unit conversion, or inferred field meaning occurs.

### Complete record FTS (5C)

Successful structured ingest also indexes all records and selected metadata into
`.curator/records.db`. This is a separate, derived FTS5 index; the existing
vault-preview index and default retrieval ranking remain unchanged.

```text
datasets.py search-records "observatory" --limit 10
datasets.py search-records "観測天文台" --extraction vault/export.extracted.md
datasets.py index-records vault/export.extracted.md
datasets.py index-records --rebuild
```

Search indexes decoded Unicode values and returns bounded excerpts, literal
previews, source locators, the existing `(vault:...)` citation, and a `[[tab-...]]`
link when promoted lineage is available. Stale in-place sources are reported and
excluded. Indexing failures are reported separately from successful extraction;
indexing/rebuild can be retried. Search exposes original literal records, while
reviewed corrections remain visible through the cited table. Use structured
queries for numeric comparisons; record FTS adds no embeddings or automatic
changes to planner mixes.
