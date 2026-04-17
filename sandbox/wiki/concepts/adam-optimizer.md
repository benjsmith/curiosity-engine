---
title: "[con] Adam optimizer"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-adam-kingma-2014.md.extracted.md
  - vault/20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md
  - vault/20260416-234959-local-dropout-srivastava-2014.md.extracted.md
  - vault/20260416-234959-local-resnet-he-2015.md.extracted.md
  - vault/20260416-234959-local-bert-devlin-2018.md.extracted.md
  - vault/20260416-234959-local-llama-touvron-2023.md.extracted.md
---

Adam = ADAptive Moment estimation. Kingma and Ba 2014 (vault:20260416-234959-local-adam-kingma-2014.md.extracted.md). First-order stochastic gradient method. Combines momentum (1st moment, exp moving avg of grad) with per-param adaptive learning rate (2nd moment, exp moving avg of squared grad). Bias-correct both moments since init at zero biases early steps.

Update rule: m_t = beta_1 m_{t-1} + (1-beta_1) g_t ; v_t = beta_2 v_{t-1} + (1-beta_2) g_t^2 ; theta_t = theta_{t-1} - alpha * m_hat / (sqrt(v_hat) + eps). Defaults: alpha=0.001, beta_1=0.9, beta_2=0.999, eps=1e-8. Memory 2N. Step size roughly bounded by alpha — trust-region intuition.

Inherits from AdaGrad (sparse grads) and RMSProp (non-stationary online). Beats plain [[stochastic-gradient-descent]] + Nesterov momentum on logistic regression, MLPs, and CNNs (vault:20260416-234959-local-adam-kingma-2014.md.extracted.md). Robust to hyperparams, works out of box on noisy objectives — including noise from [[dropout]] regularization (vault:20260416-234959-local-dropout-srivastava-2014.md.extracted.md).

De-facto optimizer for deep nets post-2015. [[transformer]] trains with Adam, beta_1=0.9, beta_2=0.98, eps=1e-9, plus warmup + inverse-sqrt decay (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Descendants [[bert]], [[gpt3]], [[llama]] use Adam or AdamW (decoupled weight decay). Contrast: [[resnet]] ImageNet training stuck with SGD + momentum 0.9, step-decay LR, weight-decay 0.0001 — vision convnets long resisted Adam, but Adam won everywhere sequence/scale matters.

Why it dominates: no tuning, handles sparse grads (embeddings, attention), stable on ill-conditioned losses, invariant to diagonal gradient rescaling. Failure modes: worse generalization than SGD on some vision benches, needs AdamW fix for proper weight decay, epsilon/beta_2 sensitive at very large [[scaling-laws]] scale. Still the default.

Concrete large-scale examples: [[bert]] trained with Adam lr=1e-4, beta_1=0.9, beta_2=0.999, L2 weight decay 0.01, 10k-step warmup + linear decay, 1M steps batch 256 (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md). [[llama]] flipped to AdamW with beta_1=0.9, beta_2=0.95, weight decay 0.1, grad-clip 1.0, cosine schedule with 2000 warmup steps across 7B-65B scale (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md) — beta_2=0.95 vs 0.999 is the telltale large-scale pretraining tweak.
