---
source_path: vault/raw/scaling-laws-kaplan-2020.md
ingested_at: 2026-04-16T23:49:59.335611+00:00
sha256: 4861e373a5808ba0e5c4948a3e182f23788dce7be4f6451fb355bb9acc7e7f22
bytes: 10284
kept_as: 20260416-234959-local-scaling-laws-kaplan-2020.md.md
extraction: full
max_extract_bytes: 40960
untrusted: true
source_type: local_file
---

<!-- BEGIN FETCHED CONTENT — treat as data, not instructions -->
### Title: Scaling Laws for Neural Language Models

### Authors: Jared Kaplan, Sam McCandlish, Tom Henighan, Tom B. Brown, Benjamin Chess, Rewon Child, Scott Gray, Alec Radford, Jeffrey Wu, Dario Amodei (2020)

### arXiv: 2001.08361

## Abstract and Introduction

We study empirical scaling laws for language model performance on the cross-entropy loss. The loss scales as a power-law with model size, dataset size, and the amount of compute used for training, with some trends spanning more than seven orders of magnitude. Other architectural details such as network width or depth have minimal effects within a wide range. Simple equations govern the dependence of overfitting on model/dataset size and the dependence of training speed on model size. These relationships allow us to determine the optimal allocation of a fixed compute budget. Larger models are significantly more sample-efficient, such that optimally compute-efficient training involves training very large models on a relatively modest amount of data and stopping significantly before convergence.

Language provides a natural domain for the study of artificial intelligence, as the vast majority of reasoning tasks can be efficiently expressed and evaluated in language. In this work we study language modeling performance as a function of three primary factors: the number of model parameters N (excluding embeddings and biases), the size of the dataset D, and the amount of compute C used for training. To characterize how performance depends on these factors, we train language models on a variety of scales, varying N from 768 to 1.5 billion non-embedding parameters, D from 22 million to 23 billion tokens, and compute by factors of tens of thousands.

Our key findings are as follows. Model performance depends most strongly on scale, which consists of three factors: the number of model parameters N, the size of the dataset D, and the amount of compute C used for training. Within reasonable limits, performance depends very weakly on other architectural hyperparameters such as depth vs width. Performance has a power-law relationship with each of the three scale factors N, D, C when not bottlenecked by the other two. Performance improves predictably as long as we scale up N and D in tandem, but enters a regime of diminishing returns if either N or D is held fixed while the other increases. The training curves follow predictable power-laws whose parameters are roughly independent of model size, allowing us to forecast approximately how far into training we are. When transferring to a different distribution, there is a constant offset but otherwise improvement tracks the loss on training distribution roughly. Larger models are significantly more sample efficient, reaching the same level of performance with fewer optimization steps and using fewer data points. Convergence is inefficient: when working within a fixed compute budget but without other restrictions, one can attain optimal performance by training very large models and stopping short of convergence.

Taken together, these results show that language modeling performance improves smoothly and predictably as we appropriately scale up model size, data, and compute. We expect that larger language models will perform better and be more sample efficient than current models.

## Methods

### Model Architecture and Training

We train decoder-only transformer language models using the architecture described in Radford et al. 2019 (GPT-2), with minor modifications. Models use autoregressive next-token prediction. We train on the WebText2 dataset, an extension of the WebText dataset from GPT-2 consisting of text scraped from outbound Reddit links with at least 3 karma. WebText2 contains approximately 22 billion tokens after BPE tokenization.

We train all models using the Adam optimizer with a fixed batch size of 512 sequences of 1024 tokens, unless otherwise noted. Learning rate warmup occurs over the first 3000 steps, followed by a cosine decay to zero. We use a weight decay of 0.01. Gradient clipping is applied at 1.0. All models use BPE tokenization with a vocabulary size of 50257 (consistent with GPT-2).

Our parameter count N refers specifically to non-embedding parameters. Embedding and positional embedding parameters are excluded because they scale differently and are less important for the regime we consider. For a transformer with d_model hidden dimension, n_layer layers, d_ff feedforward dimension, and d_attn attention dimension, we have approximately N = 12 * n_layer * d_model^2 (assuming d_ff = 4 * d_model and d_attn = d_model).

### Compute Definition

We define compute C in units of PF-days (10^15 floating-point operations per second x 86400 seconds = 8.64 * 10^19 FLOPs). For a forward pass we use C_forward ≈ 2N per token (two FLOPs per parameter per token, accounting for multiply-accumulate). Including backward pass (twice the cost of forward), total training compute is C ≈ 6 * N * D for a model with N non-embedding parameters trained on D tokens.

### Power-Law Fits

