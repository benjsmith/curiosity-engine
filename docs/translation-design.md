# Wiki translation — design doc (deferred)

**Status:** design only. Not implemented. Captured 2026-05-15 alongside the v0.3.0 RESTYLE ship so the parallel surface is recorded while it's fresh; revisit if a user actually needs a non-English wiki.

This doc captures the design space for a one-shot `/translate <target-language>` operation analogous to RESTYLE (see [`../SKILL.md` § RESTYLE](../SKILL.md), and the v0.3.0 entry in [`../CHANGELOG.md`](../CHANGELOG.md)). The mechanical skeleton transfers cleanly; five issues that don't show up in restyle do show up here, and the CE doc-class ontology question has a load-bearing answer that future implementers must respect.

## When this might matter

A team or individual whose primary working language isn't English wants their wiki — and the agent's reads of it — in that language. Today CE assumes English: schema.md, prompts.md, SKILL.md, naming.py's bracket prefixes, the viewer's sidebar labels. Translation is feasible because frontier models are competent multilingual readers and writers, and CE's worker/reviewer/ratchet machinery is language-agnostic at the architectural level. What would need explicit design is the *boundary* between "translate" and "leave English."

## The doc-class ontology decision (the load-bearing one)

**Translate body prose only. Keep the entire CE ontology layer in English.**

Specifically:
- **Type names** (`source`, `entity`, `concept`, `analysis`, `evidence`, `fact`, `summary-table`, `extracted-table`, `figure`) — English.
- **Directory names** (`wiki/sources/`, `wiki/entities/`, ...) — English.
- **Bracket prefixes in titles** (`[src]`, `[con]`, `[ana]`, `[evi]`, `[fct]`, `[tbl]`, `[tab]`, `[fig]`, `[ent]`, `[note]`, `[todo]`) — English. Source of truth is `naming.py`.
- **Frontmatter keys** (`title`, `type`, `created`, `updated`, `sources`, `project`, `style`, ...) — English.
- **Citation DSL** (`(vault:...)`, `(code:project:path:lines)`, `(table:X#id=Y)`, `(todo:...)`, `(note:...)`) — English.
- **Wikilink targets** (the stem before `|`) — English. These are filenames.
- **Filenames** — English. Renaming wiki files across translation would require rewriting every wikilink across the wiki, disrupting git history, and changing the graph node IDs.

**What does get translated:**
- Title body — the text *after* the bracket prefix. `[ent] Middleware` → `[ent] Capa intermedia`.
- All prose in the body.
- Section headings.
- List items, table cells (with the structural-preservation rules below).
- Wikilink **display labels** — every `[[stem]]` in source becomes `[[stem|target-language-display]]` in the translation. This is *mandatory*, not optional; without it, the reader sees English stems peppered through otherwise-foreign prose.

**Why the ontology stays English:** every skill script that pattern-matches on these strings — `naming.py`, `graph.py`, `sweep.py`, `score_diff.py`, the viewer's `TYPE_CANONICAL` map, `lint_scores.py` — is keyed on the English values. Localizing them would break the data contracts that every operation relies on. Readers see rendered prose, never `[ent]` codes, so there's no human-facing value to localize. This is the same separation `style:` already uses: voice changes in the body; the ontology stays put.

## Surface (the easy part)

Direct parallel to RESTYLE. Borrow the entire skeleton:

| Component | RESTYLE today | TRANSLATE future |
|---|---|---|
| Script | `scripts/restyle.py` | `scripts/translate.py` |
| Frontmatter marker | `style:` (e.g. `prose-v1`) | `lang:` (e.g. `es-v1`, `de-v1`) |
| Subcommands | `plan` / `mark` / `progress` / `score-check` | identical shape |
| Worker prompt | `restyle_worker` | `translate_worker` |
| Reviewer prompt | `restyle_reviewer` (1-in-5 spot-audit) | `translate_reviewer` (same cadence) |
| Slash command | `/restyle <target>` | `/translate <target-language>` |
| Idempotency | filter pages whose `style:` == target | filter pages whose `lang:` == target |
| Per-page commits | yes | yes |
| Resumable | yes (re-run skips marked pages) | yes |
| Ratchet override | `--bloat-mult 2.0` for prose | per-language map (see below) |
| Hash-guarded | yes | yes |
| Allowlist update | both workspace + code-repo settings | identical |

