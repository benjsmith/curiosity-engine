# Curiosity-engine and the Open Knowledge Format

This document examines Google Cloud's **Open Knowledge Format (OKF)**, maps it
onto curiosity-engine (CE), evaluates where each improves on the other, and
proposes an extension that contributes CE's strengths back to the open format.

It is a design note plus the rationale for the shipped `okf_export.py`. Nothing
here changes the three-object model (vault / wiki / curator); OKF export is a
**read-only projection** of the wiki, additive and optional — one more derived
view alongside `data.json` and the kuzu graph. For the foundation see
[`architecture.md`](architecture.md). A broader design essay (the empiricist
data-management posture) is published as a gist — see footer.

## The thesis

OKF (Google Cloud, [v0.1 Draft](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md),
Apache-2.0) is a **markdown-native** knowledge-interchange format: a "Knowledge
Bundle" is a directory of markdown "Concept" files, each a YAML frontmatter
block plus a body. Only `type` is required; cross-links are plain markdown
links; `index.md` / `log.md` are reserved; consumers MUST tolerate unknown
keys, unknown types, and broken links. It **deliberately omits provenance and
confidence**.

CE and OKF are strikingly convergent — both make **markdown files with YAML
frontmatter** the unit of knowledge — which is what makes interop cheap. But
they sit at different points on one axis: OKF is a **lowest-common-denominator
interchange** format optimised for cross-organisational exchange with zero
tooling; CE is a **superset** with typed relations, a deterministic query
substrate, emergent identity, declared shapes, and — above all — a provenance
model OKF explicitly declines to specify.

So the relationship is: **export is lossless-out; import is ratchet-preserving-in.**
CE projects to OKF without discarding its own structure (it rides along in
`x_ce_*` extension keys OKF must preserve), and consumes OKF without letting
unprovenanced external claims bypass the citation ratchet.

## What OKF v0.1 is

- **Knowledge Bundle** — a hierarchical directory of markdown files; the unit of
  distribution (git repo recommended, or tarball / zip / subdirectory).
- **Concept** — one markdown file. **Concept ID** — its path minus `.md`
  (`tables/users.md` → `tables/users`).
- **Frontmatter** — `type` is the *only* required field (free string). Recommended
  optional: `title`, `description` (one sentence), `resource` (a URI for the
  underlying asset), `tags` (list), `timestamp` (ISO 8601). Producers may add
  arbitrary keys; consumers MUST preserve them and MUST NOT reject unknown
  fields / types.
- **Relations** — standard markdown links, bundle-absolute (`/tables/x.md`) or
  relative (`./x.md`). Untyped directed edges; the relationship's *meaning* lives
  in the surrounding prose, not a typed-edge schema. Consumers MUST tolerate
  broken links.
- **Reserved files** — `index.md` (no frontmatter; markdown lists for
  "progressive disclosure"; the bundle-root index may carry `okf_version`) and
  `log.md` (optional; ISO-8601 `YYYY-MM-DD` date headings, newest first).
- **Body conventions** — `# Schema`, `# Examples`, `# Citations` headings.
- **No** provenance, confidence, identity, or shape model. Extensions are
  invited via extra frontmatter keys.

## The CE ↔ OKF mapping

| CE construct | OKF construct | Notes |
|---|---|---|
| `wiki/` page (frontmatter + prose) | Concept `.md` (frontmatter + body) | 1:1 |
| 11 typed subdirs (`entities/`, `concepts/`, …) | bundle directory hierarchy | 1:1 |
| `type` (`entity`/`concept`/…) | `type` (free string) | verbatim pass-through |
| `title: "[con] X"` | `title: X` | doc-type prefix stripped |
| first-sentence prose / `description` fm | `description` | derived |
| `iri` + `same_as` | `resource` (from `same_as`) + `x_ce_iri` / `x_ce_same_as` | IRI never in `resource` (origin-scoped) |
| `updated` / `created` | `timestamp` | ISO date |
| `aliases`, `projects` | `tags` | flattened |
| `[[Wikilink\|Display]]` | `[Display](/subdir/stem.md)` | typed edge → untyped link |
| `(vault:path)` citation | `# Citations` section + `x_ce_citations` | see below |
| class-table `table:` shape (U3) | `x_ce_table` (literal block) | no OKF home; preserved |
| `.curator/index.md` | reserved `index.md` (per dir + root) | regenerated |
| `.curator/log.md` | reserved `log.md` | fresh export-date heading |

