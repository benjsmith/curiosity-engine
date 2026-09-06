# Structured datasets

CE recognises datasets in a corpus, preserves their literal records, and lets a
curator propose a data model that connects to the rest of the wiki. Three stages
stay strictly separate:

| Stage | Who decides | Output |
|---|---|---|
| **Extraction** | code only — no LLM | literal records + metadata, in the vault |
| **Model proposal** | curator, from profiles + cited metadata | a *non-operative* draft |
| **Curation** | existing wiki review / ratchet | ordinary source / entity / concept pages |

Inferred meaning never masquerades as source data. Extraction is deterministic;
anything semantic is a proposal until a human accepts it.

The additive pipeline follow-up implements Git-backed correction recovery, stable
source-record IDs, streamed JSONL, exact nested selectors, and full-record FTS.
See [commands and design choices](dataset-pipeline-design.md).

## Scope

This release handles **JSON and JSONL** batches, reusing the CSV/XLSX and
extracted-table infrastructure. It is not a general unstructured-data modeller.

CE remains a source-backed knowledge system. Operational databases stay
authoritative: there are no connectors, no synchronisation, and no write-back.
A supplied export is treated as **dated evidence**, not as a live mirror. A
purely tabular workload with no knowledge-curation purpose belongs in a database,
not here.

Authoritative class schemas live in wiki frontmatter; SQLite remains derived
state, rebuildable from the vault originals plus the recorded mappings.

## 1. Extraction (`structured_data.py`, via `local_ingest.py`)

`.json` and `.jsonl` join the ingest extensions. Extraction is pure code: no
semantic inference, no unit conversion, no derived business values.

### Envelope detection — the complete rule set

Only these shapes are recognised. Anything else is preserved as UTF-8 text with
`extraction_quality: unsupported` and `data_complete: false`; ambiguity is
reported, never guessed.

| Root shape | Result |
|---|---|
| Array of objects | one record collection, path `""` |
| JSONL, one object per line | one record collection, path `""` |
| Object with immediate object-record arrays | one collection per array, path `"/<key>"`; every other key becomes a **metadata** table |
| Object with no record arrays | a single key/value metadata view |
| Anything else (scalars, mixed arrays, nested arrays of objects) | unsupported → UTF-8 fallback |

Envelope detection looks only at **immediate** members of the root object. There
is no recursive search for "the real records", because that guess is exactly the
kind of inference this layer refuses to make.

### Fidelity guarantees

- **Field names are RFC 6901 JSON pointers** relative to a record, so nesting is
  reversible and collisions cannot occur: `{"a":{"b":1}}` → `/a/b`, while
  `{"a/b":1}` → `/a~1b`.
- **Columns are the sorted union** of pointers across records — deterministic
  ordering, stable across runs.
- **Every cell is a JSON literal.** `null`, `""`, `false`, `0`, `"0"` and `0`
  stay distinguishable; an absent field renders as `⟨missing⟩`, which is not a
  value any JSON document can produce.
- **Numbers keep their original lexeme.** `1.2300e-12` and
  `900719925474099312345` survive exactly; nothing is routed through a binary
  float. Identifiers like `"001"` keep their quotes and leading zeros.
- **Nested arrays are preserved as JSON values.** Child-table modelling is a
  modelling-stage decision, not an extraction-stage one.

### Failure policy — no silent record loss

| Input | Behaviour |
|---|---|
| Malformed JSON | whole file rejected; nothing written |
| Malformed JSONL line | whole file rejected, with the line number |
| JSONL line that is not an object | rejected, with the line number |
| Duplicate keys in one object | rejected (last-wins would destroy a record) |
| Blank JSONL lines | skipped; they carry no record |
| Non-finite numbers (`NaN`, `Infinity`) | rejected — not valid JSON |
| Empty JSON file | rejected as malformed JSON |
| Empty JSONL file, empty array, empty object | accepted, reported as zero records |
| A limit exceeded | rejected, naming the limit |

A rejected drop-mode file is **retained**, not deleted. Success is never
reported for a partial read.

### Limits

Configurable under `auto_mode` in `.curator/config.json`:

| Key | Default |
|---|---|
| `max_raw_bytes` | 50 MB |
| `max_depth` | 64 |
| `max_fields` | 512 |
| `max_cell_bytes` | 1 MB |
| `max_records` | 100 000 |
| `max_cells` | 2 000 000 |
| `max_stage_bytes` | 512 MiB |
| `max_extract_bytes` | 200 KB (preview only) |

### Previews vs. complete records

The `.extracted.md` body is a **bounded preview** for humans, FTS, and the
existing promotion path. It is never the record store.

