# Workspace layout

A curiosity-engine workspace is three folders plus read-only views. Commands and page formats are in [`../SKILL.md`](../SKILL.md). How to open the graph is in [`viewers.md`](viewers.md).

## Vault, wiki, curator state

**Vault** (`vault/`). Raw sources you add: PDFs, papers, slides, clips, markdown. Each source can have a sibling `.extracted.md` of clean text used for search. The vault is append-only: ingest does not rewrite a source after it lands, so wiki citations keep pointing at the file you added.

**Wiki** (`wiki/`). Markdown the curator and you write, with `[[wikilinks]]` and `(vault:path)` citations. Pages live in typed directories: `sources`, `entities`, `concepts`, `analyses`, `evidence`, `facts`, `tables`, `figures`, `notes`, `todos`, `projects`, `procedures`, `executions`. Accepted wiki edits are git commits.

**Curator state** (`.curator/`). Per-workspace operational files (logs, config, prompts, local indexes). Not the notes themselves. An optional `meta-ontology.yaml` here names extra page types and the relationships between them. See [Typed edges](#typed-edges).

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

## Typed edges

`hypothesis` is a built-in page type, drawn in `#7570b3`. The other type colours are unchanged. To add a type, or a relationship such as "this evidence supports that hypothesis", add `.curator/meta-ontology.yaml`. `color` is optional on a node or an edge. When a node sets it, that colour replaces the default for the type. A custom type with no colour keeps its name and gets a stable colour of its own, rather than the white unclassified swatch. An edge colour is used for that relationship's stroke.

```yaml
version: 1
nodes:
  - type: hypothesis
    label: Hypothesis
    folder: hypotheses
    prefix: hyp
    color: "#7570b3"
    description: |
      State the claim, the scope it applies to, and what observation
      would change the verdict.
edges:
  - type: supports
    color: "#0072b2"
    source: [evidence]
    target: [hypothesis]
    description: |
      The result on the evidence page matches a prediction the
      hypothesis states.
  - type: contradicts
    color: "#d55e00"
    source: [evidence]
    target: [hypothesis]
    description: |
      The result on the evidence page conflicts with a prediction
      the hypothesis states.
```

`source` and `target` are the page types the edge runs between. On the evidence page, list the hypothesis under that name and wikilink it:

```yaml
---
title: "CPU samples"
type: evidence
supports:
  - hypotheses/queue-grows
---

The series rose with the queue length. See [[queue-grows]].
```

An edge that leaves `source` and `target` off describes a relationship the graph already draws (a body wikilink, a citation, `relates_to`). It does not add a new one. A file with no such edges, and a workspace with no file, keeps the current graph.

Rebuild after editing pages or the file, then ask along one relationship:

```bash
uv run python3 <skill_path>/scripts/graph.py rebuild wiki
uv run python3 <skill_path>/scripts/graph.py neighbors wiki hypotheses/queue-grows.md --direction in --edge supports
uv run python3 <skill_path>/scripts/graph.py retrieve wiki "queue grows" --edge supports --route graph
```

The neighbors command lists evidence pages that support that hypothesis. `retrieve --edge` walks only that relationship. Omit `--edge` and those relationships are included with wikilinks. In the classic graph, the page subgraph, and Atlas — including the viewer embedded in Switchbay — a declared type keeps its name. Node and edge colours come from the palette above, then from `color` in the file when that field is set.
