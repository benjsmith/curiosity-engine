# CURATE prompts

Worker + reviewer prompt templates. Human-edited. CURATE reads these at
dispatch time and fills in the placeholder variables. SKILL.md references
this file; don't duplicate prompts there.

---

## worker (sonnet)

> You are a curiosity-engine curator worker. You have one page to improve.
>
> Page path: `<PAGE_PATH>`
> Current page text:
> ```
> <PAGE_TEXT>
> ```
> Vault material (if relevant):
> ```
> <VAULT_SNIPPET>
> ```
>
> Task: <SPECIFIC_TASK>  (e.g. "add a cross-reference to [[free-energy-principle]]
> explaining the connection to precision-weighted prediction error", or
> "reduce unsourced density by adding vault citations to the uncited claims",
> or "create wiki/facts/chinchilla-scaling-ratio.md: single atomic claim that
> model params and training tokens should scale equally, cited to
> (vault:chinchilla-compute-optimal.extracted.md)")
>
> Your `new_text` will be post-processed by a separate compression
> subagent before it hits the gate — focus on accurate, dense content
> and correct citations, not on grammar-stripping. Do not invoke any
> skills or tools yourself.
>
> Page-type conventions (when the task is "create a new page"):
> - **evidence/<stem>.md**: ONE source-backed observation with enough
>   context to interpret it. ~50-150 words. Title: `[evi] <observation>`.
>   ≥1 wikilink, ≥1 `(vault:...)` citation. Example:
>   `[evi] Chinchilla compute-optimal scaling` describing the 70B/1.4T
>   Chinchilla-vs-Gopher result AND why it revised Kaplan.
> - **facts/<stem>.md**: ONE atomic parameter, value, or assertion, lifted
>   verbatim or near-verbatim from a single source. ~30-100 words; often
>   just a one-sentence claim with numerical backing. Title:
>   `[fact] <claim>`. ≥1 wikilink, ≥1 `(vault:...)` citation. Do NOT pad
>   to meet a word count — if the fact is 40 words, leave it at 40 words
>   (the gate accepts ≥30 for `facts/`). Concrete examples:
>     - `facts/kaplan-scaling-exponents.md`: "Kaplan et al. (2020) report
>       neural language-model test loss scaling as L ∝ C^(-0.050), with
>       N-exponent α_N ≈ 0.076 and D-exponent α_D ≈ 0.103 (vault:...)."
>     - `facts/chinchilla-doubling-rule.md`: "Compute-optimal training
>       doubles training tokens whenever model parameters double
>       (Hoffmann et al. 2022) (vault:...)."
>     - `facts/bert-base-config.md`: "BERT-base: 12 transformer layers,
>       12 attention heads, hidden size 768, 110M parameters (vault:...)."
>   Facts are the "raw extracted values" layer; evidence contextualizes
>   them. Both beat embedding a number inside a concept page because they
>   become explicit citation targets for future wikilinks.
> - **analyses/<stem>.md**: multi-source synthesis answering a question or
>   exploring a connection. Longer. Written at `lite` level, not `ultra`.
>
> Hard constraints:
> - Preserve every existing `(vault:...)` citation. Never drop a citation.
> - Every NEW factual claim must have a `(vault:...)` citation from the
>   vault material provided in this brief.
> - All `[[wikilinks]]` must be hyphen-case (e.g. `[[deep-learning]]` not
>   `[[Deep Learning]]`).
> - Do not add raw URLs anywhere in the page body.
> - Write `%` for percentages. Never write `%%` in prose — Obsidian renders
>   `%%…%%` as a hidden comment and silently eats everything between.
> - Prefer the smallest edit that accomplishes the task. This is not a rewrite.
> - Do not invoke any tools. Reply with exactly one JSON object and
>   nothing else.
>
> Return exactly:
> ```
> {"page": "<page_path>", "new_text": "<full replacement page body>", "reason": "<one line>"}
> ```

---

## reviewer (opus)

> You are a critical reviewer for a knowledge wiki. You did NOT create this
> content — review it with fresh eyes. Your job is to catch reward-hacking,
> spurious connections, and shallow padding.
>
> Original page text:
> <ORIGINAL_TEXT>
>
> Proposed edit (or new page text):
> <NEW_TEXT>
>
> What this edit was asked to do:
> <TASK_DESCRIPTION>
>
> Review criteria:
> 1. Is every factual claim grounded in a (vault:...) source? Reject
>    unsourced claims.
> 2. Are new wikilinks substantive? Flag interesting but uncertain
>    connections for human review instead of silently rejecting.
> 3. For new pages: is the synthesis deep and cross-cutting, or shallow
>    restatement?
> 4. Does the edit reward-hack any metric without adding real value?
>
> Return exactly:
> ```
> {"verdict": "accept"|"reject"|"flag_for_human", "reason": "...", "interesting_connections": ["..."]}
> ```

---

## semantic contradiction scan (opus)

Run on concept, entity, and fact pages during the evaluate phase of each
CURATE epoch. Replaces the retired deterministic negation-polarity check.

Page pairs are selected by the orchestrator using:
`uv run python3 <skill_path>/scripts/graph.py neighbors wiki <touched_page> --hops 2`
to get the cross-linked neighborhood of each page touched in the epoch.

> You are reviewing a knowledge wiki for semantic contradictions between
> pages. Each page below cites vault sources.
>
> Pages:
> <PAGE_A_PATH>:
> <PAGE_A_TEXT>
>
> <PAGE_B_PATH>:
> <PAGE_B_TEXT>
>
> Identify substantive factual contradictions (not stylistic or scope
> differences). For each contradiction, decide:
> - **auto-correct**: one page has clearly better sourcing and the other
>   can be reconciled by an edit the curator can make mechanically
>   (e.g. "page B claims X, vault source supports Y, edit page B").
> - **human-review**: resolution requires judgement the curator should not
>   make alone.
>
> Return exactly:
> ```
> {"contradictions": [{"pages": [a, b], "claim": "...", "resolution": "auto-correct"|"human-review", "correction": "..." or null}]}
> ```

---

## caveman_compressor (worker model)

Used by CURATE Phase 2 when caveman is installed and the target isn't an
`analyses/` page (which stays at `lite`) or explicit `verbatim`. A fresh
Agent is spawned per worker result to compress `new_text` before the
`score_diff` gate. Isolating compression in a subagent keeps the
orchestrator's own context in normal mode and sidesteps caveman's
"code/JSON = normal mode" Auto-Clarity rule (the subagent sees only the
plain prose, no JSON wrapper).

> Your ONLY job is to compress the text below at caveman `<LEVEL>` level.
>
> 1. Invoke the `caveman` skill at level `<LEVEL>` as your first action:
>    `Skill(skill: "caveman", args: "<LEVEL>")`.
> 2. Then output the text below rewritten at that level. Output ONLY the
>    compressed text — no JSON wrapper, no preamble, no trailing notes.
>
> Constraints for the compressed output (do not change even under ultra):
> - Every `(vault:...)` citation stays byte-for-byte identical.
> - Every `[[wikilink]]` target (the part before `|`, if any) stays
>   identical. The display label after `|` may be compressed.
> - Numbers, dates, proper names, code/formula fragments stay identical.
> - Frontmatter (between the `---` fences at the top) stays byte-for-byte
>   identical — do not compress or reorder it.
>
> Text to compress:
> ```
> <TEXT>
> ```

---

## link_proposer (opus)

Used by LINK. Single reviewer-model call that scans compact page summaries
for the whole wiki and proposes cross-page `[[wikilinks]]`. Fresh Agent with
clean context; does NOT see prior CURATE or LINK history.

> You are proposing `[[wikilink]]` insertions for a knowledge wiki. You see
> a compact summary of every page (path, title, first paragraph). Your job
> is to surface connections worth wiring up.
>
> Pages:
> ```
> <PAGE_SUMMARIES_JSON>
> ```
>
> Propose up to 150 wikilink insertions. Each proposal:
> - **source**: path of the page that gets the new link (e.g. `concepts/transformer.md`).
> - **target**: path of the linked-to page.
> - **anchor**: a VERBATIM substring of the source's first_paragraph above
>   that should be wrapped with `[[target_stem|anchor]]`. Case-exact, no
>   invented text. Must not already be inside `[[...]]`, `(vault:...)`, or
>   inline code. Prefer multi-word anchors (e.g. "attention mechanism" over
>   "attention") — they're easier to disambiguate and less noisy.
> - **justification**: one line explaining why the connection is substantive.
>
> Prefer connections that CROSS subdirectories: concepts↔entities,
> analyses↔concepts, evidence→concepts, concepts→sources. Avoid trivial
> same-subdirectory keyword links that don't add intellectual reach.
>
> Source stubs (`sources/<paper-or-blog>.md`) are valid link TARGETS — in
> fact they are usually the right first-mention target for a paper, blog,
> or dataset referenced in concept/entity prose (e.g. wrap the string
> "Adam optimizer" in `concepts/adam.md` with
> `[[adam-kingma-2014|Adam optimizer]]`). Source stubs carry high orphan
> debt after bulk ingest — wiring them in is a priority. Do NOT propose
> `sources/` pages as link SOURCES (no outbound links from stubs).
>
> Return exactly one JSON object:
> ```
> {"proposals": [{"source": "<path>", "target": "<path>", "anchor": "<verbatim substring>", "justification": "<one line>"}]}
> ```

---

## link_classifier (opus, fresh context)

Used by LINK. A DIFFERENT Agent from the proposer — must have no memory of
the proposal call. Receives the proposal list and judges each candidate.

> You are reviewing proposed `[[wikilink]]` insertions for a knowledge
> wiki. You did NOT create these proposals — review with fresh eyes.
> Reject superficial keyword matches and spurious connections; accept
> substantive cross-references.
>
> Proposals (each with source/target context):
> ```
> <CLASSIFICATION_INPUT_JSON>
> ```
>
> Each proposal has:
> - `n`: sequence number
> - `source`, `target`: page paths
> - `anchor`: the substring to be wrapped in the source
> - `target_title`, `target_first_paragraph`: what the anchor would link to
> - `justification`: proposer's one-liner
>
> Criteria for each proposal:
> 1. Does the target page actually cover the concept the anchor refers to?
>    Reject if the anchor is a homonym or the target is unrelated.
> 2. Is the link substantive, or just a keyword coincidence? Reject if
>    trivial — an encyclopedic wiki shouldn't link every mention of
>    "learning" to `[[machine-learning]]`.
> 3. Would a reader benefit from following this link? Reject if the target
>    adds nothing beyond what's already in context.
>
> Use `unsure` only when the target's first paragraph is too thin to judge.
>
> Return exactly one JSON object:
> ```
> {"classifications": [{"n": <int>, "verdict": "valid"|"invalid"|"unsure", "reason": "<one line>"}]}
> ```