## Where CE improves on OKF

OKF's four deliberate omissions are exactly CE's load-bearing features:

- **Provenance — the headline gap.** OKF has no provenance model at all. CE's
  entire epistemics is the inverse: every claim carries a `(vault:...)` citation
  into an append-only vault, and the citation ratchet (`score_diff.py`) refuses
  any edit that drops a citation or fails to FTS-match its source. This is
  *schema-on-read, ratcheted* — authority flows from sources, not from a
  vocabulary agreed in advance. It is the single largest thing CE has that OKF
  does not.
- **Identity (U1, shipped).** OKF's `resource` is a bare per-concept URI. CE mints
  workspace-stable IRIs (`ce:<class>:<workspace>:<slug>`) with an
  `owl:sameAs`-style `same_as` map to external authorities that *never gates
  identity*. Resolve once, join O(1) forever — identity compounds where an
  untyped `resource` cannot.
- **Typed relations.** OKF links are untyped; the relationship's meaning is left
  to prose. CE distinguishes `WikiLink`, `Cites`, `Depicts`, and `DataRef`
  edges, plus a mechanical `ProvisionalLink` tier — a real property graph
  (kuzu), queryable with Cypher.
- **Declared shapes (U3, shipped).** OKF's `# Schema` is prose. CE's `table:` blocks
  declare typed columns with `units` / `constraint` / `source_required`,
  enforced mechanically at insert time and at the ratchet — local and per-class,
  never a universal ontology.
- **A deterministic query substrate (U2, shipped).** OKF is files an agent reads. CE
  additionally holds a rebuildable graph (kuzu) and relational store
  (`tables.db`) so structured questions hit an engine cheaply and exactly rather
  than spending tokens per call.

## Where OKF wins

- **Standardisation.** OKF is a published, vendor-neutral, Apache-2.0 format with
  an ecosystem (Google Knowledge Catalog ingest, a static Visualizer, an
  enrichment agent). CE's markdown+DSL is bespoke to the skill.
