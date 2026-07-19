# OKF-Provenance: a provenance & identity extension for the Open Knowledge Format

**Status:** proposal (CE-authored, targeting OKF v0.1 Draft). **License intent:**
Apache-2.0, to match OKF.

This document specifies **OKF-P**, a namespaced, fully OKF-compliant extension
that adds the four things OKF v0.1 [deliberately
omits](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md):
**provenance, confidence, identity, and shape**. It is drawn directly from
curiosity-engine's model (the citation ratchet, workspace-stable IRIs +
`owl:sameAs`, and declared per-class shapes — see
[`ce-as-edm.md`](ce-as-edm.md)) and is what CE's `okf_export.py` emits today
(under the interim `x_ce_*` prefix; this spec proposes the canonical names).

It is an **extension, not a competitor.** OKF's markdown-native grain already
matches CE; the right move is to contribute the missing epistemics upward, not
to fork a rival format. See [`okf-interop.md`](okf-interop.md) for the full
comparison.

## Design constraints (inherited from OKF)

1. **Spec-legal by construction.** OKF requires consumers to preserve unknown
   frontmatter keys and to not reject documents with unrecognised fields. So an
   extension is *any additional frontmatter under a reserved namespace* — a
   conformant OKF v0.1 consumer already round-trips it untouched.
2. **Optional and degrading.** A bundle with zero OKF-P keys is valid; a consumer
   that ignores OKF-P still reads the concept. Provenance enriches, never gates
   base readability.
3. **Namespaced.** All keys live under a single top-level `okfp` mapping (or, for
   producers preferring flat keys, an `okfp_`-prefixed set), so they never
   collide with base OKF or with other extensions.

## The `okfp` block

```yaml
---
type: Chemical
title: Aspirin
description: A salicylate drug used as an analgesic and antiplatelet agent.
resource: https://pubchem.ncbi.nlm.nih.gov/compound/2244
tags: [nsaid, analgesic]
timestamp: 2026-03-04T00:00:00Z
okfp:
  identity:
    iri: ce:chemical:pharma-kb:aspirin      # origin-scoped stable id
    same_as:                                  # owl:sameAs — never gates identity
      - pubchem:CID2244
      - wikidata:Q18216
    aliases: [acetylsalicylic acid, ASA]
  provenance:
    citations:
      - source: vault:merck-index.extracted.md
        resource: https://example.com/merck-index
        content_sha256: 9f2b…c41a               # pins the cited bytes
        quote: "irreversibly acetylates COX-1"  # optional supporting span
    asserted_by: curator                        # human | curator | agent | import
    method: schema-on-read-ratcheted
  confidence:
    level: high                                 # high | medium | low | scalar 0–1
    basis: multi-source                         # single-source | multi-source | inferred
  shape:                                        # per-type, local — not universal
    columns:
      - name: ic50
        type: real
        units: nM
        constraint: ">0"
        source_required: true
---
```

### `okfp.identity`

| Key | Meaning |
|---|---|
| `iri` | Origin-scoped stable identifier. Format is producer-defined (CE uses `ce:<class>:<workspace>:<slug>`). **Consumers MUST NOT adopt a foreign `iri` as local identity** — it names an entity in the *producer's* space. |
| `same_as` | List of `authority:id` pairs (`owl:sameAs` semantics). External canonical ids that link the concept to shared authorities but **never gate identity**, so upstream re-resolution can't orphan a reference. |
| `aliases` | Curated synonyms/codenames for the concept's subject. |

The base OKF `resource` field SHOULD carry the single most-canonical resolvable
URI (derivable from `same_as`); `okfp.identity` carries the full identity graph.

### `okfp.provenance`

The core contribution. Each `citations[]` entry binds a claim to a source:

| Key | Meaning |
|---|---|
| `source` | The provenance root. A `vault:` URI (append-only document, CE's source-of-truth tier) or any resolvable URI. |
| `resource` | Optional canonical URI for the cited asset. |
| `content_sha256` | Optional hash pinning the exact cited bytes — makes a citation *version-stable* even if the source later mutates. |
| `quote` | Optional verbatim span supporting the claim (the FTS-matchable evidence CE's ratchet checks). |

`asserted_by` records who made the assertion (`human` / `curator` / `agent` /
`import`); `method` names the discipline (CE emits `schema-on-read-ratcheted`).
This is enough for a consumer to reconstruct *why a claim is believed* — the
thing OKF v0.1 has no vocabulary for.

### `okfp.confidence`

An explicit, machine-readable confidence signal: an enum (`high`/`medium`/`low`)
or a `0–1` scalar, plus a `basis` describing how it was reached
(`single-source` / `multi-source` / `inferred`). Absent in base OKF.

### `okfp.shape`

Per-concept-**type** structural constraints, mirroring CE's U3 `table:` blocks:
typed `columns` with optional `units`, `constraint` (numeric bounds like `>0`,
`[lo,hi]`), and `source_required`. **Local and per-class — explicitly not a
universal ontology**, consistent with the [`ce-as-edm.md`](ce-as-edm.md) thesis
that shared vocabularies are the part LLMs made cheap to skip. A consumer MAY
enforce shapes; a producer MAY ignore them; neither is required.

## Mapping: CE export keys → OKF-P canonical keys

`okf_export.py` currently emits the interim flat `x_ce_*` keys (guaranteed safe
under OKF's unknown-key rule). This spec proposes the canonical namespaced form:

| Current export key | Proposed OKF-P key |
|---|---|
| `x_ce_iri` | `okfp.identity.iri` |
| `x_ce_same_as` | `okfp.identity.same_as` |
| `tags` (from `aliases`) / — | `okfp.identity.aliases` |
| `x_ce_citations` (raw `vault:` list) | `okfp.provenance.citations[].source` |
| `x_ce_type` | (subsumed — base `type` already free-form) |
| `x_ce_table` | `okfp.shape` |
| — (not yet emitted) | `okfp.confidence`, `citations[].content_sha256`, `citations[].quote` |

Migration is additive: a producer can emit both during a transition; a consumer
reads whichever it understands.

## Why this is the right shape

- **It closes OKF's stated gaps** (provenance + confidence) without touching the
  base spec — pure addition under the namespace OKF already promised to preserve.
- **It is proven, not speculative.** Every field corresponds to a mechanism CE
  already runs in production (the ratchet, the IRI minter, the shape checker), so
  the extension describes real behaviour rather than aspirational structure.
- **It keeps the empiricist posture.** Provenance and shape are local and
  falsifiable; identity compounds via `same_as` without a universal ontology —
  the same trade-offs [`ce-as-edm.md`](ce-as-edm.md) defends.

## Open questions

- **Claim-level vs concept-level provenance.** OKF-P attaches citations at the
  concept level. Inline claim-level binding (CE's `(vault:...)` marker sits next
  to the sentence) has no clean markdown-body home in OKF; a footnote convention
  or a `quote`-anchored mapping are the candidates.
- **Typed links.** Provenance is frontmatter; relationship *typing* is a separate
  gap (OKF links are untyped). A companion `okfp`-namespaced link convention is
  possible but out of scope here.
- **Upstream path.** Whether to propose `okfp` as-is, or to lobby for a subset
  (`provenance` + `confidence`) into base OKF v0.2, is a governance question for
  the OKF maintainers.
