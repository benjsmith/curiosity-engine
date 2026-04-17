---
title: "[ent] Mixtral"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-mixtral-jiang-2024.md.extracted.md
  - vault/20260416-234959-local-llama-touvron-2023.md.extracted.md
  - vault/20260416-234959-local-gpt3-brown-2020.md.extracted.md
---

Mixtral 8x7B = sparse [[mixture-of-experts]] LLM. Jiang et al. 2024 @ Mistral AI, arXiv 2401.04088 (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Decoder-only [[transformer]]. 46.7B total params, 12.9B active per token (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md).

32 layers, d_model=4096, 32 heads, 8 KV heads for grouped-query [[self-attention]], head dim 128, FFN hidden 14336, vocab 32000 (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Sliding-window [[self-attention]] + [[rope]] base theta=1e6 gives 32K ctx (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Memory bandwidth bottleneck at serve time; MegaBlocks block-sparse kernels help, akin to [[flashattention-dao-2022]] IO-aware tricks (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md).

Each FFN swapped for [[mixture-of-experts]] block: n=8 SwiGLU experts, router G(x)=Softmax(TopK(x·W_g)), K=2, output sum_{i in TopK} G(x)_i · E_i(x) (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Router overhead tiny: 8·4096 params/layer. Decouples capacity from per-token FLOPs — dodges dense [[scaling-laws]] compute curve (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Lineage: Jacobs 1991, Shazeer 2017 sparsely-gated MoE, GShard, Switch Transformer (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md).

Mistral 7B / [[llama]] ancestry (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md): RMSNorm, SwiGLU, [[rope]], grouped-query [[self-attention]]. Pretrain 32K ctx multilingual EN/FR/DE/IT/ES + code, causal LM loss (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md).

Benchmarks crush [[llama]]-2 70B at 1/5 active params: MMLU 70.6 vs 69.9, ARC-C 66.0 vs 54.9, GSM8K 74.4 vs 63.2, MATH 28.4 vs 13.8, HumanEval 40.2 vs 32.2 (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Matches [[gpt3]]-3.5 70.0 MMLU (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md). French MMLU 62.5 vs 45.0 [[llama]]-2 70B. Passkey retrieval near-perfect across full 32K (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md).

Instruct variant: SFT + DPO. MT-Bench 8.30, beats [[llama]]-2-70B-chat 6.86, competitive w/ GPT-3.5 Turbo, Claude-2.1, Gemini Pro (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Less bias on BBQ/BOLD than [[llama]]-2 70B (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md).

Router analysis: no topical specialization across arXiv/GitHub/PubMed/StackExchange layers 0/15/31 on The Pile — routes syntactic not semantic, consecutive tokens sticky, uniform expert usage = healthy load balance (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Auxiliary load-balance loss prevents expert collapse. Apache-2.0 weights. Spawned Mixtral 8x22B. Proof sparse [[mixture-of-experts]] viable at instruction scale vs dense [[llama]], [[gpt3]].

See [[efficient-transformer-variants]] — MoE vs exact-attention efficiency.
