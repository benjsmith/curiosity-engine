# Curiosity Engine

A self-improving knowledge wiki for Claude Code. Add sources to a vault, build interlinked wiki pages, and let an autonomous evolve loop make the wiki better overnight.

## Quick start

Install the skill, then point Claude Code at a fresh working directory:

```bash
claude skill install curiosity-engine
mkdir my-research && cd my-research
claude
> set up a knowledge base here
> add ~/papers/some-paper.pdf to the vault
> what do I know about transformer architectures?
> run the curator for 20 cycles in the background
```

The skill bootstraps `vault/` and `wiki/` in the current directory on first use. Cloning the repo directly into `~/.claude/skills/curiosity-engine/` is an equivalent alternative to `claude skill install`.

## How it works

**The Vault** is a folder of raw source files — PDFs, Word docs, slide decks, web clips, screenshots, markdown, anything. Claude Code reads them natively through its multimodal capabilities. Text extractions sit alongside originals and are indexed in a SQLite FTS5 database for sub-millisecond BM25 search.

**The Wiki** is a git-tracked directory of markdown files that Claude Code writes and maintains. Entity pages, concept pages, synthesis documents — all interlinked with wikilinks, all citing vault sources. The wiki is the compounding artifact: it gets richer with every source ingested and every question asked.

**The Evolve Loop** autonomously improves the wiki overnight. It finds the worst-scoring page (by observable lint signals), investigates the specific problem, drafts an improvement, and accepts it only if it passes a compression-progress test: more knowledge in fewer tokens. Filler is rejected automatically. The wiki never gets worse, only better or unchanged.

## Operations

| Command | What it does |
|---|---|
| "add X to the vault" | Ingests a source file, extracts text, updates wiki pages |
| "what do I know about X?" | Searches wiki and vault, synthesizes an answer |
| "lint" | Scores every wiki page on contradictions, staleness, missing links, query failures |
| "run the curator for N cycles" | Autonomous improvement loop (runs in background) |

## The acceptance criterion

A wiki edit is accepted only if:
1. No sourced claims are lost
2. At least one improvement: better tokens-per-claim ratio, a contradiction resolved, or a wikilink added
3. The page grows at most 20% in compressed tokens

This operationalizes Schmidhuber's compression progress: genuine knowledge improvements make the wiki a shorter description of the same domain.

## What's in the vault search

SQLite FTS5 with BM25 ranking. Sub-millisecond queries. Unlimited concurrent readers (WAL mode). Zero dependencies — sqlite3 is Python stdlib. No vector database, no embeddings, no model downloads. The wiki itself is the semantic layer.

## Intellectual lineage

| From | Idea taken |
|---|---|
| [Karpathy's LLM-Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | The wiki as a compounding artifact. Three layers, four operations. |
| [Karpathy's Autoresearch](https://github.com/karpathy/autoresearch) | Keep-or-revert ratchet with a measurable metric. Git as the ledger. |
| [MemPalace](https://github.com/milla-jovovich/mempalace) | Store everything verbatim. Don't let AI decide what to forget. |
| [Caveman Compression](https://github.com/wilpel/caveman-compression) | Strip grammar at read-time. LLMs reconstruct it for free. |
| Schmidhuber (1991–2010) | Curiosity = compression progress. The first derivative of understanding. |
| Loewenstein (1994) | Curiosity peaks at intermediate knowledge gaps. Observable signals, not self-assessment. |

## What this does NOT include

- **No AAAK dialect** — regresses retrieval 12 points, bespoke notation
- **No palace hierarchy** — spatial metaphor for semantic structure; just use directories
- **No self-assessed curiosity formula** — observable signals only; avoids the noisy-TV problem
- **No meta-evolution** — fixed settings; analyze logs later with a human
- **No write-time compression** — wiki is clean markdown; compress at read-time only
- **No vector database** — FTS5 handles keyword search; the wiki handles semantics
- **No API client** — Claude Code IS the agent

## Dependencies

Zero. Every script uses Python standard library only (sqlite3, json, re, pathlib).

## License

MIT
