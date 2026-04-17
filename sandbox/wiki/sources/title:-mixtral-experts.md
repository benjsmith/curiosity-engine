---
title: [src] Title: Mixtral of Experts
type: source
created: 2026-04-12
updated: 2026-04-12
sources: [20260416-234959-local-mixtral-jiang-2024.md.extracted.md]
vault_sha256: 924f91648dd7cc0eae012c4a2a6d22d1c51d5ddd7c1ddf4b165f2308321202bd
---

Mixtral 8x7B is a Sparse Mixture of Experts (SMoE) language model introduced by Mistral AI. It has the same architecture as Mistral 7B, with the major distinction that each feedforward layer is replaced by a block of 8 feedforward experts. At each layer, for every token, a router network selects two experts to process the current hidden state and combines their outputs as a weighted sum. Although each token sees only two experts at a time, the selected experts can differ at each timestep. As a r (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md)
