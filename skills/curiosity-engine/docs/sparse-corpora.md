# Sparse and lower-density corpora

Curiosity Engine is often used on **sparse** vaults: email + calendar
enterprise dumps, uneven scientific folders, mixed notes — corpora where
many sources are short, transactional, or low-signal, and where a naive
“summarize everything into the wiki” pass produces diffuse stubs instead
of a readable map.

This note describes how curation should behave on those corpora. It is
guidance for workers and for people reading the skill; the binding
one-liners live in `SKILL.md` (Writing rules) and `template/prompts.md`.

## Goal

The wiki should be a **compressed but interpretable map** of what the
sources support:

- High-level pages carry lasting, abstracted material (what this org /
  topic / architecture *is*), with wikilinks to related pages.
- Day-to-day transactional detail (thread who-said-what, meeting
  logistics, leaf identities) stays on lower pages — or is left for
  vault retrieve / RAG — unless it is constitutively high-level (e.g. a
  lasting mandate email).
- Nothing important should be unrecoverable: wiki + query, vault SQL for
  scalars, and vault RAG for source reconstruction cover the rest.

Dense, already-well-structured corpora (mature scientific wikis, curated
handbooks) need less of this: do not invent hierarchy or rewrite good
pages into thinner textbook summaries.

## Hierarchy-fit (when the content supports it)

When the corpus *actually* has levels — organization → department →
person, or topic → subtopic — match each page’s abstraction to its
level. Prefer linking substantive related pages over dumping many leaf
sources onto a high-level page.

**Do not:**

- Invent a hierarchy the sources do not support.
- Narrate “hub / peer / leaf” structure in the prose. Readers follow
  wikilinks when they want more detail.
- Force peer-link patterns that only make sense for entity trees onto
  flat concept graphs.

Entity trees are the usual home for subordinate/peer linking. Concepts
may nest when knowledge is genuinely broken up that way; otherwise keep
concept pages as ordinary topic pages with generous wikilinks.

## What to keep out of high-level pages

- Email or meeting snippet pastes and signature blocks.
- Blow-by-blow thread chronologies (prefer vault retrieve; see
  `email_thread_policy` / `skip_retrieve` in QUERY crystallise).
- Benchmark / wave / eval / harness / fuel metadata — wiki pages are for
  readers of the domain, not the experiment.

## Practical curation order (sparse enterprise-like vaults)

1. **Wire sources** — substantive `sources/` summaries (who / when /
   subject / key claims + vault cites), not hollow stubs.
2. **Deepen high-level pages** — org / topic portraits at the right
   abstraction, with links into the graph.
3. **Selective synthesis** — analyses that crystallize multi-source
   insight; skip near-duplicate digests (`naming.py recommend-analysis`).
4. **QUERY write-back** — only after the graph and embeddings are
   healthy; never write experiment scaffolding into page bodies.

Scientific or other non-org corpora: apply the same density taste, but
skip org-shaped hierarchy unless the content is structured that way.