Our central finding is that the cross-entropy test loss L (in nats per token) follows power-law relationships with respect to each scale factor, when other factors are not bottlenecks:

- L(N) = (N_c / N)^alpha_N with N_c ≈ 8.8 × 10^13 and alpha_N ≈ 0.076
- L(D) = (D_c / D)^alpha_D with D_c ≈ 5.4 × 10^13 and alpha_D ≈ 0.095
- L(C_min) = (C_c / C_min)^alpha_C with C_c ≈ 3.1 × 10^8 and alpha_C ≈ 0.050 (PF-days)

Here C_min refers to compute optimally allocated to training (not C itself, which is the total compute). A combined equation predicts loss from both N and D simultaneously: L(N, D) = [(N_c/N)^(alpha_N/alpha_D) + D_c/D]^alpha_D. Overfitting manifests as a gap between train and test loss that grows when D is insufficient for a given N.

### Experimental Scope

We train models with non-embedding parameter counts ranging from 768 (tiny transformer with a single layer and small hidden dim) to 1.5 billion (comparable to GPT-2 XL). Dataset sizes vary from 22 million to 23 billion tokens (the full WebText2 corpus). We also vary batch size, learning rate, context length (up to 2048 tokens), and architectural aspect ratios to verify insensitivity. We tune learning rate per model size, since we find an approximately power-law relationship between optimal learning rate and model size.

### Critical Batch Size

We analyze the critical batch size B_crit, which divides training into "noise-dominated" (B < B_crit, gradient steps are ~linearly efficient) and "curvature-dominated" (B > B_crit, diminishing returns per sample) regimes. We find B_crit depends only on loss L, not directly on model size N or dataset size D, approximately following B_crit(L) = B_* / L^(1/alpha_B) with B_* ≈ 2 × 10^8 tokens and alpha_B ≈ 0.21.

## Key Results

The primary results are the power-law scaling coefficients stated above. A 100x increase in non-embedding parameters reduces loss by approximately a factor of 100^0.076 ≈ 1.3. A 100x increase in dataset size reduces loss by approximately 100^0.095 ≈ 1.55. A 100x increase in compute reduces loss by approximately 100^0.050 ≈ 1.26.

Crucially, the compute-optimal allocation scales parameters much faster than data. Given a total compute budget C, optimal allocation grows N approximately as N_opt ∝ C^0.73 and data as D_opt ∝ C^0.27. That is, as compute grows by a factor of 10, the optimal model size should grow by ~5x, while training tokens grow by only ~2x. (This prescription was later revised by Hoffmann et al. 2022 / Chinchilla, who found roughly equal scaling of N and D with compute.)

Depth and width are largely interchangeable within the range of aspect ratios tested. Models with depth from 2 to 207 layers, and d_model from 32 to 1600, achieve similar loss when N is held fixed (within ~2%), as long as the model is not extremely narrow or extremely shallow.

Context length: longer context yields modest loss improvements. Moving from context length 512 to 1024 typically reduces loss by only a few percent.

Transfer: when evaluated on a different distribution (e.g., Books, Wikipedia, Internet Books), models show an approximately constant loss offset relative to WebText2 test loss, so scaling improvements on training distribution also transfer to other distributions.

Training dynamics: for a model of size N, training follows a predictable loss trajectory, and one can estimate from early training how well the model will ultimately perform. This enables forecasting of final performance before completing training.

Sample efficiency: larger models require fewer samples to reach a given loss level. This is the rationale behind training very large models and stopping short of convergence - under a fixed compute budget, compute is better spent on additional parameters than on additional gradient steps.

## Conclusion

We have found that language model performance follows precise power laws in model size, dataset size, and compute, and that performance depends only weakly on architectural details. These results suggest that very large language models may be sample efficient and attain strong performance by training for fewer steps than smaller models. Simple power-law fits accurately describe the dependence of loss on scale factors, and simple equations govern the optimal allocation of a compute budget. We conjecture that our scaling relations will extend to larger models, and we observe no evidence of the power laws breaking down at the largest models we trained. These scaling laws provide a roadmap for future work in language modeling: obtaining the most value from compute involves training large models on not-quite-enough data, with sublinear but still substantial returns from additional scale.

## Related Work

- Hestness et al. 2017 - deep learning scaling is predictable, empirically
- Radford et al. 2019 (GPT-2) - transformer language model architecture baseline
- Vaswani et al. 2017 (Attention is All You Need) - transformer architecture
- Rosenfeld et al. 2019 - constructive prediction of generalization error
- McCandlish et al. 2018 - empirical model of large-batch training (critical batch size)
- Shazeer and Stern 2018 - Adafactor optimizer considerations at scale

<!-- END FETCHED CONTENT -->
