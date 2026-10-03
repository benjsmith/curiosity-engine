# Workspace layout

A curiosity-engine workspace is three folders plus read-only views. Commands and page formats are in [`../SKILL.md`](../SKILL.md). How to open the graph is in [`viewers.md`](viewers.md).

## Vault, wiki, curator state

**Vault** (`vault/`). Raw sources you add: PDFs, papers, slides, clips, markdown. Each source can have a sibling `.extracted.md` of clean text used for search. The vault is append-only: ingest does not rewrite a source after it lands, so wiki citations keep pointing at the file you added.

**Wiki** (`wiki/`). Markdown the curator and you write, with `[[wikilinks]]` and `(vault:path)` citations. Pages live in typed directories: `sources`, `entities`, `concepts`, `analyses`, `evidence`, `facts`, `tables`, `figures`, `notes`, `todos`, `projects`, `procedures`, `executions`. Accepted wiki edits are git commits.

**Curator state** (`.curator/`). Per-workspace operational files (logs, config, prompts, local indexes). Not the notes themselves.

Markdown in `wiki/` is the source of truth. The graph viewer (`viewer.sh`) and Open Knowledge Format export (`okf_export.py build wiki --output-dir <dir>`) are read-only projections.

## Citations

Wiki claims cite vault files. The curator rejects an edit that drops an existing citation, cites a passage the source text does not support, or adds a new page that misses the floors for that directory. Exact floors and the check scripts are in SKILL.md.

## What you run

From a workspace that already has `wiki/`:

```bash
bash <skill_path>/scripts/viewer.sh open
```

That serves the graph viewer on `http://127.0.0.1:8090`. For a same-origin embed (Switchbay, okbay), set `CE_PUBLIC_BASE` (typically `/embed/ce`) and see [`viewers.md`](viewers.md).

Query, ingest, and curate from the workspace root using the commands in SKILL.md. The curator reads the vault, writes the wiki, and answers from the wiki.
