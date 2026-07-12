# U1–U5 implementation plan (research-wiki scope)

This is the build plan for the five upgrades sketched in
[`ce-as-edm.md`](ce-as-edm.md), scoped to **CE as a self-improving
research/knowledge wiki** — *not* as an EDM platform. Every upgrade here earns
its place by the standalone value the design note already argued for (U1/U2/U4
unambiguous wins, U3 a narrower extraction-quality gate, U5 neutral infra).

**Explicitly out of scope** (deferred until/unless a real EDM or federation
requirement lands):

- maplib / RDF / OTTR / SPARQL federation — the entire "Where maplib earns its
  place" section.
- The operational-data on-ramp: schema graduation to an RDB (`schema_extract.py`),
  the `(op:<table>:<iri>@<event-id>)` citation root, event-log re-capture
  (Stages B–D), and form-validation generation.
- U3's headline "one declaration → DB constraint + form validation" payoff
  (needs graduation). We keep only U3's *curate-time* enforcement.

Everything below is **additive and optional** and preserves the three-object
model (vault / wiki / curator). Nothing changes the citation-ratchet guarantee
on `(vault:...)` source-of-truth citations.

---

## Reality check: what's already built

Grounding the plan in the current tree (paths relative to `scripts/` unless noted):

| Capability | Today | Gap to close |
|---|---|---|
| Identity minting | `identifier_cache.py` + `.curator/identifiers.db` (tables `chemicals`, `genes`); network layer `identifier_resolve.py`, gated by `config.json` | Domain-agnostic `entities` table + IRI scheme; `iri:`/`same_as:` frontmatter; resolver registry |
| Class-table store | `tables.py` over **SQLite** `.curator/tables.db`; `cmd_query` is a parameterised `SELECT` already | No first-class user/agent query verb; no Cypher surface; no aggregate planning input |
| Graph | `graph.py` over kuzu `.curator/graph.kuzu`; Cypher queries `shared-sources`/`path`/`neighbors`/`bridge-candidates` | Same — curator-internal only |
| Ratchet | `score_diff.py`: citation-count floor, bloat ceiling, FTS-match for new `(vault:)`, **row-existence for new `(table:)`** | No declared per-column shape constraints |
| `table:` grammar | `tables.py._normalize_columns`: `name/type/pk/nullable/values/default/_alias` | No `units`, no `constraint`, no `source_required` |
| Score cache | `lint_scores.py` → `.curator/.score_cache.json`, keyed `text_hash + inbound`, global key `titles_hash + vault_rowcount` | Not generalised to derived facts (aggregates / closures) |
| Cluster-scoping | `epoch_summary.py.wave_scope` (2-hop neighbourhood, threshold 500) | Not yet repurposed as a shard boundary |
| Multi-project | shipped (`projects.py`, `planner.py`, `activity_log.py`) | Slug-keyed, not IRI-keyed; cross-wiki merge lives in separate `curiosity-merge` skill |

## Sequencing

```
  U1 (identity) ──┬──▶ U4 (federation by IRI)
                  │
  U2 (query)  ────┴──▶ curator plans over aggregates
                  │
  U3 (shapes) ────┘   (independent; benefits from U2's emitter)

  U5 (derived-fact cache) — independent infra; lands whenever convenient
```

