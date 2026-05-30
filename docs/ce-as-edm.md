# Curiosity-engine as an empiricist alternative to enterprise data management

This document sketches a direction: extending curiosity-engine (CE) from a
research/knowledge tool into an **alternative philosophy of enterprise data
management (EDM)** — and the concrete upgrades that would get it there.

It is a design note, not shipped behaviour. Nothing here changes the existing
three-object model (vault / wiki / curator); every upgrade is additive and
optional. For the foundation see [`architecture.md`](architecture.md); for the
two-tier citation model this builds on, see
[`code-knowledge.md`](code-knowledge.md).

## The thesis

Traditional EDM — and the model-first reading of the medallion architecture —
treats **the model as authority**: a schema or ontology is declared upfront, a
governance body owns it, and data are conforming instances. Schema-on-write.
The ontology is a *contract imposed before the data arrives*.

CE inverts the epistemics. **Sources are authority.** Every claim is cited;
the schema is a *falsifiable hypothesis that earns its place* through curation
and can be revised with full lineage. Schema-on-read, ratcheted. This is an
empiricist (broadly Popperian) data posture against a rationalist one: the
declared model is replaced by an *emergent, provenance-backed* one that the
curator tends over time.

Reframed onto the medallion's three layers, this keeps Bronze and Silver and
**replaces Gold**: instead of "Silver DataFrames mapped to a fixed shared
ontology and published as RDF," Gold becomes *curated, cited, falsifiable
knowledge* whose authority flows from provenance and the citation ratchet, not
from a vocabulary everyone agreed to in advance.

### The architectural posture (non-negotiable)

**Operational data belongs in databases.** CE does not become a transactional
store. You do not *curate* a payments ledger; you ETL it. High-volume,
high-velocity, low-ambiguity transactional data lives in an OLTP system, full
stop. CE's edge is the **interpretive layer** over that data:
knowledge-dense, evolving, contested, provenance-critical material —
analyses, evidence, the emergent schema, and the lineage that ties an insight
back to the rows that support it.

The rule that keeps this honest, stated once and enforced throughout:

> **CE never mirrors the operational store. It cites it.**

This is the same source-of-truth vs reference distinction `code-knowledge.md`
already draws between `(vault:...)` (append-only source of truth) and
`(code:...)` (a version-pinned reference that does not gate the ratchet). We
extend it below to live operational data.

## What this borrows from the semantic medallion — and what it throws away

The semantic-web / medallion pitch bundles four separable promises. They are
not equally valuable in an LLM world:

| Promise | Verdict |
|---|---|
| **Shared vocabulary → interop** | **Discard (internally).** LLMs map between schemas pairwise and on-demand for cents. The expensive part of the semantic web — agreeing on one ontology upfront — is exactly the part models made obsolete. Survives only at boundaries you don't control (a regulator or standard that *mandates* a canonical vocabulary). |
| **Stable identity (IRIs) → join without re-deciding** | **Keep.** Deciding "are these the same entity?" with a model on every query is non-deterministic and expensive. Resolve once, mint a stable id, and joins are O(1) forever. Identity *compounds*; ontology dissolves. |
| **Write-time-expensive / read-time-cheap inversion** | **Keep.** Push the fuzzy/expensive work to write/curate time so reads are cheap, exact, and unbounded-QPS. The opposite of "let the LLM map everything on the fly," which is fine once and catastrophic at volume. |
| **Mechanically-enforced shape + set-based execution at scale** | **Keep.** Models do not do guaranteed integrity or 40M-row aggregation. A query engine does. |

So the universal ontology is the part to drop; the other three are real,
LLM-proof, and worth borrowing. Crucially, **none of them requires RDF or
maplib to obtain** — CE already has a property graph (kuzu) and a relational
store (`tables.db`). maplib/RDF earns its place only at the federation /
compliance boundary (see the closing section).

## The upgrades

Five upgrades carry the three keepers above into CE's idiom. Each names what
it borrows, the CE mechanism it extends, and where it lives.

### U1 — A domain-agnostic identity layer (the Silver "IRI" / MDM borrow)

**Borrows:** the Silver-layer stable IRI — the join key that works across
systems and across time.

**CE mechanism:** generalise `identifier_cache.py` (today: chemicals →
InChIKey via PubChem; genes → Ensembl/UniProt via MyGene — already an IRI
minter for those domains) into a domain-agnostic **entity-resolution + IRI
service**. Every entity page gets a stable minted IRI recorded in frontmatter.
Resolution against external authorities is LLM-assisted **at write time**, then
cached deterministically in `.curator/identifiers.db`. Local entities resolved
to an external canonical id carry an `owl:sameAs`-style link.

