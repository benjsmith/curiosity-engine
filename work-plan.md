# Curiosity Engine — active work plan

Rolling plan for in-flight + upcoming work. Keep under ~250 lines.
When a phase lands, summarise into `log.md` and prune here.

- **`charter.md`** — stable architectural directives.
- **`log.md`** — append-only journal (canonical when docs disagree).
- **`work-plan.md`** (this file) — short-horizon plan + handoff.

---

## ⏸ SESSION HANDOFF — 2026-07-20

**Done + verified:**
- v0.9.0 OKF export + v0.9.1 docs discoverability shipped (tag `v0.9.1`, global skill updated).
- v0.9.2 implementation plan drafted from Switch Bay curator notes + study-sim README: `skills/curiosity-engine/docs/v0.9.2-implementation-plan.md`.
- Decisions locked into the plan (see log): verbatim fact floor **15**; **table captions → always `tables/`**; optional **facts** for sole-locus or multi-source synthesis; **`two_stage_on_needle` default on**; **main-branch ship** (no PR stack); **HOLD build** until additional designs arrive.
- plan-assist bootstrap: `charter.md` + this file + `log.md`.

**Left to do (in order):**
1. Receive / review **additional designs** (incoming — do not implement yet).
2. Merge those designs into `docs/v0.9.2-implementation-plan.md` (and this work-plan).
3. Implement W1→W5 on **main** (schema → caption-candidates → caption-harvest → retrieve T2 → docs/tag).
4. Tag **v0.9.2**, push, GitHub release; global skill update.
5. Hand densify re-run + study-sim gate back to Switch Bay.

**How to run / diagnose:**
- Skill tree: `skills/curiosity-engine/`
- Tests: `python3 -m unittest discover tests` from repo root
- Plan: `skills/curiosity-engine/docs/v0.9.2-implementation-plan.md`

**Environment notes:**
- Release = commits on `main` only; no Graphite/PR requirement for CE ship.
- Do not claim study-sim wins in CE until SB gate passes.

---

## Current focus

| Item | State |
|------|--------|
| v0.9.2 caption densify + type-aware retrieve | **HOLD** — plan ready; wait for more designs |
| plan-assist docs | bootstrapped 2026-07-20 |

## Work packages (after hold lifts)

See implementation plan W1–W5. Short labels:

1. **W1** — schema: caption-text figures, table caption pages, `verbatim` floor 15  
2. **W2** — `sweep.py caption-candidates` + mark  
3. **W3** — CURATE `caption-harvest` + prompts  
4. **W4** — `graph.py retrieve` type-priority + two-stage on needle (defaults on)  
5. **W5** — docs + CHANGELOG + tag v0.9.2 on main  

## Explicit non-goals (this horizon)

- Implementing W1–W5 before additional designs review  
- PR-based release process  
- Study-sim runner inside this repo  
