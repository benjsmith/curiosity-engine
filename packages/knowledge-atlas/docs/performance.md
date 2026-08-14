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

Pointer hit testing uses a uniform screen-space grid rebuilt with each
projected layout. A hover probe inspects only neighbouring cells rather
than linearly scanning all visible marks, including in the 10k overview
envelope. Directional keyboard navigation remains a deliberate O(n)
operation because it is infrequent.

The line-free minimap caches its whole-graph node field. Camera motion
copies that small bitmap and redraws only the viewport box. At very high
density it deterministically samples to a screen-area budget and reduces
dot radius/opacity; its draw cost therefore follows minimap pixels rather
than corpus size. Classic uses the same strategy, invalidating the cache
only while D3 physics is moving or when size/theme changes.

## Memory

The engine holds one scene + two layouts (current/previous) + the
trail. Local sources hold their GraphIndex (items + adjacency): the
400-node fixture ≈ a few MB; the scaled source materialises only the
focus neighbourhood (a few hundred nodes) per scene and discards it.

## Traversal at cloud scale (the 100M-doc question)

> Historical design note: this is the `curiosity-cloud` /
> `switchbay-cloud` scaling analysis from iteration 8. The current
> local Atlas uses a pure reversible camera pan plus minimap for direct
> manipulation; `LensTraversal` remains an experimental remote-corpus
> primitive rather than the default pointer gesture.

Iteration 8 asked: if the corpus is a 100-million-doc curiosity-cloud
index served by switchbay-cloud, and the renderer is a PWA on a
laptop, how does a lens-drag from the extremes stay interactive? The
answer is that **nothing in the client scales with corpus size — only
with the gesture**, and three separate limiters guarantee it.

### What a traversal actually costs

A drag never moves documents; it moves a *scene cursor*. The pipeline
per real step is unchanged from any refocus:

| stage | where it runs (cloud corpus) | cost |
|---|---|---|
| harvest + rank + classify | server, beside the index | bounded by `40 × maxNodes` visits, not corpus |
| shell rollups (counts per type per log₁₀ band) | server, **precomputed** | O(1) lookup per scene; refreshed on ingest |
| SceneData over the wire | network | ~30–60 KB (460 nodes / 40 aggregates / 900 edges, JSON) |
| layout + draw | client | ≤ ~10³ primitives, 1–3 ms/frame |

So a scene step is ~25 ms of server pipeline + one small payload +
client work that already runs at 60 fps. The only way scale could
hurt is *frequency* — a raw drag could demand hundreds of steps per
second. The three limiters:

1. **Commit rate limit** (`COMMIT_INTERVAL_MS = 400`): at most 2.5
   real scenes/sec regardless of gesture speed. Worst-case sustained
   load on switchbay-cloud is therefore 2.5 scene builds/sec/user and
   ≤ 150 KB/sec/user of bandwidth — roughly one Google-Maps pan.
   Commits are also *coalesced*: only the newest lens target matters,
   so a slow response simply drops intermediate steps (last-write-wins,
   same as map tile fetching).
2. **Flow speed limit** (`MAX_DOCS_PER_SECOND = 250k`): the *display*
   rate of corpus passing through the lens is capped, so the odometer,
   streak intensity, and the number of shells a full-strength flick
   can cross per second are bounded. At 250k docs/sec a 100M-doc
   corpus takes ~7 minutes of continuous max-speed dragging to cross —
   deliberate: the outer shells are for *steering*, not for scrubbing
   the whole corpus, and deeper jumps belong to click-to-jump and
   search.
3. **The motion abstraction**: between commits the renderer draws
   ~12–84 streaks + one text caption — constant cost, deterministic
   (no wall clock), independent of what the flow *represents*. The
   perceived speed is decoupled from the data rate; the scene beneath
   dims rather than thrashes.

### Server-side notes for switchbay-cloud

- The per-shell totals the client needs (`shellTotalsFromScene` today
  derives them from the scene) are exactly the **type × log₁₀-band
  rollups** a corpus index maintains cheaply at ingest; serving them
  costs one cached row per focus, and staleness of minutes is
  invisible (they gear the traversal; they don't name documents).
- Predictive prefetch is the one worthwhile addition: the drag vector
  names the sector and depth of the *next* lens target, so the server
  can begin the next harvest during the current 400 ms window,
  hiding effectively all latency below ~400 ms RTT+build.
- At 100M docs the harvest itself must run against an adjacency store
  with O(degree) neighbour reads (any graph DB / adjacency-list KV
  qualifies — this is what kuzu gives CE locally); the visit cap
  makes the per-scene read budget ~18k edge reads worst-case,
  millisecond-range for a warm store.

### Client memory

Unchanged by corpus size: one scene + two layouts + the trail. A
traversal adds one `LensTraversal` (a dozen numbers) and zero
retained frames. A 100M-doc corpus and a 400-doc wiki cost the PWA
the same RAM.
