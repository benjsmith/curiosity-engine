# Curiosity Engine

Drop files into a folder, ask questions, and let an agent keep improving your notes while you sleep.

Curiosity Engine is a Claude Code skill for people who read a lot in a specific domain and want the understanding to compound. You feed sources (papers, PDFs, blog posts, docs) into a vault; an autonomous curator reads them, writes concise interlinked wiki pages, cites every claim, and tends the wiki overnight. A citation-preserving ratchet ensures the wiki only gets better or unchanged, never worse.

Built on top of [Claude Code](https://claude.com/claude-code). The wiki is plain markdown — open it in Obsidian, browse the graph, edit by hand. Everything's git-tracked.

## How it works

Three objects, three verbs.

```
  your files
      │
      ▼
  ┌─────────┐       ┌───────────┐      ┌─────────┐
  │  vault  │──────▶│  curator  │─────▶│  wiki   │
  │ (raw)   │ reads │  (agent)  │writes│ (notes) │
  └─────────┘       └───────────┘      └─────────┘
                         ▲                  │
                         │ ask              │ answer
                         └──────── you ─────┘
```

- **Vault** (`vault/`) — where your sources live. Append-only; never modified after ingest. FTS5 keyword-indexed; optional MiniLM semantic index for fuzzier queries on large corpora.
- **Wiki** (`wiki/`) — where your understanding lives. Git-tracked markdown with `[[wikilinks]]` and `(vault:path)` citations. Six subdirectories (`sources`, `entities`, `concepts`, `analyses`, `evidence`, `facts`) that carry shape — short notes in each.
- **Curator** — an agent that reads the vault, writes in the wiki, and has an autonomous mode that keeps improving the notes in the background.

Three verbs:

- **`ingest`** — *"add `~/papers/foo.pdf` to the vault"*. The source is copied in, text extracted, indexed.
- **`query`** — *"what do I know about transformers?"* The curator searches the wiki and vault, answers with citations, ends with a probing follow-up.
- **`curate`** — *"curate this wiki for an hour"*. The curator runs a plan-execute-evaluate loop, drafts improvements in parallel, gates each through a mechanical check, has a reviewer judge the wave, and commits.

## Quick start

```bash
# install the skill (pick one path — all equivalent)
claude skill install curiosity-engine
npx skills add benjsmith/curiosity-engine
# or: git clone into ~/.claude/skills/curiosity-engine/

# set up a workspace
mkdir my-research && cd my-research
claude
> set up a knowledge base here
> add ~/papers/some-paper.pdf to the vault
> what do I know about transformer architectures?
> curate this wiki for an hour
```

The first command runs `setup.sh`, which creates the folder layout, initialises the wiki git repo, drops in a Claude Code settings file that auto-allows safe operations, and optionally installs companion skills.

## What makes it different

- **Citations are load-bearing.** Every factual claim cites a vault source. A mechanical gate (`score_diff.py`) rejects any edit that drops a citation or adds one whose source doesn't FTS5-match the claim.
- **Wiki structure IS the semantic layer.** Concept and entity pages are the hubs; wikilinks express relationships. No vector DB required — though one can be bolted on for fuzzy fallback on large corpora.
- **Keep-or-revert ratchet.** Autonomous curator proposes edits; a reviewer grades; accepted edits commit, rejected ones revert. The wiki never regresses.
- **Hash-guarded scoring.** Scoring scripts are SHA-256 hashed between waves; the curator can't edit them to game its own metrics.
- **Obsidian-compatible.** Open `wiki/` as an Obsidian vault — wikilinks, backlinks, graph view all work without plugins.

## When to use (and when not)

**Reach for this** when:
- You're reading 30–300 substantial sources in a domain over weeks or months.
- You care about provenance — every claim traceable to a vault file.
- You want cross-source connections surfaced, not just stored.
- You want the understanding to persist across sessions and compound.

Good fits: personal research, literature reviews, idea gardens, due-diligence analysts, cross-field synthesis.

**Reach for something else** when:
- You want instant answers from a huge (>1000) doc store → use RAG (LlamaIndex, LangChain).
- You're working on code → use Claude Code directly on the repo.
- You need multi-user collaboration → Obsidian sync, Notion, Confluence.
- Knowledge is structured (tables, time-series) → a database.

For the full design rationale (why not RAG, how the ratchet works, where the skill struggles), see [`docs/architecture.md`](docs/architecture.md).

## Viewing in Obsidian

`wiki/` is plain markdown with `[[wikilinks]]`. Open Obsidian → **Open folder as vault** → pick `<your-workspace>/wiki`. Backlinks and the graph view light up immediately, no plugins. Leave Claude Code running in the workspace root; Obsidian picks up new pages as the curator writes them.

Treat Obsidian as a read-mostly view. Manual edits outside a `git -C wiki commit` won't be seen by the curator until the next operation reads the page.

## Caveman mode (optional compression)

[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) is a companion skill that strips filler tokens so the curator writes terse, dense pages (~30–40% reduction). Setup prompts to install it; answer `y` to wire in. Configured via the `caveman` block in `.curator/config.json`. Details in the skill's SKILL.md.

## Semantic vault search (optional)

For vaults above a few hundred sources where keyword search starts missing fuzzy matches, an optional MiniLM embedding index layered over sqlite-vec gives the curator a semantic fallback. Setup prompts to install `sentence-transformers` + `sqlite-vec` (~200MB model download); opt in only if you need it. Embeddings augment FTS5, never replace — keyword stays primary.

## Inspired by

| From | Idea taken |
|---|---|
| [Karpathy's LLM-Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | The wiki as a compounding artefact. |
| [Karpathy's Autoresearch](https://github.com/karpathy/autoresearch) | Keep-or-revert ratchet with a measurable metric. Git as the ledger. |
| [MemPalace](https://github.com/milla-jovovich/mempalace) | Store everything verbatim. Don't let AI decide what to forget. |
| [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) | Optional companion skill for read/write token compression. |

## Dependencies

- **Python 3** — most scripts use stdlib only.
- **[uv](https://github.com/astral-sh/uv)** (required) — workspace venv + script runner. Installed by `setup.sh` if missing.
- **[kuzu](https://kuzudb.com/)** (required) — embedded property-graph database for structural queries. Auto-installed into the workspace venv.
- **[sentence-transformers](https://sbert.net/)** + **[sqlite-vec](https://github.com/asg017/sqlite-vec)** (optional) — semantic vault search. ~200MB model.
- **[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)** (optional) — read/write compression.
- **git** — the wiki is a git repo.
- **Claude Code** — this is a skill, not a standalone CLI.

## License

MIT
