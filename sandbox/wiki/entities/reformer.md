---
title: "[ent] Reformer"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources: [20260416-234959-local-reformer-kitaev-2020.md.extracted.md, 20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md, 20260416-234959-local-flashattention-dao-2022.md.extracted.md]
---

Reformer (Kitaev, Kaiser, Levskaya 2020) is an efficient variant of the [[transformer]] that targets the quadratic memory and compute costs of standard [[self-attention]] so that sequences of tens of thousands of tokens become tractable on a single accelerator. The baseline architecture from Vaswani et al. scales as O(L^2) in attention and caches per-layer activations linearly in depth, which becomes infeasible at book- or image-level sequence lengths (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md).

Reformer makes three orthogonal changes. First, LSH attention replaces dense softmax(QK^T) with [[locality-sensitive-hashing]]-based bucketing: queries and keys are tied, hashed by random projection, sorted, and attended only within fixed-size chunks (plus a small overlap), cutting attention cost to O(L log L); multiple hash rounds are combined via a logsumexp correction to control collision variance (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Second, reversible residual layers (adapted from RevNet) interleave two streams so activations can be recomputed exactly during the backward pass, removing the O(N) depth-memory term. Third, chunked feed-forward computation splits the position-wise MLP along the sequence dimension to cap peak activation memory at L * d_ff.

On enwik8 at length 64k the Reformer matches full-attention quality at 1.05 bpd on a single 16 GB GPU, and scales to imagenet64 generation with comparable bits/dim. These techniques anchor the [[long-context]] research line and are cited directly by later IO-aware approaches such as [[flashattention]], which contrasts its exact, memory-hierarchy-aware tiling against prior sparse/low-rank approximations like Reformer (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

See analysis [[efficient-transformer-variants]] for cross-method comparison.
