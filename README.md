# Curiosity Engine

A self-improving knowledge wiki for Claude Code. Add sources to a vault, build interlinked wiki pages, and let an autonomous curate loop work on the wiki in the background.

## Quick start

Install the skill, then point Claude Code at a fresh working directory:

```bash
claude skill install curiosity-engine
mkdir my-research && cd my-research
claude
> set up a knowledge base here
> add ~/papers/some-paper.pdf to the vault
> what do I know about transformer architectures?
> curate this wiki in the background
```

### Alternative quick install

```bash
npx skills add benjsmith/curiosity-engine
```

Or clone the repo directly into `~/.claude/skills/curiosity-engine/`. All three paths produce an identical install — pick whichever matches your workflow.

### Viewing the wiki in Obsidian

The `wiki/` directory is plain markdown with `[[wikilinks]]`, so any Obsidian vault opened on it works out of the box:

1. Open Obsidian → **Open folder as vault** → pick `<your-workspace>/wiki`.
2. Wikilinks, backlinks, and the graph view light up immediately — no plugins needed.
3. Leave Claude Code running in the workspace root; Obsidian picks up new pages as the curator writes them. Git commits from the curator show up as normal file changes.

Treat Obsidian as a read-mostly view. You can edit by hand, but remember that any change outside a `git -C wiki commit` won't be seen by the curator until the next operation reads the page.

## How it works

**The Vault** is a folder of raw source files — PDFs, Word docs, slide decks, web clips, screenshots, markdown, anything. Claude Code reads them natively through its multimodal capabilities. Text extractions sit alongside originals and are indexed in a SQLite FTS5 database for sub-millisecond BM25 search.

**The Wiki** is a git-tracked directory of markdown files that Claude Code writes and maintains. Entity pages, concept pages, synthesis documents — all interlinked with wikilinks, all citing vault sources. The wiki is the compounding artifact: it gets richer with every source ingested and every question asked.

**The Curate Loop** autonomously tends the wiki. A single loop — **plan → execute → evaluate → stop check → loop** — picks targets by observable lint signals, drafts improvements in parallel worker subagents, applies each edit through a citation-preserving mechanical gate (no sourced claim lost, no >1.5× raw-token bloat, each new citation FTS5-matches its source), and has a fresh-context reviewer vet the non-editorial edits. Each epoch runs a fixed wallclock budget (Karpathy-style autoresearch). Filler is rejected automatically. The wiki never gets worse, only better or unchanged.

The loop never fetches new content on its own. It only reorganizes what's already in the vault, and flags a source-wishlist when the vault runs thin on a topic. Acquisition is your job; curation is the loop's.

The curator may edit exactly one thing about its own operation: `.curator/sweep.py` (its hygiene-pass script). Every diff is logged; if the post-edit improvement rate degrades, the reference copy is restored automatically. The scoring and measurement scripts (`lint_scores.py`, `score_diff.py`, `epoch_summary.py`, `scrub_check.py`, `naming.py`, `graph.py`) are SHA-256 hash-guarded on every epoch boundary, and any tampering aborts the epoch and reverts.

The loop runs until one of: user interrupt, 24h wallclock, or guard drift. When editorial rate-of-improvement saturates, the plan shifts to new analyses, open questions, and source-wishlist items rather than stopping — curiosity trumps diminishing editorial returns.

## Operations

| Command | What it does |
|---|---|
| "add X to the vault" | Ingests a source file, extracts text, updates wiki pages |
| "what do I know about X?" | Searches wiki and vault, synthesizes an answer |
| "lint" | Scores every wiki page on crossref sparsity, orphan rate, unsourced density, vault coverage |
| "curate" / "run" / "improve" / "iterate" | Autonomous CURATE loop (runs in background until interrupted, 24h, or guard drift) |

## The acceptance criterion

A wiki edit is accepted only if:
1. No sourced claims are lost (`citations(after) >= citations(before)`)
2. No extreme bloat (`body_tokens(after) <= body_tokens(before) * 1.5`, frontmatter excluded)
3. Each newly added `(vault:...)` citation FTS5-matches its source (catches spurious citations without a reviewer pass)

These are hard floors. Quality beyond the floors is judged by a fresh-context reviewer (opus by default), not by the mechanical gate.

## What's in the vault search

SQLite FTS5 with BM25 ranking. Sub-millisecond queries. Unlimited concurrent readers (WAL mode). `sqlite3` is Python stdlib — no extra install. No vector database, no embeddings, no model downloads. The wiki itself is the semantic layer.

## Caveman mode (optional)

