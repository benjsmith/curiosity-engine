---
title: "[ent] ResNet"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources: [20260416-234959-local-resnet-he-2015.md.extracted.md, 20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md, 20260416-234959-local-clip-radford-2021.md.extracted.md, 20260416-234959-local-dropout-srivastava-2014.md.extracted.md]
---

ResNet (Deep Residual Learning for Image Recognition, He et al. 2015) broke the depth barrier in convolutional nets. Before ResNet, simply stacking more layers caused a degradation problem: a 56-layer plain net had higher training error than a 20-layer one, not from overfitting but from optimization failure (vault:20260416-234959-local-resnet-he-2015.md.extracted.md). ResNet fixed this by reformulating each block as y = F(x) + x — a [[skip-connection]] that lets gradients flow directly past non-linear layers. If an identity mapping were optimal, solvers could trivially push the residual F(x) toward zero, rather than wrestling a stack of nonlinearities into reproducing x.

The identity shortcut adds zero parameters and zero compute. This is the lever that enabled 50-, 101-, and 152-layer networks to train stably end-to-end via SGD with [[batch-normalization]] after every convolution. Notably, He et al. dropped [[dropout]] entirely, relying on batch norm instead (vault:20260416-234959-local-resnet-he-2015.md.extracted.md) — a departure from the AlexNet-era regularization recipe Srivastava et al. had established (vault:20260416-234959-local-dropout-srivastava-2014.md.extracted.md).

On [[imagenet]], a 152-layer ResNet ensemble hit 3.57% top-5 error, winning ILSVRC 2015 classification, and ResNet-101 backbones swept COCO detection and segmentation. Critically, ResNet's residual-block pattern escaped vision: the [[transformer]] wraps every sub-layer (self-attention, feed-forward) in a residual connection followed by layer normalization (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md), and [[clip]]'s image encoder family includes ResNet-50 through ResNet-50x64 variants with attention pooling (vault:20260416-234959-local-clip-radford-2021.md.extracted.md). Residual learning is now the default scaffolding for deep nets of any modality.
