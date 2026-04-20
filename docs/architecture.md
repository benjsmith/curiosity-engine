# Architecture and design

This document explains the **why** behind Curiosity Engine. For operational details (commands, settings, script usage), see [`../SKILL.md`](../SKILL.md).

## The three-object model

Three objects hold the state of any curiosity-engine workspace.

**Vault** (`vault/`). Raw source files — whatever you dropped in: PDFs, papers, slide decks, web clips, markdown. Each source has a sibling `.extracted.md` with clean text used for search and by the curator. **Append-only.** Once a source is in the vault, the skill never modifies it. That makes the vault a trustworthy provenance layer: every wiki citation points at something unchanged since ingest.

**Wiki** (`wiki/`). Git-tracked markdown. The curator (and you) write pages here with `[[wikilinks]]` and `(vault:path)` citations. Six subdirectories by page type: `sources`, `entities`, `concepts`, `analyses`, `evidence`, `facts`. Each has a conventional shape (see SKILL.md's page-format section). Every accepted edit is a git commit; reversion is always available.

**Curator state** (`.curator/`). Not git-tracked. Per-workspace state: logs, schema, prompt templates, graph database, sweep copy, guard snapshot. This is the curator's operational memory — what it's tried, what's in flight, which scripts to use.

## The citation-preserving ratchet

This is the skill's single most important property. The things the curator may NOT do in a CURATE loop:

1. **Drop a citation.** `score_diff.py` verifies `citation_count(after) >= citation_count(before)`.
2. **Bloat the page.** `body_tokens(after) <= body_tokens(before) * 1.5`, frontmatter excluded.
3. **Cite the wrong source.** Each newly-added `(vault:...)` citation's surrounding claim words are FTS5-queried against the cited source's extraction. If no match, the citation is suspect and the edit is rejected.
4. **Fail the new-page floors.** New pages need ≥2 citations, ≥2 wikilinks, ≥100 words by default. Relaxed for atomic `facts/` (≥1/≥1/≥30) and `evidence/` (≥1/≥1/≥50).

On reject the edit is NOT written; the orchestrator moves on. On accept the file is written and the page is surfaced to the fresh-context batch reviewer for semantic quality judgement.

The ratchet is deliberately mechanical. It doesn't judge prose quality — that's the reviewer's job. It catches catastrophic regressions that a praise-mode reviewer might miss because they look innocuous.

## Hash-guarded scoring

The curator can edit exactly one thing about its own operation: `.curator/sweep.py` (the workspace-editable hygiene pass). Every other script that scores, gates, or evaluates is SHA-256 hashed at the start of each wave (`evolve_guard.sh snapshot`) and re-checked at the end (`evolve_guard.sh check`). Any drift aborts the wave and reverts.

Off-limits to the curator: `lint_scores.py`, `score_diff.py`, `epoch_summary.py`, `scrub_check.py`, `naming.py`, `graph.py`.

Why: a self-improving loop that can edit its own reward function is a self-deceiving loop. The guard forecloses that entire class of failure rather than relying on vigilance.

## The CURATE loop

One wave = one commit. Plan → Execute → Evaluate → Stop check. Full specification in SKILL.md's `### CURATE`. Design rationale for the shape:

**Deterministic planning.** Every wave, the orchestrator reads `epoch_summary.py` output and picks targets by mechanical rules. No reviewer call for planning — planning is selection work over pre-ranked candidates, and reviewer tokens are expensive.

**Three wave modes.**

| mode | trigger | what the wave does |
|---|---|---|
| **wire** | orphan-rate contribution > 60% of residual composite | Runs a LINK-style propose→classify→apply pass across the whole wiki. Heals inbound-link starvation. |
| **create** | saturation pivot OR vault frontier exhausted | Creates new pages: evidence 30%, facts 10%, demand promotions 20%, analyses 40% (remainder). |
| **repair** | otherwise | Edits existing worst-scoring pages + frontier work. |

Quotas within create mode are fixed percentages so evidence, facts, and demand promotions each get a floor per wave; slack rolls only to analyses (the unbounded bucket). Demand promotions send proper-noun stems to `entities/` and abstract stems to `concepts/`.

**Batch reviewer.** After a wave's workers return, ONE opus reviewer agent reviews every edit in the wave (not one reviewer per edit). Roughly 10× fewer reviewer spawns.

**Hash-guard snapshot** bookends every wave — fast check, cheap insurance.

## Scoring dimensions

Four dimensions in `lint_scores.py`, each weighted 0.25:

- **crossref_sparsity** ("under-linked") — fraction of entity/concept mentions on a page that aren't `[[linked]]`.
- **orphan_rate** ("nobody links here") — penalty for few inbound wikilinks. 0 inbound → 1.0, 1 → 0.66, 2 → 0.33, 3+ → 0.0.
- **unsourced_density** ("uncited claims") — fraction of substantive prose lines with no `(vault:...)` citation.
- **vault_coverage_gap** ("vault material this page isn't using") — fraction of top BM25 vault hits for this page's topic not cited.

Composite is the mean of the four. Worst pages float to the top of the CURATE plan.

Scores are cached per page (keyed by `text_hash + inbound_count`). Unchanged pages return cached scores — `compute_all` is linear in *changed* pages, not total pages. Invalidation triggers on text change, inbound-link change, title-set change (page added/renamed/deleted), and vault row-count change (new sources could shift vault_coverage_gap).

The ratchet uses these as proxies for wiki health, not as ground truth. A page can score 0 across all four and still be terrible prose. The batch reviewer and the sampled spot auditor are the semantic backstop.

## Trust beyond the reviewer — the spot auditor

The per-wave batch reviewer runs in praise-mode (grading well-formed edits as acceptable). Nuanced misrepresentation of a source — citing a real passage but over-reaching on the claim — often slips past. A **spot auditor** runs every N waves (default 20): an opus Agent with an explicitly adversarial prompt ("find one concrete inaccuracy in this page; quote the claim and the source passage that contradicts it"). Findings land in `## spot-audit-findings` in `.curator/log.md` for human review.

Cheap in aggregate, catches things the praise reviewer can't.

## When the skill struggles

Honest failure modes, in rough order of when you'll hit them:

- **Curator over-extracts on small corpora.** Below ~10 sources there isn't enough material for cross-source synthesis; CURATE waves produce weak analyses. Ingest more before running long sessions.
- **Keyword retrieval misses paraphrases.** FTS5 is sharp for exact / stem matches, weak for paraphrased semantic queries. The optional MiniLM+sqlite-vec layer mitigates.
- **Cognitive overhead.** Learning the vocabulary (the six page types, three wave modes, four scoring dimensions) takes effort. Not a low-touch tool.
- **Rate-limit bound.** `parallel_workers × reviewer × waves/hour` saturates API tiers. Tune via `parallel_workers` but you can't make it free.
- **~500-page wiki ceiling.** Beyond that, plan latency grows. The incremental score cache helps; the tiered-vault design (bounded wiki + unbounded indexed vault) unlocks more; cluster-scoped CURATE (see below) keeps individual waves coherent past the threshold.
- **Single user.** No merge protocol for multiple humans editing the same wiki.

## Cluster scoping at scale

Past `cluster_scope_threshold` non-source pages (default 500), `epoch_summary.py` emits a `wave_scope` field — the worst-scoring page plus every page within two wikilink hops, in either direction. Phase 1 of the CURATE loop honours the scope for **repair mode**: editorial and frontier target selection are restricted to pages inside the scope. Create and wire modes stay global (new pages aren't in the graph yet; inbound-link starvation legitimately crosses clusters).

The effect: each repair wave works on a locally coherent neighbourhood — edits to related pages compound (a wikilink added on page A is useful to page B in the same scope) and the worker brief context stays bounded even as the wiki grows into the thousands. The seed rotates: next wave picks a different worst-scoring page, which sits in a different neighbourhood.

Cluster scoping is a knob, not a fixed feature. Set `cluster_scope_threshold: 0` in config.json to disable entirely; at 100 to activate much earlier; at 1000 to defer until the wiki is genuinely large. Default 500 matches the point where a single plan-phase brief over the full wiki starts costing more than a cluster.

## Why not RAG?

RAG retrieves document chunks by embedding similarity and stuffs them into a prompt. Excellent at "find the passage", but it doesn't **compound**. Every query starts fresh; retrieval doesn't learn; cross-document connections surface only when they happen to embed close together.

Curiosity-engine inverts the split. Vectors are an optional fallback; the primary semantic layer is the wiki itself — concept pages are the hubs, wikilinks express relationships, and the curator tends the structure over time. The wiki is a compounding artefact; RAG's vector index is a cached lookup.

Short version: if you want fast answers from a fixed corpus, RAG. If you want understanding that compounds across reads, this.

## Why not just an LLM agent with file access?

An agent with Bash + Read + Write tools can absolutely read your papers and answer questions. The missing property is **state that improves**. Without a curated artefact, every session re-discovers the corpus. Without a ratchet, edits can regress prior work. Without a scoring layer, connection-hunting is ad-hoc and user-driven.

Curiosity-engine is what you get if you give that agent (a) a persistent notebook, (b) a quality gate, (c) an autonomous improvement loop, and (d) a protocol for when and how to write in it.
