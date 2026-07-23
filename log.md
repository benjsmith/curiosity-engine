# Curiosity Engine — journal

Append-only. Newest at bottom. Absolute dates only. Canonical when docs disagree.

---

## Session 1 work log — 2026-07-20

**Baseline + v0.9.2 planning hold.**

### Shipped earlier this day (context)
- **v0.9.0** — Open Knowledge Format export (`okf_export.py`, interop docs).
- **v0.9.1** — OKF discoverability in secondary docs (README Learn more, architecture, multi-project, ce-as-edm). Tag + GitHub release; global skill update via `npx skills@1.5.12 update -g curiosity-engine`.

### Decided (v0.9.2 plan)
From Switch Bay densify/study-sim insights (`switchyard/docs/ce-v0.9.2-curator-notes.md`, `bench/study_sim/README.md`) and maintainer review:

1. **Verbatim facts word floor = 15** when `verbatim: true` (else facts stay at 30).
2. **Table captions always land in `wiki/tables/`**. A **fact** page is optional: when the claim only appears there, or when synthesising the same claim across multiple loci for a better atomic page.
3. **Figure captions** → `wiki/figures/` (text-only `origin: caption-text` allowed without PDF asset).
4. **`two_stage_on_needle` default on** (type-priority default on; production T2 assembly in CE).
5. **Ship process:** work directly on **main**, commit/push/tag/release — **no PR stack**.
6. **HOLD implementation** until additional designs arrive and are reviewed; then update the plan and only then build W1–W5.

### Changed (docs only this session)
- Added `skills/curiosity-engine/docs/v0.9.2-implementation-plan.md` (then locked decisions above; PR DAG renamed to commit batches W1–W5; hold flag).
- Bootstrapped plan-assist: `charter.md`, `work-plan.md`, `log.md` (this file).

### Status
- v0.9.2: **plan hold** — not building yet.
- Heartbeat: in sync with hold; no implementation drift.

### Open
- Incoming additional designs (content TBD) — block implementation until reviewed.

---

## Session 2 work log — 2026-07-21

**Bootstrap densify implemented (v0.9.2).**

### Decided (locked)
1. Table captions → always `wiki/tables/`.
2. Figure captions → `wiki/figures/` primary; fact twin optional (`--with-facts`).
3. Mechanical gate on writes (`score_diff` new-page floors).
4. Resume = **agent memory** + `.curator/log.md` (no mandatory pack-state machine).
5. **Standalone** `bootstrap.py` — not a CURATE wave mode.
6. T2 retrieve deferred pending sim/curation feedback; can adjust later.

### Changed
- `scripts/bootstrap.py` — captions / facts-plan|pack|apply / links-plan|apply / status / prompts.
- `score_diff.py` floors; `naming.py` allowlists `origin`/`verbatim`; `figures.py` caption-text OK without asset.
- SKILL.md BOOTSTRAP; setup.sh allowlist + canary; template schema; tests; CHANGELOG; README; plan-assist handoff.

### Status
- Tests: `tests.test_bootstrap` OK.
- Tag/push v0.9.2: pending end of session or explicit ship.
- T2 still open.

### Gotchas
- Short caption fact twins need framing clause to clear 15-word floor.
- Link density still needs existing catalog nodes (concepts/entities) — cold wikis stay sparse until CURATE creates them.

---

## Session 3 work log — 2026-07-21

**Type-aware retrieve demotion (v0.9.3); T2 rejected by pilot.**

### Evidence (Switch Bay study-sim pilot, n=12, post-bootstrap wiki)
- Type-aware wiki retrieve (demote analyses) ≈ raw RAG (**0.81 vs 0.79**).
- T2 vault-first two-stage **0.56** — vault fills budget; exam tasks not needles; facts/figures lose to wholes + analyses.
- Bootstrap content (570 facts, 110 figures) was the right densify ship when ranking is correct.

### Decided
- **Ship type-aware demotion** in `graph.py retrieve` (default on).
- **Do not promote T2** into CE without new evidence.
- Exam-style queries may fail `query_is_needle`; synth ranking still ranks facts (1) before analyses (4).

### Changed
- `graph.py`: `query_is_needle`, `demote_by_type`, config `retrieve.type_priority`, `--no-type-priority`.
- Tests, SKILL, README, CHANGELOG v0.9.3, plan-assist.

---

## Session 4 work log — 2026-07-21

**Docs hygiene: planning cruft out; design essays → private gists.**

### Deleted from `skills/curiosity-engine/docs/`
- `u1-u5-implementation-plan.md`, `v0.9.2-implementation-plan.md`, `translation-design.md` (pure planning; covered by CHANGELOG + plan-assist).
- `ce-as-edm.md`, `okf-provenance-ext.md` (rewritten CE-agnostic → gists).

### Private gists (secret; flip public later if wanted)
- Empiricist EDM: https://gist.github.com/benjsmith/53abbda45872e0a4eb27bf352be75301
- OKF-P extension: https://gist.github.com/benjsmith/9e4b20a758bfe86c2b2cf59e2720243b
- *(URLs updated 2026-07-22: original secret gists deleted, recreated public — see Session 5. GitHub cannot flip gist visibility in place.)*

### Kept
- `okf-interop.md` (CE-specific) — links to gists in footer; U1–U5 marked shipped where relevant.
- Operational docs: architecture, multi-project, code-knowledge, setup-advanced, viewers, etc.

### Status
- Live skill docs no longer carry implementation-plan files or CE-steelman essays.

