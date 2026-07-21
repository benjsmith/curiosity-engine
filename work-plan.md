# Curiosity Engine — active work plan

Rolling plan for in-flight + upcoming work. Keep under ~250 lines.
When a phase lands, summarise into `log.md` and prune here.

- **`charter.md`** — stable architectural directives.
- **`log.md`** — append-only journal (canonical when docs disagree).
- **`work-plan.md`** (this file) — short-horizon plan + handoff.

---

## ⏸ SESSION HANDOFF — 2026-07-21 (type-aware demotion)

**Done + verified:**
- v0.9.2 bootstrap densify shipped (tag).
- Study-sim pilot: type-aware ≈ RAG; **T2 rejected** (0.56).
- **v0.9.3 type-aware demotion** in `graph.py retrieve` (default on; `--no-type-priority` opt-out).
- Tests: `tests.test_retrieve_type_priority`.

**Also done (docs hygiene):**
- Removed planning-only docs; CE-agnostic essays → private gists (see log Session 4).

**Left to do:**
1. Commit + push docs hygiene (no version bump required unless desired).
2. Global skill update when convenient.
3. SB study-sim re-run against CE retrieve with type_priority (not T2).
4. Do **not** implement T2 unless new evidence.
5. Optionally flip gists public after personal edit pass.

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
