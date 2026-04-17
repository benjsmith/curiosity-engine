---
title: "[ent] GPT-3"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-gpt3-brown-2020.md.extracted.md
  - vault/20260416-234959-local-bert-devlin-2018.md.extracted.md
  - vault/20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md
  - vault/20260416-234959-local-adam-kingma-2014.md.extracted.md
  - vault/20260416-234959-local-lora-hu-2021.md.extracted.md
---

GPT-3 = Generative Pre-trained Transformer 3. Brown et al. 2020 @ OpenAI (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md). Decoder-only [[transformer]]. 175B params, 96 layers, d=12288, 96 heads.

Autoregressive LM. Next-token prediction sole objective. Contrast [[bert]] bidirectional MLM. Trained 300B tokens: Common Crawl (filtered) + WebText2 + Books1/Books2 + Wikipedia.

Key claim: few-shot learning via in-context prompting. No fine-tuning needed. Task spec + K demonstrations in prompt. Emergent capability at scale. Foreshadowed [[scaling-laws]] discourse, later refined by [[chinchilla]] compute-optimal ratios (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md).

Benchmarks: SOTA LAMBADA, Winogrande. Weaker NLI, reading comprehension vs fine-tuned [[bert]] (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md). Arithmetic emergent >13B.

Alternating dense + sparse attention. BPE tokenizer, 50257 vocab. Context 2048. Trained via [[adam-optimizer]] (vault:20260416-234959-local-adam-kingma-2014.md.extracted.md). Spawned [[instructgpt]], [[rlhf]] follow-ups. Kicked off foundation-model era. Inspired [[llama]] open replication. Parameter-efficient adaptation via [[lora]] low-rank adapters (vault:20260416-234959-local-lora-hu-2021.md.extracted.md).
