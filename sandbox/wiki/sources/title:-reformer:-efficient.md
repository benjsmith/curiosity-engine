---
title: [src] Title: Reformer: The Efficient Transformer
type: source
created: 2026-04-12
updated: 2026-04-12
sources: [20260416-234959-local-reformer-kitaev-2020.md.extracted.md]
vault_sha256: f0a5b3775d9ed4b689d390e63953cba226967290d7be886c6bbf2f0bd293530e
---

arXiv: 2001.04451 Transformer models have become the de facto architecture for sequence modeling tasks across natural language processing, vision, and reinforcement learning. However, training large Transformers is extremely resource-intensive. A single-layer Transformer with sequence length L requires O(L^2) memory and compute for the self-attention operation, and storing activations for backpropagation scales with both the depth N and the sequence length. For sequences of tens of thousands of  (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md)
