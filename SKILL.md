---
name: curiosity-engine
description: "Self-improving knowledge wiki with a vault of raw sources. Use when the user mentions 'curiosity engine', 'wiki', 'vault', 'knowledge base', 'ingest', 'evolve', 'curator', 'lint', or wants to add sources, query accumulated knowledge, check wiki health, or run autonomous improvement. Also triggers on 'add to vault', 'what do I know about', 'improve wiki', 'set up knowledge base', 'new knowledge base', 'run curator'. Use even without explicit naming — if the user wants to file something for later or asks about accumulated knowledge, this is the skill."
---

# Curiosity Engine

A self-improving knowledge wiki. Add sources to a vault, build interlinked wiki pages, and let an autonomous evolve loop make the wiki better overnight.

Inspired by Karpathy's LLM-Wiki (the wiki as compounding artifact), Autoresearch (keep-or-revert ratchet), MemPalace (store everything verbatim), and Caveman Compression (strip grammar at read-time). The acceptance criterion uses Schmidhuber's compression progress: more knowledge in fewer tokens.

## Setup

On first trigger, check if `wiki/schema.md` exists in the working directory. If not, bootstrap a new knowledge base:

1. Ask: "Where should I set up the knowledge base? Here, or a specific path?"
2. `cd` to the chosen path, then run:

```bash
bash <skill_path>/scripts/setup.sh
```

This creates the full project structure, initializes git in the wiki, and creates the FTS5 search index. Tell the user: "Knowledge base ready. Try: 'add ~/some-file.pdf to the vault'"

## Data stores

**Vault** (`vault/`) — Folder of raw source files. Append-only. Never modify existing files.
- Search: `python3 <skill_path>/scripts/vault_search.py "query"` → JSON
- You can read PDFs, images, DOCX, PPTX natively — no extraction libraries needed
- Each source gets a `.extracted.md` alongside it for FTS5 indexing

**Wiki** (`wiki/`) — Git-tracked markdown. You own this entirely.
- YAML frontmatter: title, type, created, updated, sources
- `[[wikilinks]]` between pages, `(vault:path)` source citations
- `index.md` catalogs all pages; `log.md` records all operations

Read `wiki/schema.md` before any operation.

## Operations

### INGEST — "add to vault", "ingest this paper", "file this"

1. Copy original to `vault/` preserving filename (add numeric suffix if duplicate).
2. Read the file directly (multimodal).
3. Write clean text extraction as `vault/<name>.extracted.md`.
4. Index: `python3 <skill_path>/scripts/vault_index.py "vault/<name>.extracted.md" "<title>"`
5. Identify key entities, concepts, claims.
6. Create or update wiki pages in appropriate subdirectory (entities/, concepts/, etc.).
7. Create source summary in `wiki/sources/`.
8. Update `wiki/index.md`. Append to `wiki/log.md` with timestamp.
9. `cd wiki && git add -A && git commit -m "ingest: <filename>"`

### QUERY — "what do I know about X", "search for Y"

1. Read `wiki/index.md` to find relevant pages.
2. Load pages. Run `python3 <skill_path>/scripts/vault_search.py "query"` for vault hits.
3. Read original vault files directly if more context needed.
4. Synthesize answer citing `[[wiki pages]]` and `(vault:path)` sources.
5. If significant new synthesis, offer to file as `wiki/analyses/<topic>.md`.
6. Log: question, pages used, whether vault fallback was needed.

### LINT — "check wiki health", "what needs work", "lint"

1. Run: `python3 <skill_path>/scripts/lint_scores.py`
2. Present ranked results (worst first). Explain each problem dimension.
3. Append summary to `wiki/log.md`.

Lint dimensions (all 0-1, higher = worse):
- **contradictions** — claims disputed by other pages/vault (stub in v1, returns 0)
- **freshness_gap** — stale sources when newer exist (stub in v1, returns 0)
- **crossref_sparsity** — entities/concepts mentioned but not `[[linked]]`
- **query_misses** — past queries needing vault fallback for this page

### EVOLVE — "improve the wiki", "run the curator", "evolve N cycles"

Run as **background task**. Per cycle:

1. `python3 <skill_path>/scripts/lint_scores.py` → pick page with highest composite score.
2. Read page. Identify worst lint dimension.
3. Generate ONE targeted research question:
   - contradictions → "Source X says A, source Y says B — which is current?"
   - freshness_gap → "Are there newer sources updating these claims?"
   - crossref_sparsity → "What concepts should this page link to?"
   - query_misses → "What info were users looking for that this page lacked?"
4. Investigate: vault search, read sources, read linked wiki pages, web search if useful.
5. Draft updated page.
6. **Acceptance test** — measure with `python3 <skill_path>/scripts/compress.py wiki/<page>.md`:
   - Output: `compressed_tokens=N sourced_claims=M tpc=X.X`
   - Sourced claims = non-empty lines containing `(vault:`
   - Accept only if ALL:
     a. `sourced_claims(after) >= sourced_claims(before)`
     b. At least one of: `tpc` decreased, wikilink added, contradiction resolved
     c. `compressed_tokens(after) <= compressed_tokens(before) * 1.2`
7. **ACCEPTED** → write page, `cd wiki && git add <file> && git commit -m "evolve: <page> | <reason>"`
8. **REJECTED** → discard draft. Do NOT write.
9. Print: `[evolve K/N] <page> (score: X.XX) → ACCEPTED/REJECTED: <reason>`
10. Append one-line result to `wiki/log.md`.

On completion: "N cycles: M accepted, K rejected. Notable improvements: ..."

## Writing rules

- **Never modify vault files** (only add new ones + their `.extracted.md`).
- **Concise prose.** Short sentences. No filler. Every sentence carries information.
- **Cite every factual claim:** `(vault:papers/attention.extracted.md)`
- **Link generously:** `[[Entity Name]]` for every mention that has or deserves a page.
- **Update index.md** on any page creation or deletion.
- **Append to log.md** after every operation with ISO timestamp.
- **Git commit** in wiki/ after every accepted change.

## Wiki page format

```markdown
---
title: Page Title
type: entity | concept | source | analysis
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: [path/to/source.extracted.md]
---

Concise factual prose. [[Cross References]]. (vault:source/path) citations.
```
