# Curiosity Engine — active work plan

Rolling plan for in-flight + upcoming work. Keep under ~250 lines.
When a phase lands, summarise into `log.md` and prune here.

- **`charter.md`** — stable architectural directives.
- **`log.md`** — append-only journal (canonical when docs disagree).
- **`work-plan.md`** (this file) — short-horizon plan + handoff.

---

## ⏸ SESSION HANDOFF — 2026-07-21

**Done + verified:**
- Locked bootstrap design (table→tables/, figure primary + fact optional, mechanical gate, agent-memory resume, standalone script not CURATE mode).
- Implemented **v0.9.2 bootstrap densify**:
  - `skills/curiosity-engine/scripts/bootstrap.py`
  - floors / FM keys / figures caption-text
  - SKILL.md BOOTSTRAP, setup allowlist canary, schema note, tests
  - CHANGELOG + README feature line
- Unit tests: `python3 -m unittest tests.test_bootstrap` green.

**Left to do (in order):**
1. Commit + push + tag **v0.9.2** on main when this session ships (or next).
2. Global skill update after tag.
3. Optional: type-aware / two-stage retrieve (T2) after study-sim feedback.
4. Optional: densify re-run / study-sim gate in Switch Bay using CE bootstrap API.

**How to run bootstrap:**
```bash
uv run python3 skills/curiosity-engine/scripts/bootstrap.py captions wiki --apply
uv run python3 skills/curiosity-engine/scripts/bootstrap.py facts-plan wiki
uv run python3 skills/curiosity-engine/scripts/bootstrap.py facts-pack wiki --pack-index 0
# agent LLM → JSON →
uv run python3 skills/curiosity-engine/scripts/bootstrap.py facts-apply wiki --json-file /tmp/facts.json
uv run python3 skills/curiosity-engine/scripts/bootstrap.py links-plan wiki
# agent LLM → JSON →
uv run python3 skills/curiosity-engine/scripts/bootstrap.py links-apply wiki --json-file /tmp/links.json
uv run python3 skills/curiosity-engine/scripts/graph.py rebuild wiki
```

**Environment notes:**
- LLM not inside bootstrap.py — agent makes multi-provider calls; log pack_index in `.curator/log.md`.
- T2 retrieve still not shipped; densify content alone may not win study-sim until ranking lands.

---

## Current focus

| Item | State |
|------|--------|
| v0.9.2 bootstrap densify | ✅ implemented (tag pending) |
| T2 type-aware retrieve | ⏳ wait study-sim / curation feedback |
| plan-assist | live |

## Explicit non-goals (this horizon)

- Embedding bootstrap LLM in Python (stays agent-orchestrated)
- Polluting CURATE wave modes with bootstrap packs
- Study-sim claims in intro decks
