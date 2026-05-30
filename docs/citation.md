# Acknowledgements & Citation

The skill's table-handling design — store extracted tables as
canonical `[tab]` wiki pages with the full row data in the rdb,
treat numeric values as literal transcriptions never to be derived
from, and keep extraction-time work cheap and deterministic — was
informed by the design principles described in the BigMixSolDB
paper:

> Voinea, A.; Thöni, A. C. M.; Veenman, E.; Huck, W. T. S.;
> Kachman, T.; Mabesoone, M. F. J. *BigMixSolDB: Extraction of a
> solubility database in solvent mixtures with an uncertainty-
> quantified large language model-based pipeline*. ChemRxiv
> preprint, 2026. DOI:
> [10.26434/chemrxiv.15001616/v1](https://doi.org/10.26434/chemrxiv.15001616/v1)
>
> Original code & data:
> <https://github.com/BigChemistry-RobotLab/BigMixSolDB> ·
> Zenodo:
> [10.5281/zenodo.19388678](https://doi.org/10.5281/zenodo.19388678)

What this skill **does not** borrow: the paper's Docling + frontier-
LLM-YAML extraction stack itself. We use `pypdf` + `pdfplumber` for
table extraction (alongside `openpyxl` and `python-pptx` for
spreadsheets and slide decks), all running locally with no model
call at ingest. Concretely transferred from the paper's design:

- **Extract literally; never derive.** Numeric values in
  extracted-table pages are flagged with a literal-transcription
  notice; downstream workers are instructed not to unit-convert or
  compute when citing.
- **Per-table page artefacts with full provenance.** Each
  pdfplumber-recovered table becomes its own
  `wiki/tables/tab-<source>-t<n>.md` page citing the source via the
  standard `(vault:...)` DSL, mirroring the paper's per-source
  structured artefact.
- **Snapshot + summary above a row threshold** (default 100 rows).
  Page becomes a 10-row snapshot plus per-column summary (numeric
  min/max or distinct-value sample); the full table lands in
  `.curator/tables.db` for queryable access. This mirrors the
  paper's separation of "human-readable artefact" from "machine-
  queryable database."
- **Row-level provenance in the rdb.** Every row in
  `_extracted_tables` carries `source_stub`, `source_extraction`,
  and `extraction_sha`, so the database is reproducible from the
  git-tracked corpus.

If you use this skill's scientific-extraction pipeline in published
work, please cite the paper above to credit the design principles.
The implementation is the curiosity-engine project's own.

## Other influences

| From | Idea taken |
|---|---|
| [Karpathy's LLM-Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | The wiki as a compounding artefact. |
| [Karpathy's Autoresearch](https://github.com/karpathy/autoresearch) | Keep-or-revert ratchet with a measurable metric. Git as the ledger. |
| [MemPalace](https://github.com/milla-jovovich/mempalace) | Store source material verbatim; don't distill at ingest. |
