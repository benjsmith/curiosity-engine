---
title: "[ent] LLaMA"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-llama-touvron-2023.md.extracted.md
  - vault/20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md
  - vault/20260416-234959-local-gpt3-brown-2020.md.extracted.md
  - vault/20260416-234959-local-adam-kingma-2014.md.extracted.md
  - vault/20260416-234959-local-lora-hu-2021.md.extracted.md
---

LLaMA = Large Language Model Meta AI. Touvron et al. 2023 @ Meta (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md). Decoder-only [[transformer]] family: 7B / 13B / 33B / 65B params.

Design follows [[chinchilla]] compute-optimal recipe (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md): over-train small models on more tokens. 1-1.4T tokens all public sources. Contrast [[gpt3]] 300B tokens, 175B params.

Architectural tweaks: RMSNorm pre-norm, SwiGLU activation, rotary position embeddings ([[rope]]). Removed bias terms. Grouped-query attention in successor LLaMA-2.

Trained via [[adamw]] / [[adam-optimizer]] family (vault:20260416-234959-local-adam-kingma-2014.md.extracted.md). Context 2048. BPE SentencePiece 32K vocab. LLaMA-13B beats [[gpt3]]-175B most benchmarks at fraction cost (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md).

Released weights to researchers (later leaked). Catalyzed open LLM ecosystem: Alpaca, Vicuna, LLaMA-2 open weights, [[mixtral]] MoE variant. Spawned massive fine-tune ecosystem via [[lora]] low-rank adapters (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Standard base for [[rlhf]] alignment experiments.

[[llama]] dense = all params fire per token; [[mixtral]] sparse MoE fires only top-2 of 8 experts (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Same family roots, different compute-vs-memory tradeoff.

Training followed [[scaling-laws-kaplan-vs-chinchilla]] Chinchilla rules (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md).
