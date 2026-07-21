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
