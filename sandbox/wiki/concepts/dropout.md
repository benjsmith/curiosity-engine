---
title: "[con] Dropout regularization"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-dropout-srivastava-2014.md.extracted.md
  - vault/20260416-234959-local-adam-kingma-2014.md.extracted.md
  - vault/20260416-234959-local-resnet-he-2015.md.extracted.md
  - vault/20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md
---

Dropout = stochastic [[regularization]] trick. Srivastava, Hinton, Krizhevsky, Sutskever, Salakhutdinov 2014 (vault:20260416-234959-local-dropout-srivastava-2014.md.extracted.md). Core move: during training, zero each unit independently with prob 1-p (keep prob p, typical p=0.5 hidden, p=0.8 input). Rescale at test (weights * p) or during train (inverted dropout, acts * 1/p). Masks sampled per example per step.

Why it works: kills co-adaptation. No unit can count on any other specific unit being alive, so each feature detector must be individually useful. Forces sparser, less entangled, more robust representations. Fights [[overfitting]] hard when params >> data.

Ensemble view: training explores 2^n thinned subnetworks (n droppable units). Test-time weight scaling approximates the geometric mean of all those subnets — cheap implicit [[ensemble-methods]] inside one model. For single sigmoid/softmax layer the scaling rule is exact; for deep nets it is a strong approximation.

Results at pub time: MNIST 0.79% err (SOTA), CIFAR-10 14.98% -> 12.61% -> 9.32% (+aug), CIFAR-100 43.48% -> 37.20%, TIMIT 23.4% -> 21.8%, ImageNet top-5 48.6% -> 42.4% on AlexNet (vault:20260416-234959-local-dropout-srivastava-2014.md.extracted.md). Gains across vision, speech, text, compbio.

Implementation notes: pair with high LR (10-100x) and high momentum (0.95-0.99), add max-norm constraint ||w||_2 <= c for stability. Complementary to data augmentation. Less effective on conv layers — apply mainly at top fully-connected layers of convnets. Costs ~2-3x training epochs to converge.

Downstream: [[transformer]] uses residual dropout p=0.1 on every sub-layer output before the add-and-norm, plus dropout on embedding sums, critical for not overfitting WMT (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). Ablation in that paper shows dropout is one of the most important regularizers for the arch. [[bert]], GPTs, and most LLM pretraining inherit this residual-dropout pattern.

Interaction with optimizers: dropout injects extra gradient noise on top of minibatch noise, so stochastic optimizers must tolerate noisy objectives — [[adam-optimizer]] explicitly lists dropout as a motivating source of objective noise it is designed to handle (vault:20260416-234959-local-adam-kingma-2014.md.extracted.md). Adam + dropout + ReLU MLP on MNIST hits ~1.4% err.

Counterexample: [[resnet]] ImageNet training drops dropout entirely, leaning on batch-norm instead ("We do not use dropout, following Ioffe and Szegedy") (vault:20260416-234959-local-resnet-he-2015.md.extracted.md). Batch-norm's own regularizing noise plus deep residual structure made dropout redundant/harmful for that vision recipe — foreshadowing the norm-vs-dropout split between vision convnets and sequence transformers.

Descendants: DropConnect (drop weights not units), DropPath / stochastic depth (drop whole residual blocks), Zoneout (drop RNN state updates), variational dropout (same mask across timesteps), attention dropout. All ride the same thinned-ensemble intuition.
