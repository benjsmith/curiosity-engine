---
title: "[con] Pre-training"
type: concept
created: 2026-04-17
updated: 2026-04-17
sources: [20260416-234959-local-bert-devlin-2018.md.extracted.md, 20260416-234959-local-gpt3-brown-2020.md.extracted.md, 20260416-234959-local-t5-raffel-2019.md.extracted.md, 20260416-234959-local-clip-radford-2021.md.extracted.md]
---

# Pre-training

Pre-training is the dominant paradigm for modern neural systems: learn broad, task-agnostic representations from enormous pools of unlabeled or weakly-labeled data, then adapt to downstream tasks via fine-tuning, linear probing, or in-context prompting. Under the hood it is a family of [[self-supervised-learning]] objectives, most of which ride on a [[transformer]] backbone. The interesting design axis is the objective itself — what signal gets squeezed out of raw data — and different choices yield qualitatively different models.

## (a) Masked language modeling (BERT)

[[bert]] pre-trains a bidirectional encoder by corrupting a fraction of input tokens and asking the model to reconstruct them. 15% of WordPiece tokens are selected; of those, 80% become `[MASK]`, 10% become a random token, and 10% are left unchanged, predicted via cross-entropy (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md). A secondary next-sentence-prediction (NSP) task provides pairwise signal. The result is a representation that conditions jointly on left and right context, and a single pre-trained model that fine-tunes to state-of-the-art on 11 NLP tasks with only a thin task-specific head.

## (b) Causal language modeling (GPT-3)

[[gpt3]] takes the opposite stance: an autoregressive decoder trained purely to predict the next token. Scaled to 175B parameters on roughly 300B tokens of filtered Common Crawl, WebText2, books, and Wikipedia, the same vanilla objective yields strong few-shot behavior without gradient updates at evaluation time (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md). The lesson is that causal LM, at sufficient scale, absorbs enough statistical structure to do in-context learning — prompting replaces fine-tuning.

## (c) Span corruption (T5)

[[t5]] systematically sweeps pre-training objectives under a unified text-to-text framework and finds that a span-corruption variant inspired by SpanBERT beats BERT-style single-token MLM: consecutive spans (mean length 3, 15% corruption rate) are replaced with sentinel tokens and the decoder reconstructs the dropped spans in order (vault:20260416-234959-local-t5-raffel-2019.md.extracted.md). This bridges MLM and seq2seq — an encoder-decoder that learns denoising with longer-range structure than single-token masking allows.

## (d) Contrastive vision-language (CLIP)

[[clip]] reframes pre-training around cross-modal alignment. Given a batch of N image-text pairs, an image encoder and text encoder are trained jointly so that matched pairs have high cosine similarity in a shared embedding space and the N^2 - N mismatches are pushed apart via symmetric InfoNCE cross-entropy (vault:20260416-234959-local-clip-radford-2021.md.extracted.md). Crucially, the authors found a generative captioning objective was 3-4x less sample-efficient than the contrastive one. The resulting model enables zero-shot classification via prompt embedding, matching a supervised ResNet-50 on ImageNet without any labels from it.

## (e) Distributional embedding (Word2Vec)

Before Transformers, [[word2vec]] established the distributional ancestor of today's pre-training: predict a word from its neighbors (CBOW) or its neighbors from a word (skip-gram), and the shallow network that emerges encodes surprisingly rich semantic geometry (vault:20260416-234959-local-word2vec-mikolov-2013.md.extracted.md). MLM, causal LM, and span corruption all inherit its core bet — that local co-occurrence statistics, accumulated at scale, compress into useful representations.

## Shared lens

Across these five objectives the recipe is the same: (1) pick a cheap, abundant self-supervisory signal (masked tokens, next tokens, corrupted spans, matched pairs, co-occurring words); (2) optimize cross-entropy or contrastive loss at scale; (3) reuse the frozen or fine-tuned representation. What varies is the induced structure — bidirectional vs. causal, single-modality vs. cross-modal, token-level vs. span-level — and that choice determines which downstream interface (fine-tune, prompt, linear probe, zero-shot) is natural.