The complete representation is the **retained original plus the recorded
extractor version and options** — the smallest additive design, since it adds no
second copy of the data that could drift. `structured_data.load_extraction()`
re-reads the original, verifies its SHA-256 against the frontmatter, and replays
the extraction deterministically. If the hash no longer matches, replay refuses
rather than returning something that is not what was ingested.

Frontmatter records `sha256`, `kept_as` (or `source_in_place`),
`structured_version`, `structured_format`, `structured_options`,
`records_accepted`, `records_rejected`, `data_complete`, and
`preview_truncated`. `data_complete` describes the **source**;
`preview_truncated` describes only the Markdown. The two are reported separately
so a truncated preview is never mistaken for a partial dataset.

### Untrusted content

Extractions keep the `untrusted: true` frontmatter and the
`<!-- BEGIN/END FETCHED CONTENT -->` wrapping. Scrub runs over **decoded values
across the whole file**, including records that never appear in the preview, so
content cannot smuggle injection markers past the gate by sitting beyond the cap.

Source metadata is data. A dataset field called `table`, `instructions`, or
`type` is a value, never CE control frontmatter — the two namespaces never mix.
Markdown escaping neutralises pipes, brackets, backticks, and line breaks in the
rendered preview, but the rendered cell is never the only recoverable form: the
literal is always recoverable from the original.

Escaping also covers backslashes, emphasis, and strikethrough, including the
unsupported-root fallback. Fallback data cannot close the fetched-content
wrapper or introduce wiki links. `structured_preview_version` versions rendering
separately from extraction: a rendering upgrade produces a new extraction on
re-ingest, preserving existing citation targets and `json-records-v1` replay.

## 2. Promotion (`sweep.py promote-extracted-tables`)

The existing public contract is unchanged: `tables_present` / `tables_extracted`
discovery, ≤100 rows → full GFM, >100 rows → 10-row snapshot plus column
summary, full rows in `.curator/tables.db._extracted_tables`, standard `[tab]`
naming, source-stub backlinks, `WikiLink` / `Cites` edges, and numeric-review
protections.

Two additive changes:

- **Promotion reads the full extraction, not the Markdown preview.** For
  structured sources it calls `load_extraction()`, so `_extracted_tables` gets
  every accepted row even when the preview held a handful.
- **The PDF false-positive filter is format-aware.** `looks_spurious_table` still
  guards PDF output; it is bypassed for JSON, where a single-column or one-row
  collection is legitimate data rather than misdetected prose.

Structured `[tab]` pages additionally carry `collection_path`, `collection_kind`,
`record_encoding: json-literal-v1`, and `table_content_sha`. Row origins land in
`.curator/tables.db._structured_lineage` (`table_stem`, `row_idx`,
`collection_path`, `source_locator`, `source_sha`, `extractor_version`), where
the locator is a JSON pointer (`/12`) or a JSONL line (`line:13`).

**Unchanged detection** is source identity plus extractor identity:
`extraction_sha` + `row_count` + `is_snapshot` + `table_content_sha`. A changed
value in an unchanged number of rows is therefore detected. Re-ingesting the same
bytes under a newer extractor version produces a new extraction rather than
silently reusing the old one, and an older UTF-8 JSON extraction is left in place
with its citations intact. Pages carrying `numeric_review_done` are never
overwritten by a replay.

The rendering version also participates in unchanged detection for unreviewed
tables. Reviewed pages remain protected across snapshot-threshold and content-hash
changes. Accepted reviews now reference Git-trackable correction recipes. Existing
reviewed pages need `tables.py checkpoint-reviews` while their database is still
available; future recovery can then reconstruct those corrections.

Computed profiles and column summaries are labelled derived. They never
substitute for the literals.

## 3. Model proposal (`datasets.py`)

An explicit, bounded, post-ingest operation. It does **not** change CURATE's
default planner modes or quotas, and no CURATE wave triggers it automatically.

```
datasets.py profile <extraction>...              # observations only
datasets.py propose --spec <json> --output <json>  # non-operative draft
datasets.py check   <proposal>                   # sources + entity hash current?
datasets.py apply   <proposal> --reviewed-page <md>  # through the ratchet
datasets.py plan    <proposal> --output <json>   # pin reviewed schema for import
```

`profile` reports observed properties only — per-field type counts, nullability,
distinct counts, uniqueness **over the full accepted collection**, duplicate
record counts, and sample literals. It states no business meaning.

A proposal spec requires the curator's judgments, which cannot be derived:
`grain` (what one row represents) and `membership_evidence` (why these files are
one dataset). It also carries `semantic_notes`, `unresolved` issues, entity and
attribute structure, candidate keys, units, and original-field mappings.

