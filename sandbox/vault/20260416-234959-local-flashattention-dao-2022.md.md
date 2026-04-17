### Title: FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness

### Authors: Tri Dao, Daniel Y. Fu, Stefano Ermon, Atri Rudra, Christopher Re (2022)

### arXiv id: 2205.14135

### Abstract and Introduction

Transformers have become the dominant architecture for language, vision, and multimodal modeling, but their self-attention layers remain a major bottleneck in both time and memory: the standard attention implementation has compute and memory complexity that is quadratic in sequence length N. Much prior work has attempted to reduce this cost by approximating attention with sparse, low-rank, or kernelized variants. While these methods can reduce FLOPs, they often do not translate into wall-clock speedups on modern accelerators, and they typically trade some accuracy for efficiency.

FlashAttention takes a different stance. The authors observe that on GPUs the dominant cost of attention is not arithmetic but memory input/output (IO): moving the N x N attention matrix between high-bandwidth memory (HBM) and on-chip SRAM is what actually costs most of the time and memory. Standard attention materializes the full softmax(QK^T) matrix in HBM. For N = 4096 and head dim 64, this is hundreds of MB of reads and writes per head per layer, dominating runtime despite the FLOPs being much lower than MatMuls in the MLP.

FlashAttention is an exact attention algorithm that is IO-aware: it tiles Q, K, V into blocks, loads blocks into SRAM, and computes attention and the softmax online without ever materializing the full attention matrix in HBM. Using an online softmax trick (a numerically stable streaming computation), FlashAttention computes the same output as standard attention bit-for-bit, but with O(N^2 d / M) HBM accesses rather than O(N^2 + Nd), where M is the SRAM size. Empirically this yields 2-4x wall-clock speedups and up to 10-20x memory savings, enabling training transformers on much longer sequences than was previously practical.

The paper introduces both FlashAttention (forward and backward) and block-sparse FlashAttention (exact attention with sparsity patterns). It demonstrates end-to-end speedups in training BERT (15% faster than the MLPerf record), GPT-2 (3x faster), and long-range transformers on Path-X and Path-256. The work has had outsized practical impact, becoming the de facto attention implementation in PyTorch, FlashAttention-2, and vLLM.

### Methods

Background on memory hierarchy. Modern GPUs (e.g., A100) have a two-level memory hierarchy: a large but relatively slow HBM (40-80 GB, about 1.5-2.0 TB/s bandwidth) and a much smaller but very fast on-chip SRAM (per-SM shared memory, tens of KB, about 19 TB/s on A100). Kernels whose runtime is dominated by HBM traffic are memory-bound; those dominated by compute are compute-bound. Standard attention is memory-bound for typical sequence lengths because materializing and softmaxing the N x N matrix requires O(N^2) HBM reads and writes.

Standard attention. Given Q, K, V in R^{N x d} with N the sequence length and d the head dimension, attention computes S = QK^T in R^{N x N}, P = softmax(S) row-wise, and O = PV. The textbook implementation materializes S and P in HBM. This incurs O(N d) reads of Q, K, V plus O(N^2) reads/writes of S and P, giving O(N^2 + Nd) HBM traffic. Memory cost is also O(N^2) for storing P, which is the main blocker on long sequences.

FlashAttention forward pass. The algorithm tiles Q into blocks of B_r rows and K, V into blocks of B_c rows. The outer loop iterates over K, V blocks; the inner loop over Q blocks. For each (Q_i, K_j, V_j) tile loaded into SRAM, the algorithm computes the block S_ij = Q_i K_j^T, applies an online softmax to update running statistics m_i (row max) and l_i (row normalizer), and accumulates the partial output O_i. The online softmax uses the identity softmax(x concat y) = rescale(softmax(x)) concat rescale(softmax(y)) with appropriate running max and sum updates, enabling exact streaming computation.

Block sizes are chosen so that Q_i, K_j, V_j, and the block O_i fit in SRAM simultaneously. On A100 with 40 KB/SM of usable SRAM, the authors use B_c = ceil(M / (4d)) and B_r = min(B_c, d). The algorithm writes the final O to HBM only once and never stores the full S or P. Total HBM traffic is O(N^2 d^2 / M) vs O(N^2 + Nd) for standard attention. For d = 64 and M ~ 100 KB, this is roughly a 10x reduction.