**Why it matters:** this is master-data management, but *emergent and
provenance-backed* instead of declared by a governance committee. It is the
thing that compounds. It also upgrades `curiosity-merge` from slug-keyed to
**IRI-keyed** reconciliation, and — see U4 — is what lets bounded sub-wikis
federate.

**Lives in:** `identifier_cache.py` (generalised), entity-page frontmatter
(`iri:`, `same_as:`), `.curator/identifiers.db`.

### U2 — A deterministic query substrate alongside the LLM (the read/write inversion)

**Borrows:** SPARQL-over-Gold's property — structured questions answered by an
engine, cheaply and exactly, not by a model per call.

**CE mechanism:** today `query` mode answers everything through an LLM agent —
correct for *"what do I know about X?"*, wrong for *"all deals > $1M closing
this quarter joined to counterparties in sector Y."* CE already holds the
substrate: `tables.db` (DuckDB-queryable class-table rows) and `graph.kuzu`
(Cypher-queryable relationships). Promote them to a **first-class query
surface**: structured questions hit the engine (cheap / exact / unbounded
QPS); only *synthesis* questions spend tokens. The curator itself uses this to
plan over real aggregates instead of re-reading prose.

**Why it matters:** this is the medallion's write-time/read-time inversion, and
CE is most of the way there — the engines exist; they're curator scratch
today, not a surface.

**Lives in:** a thin query module over `tables.db` + `graph.kuzu`; exposed to
agents/users as a query verb. (In switchyard this is the natural home for the
`!sql` / a `!gql` rail verb — see that repo's step R.)

### U3 — Declared shapes, mechanically enforced (the SHACL idea, in CE's idiom)

**Borrows:** SHACL's mechanical shape validation — "every Invoice links to a
Customer; close_date ∈ [created, +2y]."

**CE mechanism:** the citation ratchet already enforces invariants mechanically
(`score_diff.py`: no citation dropped, no bloat, cited source must FTS-match
the claim). Extend the `table:` schema block on entity pages to declare
**shape constraints** alongside columns, and enforce them at curate time in
CE's **own hash-guarded Python** — no maplib SHACL, so the non-OSS-SHACL
licensing wrinkle never bites. Validation is **local and per-class**, never
global agreement: the emergent ontology becomes *validated* without becoming
*universal*.

**The payoff that ties the whole doc together:** a shape is declared **once**
and has **three consumers** — (1) the curate-time ratchet rejects edits that
violate it, (2) it materialises as a DB `CHECK` / `NOT NULL` / `FK` constraint
when the schema graduates (see the on-ramp below), and (3) it renders as a
**form-validation rule** in the data-entry UI built on top. One source of
truth for the schema; three enforcement points.

**Lives in:** an extended `table:` frontmatter grammar; a new hash-guarded
`shape_check.py`; consumed downstream by U2's emitter.

### U4 — Federation by identity (cluster-scoping as the sharding strategy)

**Borrows:** the medallion's "IRI as universal join key" — minus the universal
ontology.

**CE mechanism:** at enterprise scale you do not curate one 50M-fact wiki. You
curate **many bounded, topic/domain-scoped sub-wikis**, each at CE's proven
~500–1000-page sweet spot, federated by the IRIs from U1 and merged on demand.
CE's existing 2-hop `wave_scope` cluster-scoping (`epoch_summary.py`, default
threshold 500) *becomes the sharding boundary*: each shard is a locally
coherent neighbourhood; the identity layer is what lets shards join across the
seam. This is precisely the scaling primitive the user already has in hand for
large graphs — repurposed from "keep a wave coherent" to "keep a shard
bounded."

**Why it matters:** it answers "how does the CE philosophy scale?" — not a
bigger graph, but **many bounded graphs federated by stable identity**, which
is exactly the join power the medallion's IRI buys, with the dropped part
(universal ontology) genuinely gone.

**Lives in:** the multi-project model (see [`multi-project.md`](multi-project.md))
extended to cross-workspace IRI federation; `curiosity-merge` as the
reconciliation path.

### U5 — Incremental materialisation (token-budget discipline)

**Borrows:** Datalog/materialised-view derivation — compute once, invalidate on
change.