Guard rails:

- **Column names never merge on coincidence.** Membership is the curator's cited
  claim, not a name match.
- **Meaning is never inferred from a suggestive field name.** A field called
  `wavelength` gets its unit from cited metadata or from nothing.
- **Uniqueness is validated over the whole collection**, not a preview.
- **Generated identifiers are labelled technical IDs** (`record_id`, derived from
  the row's source origin). Composite or absent natural keys are represented
  within current `tables.py` capabilities via that technical key, so repeated
  observations of one subject never silently become duplicate entities.
- **Existing human schemas are authoritative.** If the entity page already
  declares a table, the proposal cannot replace it; the curator must supply an
  explicit column-to-pointer mapping instead. Inference is blocked, not merged.
  Existing schemas default to their mapped primary key; `technical_key` must be
  explicitly supplied when using an unmapped technical primary key. New inferred
  schemas still default to `record_id`.
- **`unresolved` blocks application.** Open modelling questions must be closed
  first.

### Staying non-operative

`propose` writes no wiki page. Application goes through the existing review
path: the curator authors the entity page, and `apply` re-checks the entity
content hash, scrubs, verifies that newly added citations actually relate to
their claims, and runs the standard `score_diff` ratchet before an atomic
replace. The hash is rechecked immediately before the write, so a page edited
during review is never clobbered.

## 4. Validated import (`tables.py import-dataset`)

Rows reach class tables only through `tables.py`. There are no direct class-table
SQL writes and no destructive migrations.

```
tables.py import-dataset --plan <plan.json> [--dry-run]
```

The import is atomic and validated per row: declared types, nullability, JSON
well-formedness, and shape constraints. It refuses to run unless the live schema
matches the reviewed entity page, and it rechecks the entity hash immediately
before commit.

- **Existing rows are never overwritten.** A primary key that already exists with
  different values is a conflict and aborts the whole import.
- **Replay is idempotent.** Re-running a plan reports `unchanged`, not duplicates.
  New proposals also pin the extraction file's hash, so edited extraction
  settings or metadata invalidate the proposal. Older v1 manifests lacking that
  additional hash remain readable and retain their original source-hash checks.
- **Every row keeps its origin** in `_dataset_lineage` (`origin_json` with
  extraction, collection, locator and source hash; plus the mapping, the fields
  that were missing, and the plan hash).
- **A manifest is written** to `wiki/_data/imports/<plan_hash>.json`, git-tracked
  alongside the accepted pages, and is itself a replayable plan. An interruption
  can leave an unused manifest; it can never leave committed rows without a
  replay recipe.
- **A missing derived database is recoverable**: run `tables.py recover --dry-run`,
  then `tables.py recover`; it reconstructs manifest-backed data and referenced corrections.
- **Absent and null stay distinguishable.** SQL has one NULL, so a missing field
  and an explicit `null` both store as NULL in the class table; the fields that
  were *absent* for a row are recorded in `_dataset_lineage.missing_json`, so the
  distinction the extractor preserved is not lost at the import layer.

Originals and manifests are published from complete, flushed temporary files;
failed writes leave no partial final artifact that blocks a retry. Repeated
drop-folder ingests consume the duplicate once its retained original is verified,
and report indexing failures separately from successful extraction.

The importer preserves `-0` and integers outside SQLite's exact integer range as
text. Numeric summaries omit bounds with an explicit warning if a valid JSON
exponent exceeds the summary library's range. Existing rows are not migrated:
an older import that stored explicit JSON null as the text `null` will conflict
with the corrected SQL NULL representation and needs a reviewed correction.

The `tables.py recover` command combines promotion, correction replay, schema
sync, manifest imports, and record-index rebuilding. See
[dataset pipeline follow-up](dataset-pipeline-design.md) for the Git workflow,
legacy review checkpoints, concurrency requirements, and recovery limits.

## 5. Curating knowledge

Ingest stays lean. The post-ingest operation produces or updates ordinary pages
through the existing gates — never one page per row, per metadata field, or per
page type. Populate what the evidence warrants, reuse existing pages and identity
conventions, and create reciprocal links.

| Type | What it carries here |
|---|---|
| `sources/` | dataset origin, version, collection method, scope, coverage, metadata, limitations, links to source tables |
| `entities/` | the dataset, its producer, instruments, domain entities/classes, identity, and the accepted table schema |
| `concepts/` | field definitions, measures, methods, domain vocabulary |
| `evidence/` | supported observations with method / result / interpretation |
| `facts/` | reusable atomic claims |
| `analyses/` | supported synthesis, relationships, caveats, open questions |
| `tables/`, `figures/` | comparisons and visual findings, with query/transformation provenance |

Use the existing citation contracts. Extracted records are cited by linking the
`[[tab-...]]` page plus the `(vault:<extraction>)` citation — there is **no**
citation syntax for `_extracted_tables` rows, and none may be invented. Accepted
class data uses the supported row/query forms,
`(table:<name>#id=<id>)` and `(table:<name>?query=<q>)`.

For unstructured sources nothing changes. Records may be modelled only from
explicit extracted evidence with source-span provenance; uncertain LLM-derived
records stay proposals subject to the existing review gates. This release adds no
general-purpose unstructured extractor, no corpus-specific harvest fork, and no
automatic CURATE wave.

## Example workflow

A spectrograph run arrives as two JSON files, each an object with metadata plus
an `observations` array.

```bash
SKILL=<skill_path>

# 1. Ingest. Metadata and records stay separate collections.
uv run python3 $SKILL/scripts/local_ingest.py --file ~/data/spectra-run12.json
uv run python3 $SKILL/scripts/local_ingest.py --file ~/data/spectra-run13.json

# 2. Stubs, then promotion: [tab] pages + full rows in _extracted_tables.
uv run python3 $SKILL/scripts/sweep.py fix-source-stubs wiki
uv run python3 $SKILL/scripts/sweep.py promote-extracted-tables wiki

# 3. Profile — observations only, over every accepted record.
#    Run from the workspace root; extraction paths must live under vault/.
uv run python3 $SKILL/scripts/datasets.py profile \
    vault/structured-*-spectra-run12.json.extracted.md \
    vault/structured-*-spectra-run13.json.extracted.md
```

Read the profiles, then write a spec supplying the judgments code cannot make:

```json
{
  "entity_page": "wiki/entities/spectral-observation.md",
  "name": "spectral_observation",
  "grain": "one spectrograph reading of one sample",
  "membership_evidence": "both files declare instrument spectrograph-alpha and identical wavelength_units metadata",
  "semantic_notes": {"/wavelength": "unit comes from file metadata, not the field name"},
  "sources": [
    {"extraction": "vault/structured-...-spectra-run12.json.extracted.md", "collection": "/observations"},
    {"extraction": "vault/structured-...-spectra-run13.json.extracted.md", "collection": "/observations"}
  ]
}
```

```bash
# 4. Draft the proposal. Writes nothing to the wiki.
uv run python3 $SKILL/scripts/datasets.py propose --spec spec.json --output proposal.json
```

Author `reviewed.md`: frontmatter carrying the proposed `table` block, and a body
that explains the grain, the unit provenance, and the membership evidence — with
real citations to both extractions and wikilinks to the `[tab]` pages. Then:

```bash
# 5. Apply through the ratchet (scrub + citation check + score_diff).
uv run python3 $SKILL/scripts/datasets.py apply proposal.json --reviewed-page reviewed.md

# 6. Schema from the reviewed page, then the pinned import plan.
uv run python3 $SKILL/scripts/tables.py sync wiki/entities/spectral-observation.md
uv run python3 $SKILL/scripts/datasets.py plan proposal.json --output plan.json
uv run python3 $SKILL/scripts/tables.py import-dataset --plan plan.json --dry-run
uv run python3 $SKILL/scripts/tables.py import-dataset --plan plan.json

# 7. Normal wiki wiring.
uv run python3 $SKILL/scripts/sweep.py fix-index wiki
uv run python3 $SKILL/scripts/graph.py rebuild wiki
```

Now `query_router.py sql` can answer aggregate questions over
`spectral_observation`, the `[tab]` pages carry the literal records, and the
entity and concept pages explain what a row means — with the inference visible as
inference.

## Limitations

- JSON/JSONL only. No parquet, no SQLite dumps, no database connectors.
- Automatic envelope detection is deliberately shallow; exact `--record-pointer`
  and `--metadata-pointer` selectors opt into nested collections. Without selectors,
  a nested record envelope inside
  a root object remains metadata, while unsupported root shapes use the text
  fallback. No recursive collection discovery is performed.
- Nested arrays stay JSON values. There is no automatic child-table extraction.
- One primary key per imported table; composite natural keys are represented
  through a technical key plus documented mappings.
- `float`-typed columns are rejected for exact numeric lexemes: use `int` or
  exact `text`/`json` so precision survives.
- The whole file must fit `max_raw_bytes`. JSONL file operations stream through
  temporary SQLite; ordinary JSON remains bounded in memory.
- Replay depends on the retained original. If a `source_in_place` file changes on
  disk, replay refuses — by design — and the source must be re-ingested as new
  dated evidence.