## Session 5 work log — 2026-07-22

**Docs watchout fixes (pre-bump); gists flipped public; awesome-llm-wiki PR drafted (hold).**

### Type-aware overclaim corrections (Switch Bay reorientation: v0.9.3 was hasty; regress/correct planned)
- `README.md` feature bullet: "Policy validated…" now scoped to **routing**; demotion described as an all-query reorder, pilot-supported, **under re-evaluation**; "analyses remain the primary synthesis substrate".
- `SKILL.md` retrieve verb: states demotion applies to **every** query (not needle-only), cites pilot honestly (n=12, ≈ raw RAG), points agents at `--no-type-priority` for synthesis queries.
- Code reality (unchanged this session, for the future fix): `graph.py` applies `demote_by_type` to all queries before `--limit`; non-needle analyses rank 4 — below sources/facts/figures/tables/evidence/entities/concepts — so analyses can drop out of the window on synthesis queries. Contradicts intent-taxonomy freeze ("demotion is needle-mode only").
- `charter.md` build-order row updated (tagged; heuristic under re-evaluation). `bootstrap.py` docstring switchyard pointer removed.

### Gists → public (URLs changed; GitHub can't flip visibility in place)
- Empiricist EDM: https://gist.github.com/benjsmith/53abbda45872e0a4eb27bf352be75301
- OKF-P extension: https://gist.github.com/benjsmith/9e4b20a758bfe86c2b2cf59e2720243b
- Content fixes on republish: gists now cross-link each other; "License intent" → "License"; OKF-P nested `okfp` block declared normative (flat `okfp_` prefix informative); "production systems" → "a working open-source implementation". Old secret gists deleted; `okf-interop.md` footer updated.
- Note: gists are owned by the `benjsmith` account (same as repo + gh auth) — a browser logged into another account won't list them.

### awesome-llm-wiki PR (prepared, NOT submitted — hold until v0.9.4 retrieval correction lands)
- Draft: `pr-draft-awesome-llm-wiki.md` (repo root, untracked scratch — do not commit to the skill).
- Positioning: report the epistemics (citation ratchet, identity + abstention gate, shapes, federation, OKF-P), not "another wiki". No retrieval-ranking claims until Phase-2 bench survives.
- Follow-up PR planned separately: Switch Bay (agentic UI over CE + curiosity-merge) once released.

## Session 6 work log — 2026-07-23

**v0.9.4 — reverted the v0.9.3 type-aware retrieve demotion (superseding Session 5's doc-softening, which was an interim step).**

### Decision
The n=12 study-sim exam pilot that justified v0.9.3 showed type-aware ≈ raw RAG (0.81 vs 0.79) — a wash, not a win — and we now judge it too small and too in-distribution to change retrieval ranking at all. A valid test of CE QUERY superiority needs an **out-of-distribution** set (closed-book baseline should score poorly) that is **much larger**; we don't have one yet. Rather than ship a ranking change that may actively harm common synthesis/matched-analysis cases (analyses demoted below every atom, dropped before `--limit`) for an unproven needle-case benefit, retrieval returns to neutral distance-only ranking.

### Code (regressed to pre-v0.9.3 / 14fd40e^)
- `graph.py`: restored parent file directly (was byte-identical to the v0.9.3 post-image, so a clean surgical revert). Gone: `_TYPE_PRIORITY`, `_QUOTEISH`, `query_is_needle`, `page_type_bucket`, `type_rank`, `demote_by_type`, `_retrieve_type_priority_enabled`, the `cmd_retrieve` `type_priority`/`needle` plumbing, the `--no-type-priority` flag, and the `needle`/`type_priority`/`type_bucket`/`type_rank` response fields.
- Removed `tests/test_retrieve_type_priority.py`. Removed `retrieve.type_priority` from `template/config.example.json`. (`template/config.json` never had it.)
- Suite: 54 tests green (was 63; −9 from the deleted type-priority test).

### Docs (type-aware behaviour removed, not softened)
- `README.md` + `SKILL.md` retrieve verb restored to their exact pre-v0.9.3 wording; SKILL config-example line + `retrieve`/`type_priority` config-doc bullet removed; `docs/testing.md` test-list clause removed.
- `CHANGELOG.md`: added the v0.9.4 revert entry (v0.9.3 entry kept as history). Also de-linked two now-broken relative links in the historical v0.5.0 entry (`docs/ce-as-edm.md`, `docs/u1-u5-implementation-plan.md` — deleted by ec6393d) to plain inline references.
- `charter.md`: build-order rows updated; DO-NOT directive now bars retrieval-ranking changes without a valid OOD benchmark.

### ec6393d accuracy check (requested)
Accurate: the five files it claims to delete are gone; okf-interop kept with gist footer; rewrites faithful. Only blemish found — the two broken CHANGELOG links above, now fixed. No other dangling references outside CHANGELOG/log history.

### Also done since Session 5 was written (2026-07-22, same work window)
- Patched the public OKF-P gist: added the "Why an extension" paragraph pre-empting OKF's minimalism objection ("interoperability surface, not the content model"), and qualified the base-v0.2 upstream option as recommended-optional-only.
- Drafted the upstream OKF proposal issue → `okf-p-upstream-issue-draft.md` (repo root, gitignored). Positioned against the seven live overlapping threads (#140/#47 transport trust, #52/#214 publication metadata, #92 SOURCES, #183 typed/confidence links, #53 governance). NOT filed — user reviews first; Google CLA needed only for a later PR, not the issue.