FlashAttention backward pass. The backward pass recomputes S and P on-chip from O, dO, and the saved statistics m, l rather than reading a stored P from HBM. This recomputation costs additional FLOPs but saves O(N^2) HBM traffic and O(N^2) memory. Because attention is memory-bound, the backward pass is faster in wall-clock time despite doing more arithmetic. The backward is derived by carefully tiling dQ, dK, dV updates so that no full N x N matrix is ever materialized.

Block-sparse FlashAttention. The authors also extend the algorithm to structured sparse attention patterns. Given a block-level sparsity mask M in {0,1}^{N/B x N/B}, block-sparse FlashAttention skips (Q_i, K_j) block pairs where M_ij = 0, yielding wall-clock speedups proportional to sparsity. Unlike most prior sparse-attention implementations, which were slower than dense attention on GPU, block-sparse FlashAttention achieves real speedups while remaining exact under the given mask.

Implementation. The authors implement FlashAttention as custom CUDA kernels integrated into PyTorch. Blocks are processed inside a single kernel launch per head (fused kernel) to avoid extra HBM round-trips. Numerical stability is ensured by standard max-subtract softmax with running statistics stored in fp32 even when inputs are fp16.

### Key Results

Micro-benchmarks on A100 40GB: FlashAttention is 3x faster than the PyTorch attention baseline on sequence length 1024 (d = 64, 12 heads, fp16), 4x faster at 2048, and fits into memory at lengths up to 64k tokens where baseline attention runs out of memory at ~8k. For block-sparse FlashAttention at 75% sparsity, speedups reach 2-4x over dense FlashAttention.

Training BERT-large: FlashAttention trains BERT-large (seq len 512) 15% faster than the MLPerf 1.1 reference, the first BERT-large training run to beat the MLPerf record using only algorithmic improvements. Wall-clock time to target MLM accuracy drops from 20.0 to 17.4 minutes on 8xA100.

Training GPT-2 medium (345M parameters, seq len 1024): FlashAttention provides a 3x speedup end-to-end versus the HuggingFace baseline and 1.7x versus Megatron-LM's fused attention, yielding equivalent perplexity (17.5) in 2.7 days rather than 9.5 days on 8xA100.

Long-range arena: FlashAttention enables training transformers on Path-X (seq len 16384) and Path-256 (seq len 65536), neither of which were tractable before due to memory. The model achieves 61.4% on Path-X (vs chance 50%) and 63.1% on Path-256, becoming the first transformer model to perform above chance on Path-256.

Memory footprint: for seq len 8192 and batch size 1 with 12 heads and d = 64, FlashAttention uses about 20 MB for attention state, versus about 2 GB for standard attention, a roughly 100x reduction.

### Conclusion

FlashAttention reframes attention efficiency as an IO problem rather than a FLOPs problem. By tiling Q, K, V into SRAM blocks and using an online softmax to compute exact attention without materializing the N x N matrix, it achieves 2-4x wall-clock speedups, fits into memory at sequence lengths an order of magnitude longer than standard attention, and recomputes the attention matrix in the backward pass to avoid quadratic memory. Because FlashAttention is exact, it is a drop-in replacement for standard attention and has seen rapid adoption. Beyond the immediate gains, the paper argues that IO-aware algorithm design should be a first-class consideration for deep learning primitives. Follow-up work (FlashAttention-2, FlashAttention-3) has since pushed the wall-clock utilization of A100 and H100 GPUs even closer to peak, and the techniques generalize to other memory-bound operators.

### References to related work

- Vaswani et al. 2017 "Attention Is All You Need"
- Rabe and Staats 2021 "Self-attention Does Not Need O(n^2) Memory"
- Child et al. 2019 "Generating Long Sequences with Sparse Transformers"
- Kitaev et al. 2020 "Reformer: The Efficient Transformer"
- Choromanski et al. 2020 "Rethinking Attention with Performers"
- Milakov and Gimelshein 2018 "Online Normalizer Calculation for Softmax"
