---
title: "[con] Self-Attention"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md
  - vault/20260416-234959-local-flashattention-dao-2022.md.extracted.md
  - vault/20260416-234959-local-reformer-kitaev-2020.md.extracted.md
---

Self-attention = token mix op. Each pos attends all pos same seq (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Core of [[transformer]], replaces recurrence + convolution entirely (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md).

Mechanism: project input to Q, K, V via learned linear maps (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Score = softmax(QK^T / sqrt(d_k)) V (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Sqrt(d_k) scale stops softmax saturation at large d_k (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md).

Multi-head: h=8 parallel heads, d_model=512, d_k=d_v=64, concat out (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Single-head worse by 0.9 BLEU on WMT en-de (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Masked variant preserves autoregressive for causal LM like [[gpt3]] (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Bidirectional feeds [[bert]] encoder.

Cost = O(n^2) seq len N (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Memory wall: standard impl materializes full N x N softmax matrix in HBM (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). For N=4096 head-dim 64 = hundreds MB HBM traffic per head per layer, memory-bound not compute-bound (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

Fix 1 exact: [[flashattention]] (Dao 2022) tiles Q,K,V into SRAM blocks, online softmax, never materialize N x N (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). HBM traffic O(N^2 d^2 / M) vs O(N^2 + Nd) baseline, yields 2-4x wall-clock speedup + 10-20x memory cut (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Backward recomputes S,P on-chip from saved m,l stats, saves O(N^2) mem (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). BERT-large 15% faster than MLPerf, GPT-2 medium 3x over HuggingFace (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

Fix 2 approx: [[reformer]] (Kitaev 2020) swaps dot-product for LSH attn, O(L log L) cost (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Random projection hash buckets near-cosine vectors, Q tied to K so query shares bucket with own key (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Multi-round hashing cuts collisions (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Plus reversible residual layers recompute activations, peak mem near-linear in L (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Enables 64k seq len single 16GB GPU, matches full attn at 1.05 bpd enwik8 (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md).

Sinusoidal positional encoding injects order since attn is permutation-equivariant (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Enables parallel token compute + direct long-range gradient flow (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Foundation op for [[llama]], [[t5]], [[clip]], [[mixture-of-experts]]. No self-attn, no modern LLM stack.