Target syntax: ISO 639-1 lowercase + version suffix — `es-v1`, `fr-v1`, `de-v1`, `ja-v1`, `zh-v1`, `ru-v1`, `en-v1` (for translating back to English). The version suffix lets a future schema change re-process every page without a forced flag.

`translate.py plan wiki --target <code> [--types ...] [--limit N]` enumerates candidates, filters by `lang:` marker, prints rough cost estimate (input tokens × Sonnet rate × expansion factor for target language, plus 1-in-5 reviewer overhead at ~5× rate). Mirrors `restyle.py plan` almost line for line.

## The five non-obvious issues

### 1. CURATE feedback loop — the architectural decision

Once the wiki is in Spanish, what does the next `curate` wave do? CURATE workers read `.curator/prompts.md` (English) and produce English prose. Without intervention, every CURATE wave re-introduces English into a translated wiki — a fighting state worse than mixed-language quiescence.

Two paths:

**Path A (one-shot translation, CURATE stays English).** Translation is a render step the user repeats periodically. CURATE writes English; user re-runs `/translate spanish` after each substantive curate run. Simple to ship. Painful in practice — wikis are meant to grow continuously, not be re-translated weekly.

**Path B (workspace language config).** Add a `workspace_language: <code>` key to `.curator/config.json`. CURATE worker prompts include "write final content in {language}" filled at dispatch time. `prompts.md` itself stays English (instructions to the agent, not output) but the worker emits target-language prose. Translation becomes a one-time migration; subsequent CURATE runs maintain consistency.

**Recommendation:** ship Path A first to validate demand. The pain is bounded — a few English edits after curate, then a quick re-translate. If users actually adopt it, layer Path B on top (no schema change to the wiki, just a config flag and worker-prompt templating). Path B is the right end-state but premature without a first user.

### 2. Wikilink display labels become mandatory

`[[middleware]]` in otherwise-Spanish prose reads jarringly and breaks the translation's coherence. The worker has to use the `|display` form everywhere: `[[middleware|Capa intermedia]]`.

CE already supports this — `score_diff.py`, `graph.py`, and the viewer all honor the `target|display` split — but the `translate_worker` prompt has to be insistent:

