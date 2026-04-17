---
title: [src] Title: LoRA: Low-Rank Adaptation of Large Language Models
type: source
created: 2026-04-12
updated: 2026-04-12
sources: [20260416-234959-local-lora-hu-2021.md.extracted.md]
vault_sha256: fdc1962a71dc0f47b97af8ad4f3d173c40d6a661fd64e568b1e52b993cf4f2bc
---

As pretrained language models have scaled to hundreds of billions of parameters, full fine-tuning for each downstream task has become increasingly impractical. Storing and serving a separate copy of a 175B-parameter model like GPT-3 for every task costs roughly 350 GB per task checkpoint in fp16, and switching between tasks at inference requires expensive parameter swaps. Parameter-efficient fine-tuning methods such as adapter layers (Houlsby et al. 2019), prefix tuning (Li and Liang 2021), and  (vault:20260416-234959-local-lora-hu-2021.md.extracted.md)