- **Zero tooling.** "If you can `cat` a file you can read it." CE's `(vault:...)`
  and `[[wikilink]]` DSLs need CE to interpret fully (they degrade gracefully to
  plain markdown, but the semantics are CE's).
- **Lower friction for cross-org exchange.** Untyped links and a free-form `type`
  vocabulary are a feature when two organisations must exchange knowledge without
  first agreeing on an ontology — the coordination cost LLMs made cheap to skip
  *internally*, while still needing a wire format at organisational boundaries.

This is the natural **federation / compliance boundary** role: not a heavyweight
RDF/maplib stack (with non-OSS SHACL / Datalog traps), but a file format peers can
`cat`. **OKF fills that role while being markdown-native**, so it
matches CE's own source-of-truth grain far better than RDF ever could. Export is
a thin projection, not a semantic re-encoding.

## The export mapping (as shipped)

`okf_export.py build <wiki> --output-dir <bundle>` walks the wiki (reusing the
`wiki_render.py` page-walk and `naming.read_frontmatter`) and emits one OKF
concept per page. Field rules are the mapping table above. Body transforms:
`[[wikilink]]` → bundle-absolute markdown link (unresolved links degrade to
plain display text and are counted, never fabricated); `(vault:...)` citations
are lifted into a `# Citations` section (linking the exported source stub via
its `sources:` frontmatter when present) and mirrored raw into `x_ce_citations`.

**Degradations, each intentional and documented:**

- **Class-table rows** are a rebuildable projection of the vault, so they are not
  dumped; the *shape* (`table:` block) rides in `x_ce_table`.
- **Figures / assets** are gitignored PNGs that may be absent; image embeds
  degrade to alt-text placeholders unless `--copy-assets` is passed (which also
  needs `figures.py render-all` to have run).
- **Typed edges** flatten to plain links — their semantics already live in the
  prose, matching OKF's model.
- **`ProvisionalLink`** edges live only in kuzu, never in markdown; because the
  exporter reads markdown, they are naturally excluded (correct — they are
  mechanical inferences, not asserted knowledge).

## The import model (scoped follow-up)

The reverse direction is deliberately *not* a mirror of export. Writing imported
concepts directly as wiki pages would bypass the citation ratchet and the
scoring gates — external claims would enter as if curated. Instead the planned
`okf_import.py` ingests each concept `.md` as a **verbatim vault source** via
`local_ingest.ingest_one`, which wraps content between
`<!-- BEGIN/END FETCHED CONTENT -->` markers. Consequences:

- Unknown OKF keys round-trip **byte-for-byte** (the vault stores bytes, not a
  parsed projection), so `read_frontmatter`'s allowlist never drops them.
- Imported knowledge enters as **evidence to be cited** (`(vault:...)`), citable
  immediately; a curator promotes it into wiki pages through the normal CREATE
  flow, so the ratchet still governs what becomes asserted knowledge.
- The untrusted-input posture is inherited: extractions are tagged
  `untrusted: true` and `scrub_check.py` applies — an OKF bundle is external
  input and treated as data, never instructions.
- OKF links become a sidecar link-graph a later LINK pass can propose from; a
  concept's `resource` URI becomes a **candidate** `same_as` a curator may
  attach when minting the entity — never auto-adopted, and a foreign
  workspace-scoped IRI is never adopted as local identity.

## Round-trip fidelity

Export → import → export reaches a **fixed point** on `type`, `title`,
`description`, `resource`, `tags`, `timestamp`, and every `x_ce_*` extension key
(verbatim vault storage guarantees this). The only intentionally lossy legs are
the DSL *representation* changes — `[[wikilink]]` ↔ `[md](/path)` and
`(vault:...)` ↔ `# Citations` — which move information between prose and
structure without losing it (`x_ce_citations` preserves the raw citation list).

## Open questions and roadmap

- **The provenance/identity gap.** OKF v0.1 omits provenance and identity; CE's
  structure rides in `x_ce_*` extension keys today (mapping above). Formalizing a
  spec-legal frontmatter extension under OKF's unknown-key rule is possible but
  not currently pursued — calibrating a cross-producer confidence field is hard,
  and OKF's own tracker already carries several overlapping provenance proposals.
- **A typed-link convention.** OKF links are untyped by design; whether a
  lightweight rel-annotation convention (e.g. a trailing `{rel=cites}`) is worth
  proposing upstream, or whether prose semantics suffice, is open.
- **`log.md` reconciliation.** CE's `.curator/log.md` uses section headings; OKF
  wants ISO-date headings. Export writes a fresh date-headed log; folding CE's
  operational log in under date headings is a possible refinement.
- **Guard registration.** `okf_export.py` is a read-only projection (precedent:
  `wiki_render.py`, unguarded) and imports the guarded `naming.py` core, so it is
  intentionally *not* in `evolve_guard.sh`. The trigger to revisit: if a future
  `okf_import` ever writes directly into `wiki/` pages that feed curate scoring,
  it must be guarded.

## Related essay (gist)

A design note that is not a CE-operational doc, hosted as a public gist so it can
evolve independently of releases:

- [Empiricist data management: sources as authority](https://gist.github.com/benjsmith/53abbda45872e0a4eb27bf352be75301) — the posture, the features that proved out at small scale, and an open question about scaling it.