- **Phase 1 — Foundation:** U1, U2. These unlock the rest and pay off immediately.
- **Phase 2 — Quality gate:** U3. Small, self-contained, slots into the ratchet.
- **Phase 3 — Scale:** U4 (needs U1's IRIs), U5 (independent, do anytime).

U5 has no dependencies and can be pulled forward if the curator is burning
tokens re-deriving aggregates before U2 lands; otherwise it's most useful *after*
U2 gives it derived facts worth caching.

---

## U1 — Domain-agnostic identity layer

**Goal.** Generalise the chemical/gene IRI minter into an entity-class-agnostic
**entity-resolution + IRI service**, so every entity page can carry a stable
minted `iri:` and optional external `same_as:` links — resolved LLM-assisted at
write time, cached deterministically.

**Why (research value, no EDM needed).** "Is this the same protein / author /
construct across 40 papers?" is a core research-wiki problem CE solves today only
for chemicals/genes. A workspace-stable internal id with optional external
`same_as` strengthens disambiguation and turns `curiosity-merge` reconciliation
from slug-keyed to IRI-keyed.

### Changes

1. **`.curator/identifiers.db` — new `entities` table** (additive; existing
   `chemicals`/`genes` tables untouched):
   ```sql
   CREATE TABLE IF NOT EXISTS entities (
       iri          TEXT PRIMARY KEY,   -- minted, stable
       entity_class TEXT NOT NULL,      -- chemical|gene|protein|person|org|concept|...
       page_path    TEXT,               -- wiki/entities/...md (nullable until placed)
       same_as_json TEXT,               -- {"pubchem":"CID2244","wikidata":"Q18253",...}
       status       TEXT NOT NULL,      -- ok|unresolved|local-only
       resolved_at  TEXT NOT NULL
   );
   ```

2. **IRI scheme — workspace-stable, URI-shaped, no external authority required:**
   - `ce:<entity_class>:<workspace>:<slug-or-uuid>` for local concepts with no
     external authority (the common case).
   - When an external canonical id resolves, store it in `same_as_json`; the IRI
     stays the workspace id (identity must not break if PubChem renames a CID).
   - Deterministic minting: slug derived from normalised title; UUID fallback only
     on collision. No `Date.now()`/random in the keyed path (reproducibility).

3. **Resolver registry** (`identifier_resolve.py`): generalise `_resolve_chemical`
   / `_resolve_gene` into a registry keyed by `entity_class`. Each resolver returns
   `(status, same_as_dict)`. Chemicals/genes become two registered resolvers;
   `concept`/`person`/`org` default to local-only (no network) unless an authority
   is configured. Keep the existing **cache-first, network-gated, two-step
   (`review` → `run --yes`)** security model unchanged.

4. **Frontmatter grammar** (`naming.py`): add `iri`, `same_as`, `entity_class` to
   `ALLOWED_FM_KEYS`. `same_as` parses as a bracket-list of `authority:id` pairs
   (reuse the existing `normalise_columns` bracket-list parser). Document in
   `template/schema.md`.

5. **Cache layer** (`identifier_cache.py`): add `lookup_cached_entity(iri)` /
   `mint_entity(entity_class, title, same_as=...)` / `write_entity(...)` alongside
   the existing chemical/gene functions; add `entities` to `cache-stats`. CLI:
   `mint-entity`, `lookup-entity`.

6. **`curiosity-merge` (separate repo): IRI-keyed reconciliation** — when two
   pages carry the same `iri:` (or `same_as` pointing at the same external id),
   they're merge candidates regardless of slug. Tracked here as a downstream task;
   the merge skill consumes the `entities` table.

**Files:** `identifier_cache.py` (new entity fns), `identifier_resolve.py`
(resolver registry), `naming.py` (`ALLOWED_FM_KEYS`), `template/schema.md` (grammar),
`.curator/identifiers.db` (new table). Downstream: `curiosity-merge` repo.

**Risks / guards.**
- *Identity drift* — minted `same_as` links are LLM-proposed and need the same
  fresh-context-reviewer + spot-auditor discipline as the rest of curation. Add an
  identity-resolution review pass (mirror the numeric-review pattern in `sweep.py`).
- *IRI churn* — never key identity on the external id; key on the workspace IRI so
  re-resolution can't orphan citations.

**Test plan.** Round-trip mint→lookup determinism; collision → UUID fallback;
chemical/gene resolvers still pass existing tests; offline mode (`CURIOSITY_ENGINE_OFFLINE=1`)
yields `local-only` not an error; frontmatter round-trips `iri`/`same_as`.

---

## U2 — Deterministic query substrate alongside the LLM

**Goal.** Promote `tables.db` (SQLite) and `graph.kuzu` (Cypher) from
curator-internal scratch into a **first-class query surface**: structured
questions hit the engine (cheap, exact); only *synthesis* questions spend tokens.
The curator plans over real aggregates instead of re-reading prose.

**Why.** This is the write-time/read-time inversion, and CE is most of the way
there — `cmd_query` is already a guarded parameterised `SELECT`; the kuzu Cypher
queries already exist. The work is *surfacing and routing*, not building engines.

> **Scope correction vs the design note:** `tables.db` is SQLite, not DuckDB.
> SQLite handles CE's scale (hundreds–thousands of pages, modest class tables)
> fine, but do not promise "40M-row aggregation." If a corpus ever needs that,
> swapping the read path to DuckDB-over-the-same-file is a later, isolated change.

### Changes

1. **New `query_router.py`** — single entrypoint that classifies intent and routes:
   - **structured** (aggregations/filters/joins over class tables) → SQL over
     `tables.db`, reusing `tables.py`'s injection-guarded query path. Read-only.
   - **structural** (paths, neighbours, shared-sources, bridges) → Cypher over
     `graph.kuzu`, reusing `graph.py`'s existing queries. Read-only transaction.
   - **synthesis** (interpretation, "what do I know about X") → fall through to the
     existing LLM + `vault_search.py` path (unchanged).
   - Classification is a cheap LLM step *or* a deterministic prefix (`!sql` / `!gql`)
     so power users skip the classifier. (The `!sql`/`!gql` rail-verb framing from
     the switchyard note maps here, but is optional.)

2. **Schema introspection for agents/users** — expose "what can I query": list
   class tables + columns (from `_schema_meta`) and the kuzu node/edge catalogue,
   so the router (and the user) know the surface without re-reading entity pages.

3. **Curator integration** — `epoch_summary.py` already reads `tables.db._audit_log`
   and the graph for plan signals; add an aggregate-query hook so the planner can
   ask "count rows by class / unsourced row density" directly instead of via prose
   heuristics. Keep it a *read* into the plan JSON; no behavioural coupling.

4. **SKILL.md** — document the `query` surface under the existing QUERY section:
   structured/structural questions route to the engine; synthesis stays LLM.

**Files:** new `query_router.py`; small read-only helpers in `tables.py` /
`graph.py` (introspection, read-only Cypher wrapper); `epoch_summary.py` (aggregate
hook); `SKILL.md` (QUERY section).

**Risks / guards.**
- *Read-only enforcement* — the router must never issue DDL/DML. SQL path: allowlist
  `SELECT`/`WITH` only; reuse existing param-binding. Cypher path: wrap in a
  read-only kuzu transaction; reject `CREATE`/`SET`/`DELETE`.
- *Misclassification* — a synthesis question routed to SQL returns nothing useful;
  default to the LLM path on low classifier confidence (fail safe, not silent empty).

**Test plan.** Golden queries: an aggregate (`GROUP BY` over a class table), a
graph path, a synthesis question — assert each routes to the right backend; assert
DDL/DML attempts are rejected; assert introspection lists current tables/edges.

---

## U3 — Declared shapes, mechanically enforced (curate-time only)

**Goal.** Extend the `table:` column grammar with **shape constraints** and enforce
them at curate time in CE's own hash-guarded Python — *no maplib SHACL*. Scoped to
the research value: a mechanical quality gate on extracted/curated rows ("every
measurement row carries units + a source page"), in the spirit of the numeric-review
pass.

**Why (and the honest asterisk).** The design note flags U3 as the weakest outside
EDM because its headline payoff (one declaration → DB constraint + form validation)
needs graduation, which we're not doing. What survives is real but narrower: a
per-class validation gate on `table:` rows. Build *that*; skip the DDL/form consumers.

### Changes

1. **Extend `table:` column grammar** (`tables.py._normalize_columns`) with optional
   keys, all backward-compatible (absent = today's behaviour):
   ```yaml
   columns:
     - name: ic50
       type: real
       units: nM                 # NEW: declared unit
       constraint: ">0"          # NEW: simple comparator / range
       source_required: true     # NEW: row must carry a (vault:) citation for this value
   ```

2. **New hash-guarded `shape_check.py`** — given an entity page's schema and its
   rows (from `tables.db`), validate: units present where declared, numeric
   constraints satisfied, `source_required` columns backed by a citation. Returns a
   suspect list in the same shape as `score_diff.py`'s citation suspects.

3. **Ratchet integration** — `score_diff.py` already calls `verify_table_citations`
   on new `(table:)` citations; add a parallel `verify_table_shapes` call so a curate
   edit that introduces a shape-violating row is rejected with a clear reason. This
   is the *single* consumer in research scope (the DB-constraint and form consumers
   stay deferred with the EDM on-ramp).

4. ~~**Wire into the existing numeric-review pass** (`sweep.py`).~~ **Deferred as
   redundant** (assessed during implementation): shape constraints are declared on
   entity-page *class-table* schemas, while numeric-review operates on raw
   `_extracted_tables` rows that carry no shape declarations. The value chain already
   exists — when an extracted table is promoted to a class table, the insert-time gate
   shape-checks every row. A second hook in `sweep.py` would duplicate enforcement for
   marginal gain. Revisit only if shapes are ever declared directly on `[tab]` pages.

**Files:** `tables.py` (grammar), new `shape_check.py` (hash-guarded), `score_diff.py`
(`verify_table_shapes` hook), `sweep.py` (numeric-review integration),
`template/schema.md` (grammar docs).

**Risks / guards.**
- *Over-strictness* — shapes are **local and per-class**, never global agreement; a
  page without shape declarations is unaffected. Start with `units`/`source_required`;
  defer richer constraint grammars until a real need appears.
- *Hash-guard discipline* — `shape_check.py` must be hash-guarded like `score_diff.py`
  so the curator can't silently weaken it.

**Test plan.** A row missing declared units → rejected; a value violating `>0` →
rejected; `source_required` with no citation → rejected; an undeclared-shape page →
unchanged pass; existing ratchet tests still green.

---

## U4 — Federation by identity

**Goal.** Answer "how does CE scale?" — not one 50M-fact wiki but **many bounded
sub-wikis at CE's proven ~500–1000-page sweet spot, federated by the IRIs from U1**,
with `wave_scope` cluster-scoping repurposed as the shard boundary and
`curiosity-merge` as the reconciliation path.

**Why (on-mission).** This is *the* scaling story for CE's own stated sweet spot
(hundreds–thousands of sources over months) against its ~500-page plan-latency
ceiling. Long-horizon research, not EDM.

### Changes

1. **Depends on U1.** Cross-wiki join is only deterministic once entities carry
   stable IRIs. Land U1 first.

2. **`wave_scope` → shard boundary** (`epoch_summary.py`): the existing 2-hop
   neighbourhood (threshold 500) already produces a locally coherent slice. Add a
   "shard export" reading — given a `wave_scope` cluster, emit the bounded
   neighbourhood as a candidate sub-wiki seed. Repurposes the primitive from "keep a
   wave coherent" to "keep a shard bounded"; no new clustering algorithm.

3. **IRI-keyed merge in `curiosity-merge`** (separate repo): extend the existing
   merge logic (today: vault-SHA dedup + slug-keyed page reconciliation) so that
   pages sharing an `iri:` reconcile across the seam regardless of slug. This is the
   "identity is what lets shards join" payoff. Keep the `origin:` audit-tag mechanism
   the merge spec already defines.

4. **Multi-project bridge** ([`multi-project.md`](multi-project.md)): the shipped
   project model (derived tags, recency planner, cross-project bridge candidates) is
   the intra-workspace analogue; U4 extends the same idea to *cross-workspace* IRI
   federation. No change to the shipped multi-project code is required for phase 1 —
   federation lives in the merge skill consuming U1's `entities` table.

**Files:** `epoch_summary.py` (shard-export reading of `wave_scope`); downstream
`curiosity-merge` repo (IRI-keyed reconciliation); docs in `multi-project.md`.

**Risks / guards.**
- *Cross-repo coordination* — the bulk of U4 is in `curiosity-merge`, not this tree.
  Land U1's `entities` table as the stable contract first, then iterate the merge
  skill against it.
- *Shard coherence* — a 2-hop cluster can still straddle topics; surface the shard
  candidate for human confirmation rather than auto-sharding (no silent splits).

**Test plan.** Two workspaces sharing an entity → merge reconciles by IRI not slug;
`wave_scope` shard-export returns a bounded, connected neighbourhood; `origin:` tags
preserved through merge.

---

## U5 — Incremental materialisation (derived-fact cache)

**Goal.** Generalise the score cache into a **derived-fact cache**: aggregates,
transitive closures, and cross-shard link proposals materialised at write time and
invalidated linearly in *changed* pages — so "given enough token budget" stops
meaning "re-derive the world on every read."

**Why (neutral infra).** Helps any corpus regardless of direction. It's the Datalog
materialised-view idea without Datalog, reusing machinery CE already has.

### Changes

1. **Generalise the cache key model** from `lint_scores.py`'s
   `text_hash + inbound` / global `titles_hash + vault_rowcount` into a reusable
   `derived_cache.py` that keys any derived fact by the `text_hash`es of its
   *dependency set* and invalidates when any dependency hash changes — the same
   O(changed) story, not O(total).

2. **Cache store** — `.curator/.derived_cache.json` (mirror the existing
   `.score_cache.json` shape: best-effort atomic write, silent-fail tolerant), or
   fold into the score cache file under a `derived:` namespace. Separate file is
   cleaner for invalidation reasoning.

3. **First consumers** (each independently optional):
   - U2 aggregate queries the curator plans over → cache the aggregate, invalidate on
     class-table row churn (`tables.db._audit_log` already tracks this).
   - Graph transitive closures / bridge candidates (`epoch_summary.py`) →
     materialise, invalidate on graph rebuild.

4. **Budget discipline** — spend the token budget where it compounds (identity
   resolution, shape validation, cross-shard linking); never on re-deriving structure
   a read could have cached. This is the explicit rule the cache enforces.

**Files:** new `derived_cache.py` (generalised from `lint_scores.py` cache helpers);
consumers in `epoch_summary.py` and the U2 router opt in.

**Risks / guards.**
- *Stale derivations* — the dependency set must be complete or the cache serves stale
  facts. Start with single-dependency derivations (aggregate over one table) where the
  dependency set is unambiguous; add multi-dependency closures once the invalidation is
  proven.
- *Silent caps* — if a derivation is bounded (top-N), `log` what was dropped; never let
  a truncated cache read as complete.

**Test plan.** Change one source page → only dependent facts recompute (assert others
served from cache); add a vault row → global key flips, cache discarded (matches
score-cache semantics); aggregate cache matches a fresh `query_router` computation.

---

## Net

- **U1, U2, U4** are unambiguous upgrades to CE-as-research-wiki; they're the
  load-bearing ones and U1→U2 is the foundation.
- **U3** ships as a narrower per-class extraction-quality gate (units +
  source-required), with the DB/form consumers deferred alongside the EDM on-ramp.
- **U5** is neutral infrastructure, landable anytime, most valuable after U2.

Each upgrade deepens a capability CE already has half-built. None imports a foreign
concept, and none requires maplib/RDF — which stays deferred behind a real
federation/compliance decision, exactly as the design note gates it.

---

## Implementation status (shipped)

All five landed in-repo and were verified against a real 382-page test wiki
(`curiosity-test`) and controlled fixtures.

| | Shipped in this repo | Deferred / cross-repo |
|---|---|---|
| **U1** | `entities` table + `mint-entity`/`lookup-entity` in `identifier_cache.py`; deterministic workspace-stable IRIs (idempotent, stable collision suffix, same_as merge); resolver registry in `identifier_resolve.py`; `iri`/`same_as`/`entity_class` frontmatter in `naming.py`; docs in `schema.md` | IRI-keyed reconciliation consumes the `entities` table in **`curiosity-merge`** (separate repo) |
| **U2** | `query_router.py` (`sql`/`cypher`/`introspect`/`classify`), read-only doubly enforced (engine + statement allowlist); `table_aggregates` planner hook in `epoch_summary.py`; QUERY docs in `SKILL.md` | NL→SQL/Cypher translation stays an LLM step (by design); DuckDB swap only if a corpus ever needs >SQLite scale |
| **U3** | `units`/`constraint`/`source_required` in the `table:` grammar; `shape_check.py` (hash-guarded); insert-time gate in `tables.py`; `verify_table_shapes` in the `score_diff.py` ratchet; `schema.md` docs | numeric-review hook (assessed redundant — see U3 §4); DB-constraint + form-validation consumers (EDM-only) |
| **U4** | `shard_export` + `epoch_summary.py --shard <seed>`: bounded 2-hop shard + seam-IRI detection; federation-by-identity docs in `multi-project.md` | shard ingestion + IRI-keyed merge in **`curiosity-merge`** |
| **U5** | `derived_cache.py` (hash-guarded): dependency-fingerprint cache, `table_fingerprint`/`graph_fingerprint`, `memoize`, demonstrated `cached-aggregate` consumer; O(changed) invalidation verified | wider consumer wiring (graph closures, cross-shard links) as needs arise |

**Verification highlights:** U1 mint determinism / collision / same_as-merge; U2 real
Cypher (`transformer`: 114 inbound) + `read_only=True` write-block confirmed against
kuzu 0.11.3; U3 insert + ratchet both reject violating rows and pass clean ones; U4
seam detection (shard `{seed, mid, shared-entity}`, external linker surfaced); U5
miss→hit→(row churn)→miss→hit. All guarded scripts (`shape_check.py`,
`derived_cache.py`) resolve in `evolve_guard.sh` with no `MISSING`.

**Two WAL gotchas fixed in passing:** opening a live WAL-mode SQLite db with `mode=ro`
hangs (its `-shm` needs write access); both new read paths use a normal connection with
`PRAGMA query_only=ON` instead. And a killed-but-unreaped process holding the kuzu lock
blocks subsequent opens — relevant to any tooling that backgrounds graph reads.

**Not done (out of scope, as agreed):** maplib/RDF, the operational `(op:...)` citation
root, `schema_extract.py` schema graduation, and form generation — all gated behind a
future EDM/federation decision.
