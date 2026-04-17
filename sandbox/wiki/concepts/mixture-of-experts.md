---
title: "[con] Mixture-of-Experts"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-mixtral-jiang-2024.md.extracted.md
  - vault/20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md
  - vault/20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md
---

Mixture-of-experts (MoE) = sparse conditional compute. Replace dense FFN in [[transformer]] block with N expert FFNs plus router (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Only top-k experts fire per token.

Router = small linear + softmax over experts. Top-k (k=1 or 2) gating. Load-balance aux loss prevents expert collapse. Total params huge, active params small per token. Decouples capacity from FLOPs.

[[mixtral]] 8x7B (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md) = 8 experts, top-2 routing per layer. 47B total, ~13B active. Matches/beats [[llama]] 70B dense on many benches at fraction inference cost. Open-weight MoE landmark. [[mixtral]] router picks 2-of-8 per token per layer (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md), proving MoE viable outside big-lab walls.

Tradeoffs: memory heavy (all experts resident VRAM), routing overhead, training instability, harder distill. Scaling favors MoE when compute-bound but memory-cheap. Interacts with [[scaling-laws]] - different frontier than dense.

Lineage: Shazeer 2017 sparse gating, Switch Transformer, GShard, GLaM, DeepSeek-MoE. Active params often fine-tuned with [[lora]]. Self-attn shared across experts (see [[self-attention]]).