[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) is a companion skill for read-time and write-time token compression. Setup prompts to install it; answer `y` to wire it in. Configured via the `caveman` block in `.curator/config.json`:

```json
"caveman": { "read": "ultra", "write_analysis": "lite", "write_other": "ultra" }
```

- **read (ultra)** — every wiki page or vault passage is stripped of articles, copula, filler adverbs, pronouns, transitions, and prepositions before the curator reads it. ~30–40% fewer input tokens per read.
- **write_analysis (lite)** — `analyses/` pages stay human-readable; only filler adverbs and transition words are stripped. ~10–15% reduction.
- **write_other (ultra)** — `entities/`, `concepts/`, `sources/`, `evidence/`, `facts/` are written in dense telegraphic form. Compounds: every future read of a compressed page is cheaper.

### Pros

- **Bigger epochs in the same context budget.** ~30–40% savings compound across long runs and let more pages fit into each worker brief.
- **Cheaper full-wiki scans** (lint, epoch_summary, query) once most pages are written at ultra.
- **LLMs reconstruct grammar natively** — worker quality is roughly unchanged on compressed input.
- **Still plain markdown.** Wikilinks, citations, and frontmatter are preserved, so Obsidian and git diffs keep working.

### Cons

- **Ultra pages read poorly to humans.** Open them in Obsidian and you'll see telegraphic text. If you want to browse prose, request an `analyses/` page (lite) or skip caveman entirely.
- **Extra skill dependency** — one more thing to install and keep current.
- **Small quality risk on edge cases.** Very short pages or heavily idiomatic passages occasionally lose nuance under ultra.
- **Irreversible in place.** The uncompressed version isn't stored; recovering expanded prose means re-synthesizing from the vault.

### No-caveman fallback

If you skip caveman, CURATE works verbatim. The `caveman` block in `config.json` is ignored, pages are written in standard prose, and context-budget mitigations kick in instead: capped per-batch page reads, slice-reads of frontmatter + substantive prose, and `lint_scores.py --minimal` output. Expect proportionally shorter epochs for the same token budget.

## Inspired by

| From | Idea taken |
|---|---|
| [Karpathy's LLM-Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | The wiki as a compounding artifact. |
| [Karpathy's Autoresearch](https://github.com/karpathy/autoresearch) | Keep-or-revert ratchet with a measurable metric. Git as the ledger. |
| [MemPalace](https://github.com/milla-jovovich/mempalace) | Store everything verbatim. Don't let AI decide what to forget. |
| [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) | Optional companion skill for read- and write-time token compression. Strip predictable grammar tokens so the curator burns less context. See [Caveman mode](#caveman-mode-optional). |

## What this does NOT include

- **No autonomous web fetching** — the skill never pulls content from URLs on its own. You add sources; the loop improves them. This eliminates the prompt-injection surface entirely.
- **No AAAK dialect** — regresses retrieval 12 points, bespoke notation
- **No palace hierarchy** — spatial metaphor for semantic structure; just use directories
- **No self-assessed curiosity formula** — observable signals only; avoids the noisy-TV problem
- **Limited meta-evolution** — the curator may only edit its own hygiene script (`.curator/sweep.py`); reverse-diffs on degradation. Scoring scripts are hash-guarded.
- **No vector database** — FTS5 handles keyword search; the wiki handles semantics
- **No API client** — Claude Code IS the agent

## Dependencies

- **Python 3** — most scripts use stdlib only (`sqlite3`, `json`, `re`, `pathlib`).
- **[uv](https://github.com/astral-sh/uv)** (required) — workspace venv + script runner. `setup.sh` installs uv if missing, creates `./.venv`, and installs kuzu into it. The skill's canonical command is `uv run python3 <skill_path>/scripts/<name>.py ...`; the `uv run` prefix auto-discovers `./.venv` so imports resolve without activation.
- **[kuzu](https://kuzudb.com/)** (required) — embedded property-graph database behind `graph.py`. Stores `WikiPage`/`VaultSource` nodes and `WikiLink`/`Cites` edges; used for neighbors, shortest-path, shared-source, and bridge-candidate queries during plan/evaluate. Installed automatically into the workspace `.venv` by `setup.sh`.
- **[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)** (optional) — read/write token compression. See [Caveman mode](#caveman-mode-optional). Setup offers to install it via `npx skills add JuliusBrussee/caveman`.
- **git** — the wiki is a git repo; every accepted edit commits.
- **Claude Code** — this is a skill, not a standalone CLI. There is no separate API client; Claude Code is the agent.

## License

MIT
