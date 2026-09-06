# Structured-data implementation review — 2026-09-06

Baseline: clean v1.6.1 tree, `a0e0db874c1b94a94748b264c668977f132a6ca9`.
Reviewed the JSON/JSONL extractor, ingest integration, promotion, profiles,
schema proposals, reviewed-page application, class imports, and replay.

The architecture is sound: literal extraction, proposed meaning, and accepted
class data are separate stages. RFC 6901 pointers avoid ambiguous dotted-key
collisions; the retained original allows complete promotion beyond the preview
cap. Existing human schemas and CURATE's default planner remain authoritative.

## Bugs fixed

| Failure | Correction |
|---|---|
| Re-promotion overwrote reviewed tables when the snapshot threshold or table hash differed | Check the numeric-review guard before rendering/idempotency decisions |
| Existing human schemas defaulted to an absent `record_id` column | Default to explicit field mappings for existing schemas |
| Explicit null in a JSON-typed class column became the string `null` | Store SQL NULL; retain missing-field distinction in lineage |
| Very large integers crashed profiling; `-0` became `0` | Infer exact text conservatively and reject lossy integer mappings |
| Extreme valid exponents crashed snapshot summaries | Keep literal rows; report unavailable bounds |
| Quotes/backslashes in collection names produced invalid YAML | Serialize structured titles and normalization lists safely |
| Markdown formatting changed displayed literal cells; fallback could close the fetched wrapper | Escape Markdown syntax in cells and unsupported-root previews |
| Old preview caches bypassed rendering fixes | Version rendering independently; refresh unreviewed promotion output |
| Duplicate drop files remained queued; indexing failure turned unchanged extraction into failure | Consume verified duplicates and report index failures separately |
| Interrupted original/manifest writes could leave partial final files and block retry | Publish complete temporary files exclusively |
| Proposals pinned original bytes but ignored edited extraction metadata/options | Also pin extraction bytes for new proposals; support older manifests |
| Case-only duplicate columns and malformed declarations passed dataset schema validation | Reject before importing/applying schemas |

## Validation and compatibility

Regression coverage includes JSON/JSONL shapes, nested pointers, literal types,
null/missing lineage, numeric precision, limits, scrub and wrapper safety,
snapshots with complete SQLite rows, promotion idempotency, numeric-review
protection, human schemas, stale proposals/plans, transaction rollback,
interrupted publication, old manifests, and database reconstruction from imports.

Compatibility fixtures use unchanged CSV/XLSX goldens, a PPTX golden, real pypdf
and pdfplumber inputs (prose, bordered tables, combined output, blank-page
multimodal fallback), text ingest, and the real FTS index. Existing orchestration,
citation, vault-path, bootstrap, graph-render, and vector suites also run.
These are synthetic workspaces; no live v1.4.0 paper workspace was modified or
used as a migration trial. No class-table migration or automatic CURATE wave
was added.

Final result: **238 tests passed, zero skipped** with the dependencies listed in
`docs/testing.md`; `git diff --check` passed. Existing test output includes SQLite
connection ResourceWarnings and the graph renderer's expected missing-kuzu
fallback warning; the live kuzu rebuild was not exercised.

Already-imported rows are not silently repaired. In particular, a legacy JSON
column containing text `null` needs a reviewed correction before a new import
can agree with it. Existing citation targets are retained.

## Recommended next round, in priority order

Historical recommendations from the initial review. The approved follow-up
(1B/2B/3C/4B/5C) ships in v1.7.0; see [the implementation guide](dataset-pipeline-design.md)
for the chosen behavior and limits. The combined suite passes 260 tests.

1. **A complete recovery command and correction ledger.** Class import manifests
   are replayable, but reviewed extracted-table corrections currently depend on
   SQLite rows/backups. Losing the database loses corrections beyond a snapshot.
   Persist reviewed cell changes as versioned artifacts, then provide one command
   to sync schemas, promote originals, replay corrections, and replay imports.
   Report a missing reviewed row store explicitly instead of simply skipping it.
2. **Stable dataset/record identity across re-ingest.** Technical IDs include the
   extraction filename, which depends on source location and preview settings.
   Moving a file or changing the preview cap can create fresh technical IDs for
   the same source records. Add explicit dataset/version identity and a reviewed
   cross-export deduplication policy; keep provenance separate from domain keys.
3. **Streaming and aggregate resource limits.** JSONL still accumulates records,
   flattened values, rendered cells, profiles, and import payloads in memory.
   Stream parsing and staged imports; enforce field/cell budgets across all
   selected files, not just individual extractions and the combined row count.
4. **Explicit nested collection selectors.** Let a spec nominate JSON pointers
   such as `/response/data/items` and separate metadata pointers. This handles
   common API envelopes deterministically without guessing grain or meaning.
5. **Searchable records beyond the preview.** FTS currently indexes only the
   bounded Markdown. Add a bounded record index with source locators and decoded
   Unicode values, or route data questions directly to structured queries.
   Test retrieval for facts that occur only after the preview cutoff.

Keep these additive. No per-row wiki pages, inferred units, automatic schema
replacement, corpus-specific forks, or default planner changes are needed.
