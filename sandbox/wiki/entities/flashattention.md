---
title: "[ent] FlashAttention"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-flashattention-dao-2022.md.extracted.md
  - vault/20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md
  - vault/20260416-234959-local-reformer-kitaev-2020.md.extracted.md
  - vault/20260416-234959-local-bert-devlin-2018.md.extracted.md
---

FlashAttention = IO-aware exact attention algorithm. Dao, Fu, Ermon, Rudra, Re 2022, arXiv 2205.14135 (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Drop-in replacement for standard [[self-attention]] in the [[transformer]] stack (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Same output bit-for-bit, not an approximation (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Scaled dot-product attention = softmax(QK^T / sqrt(d_k)) V from [[attention-is-all-you-need]] primitive (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md).

Key insight: on GPU, attention is memory-bound, not compute-bound (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Standard impl materializes the N x N softmax(QK^T) matrix in HBM => O(N^2) reads/writes dominate runtime (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). FlashAttention reframes as [[gpu-memory-hierarchy]] problem: HBM = 40-80 GB @ ~1.5-2.0 TB/s (slow, big), on-chip SRAM = tens of KB/SM @ ~19 TB/s (fast, tiny) (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

Mechanism: tile Q into B_r-row blocks, K/V into B_c-row blocks, load tiles into SRAM, fused CUDA kernel computes S_ij = Q_i K_j^T on-chip (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Online softmax (Milakov-Gimelshein style) maintains running row-max m_i and normalizer l_i, rescaling partial outputs as new blocks stream in => exact softmax without storing full P (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). HBM traffic drops from O(N^2 + Nd) to O(N^2 d^2 / M), ~10x reduction at d=64 (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

Backward pass recomputes S, P on-chip from saved (m, l, O, dO) instead of reading stored P — trades FLOPs for HBM traffic, still faster wall-clock since memory-bound (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

Speedups: 3x vs PyTorch attention @ seq 1024, 4x @ 2048, fits 64k tokens where baseline OOMs at ~8k (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). End-to-end: [[bert]]-large trains 15% faster than MLPerf 1.1 record (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md), wall-clock 20.0 -> 17.4 min on 8xA100 (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). GPT-2 medium 3x faster, 2.7 days vs 9.5 days (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Path-X / Path-256 [[long-context]] become tractable for first time, 61.4% on Path-X, 63.1% on Path-256 (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Memory: ~100x reduction at seq 8192, ~20 MB vs ~2 GB (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

Contrast with approximate [[reformer]] LSH-attention: Reformer cuts FLOPs to O(L log L) via locality-sensitive hashing buckets but trades exactness and requires multiple hash rounds to reduce collision (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Reformer also pairs reversible residual layers to cut activation memory across depth N (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). FlashAttention keeps exact softmax + dense mask, wins via IO-aware kernel design (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Block-sparse FlashAttention extends to structured sparsity masks, achieves real GPU speedups unlike most sparse-attn impls (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

Impact: became the de facto attention kernel in PyTorch, vLLM, HuggingFace (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Spawned FlashAttention-2, FlashAttention-3 (Hopper/H100) (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Established IO-aware design as first-class principle for DL primitives (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md).

See analysis [[efficient-transformer-variants]] for FLASH/Reformer/MoE comparison.
