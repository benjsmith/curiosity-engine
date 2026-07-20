# Curiosity Engine — charter

`log.md` is the canonical journal. **When this file disagrees with log.md, log.md wins.**

Self-improving knowledge wiki skill: vault (append-only sources) + wiki (git-tracked markdown) + curator loops with a citation-preserving ratchet. Installable skill lives at `skills/curiosity-engine/`.

## Hard directives

### DO
- Keep the **citation-preserving ratchet** inviolable (`score_diff`, scrub, hash-guarded scripts). No edit may drop citations or invent vault provenance.
- Land durable skill changes in **this repo** (`skills/curiosity-engine/`), not in ad-hoc install copies or Switch Bay forks.
- Prefer **markdown as source of truth**; derived projections (viewer, OKF export) are read-only.
- Production **knowledge retrieval is CE-owned** (`graph.py retrieve`, vault FTS/hybrid, kuzu). External tools (e.g. Switch Bay) consume CE; they must not permanently reimplement retrieval once CE has the product path.
- **Release from `main`:** ordered commits → push → annotated tag → GitHub release. No PR stack required for this project’s ship process (as of 2026-07-20).
- Use plan-assist docs (`charter.md` / `work-plan.md` / `log.md`) for session continuity; heartbeat at boundaries.

### DO NOT
- Claim study-sim / Phase-2 denser product wins in CE marketing or intro materials until Switch Bay’s study-sim gate passes.
- Let the curator edit hash-guarded skill scripts at runtime.
- Fetch the open web as part of CURATE; acquisition is human/vault ingest only.
- Build v0.9.2 caption-harvest / T2 retrieve **until the additional designs under review land** and the implementation plan is re-approved (hold 2026-07-20).

## Architecture invariants

- Three-object model: **vault** · **wiki** · **curator state** (`.curator/`).
- Eleven wiki page types; type-specific floors in `score_diff`.
- Entity-resolution gate on synthesis retrieve (v0.8.3+).
- OKF export (v0.9.0+) is a projection, not a second wiki.

## Build-order status (high level)

| Track | Status |
|-------|--------|
| v0.9.0 OKF export | ✅ shipped |
| v0.9.1 OKF docs discoverability | ✅ shipped |
| v0.9.2 caption densify + type-aware retrieve | ⏳ plan hold — wait for more designs |
| Study-sim product gate | ⏳ Switch Bay (not CE package) |

## Gotchas

- Global skill install: `npx skills@1.5.12 update -g curiosity-engine` (pin avoids historical CLI root-layout bugs for old layouts; subdirectory layout is fine).
- Switch Bay must not treat `/Users/benj/Documents/bin/curiosity-engine` (or any external path) as the writable skill source — this git repo is authoritative.
