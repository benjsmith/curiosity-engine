---
title: "[con] Transformer"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md
  - vault/20260416-234959-local-bert-devlin-2018.md.extracted.md
  - vault/20260416-234959-local-gpt3-brown-2020.md.extracted.md
  - vault/20260416-234959-local-flashattention-dao-2022.md.extracted.md
  - vault/20260416-234959-local-reformer-kitaev-2020.md.extracted.md
  - vault/20260416-234959-local-llama-touvron-2023.md.extracted.md
---

Transformer = neural net arch. Pure [[self-attention]], no recurrence, no conv. Vaswani 2017 (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Encoder-decoder N=6 stack. Multi-head attn (h=8, d_model=512) + position-wise FFN + residual + layernorm. Scaled dot-product softmax(QK^T/sqrt(d_k))V. Sinusoidal pos embeddings. Decoder masked = autoregressive. Hit 28.4 BLEU WMT14 En-De in 3.5 days on 8 P100.

Key win: parallel across sequence. RNN bottleneck gone. GPU-friendly. Scales to billions (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md).

Flavors. Encoder-only = [[bert]]: bidirectional, MLM masks 15% tokens, [CLS]/[SEP], 110M/340M params, fine-tune for GLUE/SQuAD (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md). Decoder-only = [[gpt3]]: autoregressive, 175B params, 96 layers, 2048 context, few-shot via in-context learning, no gradient updates at eval (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md). Encoder-decoder = [[t5]]. [[llama]] decoder-only open-weight (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md). Also [[clip]] vision-lang, [[mixture-of-experts]] sparse.

Quadratic O(L^2) attn drives efficiency work. [[flashattention]] reframes as IO problem: tile Q,K,V into SRAM, online softmax, never materialize N×N in HBM. Exact, 2-4x speedup, 10-20x mem savings (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). [[reformer]] approx: LSH buckets by cosine sim, O(L log L), reversible residuals cut activation mem, 64k seq on one 16GB GPU (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Fine-tune: [[lora]]. Post-train: [[rlhf]], [[dpo]].

Dominant post-2017. Replaced LSTM/CNN in NLP. Vision too (ViT). Multimodal backbone. See [[scaling-laws]]. Optim: [[adam]]. Regularize: [[dropout]].

Efficient variants analyzed at [[efficient-transformer-variants]]. [[pre-training]] objectives span MLM, causal, span-corruption.
