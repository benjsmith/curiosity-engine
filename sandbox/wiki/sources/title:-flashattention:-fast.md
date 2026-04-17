---
title: [src] Title: FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness
type: source
created: 2026-04-12
updated: 2026-04-12
sources: [20260416-234959-local-flashattention-dao-2022.md.extracted.md]
vault_sha256: 351d0f5d2a133e7de9f5aa76d620b6da783d1ae5aad4be3bf28765c86fedec69
---

Transformers have become the dominant architecture for language, vision, and multimodal modeling, but their self-attention layers remain a major bottleneck in both time and memory: the standard attention implementation has compute and memory complexity that is quadratic in sequence length N. Much prior work has attempted to reduce this cost by approximating attention with sparse, low-rank, or kernelized variants. While these methods can reduce FLOPs, they often do not translate into wall-clock s (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md)