> Every `[[stem]]` in the source becomes `[[stem|display]]` in the translation. The stem stays exactly as it appears in the source (it's a file path). The display is the target-language rendering. Never translate the stem.

The `translate_reviewer` checklist gains a corresponding line — target preserved byte-for-byte, display reads naturally in target language.

### 3. Per-language bloat caps

RESTYLE uses a single `--bloat-mult 2.0` cap for hydration. Translation expansion is per-language:

| Direction | Typical expansion | Recommended bloat cap |
|---|---|---|
| EN→DE | ~1.3× (German is wordier) | 1.6 |
| EN→ES, EN→FR | ~1.15-1.25× | 1.5 |
| EN→RU | ~1.05-1.1× | 1.4 |
| EN→PT, EN→IT | ~1.15-1.2× | 1.5 |
| EN→JA | ~1.0× | 1.4 |
| EN→ZH | ~0.4-0.5× (Chinese is denser) | 1.2 (compression direction; cap mostly irrelevant) |

Solution: a small `LANG_BLOAT_CAP = {...}` map in `translate.py`, with `1.5` as the conservative default for unmapped languages. `score_diff.py --bloat-mult <n>` (the v0.3.0 flag) takes the value; no further `score_diff` changes needed.

**Floor edge case for compression direction.** Chinese (and Japanese) translations of short pages may fall *below* `score_diff.py`'s new-page floor (≥100 words default for analyses, ≥50 for evidence, ≥30 for facts). The current floor is word-count-based and English-tuned. Two fixes:

- Loosen floors in `_floors_for()` when `lang:` is set to a CJK target (per-language floor map).
- Or skip the new-page-floor check entirely for translate waves (the page already existed; we're not creating it).

The second is cleaner — translate operates on existing pages, never creates new ones, so new-page floors don't conceptually apply. Add a `--skip-new-page-floor` flag to `score_diff.py` (or have `translate.py score-check` not pass `--new-page`). Probably the simpler choice.

### 4. Untranslatable spans

The `translate_worker` prompt must preserve byte-for-byte:

- **Frontmatter** — entire YAML block. The `lang:` and `updated:` keys are set by `translate.py mark` AFTER the rewrite.
- **Code fences and inline code** — `\`...\``, `\`\`\`...\`\`\``. Worker may translate adjacent prose explaining the code but never the code body.
- **`(vault:...)` citations** — paths, never translated.
- **`(code:...)` citations** (code-repo mode) — same, paths only.
- **Wikilink stems** (before `|`) — exactly as in source.
- **Numbers, dates, units** — *do not convert*. "1500 USD" stays "1500 USD"; "2024-04-15" stays as-is; "5 mg/L" stays "5 mg/L". A German reader expects `1.500,50` but the source's `1,500.50` is data, not prose — converting is the same class of error as unit-converting in `scientific_table_extractor`.
- **Decimal separators** in source — preserved.
- **Proper names** — generally not translated. "Postgres" stays "Postgres", not "Posgrés". Use translator judgment for transliteration: place names commonly localized (Munich ↔ München) follow the target language's convention; technical product names stay as-is.
- **Block quotes from source material** — quoted text from a `(vault:...)` source stays in the original language. The worker may add `> [translator's note: …]` immediately after a quote for context, but never rewrites the quoted text. Translating quotes loses fidelity that the citation marker is supposed to preserve.
- **Bracketed title prefix** (`[src]`, `[con]`, ...) — English, always.

Most of these mirror the restyle worker's rules. The numbers/units rule is the genuinely new one and is the most likely thing for a worker to violate without explicit instruction.

### 5. Vault stays in the original language — by design, but worth naming

CE's vault is append-only provenance. Translation operates on `wiki/` only. The vault stays whatever language(s) it was ingested in — typically English for English research, mixed for multi-language ingest.

This means a translated Spanish wiki page can cite an English vault source. That's fine — citation is provenance, not a quote requirement; the wiki page's commentary on the source is Spanish, but the source itself remains its original-language text.

The UX wart: a query like "what do I know about X?" in the translated workspace surfaces a Spanish wiki answer with `(vault:english-paper.extracted.md)` citation markers pointing at English source files. Acceptable. Translating the vault would defeat provenance — it'd make the wiki's citation gate point at re-stated content rather than the document the user actually has on disk.

If a future user wants both — translated wiki + accessibility of source text in their language — the right answer is probably a separate optional `.extracted.<lang>.md` companion file generated on demand. Out of scope here; flag it as a v3.2 idea.

## Other issues, smaller but worth noting

- **Viewer chrome stays English.** `template/wiki-view/static/sidebar.js` has `TYPE_LABEL` ("Analyses", "Concepts", ...). For a translated wiki, the user reads English navigation chrome wrapping foreign content. Localize the viewer when a second-target-language user actually requests it — likely a small `template/wiki-view/static/i18n.js` with a `lang:` key in `data.json` (analogous to `palette:`).
- **Bilingual reviewer required.** The `translate_reviewer` model must be fluent in both source and target language. Frontier Claude / GPT / Gemini are; smaller local Ollama models often aren't. Document in the operation's `### TRANSLATE` SKILL.md section: reviewer model = frontier required.
- **Mixed-language partial-wave state.** During the wave, half the wiki is translated, half is in the source language. Queries and CURATE runs in that window see mixed content. Add a wave-end guard in the orchestration: refuse to declare the wave done if `translate.py plan` shows remaining candidates. (Same shape as restyle's resumability check; just enforces a "fully done" stopping condition.)
- **Source-language detection.** First-time translation: worker detects source language. Frontier models do this well. After the first translation, the `lang:` marker is authoritative for re-runs.
- **Caveman interaction.** A workspace with `caveman.enabled = true` reads pages through caveman's token-strip at agent-read time. Translation requires the worker to see the *actual* page bytes (not the stripped version) to produce a faithful translation. Operation must require `caveman.enabled = false` at workflow start, same way restyle does for prose target. The warning + offer-to-flip in `### RESTYLE` is the template.
- **Re-translation idempotency.** Re-running `/translate spanish` against an already-Spanish wiki is a no-op (every page's `lang:` matches). Re-running `/translate french` against a Spanish wiki translates Spanish → French (source-language detection picks up Spanish from the `lang:` marker). This is fine but worth a test case: the worker must read the page as-is and translate to the new target, not assume English source.
- **Backtranslation `/translate english`** is a first-class case, not a hack. A workspace whose owner gave up on Spanish and wants to flip back uses this. The reviewer cost matters here: backtranslation review needs a model that catches re-Englishing artifacts ("the request was performed by the system" — recognisable bad-translationese back-to-EN prose).
- **Multi-target ambiguity.** What if a user wants different *projects* in different languages (e.g., `auth-service` in Spanish for the Spanish team, `data-platform` in English)? Out of scope for v1. The `lang:` marker is per-page, so the data model supports it; the orchestration just isn't designed for mixed-target waves. Flag for v3.1+.

## Recommended scope when this gets implemented

**v1 (Path A — one-shot translation, CURATE stays English):**

- `scripts/translate.py` with `plan` / `mark` / `progress` / `score-check`
- `lang:` frontmatter key (additive — coexists with `style:`)
- `LANG_BLOAT_CAP` map in `translate.py` (default 1.5; per-language overrides for DE, ZH, JA, RU)
- `translate_worker` + `translate_reviewer` prompts in `prompts.md`
- `/translate <target>` slash command
- `### TRANSLATE` section in SKILL.md
- `--skip-new-page-floor` flag on `score_diff.py` (additive — restyle could opt into it too)
- Hash-guard + setup.sh allowlist updates + canary
- Cross-references from `### RESTYLE` ("translate is the same shape but with the issues in docs/translation-design.md")
- Caveman-conflict warning identical to restyle's

**v1.1 (if compression-direction floor issues bite):** per-language floor map in `score_diff.py` for CJK targets.

**v3.1 (Path B — `workspace_language` config):** add `workspace_language` to `.curator/config.json`; CURATE worker prompts include "write final content in {language}"; restyle worker prompts likewise. Translation becomes the one-time migration; CURATE maintains the workspace's language afterward.

**v3.2 (if anyone asks):** optional `.extracted.<lang>.md` vault companion files for translated source access without breaking provenance.

Estimated v1 effort: ~250-350 new lines split across `translate.py` (largest, ~150), `prompts.md` (2 blocks, ~70 lines combined), `score_diff.py` (~15 lines for the skip-new-page-floor flag), `SKILL.md` (~30 line section), and the smaller wiring updates. Same shape as v0.3.0 RESTYLE; should land in roughly the same effort budget.

## Why this stays deferred for now

No user has asked. CE today is a single-user / small-team artifact, and English happens to be where every existing user works. Building this preemptively burns design budget on a feature with no usage signal. The cheapest path is: write the design while it's fresh (this doc), wait for a real request, ship v1 quickly when it comes. The doc means the eventual implementer doesn't re-derive the doc-class-ontology decision or rediscover the per-language bloat-cap problem.

## Cross-references

- [`../SKILL.md` § RESTYLE](../SKILL.md) — the operation this design is a sibling of
- [`../CHANGELOG.md`](../CHANGELOG.md) — v0.3.0 ships RESTYLE; v0.2.0 introduces code-repo mode (similar deferred-features pattern in [`code-knowledge.md`](code-knowledge.md))
- [`../scripts/restyle.py`](../scripts/restyle.py) — direct template for `translate.py`'s shape
- [`../template/prompts.md`](../template/prompts.md) — `restyle_worker` / `restyle_reviewer` are the direct templates for `translate_worker` / `translate_reviewer`
- [`../scripts/naming.py`](../scripts/naming.py) — source of truth for the English bracket prefixes that stay English under translation
