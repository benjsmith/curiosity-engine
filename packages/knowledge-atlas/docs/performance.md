# Performance characteristics

All numbers from the automated harness/e2e runs on 2026-08-06
(headless Chromium, software rendering, containerised Linux — i.e. a
*pessimistic* environment; real hardware is faster). Seed 42,
viewport 1280×800, default budget (60 nodes / 12 aggregates /
120 edges / 40 labels).

## Where time goes

| stage | typical cost | scales with |
|---|---|---|
| harvest (BFS, visit-capped) | < 5 ms | `40 × maxNodes` visits, never corpus size |
| ranking | < 5 ms | harvest size |
| MMR diverse selection | O(harvest × maxNodes) — the dominant term on dense graphs | budget² |
| discovery classes | moderate; contrast iterates all items of the *local index* | local index size |
| aggregation + landmarks + edges | < 5 ms | harvest size |
| layout: focus / hyperbolic / adaptive-routed | < 1 ms | scene size (closed-form) |
| layout: force | ~70 ms | 350 pre-warm ticks × scene size |
| canvas draw | 1–3 ms/frame | scene size (≤ ~10³ primitives) |

Measured scene builds: 22 ms warm on the 400-node workspace fixture,
25 ms on the 1,000,000-leaf procedural corpus (bounded materialise +
same pipeline — the number that proves the scaling contract), 194 ms
worst-case on the dense small-world stress fixture (K=8 ring + hubs;
MMR pairwise Jaccard and bridge-span checks dominate).

## Known hot spots and their fixes (when needed)

1. **MMR selection** — pairwise `neighbourJaccard` against every
   already-picked node. Fix: cap comparisons to the top-m picked, or
   precompute minhash signatures. Only matters on dense meshes.
2. **Contrast class** — iterates the whole local item index looking
   for shared-source pairs. Fine ≤ ~10⁴ local items; for cloud
   sources this moves server-side anyway (the pipeline runs wherever
   the index lives).
3. **Force layout pre-warm** — 350 synchronous ticks. It's the P0
   parity baseline, not the default; don't optimise.

## Frame budget

Transitions are a single 300 ms rAF-driven interpolation (100 ms under
reduced motion); when idle **zero** rAF work is scheduled (draws happen
only on interaction/scene events). Label placement is greedy AABB in
score order, capped by `maxLabels`; the text-measure cache makes
re-draws label-cost-free.

## Memory

The engine holds one scene + two layouts (current/previous) + the
trail. Local sources hold their GraphIndex (items + adjacency): the
400-node fixture ≈ a few MB; the scaled source materialises only the
focus neighbourhood (a few hundred nodes) per scene and discards it.
