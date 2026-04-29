# Table-extraction comparison results

Per-fixture verdicts across the four backend adapters under `tests/extractors/`. Verdict legend: PASS = every assertion satisfied; PART(h/t) = `must_contain` short by t-h items but no other failures; FAIL = a `must_not_contain` matched, the backend errored, or zero `must_contain` hits; n/a = backend doesn't claim to support this fixture kind; UNAV = backend missing optional deps.

- `baseline_pypdf` — available
- `docling_pass` — UNAVAILABLE: docling models not cached locally and HuggingFace unreachable: LocalEntryNotFoundError. To enable, run Docling once with network access so models are cached, then re-run the harness.
- `local_ingest_extras` — available
- `pdfplumber_pass` — available

## Comparison grid

| fixture | `baseline_pypdf` | `docling_pass` | `local_ingest_extras` | `pdfplumber_pass` |
|---|---|---|---|---|
| chem-buffer.pdf | PASS | UNAV | n/a | PASS |
| gene-expression.csv | FAIL | UNAV | PASS | n/a |
| merged-headers.xlsx | FAIL | UNAV | PASS | n/a |
| pptx-3x3.pptx | FAIL | UNAV | PASS | n/a |
| prose.pdf | PASS | UNAV | n/a | PASS |
| scientific-table.pdf | PASS | UNAV | n/a | PASS |

## Per-cell detail

### `chem-buffer.pdf`

- **baseline_pypdf** — PASS (note: few_words=37) [{'extraction_method': 'pypdf_failed', 'has_math': False, 'has_tables': False, 'sanity_passed': False, 'sanity_note': 'few_words=37', 'multimodal_recommended': True}]
- **docling_pass** — UNAV (error: docling models not cached locally and HuggingFace unreachable: LocalEntryNotFoundError. To enable, run Docling once with network access so models are cached, then re-run the harness.)
- **local_ingest_extras** — n/a
- **pdfplumber_pass** — PASS [{'tables_found': 1, 'extraction_method': 'pypdf+pdfplumber'}]

### `gene-expression.csv`

- **baseline_pypdf** — FAIL (error: not_extracted (.csv not in DEFAULT_EXTS))
- **docling_pass** — UNAV (error: docling models not cached locally and HuggingFace unreachable: LocalEntryNotFoundError. To enable, run Docling once with network access so models are cached, then re-run the harness.)
- **local_ingest_extras** — PASS [{'extraction_method': 'csv-stdlib'}]
- **pdfplumber_pass** — n/a

### `merged-headers.xlsx`

- **baseline_pypdf** — FAIL (error: not_extracted (.xlsx not in DEFAULT_EXTS))
- **docling_pass** — UNAV (error: docling models not cached locally and HuggingFace unreachable: LocalEntryNotFoundError. To enable, run Docling once with network access so models are cached, then re-run the harness.)
- **local_ingest_extras** — PASS [{'extraction_method': 'openpyxl'}]
- **pdfplumber_pass** — n/a

### `pptx-3x3.pptx`

- **baseline_pypdf** — FAIL (error: not_extracted (.pptx not in DEFAULT_EXTS))
- **docling_pass** — UNAV (error: docling models not cached locally and HuggingFace unreachable: LocalEntryNotFoundError. To enable, run Docling once with network access so models are cached, then re-run the harness.)
- **local_ingest_extras** — PASS [{'extraction_method': 'python-pptx'}]
- **pdfplumber_pass** — n/a

### `prose.pdf`

- **baseline_pypdf** — PASS [{'extraction_method': 'pypdf', 'has_math': False, 'has_tables': False, 'sanity_passed': True, 'sanity_note': 'ok', 'multimodal_recommended': False}]
- **docling_pass** — UNAV (error: docling models not cached locally and HuggingFace unreachable: LocalEntryNotFoundError. To enable, run Docling once with network access so models are cached, then re-run the harness.)
- **local_ingest_extras** — n/a
- **pdfplumber_pass** — PASS [{'tables_found': 0, 'extraction_method': 'pypdf+pdfplumber'}]

### `scientific-table.pdf`

- **baseline_pypdf** — PASS (note: few_words=37) [{'extraction_method': 'pypdf_failed', 'has_math': False, 'has_tables': False, 'sanity_passed': False, 'sanity_note': 'few_words=37', 'multimodal_recommended': True}]
- **docling_pass** — UNAV (error: docling models not cached locally and HuggingFace unreachable: LocalEntryNotFoundError. To enable, run Docling once with network access so models are cached, then re-run the harness.)
- **local_ingest_extras** — n/a
- **pdfplumber_pass** — PASS [{'tables_found': 1, 'extraction_method': 'pypdf+pdfplumber'}]
