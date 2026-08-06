# ADR-001 — Renderer and geometry

Status: accepted (2026-08-06) · Deciders: atlas experiment session · PLAN.md AD-2/AD-3

## Context

The Knowledge Atlas needs a renderer for budget-bounded scenes
(≤ ~60 nodes + 12 aggregates + 120 edges + 40 labels per frame) that
embeds in two hosts: Curiosity Engine's dependency-free static
wiki-view (vanilla JS, vendored bundles, no Node at runtime) and
Switchbay's React PWA (whose Playwright e2e runs Chromium on
SwiftShader software GL). Candidate technologies: SVG (the current
D3 viewer), Canvas 2D, WebGL, WebGPU.

## Decision: Canvas 2D behind a `SceneRenderer` interface

- **The scene budget, not the corpus, bounds draw items.** At ≤ ~10³
  primitives per frame Canvas 2D sustains 60fps with headroom
  (measured: full-scene draw ≈ 1–3 ms on the harness; transitions
  never dropped below refresh in e2e). WebGL's batching advantage
  starts mattering one to two orders of magnitude later.
- **Text is the product.** Labels and counts carry most of the
  comprehension load (PLAN §1); Canvas 2D gives crisp, theme-aware
  text for free, where WebGL needs an SDF/atlas pipeline — the single
  hardest part of GPU graph renderers.
- **No GPU variance.** Switchbay e2e forces SwiftShader; CE users run
  everything from old laptops to headless VMs. Canvas 2D behaves
  identically everywhere; a WebGL renderer would need a fallback
  anyway — which would be… Canvas 2D.
- **Zero dependencies** (Switchbay house rule: no heavy frameworks;
  CE: vendored, self-contained bundles). The whole IIFE is 56 KB
  minified / 20 KB gzip including the engine and CE adapter.
- **Escape hatch kept.** The renderer is a `SceneRenderer`
  implementation consuming `Frame` objects; hit testing lives in the
  core, not the renderer. A WebGL renderer can be added without
  touching the engine if P-series measurements ever show Canvas as
  the bottleneck. They currently show scene *build* (pipeline) cost
  dominating draw cost, so GPU work would optimise the wrong stage.

## Geometry

Four interchangeable, seeded, pure layout adapters (PLAN §8):
`force` (d3-force, CE-viewer constants, P0 parity), `focus`
(concentric rings + stable per-type angular sectors — the default),
`hyperbolic` (same sector assignment through a Poincaré-style
`tanh` radial compression; P3 comparison), `adaptive` (topology-routed;
P5). d3-force is the only layout dependency (~25 KB, deterministic
seeded LCG since v2); the other three are closed-form math.

Angular geography comes from landmark anchors: a pure hash of the
landmark id (golden-angle strided), so a type's direction is stable
across scenes, sessions, layouts and hosts — the mental-map anchor the
Louvain treemaps lacked.

## Consequences

- Renderer stays swappable; `Frame` is the compatibility surface.
- Label collision is greedy AABB in score order (ported from the CE
  viewer's auto-label logic) — O(n²) in labels, capped at maxLabels.
- Canvas hit testing is done in the core against layout positions
  (linear scan at budget scale), keeping renderers "dumb".
- If a future host needs >10³ visible items per frame, that is a
  scene-budget smell before it is a renderer smell.
