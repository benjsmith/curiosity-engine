# Curiosity Engine — active work plan

Rolling plan for in-flight + upcoming work. Keep under ~250 lines.
When a phase lands, summarise into `log.md` and prune here.

- **`charter.md`** — stable architectural directives.
- **`log.md`** — append-only journal (canonical when docs disagree).
- **`work-plan.md`** (this file) — short-horizon plan + handoff.

---

## ⏸ SESSION HANDOFF — 2026-07-23 (v0.9.4 regression: type-aware demotion reverted)

**Done + verified (see log Session 6):**
- **v0.9.4 reverts v0.9.3 type-aware demotion.** `graph.py retrieve` back to distance-then-overlap ranking; all type-priority code/flags/config/response-fields/tests removed; type-aware behaviour scrubbed from README, SKILL, config example, testing.md; CHANGELOG v0.9.4 entry added.
- Rationale: n=12 study-sim pilot was a wash (0.81 vs 0.79) and too in-distribution to justify a ranking change; it may harm common synthesis cases. Ranking stays neutral until a valid OOD + larger benchmark exists.
- ec6393d accuracy check done (clean; fixed 2 broken historical CHANGELOG links).
- Earlier (2026-07-22): gists public + cross-linked; OKF-P gist rationale patch; `okf-p-upstream-issue-draft.md` drafted (gitignored, unfiled).

**Next:**
1. Commit + push + **tag v0.9.4** on main; GitHub release. Global skill update (`npx skills@1.5.12 update -g curiosity-engine`).
2. **Benchmark before any retrieval-ranking change.** Build/borrow an out-of-distribution QUERY set (closed-book baseline should score poorly) that is substantially larger than n=12. This is the gate for re-attempting type-aware demotion, vault-first two-stage, or any ranking change — in Switch Bay, not the CE package.
3. Submit `pr-draft-awesome-llm-wiki.md` (was held for v0.9.4 — now unblocked; re-verify list HEAD + insertion points first).
4. Review + file `okf-p-upstream-issue-draft.md` upstream (user's call; CLA only for a later PR).
5. Follow-up PR: Switch Bay entry once released (agentic UI over CE + curiosity-merge).

---

## Current focus

| Item | State |
|------|--------|
| v0.9.4 type-aware revert | ✅ done (tag pending) |
| Retrieval-ranking changes (type-aware / two-stage / etc.) | ⛔ blocked on a valid OOD benchmark |
| Valid CE-QUERY benchmark (OOD, large) | ⏳ Switch Bay — the gate for #2 above |
| plan-assist | live |

## Explicit non-goals (this horizon)

- Retrieval-ranking changes without a valid benchmark (see charter DO NOT)
- Embedding bootstrap LLM in Python (stays agent-orchestrated)
- Polluting CURATE wave modes with bootstrap packs
- Study-sim claims in intro decks