**CE mechanism:** CE already caches scores keyed by `text_hash + inbound_count`
and invalidates linearly in *changed* pages. Extend the same pattern to
**derived facts** — aggregates, transitive closures, cross-shard link
proposals: materialise at write time, invalidate on source change. This is the
Datalog-view idea without Datalog, and it is how "given enough token budget"
stops meaning "re-derive the world on every read." Spend the budget where it
compounds (identity resolution, shape validation, cross-shard linking); never
on re-deriving structure a read could have cached.

**Lives in:** the existing score-cache machinery, generalised to a derived-fact
cache.

### These upgrades stand on their own, independent of the EDM framing

The EDM direction in this doc is *one consumer* of U1–U5, not their
justification. None of them is an imported foreign concept — each deepens a
capability CE already has half-built (`identifier_cache`, `tables.db`, kuzu,
cluster-scoping, the multi-project model). They make CE more itself. For CE used
exactly as originally envisioned — a self-improving research/knowledge wiki —
the value holds:

- **U1 (identity) — clear win.** Entity resolution across sources ("is this the
  same protein / author / construct across 40 papers?") is a core research-wiki
  problem. CE already does it for chemicals/genes; generalising IRI minting +
  `same_as` strengthens disambiguation and turns `curiosity-merge` from
  slug-keyed to IRI-keyed reconciliation. For generic concepts with no external
  authority, the IRI is mostly a workspace-stable internal id with optional
  external `same_as` — still a real gain.
- **U2 (query substrate) — clear win, concentrated.** Structured questions over
  accumulated class-table data and structural questions over the graph,
  answered without spending tokens; the curator plans over real aggregates. The
  value scales with how much structured/tabular content the corpus has — which
  is exactly CE's scientific-extraction sweet spot. Thinner for purely-prose
  corpora; the curator-side benefit is general.
- **U4 (federation) — clear win, on-mission.** This is *the* scaling answer for
  CE's own stated sweet spot (hundreds–thousands of sources over months) against
  its own ~500-page plan-latency ceiling. Many bounded sub-wikis federated by
  IRI, with cluster-scoping as the shard boundary, is a long-horizon-research
  story, not an EDM one.
- **U3 (declared shapes) — the asterisk.** Its headline payoff (one declaration
  → DB constraint + form validation) *is* EDM-specific; without graduation there
  are no forms and no DDL. Its residual research value is narrower but real: a
  mechanical quality gate on extracted `table:` rows ("every measurement row
  carries units + a source page"), in the spirit of the numeric-review pass. The
  weakest of the four outside EDM.
- **U5 (incremental materialisation) — use-case-agnostic infra.** Helps any
  corpus regardless of direction.

Net: **U1, U2, U4 are unambiguous upgrades to CE as originally envisioned; U3
collapses to a narrower (still real) extraction-quality benefit; U5 is neutral
infrastructure.** The EDM direction is upside layered on top, not a precondition
for the upgrades to pay off.

## The document-to-database on-ramp

A use case that fits the empiricist posture exactly: **many enterprises run
"operational" data as scattered Excel sheets and documents.** That archive is
not a database — it is a pile of documents with a *latent* schema nobody ever
declared. CE's relational + graph capabilities make it an **on-ramp**: ingest
the archive, let the schema *emerge* through curation, then graduate that
schema into a real operational RDB with data-entry interfaces on top.

The flow has four stages. Provenance threads through all of them (next
section).

### Stage A — Archive → CE (documents as Bronze/Silver)

Scattered spreadsheets and docs are *ideal* vault citizens: they are already
documents, and the vault is append-only. CE ingests them with the existing
pipeline — each file → a vault source + `.extracted.md`; tables inside →
`tab-*.md` extracted-table pages with rows mirrored into `tables.db`, each row
citing `(vault:...)` provenance. **This is already supported.** The emergent
schema is the union of column-shapes the curator promotes into entity-page
`table:` declarations as it recognises that, say, forty spreadsheets are all
describing the same `Customer` shape under different headers.

Identity (U1) does the heavy lifting here: the same customer appearing across
forty sheets resolves to **one IRI**, which is the medallion's Silver-layer
entity-resolution payoff — obtained by curation, with provenance, instead of by
fiat.

### Stage B — Schema graduation: CE → operational RDB

Once curation has stabilised the emergent schema, **extract it into a real
operational database.** A proposed `schema_extract.py` reads the class-table
schemas + their inferred dtypes + the citation/relationship graph and emits:

- **DDL** — `CREATE TABLE` per entity class, primary key = the entity IRI
  (U1). **Foreign keys come from the graph**: kuzu relationship edges between
  entity classes become FK constraints. (The graph *is* the relational
  schema's relationship structure — a clean payoff.)
- **Constraints** — the U3 shape declarations materialise as `CHECK` /
  `NOT NULL` / FK constraints.
- **A seed migration** — loads the curated rows, and critically, **each seeded
  row carries its originating `(vault:...)` citation in a `provenance` column.**
  The operational DB is *born with provenance*: every migrated row knows which
  spreadsheet cell it came from, instead of starting blank.

Data-entry interfaces (forms) are then built on top of this DDL — and their
field validation is generated from the *same* U3 shapes. One declaration in
CE; enforced at curate-time, as a DB constraint, and in the form.

### Stage C — Live operation

The operational DB is now a live system of record. People enter and mutate rows
through the forms. These rows are mutable and high-velocity — the exact data
class the posture says belongs in a DB, not in CE. CE does **not** ingest them
as documents (that would either freeze mutable data or flood the vault).

### Stage D — Knowledge re-capture

The operational system's **event stream** drains back into CE as a capture
surface — the same way `code-knowledge.md` treats PR threads, commits, and
agent transcripts: *already produced as part of normal work, structured, on a
deterministic trigger.* The curator promotes **insights** — analyses, evidence,
facts — from patterns *across* events, citing them by reference (next section).
CE accumulates the interpretive layer over the live DB, ratcheted and curated
exactly as it curates documents.

The result is a **loop**, threaded by the IRI at every hop:

```
  Excel/docs ──ingest──▶ CE (vault + emergent schema)
                              │
                              │ schema_extract (Stage B)
                              ▼
                       operational RDB  ◀── data-entry forms
                              │            (rows born from events)
                              │ event stream (Stage D)
                              ▼
                    CE insights (analyses/evidence/facts)
                              │
                              └────────── compounds back into the wiki
```

Documents → CE schema → operational DB (provenance inherited) → events → CE
insights (provenance cited) → … and the entity IRI is the single thread running
through every arrow. This is the "smooth data capture flowing to knowledge
capture and curation" the on-ramp is for.

## Provenance for operational data (the open question, answered)

The hard question the on-ramp raises: CE's provenance model assumes
**append-only documents** (`(vault:path)` cites something unchanged since
ingest). Operational data is **mutable, row-level, high-velocity**. What is the
provenance *unit*, and how does it flow once data is born in a form rather than
extracted from a file?

### Provenance is dual-rooted and version-pinned

Introduce a second citation root for operational assertions, alongside
`(vault:...)`:

```
(vault:path/to/sheet.extracted.md)        # document-rooted: the archived past
(op:<table>:<entity-iri>@<event-id>)       # event-rooted: the live present
```

The key move: **the provenance unit for operational data is the event, not the
row.** A row mutates; the *event that asserted a particular value at a
particular version* does not. So an `(op:...)` citation pins an `event-id`,
making it version-stable even though the underlying row is live. This requires
the operational system to emit an **append-only event log** (who, when, what,
which form, which validation passed) — which is the operational analog of CE's
append-only vault, and the thing Stage D drains.

### Reference-tier, not source-tier

`(op:...)` is a **reference-tier** citation, exactly as `(code:...)` is in
`code-knowledge.md`: tracked by the graph builder for backlinks and drift, but
it does **not** gate the citation-preserving ratchet — only `(vault:...)`
source-of-truth citations do. This is what enforces *CE never mirrors the
operational store; it cites it.* The rows stay in the DB (system of record for
transactions); CE holds the interpretation, pinned to the event that justifies
it, and flags drift when the live row has moved past the cited event.

### The provenance lifecycle, end to end

1. **Archive ingest (Stage A).** Root = document. `(vault:sheet.extracted.md)`.
   Curation mints the entity IRI (U1) and promotes the emergent schema (U3).
2. **Graduation (Stage B).** The operational DB inherits provenance: each
   seeded row's `provenance` column carries its originating `(vault:...)`
   citation. The DB is born knowing where its rows came from.
3. **Live operation (Stage C).** New rows are born from form events. They have
   **event provenance** (`(op:...@event)`) instead of document provenance — but
   the **same IRI**. Provenance root shifts from document to event without
   breaking identity.
4. **Re-capture (Stage D).** The event stream drains into CE; the curator
   writes analyses/evidence/facts citing `(op:...)`. Insights compound. Drift
   audit (the existing `table_citation_risk` pattern, generalised) flags any
   wiki claim whose cited event has been superseded by a later row state.

So provenance is **bi-temporal and dual-rooted**: document-rooted for the
archived past, event-rooted for the live present, both append-only, both
version-stable, joined by one IRI. The archive's frozen provenance and the live
system's streaming provenance are the same mechanism with two roots — and the
curator treats both as evidence under the same ratchet discipline (with
`(op:...)` at reference tier so live mutability never corrupts the source-tier
guarantee).

### The never-mirror rule, made concrete

CE must not drown in operational event volume, and must not become a slow
replica of the DB. Two governing rules:

- **Cite, don't copy.** The vault receives *event digests, anomalies, and
  flagged transitions* — not the firehose. The full rows stay in the
  operational DB and are reached live via U2's query substrate.
- **Sample and interpret.** The curator's unit of value is a cross-event
  *insight*, not a row. U4 scoping bounds which slice of the operational graph a
  wave reasons over; U5 materialisation caches the aggregates so reads don't
  re-scan the stream.

## Where maplib actually earns its place

For the self-contained on-ramp (Excel → CE → Postgres + forms), **you do not
need RDF or maplib at all.** kuzu gives the relationship graph that becomes FKs;
`tables.db`/DuckDB gives set-based execution; `schema_extract.py` targets SQL
DDL directly. The four keepers (U1–U3, U5) are all obtainable natively.

maplib/RDF becomes worth the dependency at exactly one place: the **federation /
compliance boundary** — when the operational estate must *publish* to or
*consume* from an external RDF/DCAT catalog, or speak a *mandated* standard
vocabulary (FIBO, schema.org, a regulator's ontology) that you don't control.
There, LLM mapping authors the crosswalk but the wire format must be the
canonical IRI, deterministically. maplib's OTTR templates project CE's emergent
schema into that standard on the way out, and SPARQL federates against external
graphs on the way in. Budget around the non-OSS pieces: mapping + SPARQL
SELECT/CONSTRUCT/INSERT + serialisation are open; SHACL and Datalog are not
(U3's validation deliberately stays in CE's own Python to avoid that).

In short: **borrow the four concepts natively; reach for maplib itself only as
the export/compliance adapter at the edge.**

## When this fits and when it doesn't

**Fits well when:**

- Operational data is currently scattered across spreadsheets and documents
  with no declared schema, and someone needs to turn it into a real system.
- Provenance matters — every operational value traceable to the spreadsheet
  cell or the form event that produced it.
- The domain is interpretive: the value is in cross-record analysis, not just
  storage.

**Doesn't fit when:**

- The data is already in a well-modelled operational DB with a maintained
  schema. CE doesn't replace it; at most it curates an interpretive layer over
  the event stream.
- The data is purely transactional with no interpretive layer (a payments
  ledger). Use a database directly; the posture says so.
- You need real-time operational reads at scale *from CE*. CE cites the live
  store via U2; it is not itself the low-latency serving path.
- A regulator mandates a fixed ontology end-to-end. Then the universal ontology
  is not overrated — it is required — and this is a thinner win (maplib as a
  compliance adapter, not CE as the model).

## Open questions and roadmap

- **Event-log contract.** Stage D assumes the operational system emits a
  structured append-only event log. Defining the minimal schema for that log
  (and adapters for systems that only expose CDC or audit triggers) is the
  first real design task.
- **Drift semantics for `(op:...)`.** When a cited event is superseded, is the
  wiki claim *stale*, *wrong*, or *historically-true-as-of*? The
  `table_citation_risk` machinery gives the trigger; the resolution policy
  (re-curate vs annotate vs archive) needs specifying.
- **`schema_extract.py` round-tripping.** Stage B emits DDL from the emergent
  schema. Should later DB-side schema changes (a DBA adds a column) flow *back*
  into the CE entity page, or is graduation one-way? One-way is simpler and
  truer to the posture; bidirectional is more useful and riskier.
- **Identity-resolution review.** U1 mints IRIs and proposes `same_as` links at
  write time. These need the same fresh-context-reviewer + spot-auditor
  discipline the rest of curation has, or entity resolution silently drifts.
- **Sequencing.** U1 (identity) and U2 (query substrate) are the foundation and
  unlock the rest; U3 (shapes) is the bridge to graduation; U4/U5 are the
  scaling story; the on-ramp and operational provenance sit on top of all of
  them. maplib is explicitly last, gated on a real federation requirement.

This is a direction, not a commitment. The empiricist posture and the
never-mirror rule are the load-bearing constraints; everything else is
negotiable against them.
