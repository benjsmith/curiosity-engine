# Extension points

The module is layered exactly as PLAN §4 prescribes; each seam below
is a supported place to extend without forking the core.

## 1. Data sources (`AtlasDataSource`)

Implement `getScene` / `getItem` / `getExplanation`. Two styles:

- **Local**: build a `GraphIndex`, extend `LocalSceneSource` — the
  shared client-side pipeline does the rest (this is what the CE
  adapter and fixtures do).
- **Remote**: return ready-made `SceneData` from a server that runs
  the same pipeline near the data (`RemoteDataSource.url(base)` speaks
  the contract over fetch; `ScaledDataSource` is the reference
  implementation of a bounded server-side builder). Honour
  `request.budget` and the `AbortSignal`.

## 2. Layout adapters (`LayoutAdapter`)

Pure `(scene, ctx) -> LayoutResult`, seeded via `ctx.seed`, previous
layout available for continuity. Register by extending the `ADAPTERS`
map in `core/engine.ts` (a public registry is deliberate future work —
adding one today is a one-line core change). Use
`anchorAngle("type:<t>")` if the layout should keep the shared angular
geography.

## 3. Renderers (`SceneRenderer`)

`mount(canvas) / render(frame) / destroy()`. The `Frame` carries
scene + layout + interaction state + resolved theme; hit testing stays
in the core, so a renderer never needs to understand semantics. A
WebGL implementation slots in here (ADR-001 keeps this seam open).

## 4. Discovery classes and lenses

Class quotas, relation weights and type weights are lens-controlled
(`AtlasLens`) without code changes. New *classes* are a core change in
`scene/discovery.ts`: add the pool builder + a `DiscoveryClass` union
member; every candidate must carry a `reason` (tests enforce it).

## 5. Theme

`AtlasTheme.tokens` (bg / text / textMuted / line / accent /
aggregateFill / horizonBg) + per-type `palette`. Hosts can read their
CSS custom properties and pass them through (see the Switchbay
example); the CE adapter forwards the payload's palette automatically.

## 6. Events and commands

Hosts integrate exclusively via `AtlasEvent` (out) and
`AtlasController` (in) — search boxes, breadcrumbs, doc panels, split
chrome all live host-side. `serializeTrail`/`restoreTrail` is the
persistence seam (versioned JSON).

## 7. Curiosity Engine payload enrichment

`indexFromCEData` detects enriched payloads (edge types beyond
wikilink/depicts) and switches off its own co-citation derivation —
so the planned `wiki_render.py --atlas-edges` export (PLAN §14.3) can
land server-side without adapter changes.
