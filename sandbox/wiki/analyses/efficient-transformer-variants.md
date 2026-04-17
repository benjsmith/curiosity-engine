---
title: "[ana] Efficient Transformer variants: FlashAttention, Reformer, Mixtral"
type: analysis
created: 2026-04-17
updated: 2026-04-17
sources: [20260416-234959-local-flashattention-dao-2022.md.extracted.md, 20260416-234959-local-reformer-kitaev-2020.md.extracted.md, 20260416-234959-local-mixtral-jiang-2024.md.extracted.md, 20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md]
---

# Efficient Transformer variants: when each one dominates

The original [[transformer]] of Vaswani et al. replaced recurrence with scaled dot-product [[self-attention]], enabling parallel training and state-of-the-art translation at a fraction of prior cost (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). But attention computes `softmax(QK^T / sqrt(d_k)) V` over an L x L matrix, so both compute and memory scale quadratically with sequence length L. Three subsequent lines of work attack this bottleneck from different angles: [[flashattention]] treats attention as an IO problem, [[reformer]] approximates attention with locality-sensitive hashing, and [[mixtral]] sidesteps the FFN parameter wall with sparse [[mixture-of-experts]] routing. Each dominates a different operating point.

## FlashAttention: exact, IO-aware, compute-bound regime

FlashAttention reframes the attention bottleneck. On an A100, HBM bandwidth is ~1.5-2.0 TB/s while on-chip SRAM delivers ~19 TB/s, so materializing the N x N attention matrix in HBM dominates wall-clock time despite modest FLOPs (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). The algorithm tiles Q, K, V into SRAM blocks, streams an online softmax across tiles, and never writes the full S or P matrix to HBM. The output is bit-for-bit identical to standard attention, but HBM traffic drops from O(N^2 + Nd) to O(N^2 d^2 / M), yielding 2-4x wall-clock speedups and roughly 10-100x memory savings on long sequences (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). FlashAttention dominates when you want exact attention and sequence length is in the regime where attention is memory-bound (roughly 1k-16k tokens on current GPUs). It is a drop-in replacement with no accuracy tradeoff, which is why it has become the default kernel in PyTorch and vLLM.

## Reformer: approximate, sub-quadratic, very long context

Reformer attacks the asymptotic cost rather than the constant factor. LSH attention buckets queries and keys by random projection so each query only attends to a small chunk of similar keys, giving O(L log L) complexity. Reversible residual layers eliminate per-layer activation storage, so depth N no longer multiplies memory (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). Together these fit a 12-layer model at sequence length 64k on a single 16 GB GPU and match full-attention perplexity on enwik8 with enough hash rounds. Reformer dominates where FlashAttention still cannot fit: [[long-context]] regimes of tens of thousands of tokens where even O(N^2) SRAM-tiled computation is infeasible or simply too slow. The tradeoffs are real — hash collisions require multiple rounds, short sequences see no speedup, and the approximation introduces variance that needs tuning.

## Mixtral: sparse compute across experts

Mixtral 8x7B takes a different axis entirely. Attention still runs as usual (with sliding window and grouped-query attention for a 32k context); the efficiency win comes from replacing each FFN with eight expert FFNs and a top-2 router, so each token activates only ~13B of 47B total parameters (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). Inference FLOPs match a 13B dense model while quality matches or exceeds dense 70B baselines on MMLU, code, math, and multilingual benchmarks. Mixtral dominates when the bottleneck is FFN compute at inference and you can afford the memory to host all experts. Memory bandwidth becomes the practical constraint since all 47B parameters must be resident or paged (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md).

## Tradeoffs at a glance

- Exact vs approximate: FlashAttention and the dense parts of Mixtral preserve exactness; Reformer trades a small amount of accuracy (controlled by hash rounds) for sub-quadratic scaling.
- Memory-IO vs FLOP savings: FlashAttention cuts HBM traffic without changing FLOPs; Reformer cuts FLOPs and activation memory; Mixtral cuts per-token FLOPs by sparse routing but inflates total parameter memory.
- Training vs inference: FlashAttention helps both forward and backward passes and enabled the first BERT-large MLPerf-beating run (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). Reformer's reversible layers mainly help training memory. Mixtral's MoE routing mainly helps inference cost at fixed quality.
- Short vs long context: at 1k-4k tokens FlashAttention is the clear winner; past 16k, Reformer-style sub-quadratic methods or sliding-window schemes become necessary; Mixtral is orthogonal and composes with either.

In practice modern systems stack these: a Mixtral-style MoE backbone with FlashAttention kernels and, for extreme contexts, approximate attention in the style of Reformer. Each paper picks a different layer of the memory hierarchy or parameter budget to exploit, which is why they coexist rather than supersede one another.
