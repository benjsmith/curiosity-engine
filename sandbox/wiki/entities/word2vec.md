---
title: "[ent] Word2Vec"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources: [20260416-234959-local-word2vec-mikolov-2013.md.extracted.md, 20260416-234959-local-bert-devlin-2018.md.extracted.md, 20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md]
---

Word2Vec is the foundational word-embedding model introduced by Mikolov, Chen, Corrado, and Dean in 2013. Two log-bilinear architectures compose it: Continuous Bag-of-Words (CBOW), which predicts a center word from averaged context embeddings, and Skip-gram, which predicts surrounding context words from a single center word. Both strip out the expensive nonlinear hidden layer of earlier neural language models, making it feasible to train on 6 billion tokens in under a day on commodity CPUs (vault:20260416-234959-local-word2vec-mikolov-2013.md.extracted.md).

The big idea: dense low-dimensional vectors (typically 300-d) capture semantic and syntactic structure such that simple arithmetic works — vector("king") - vector("man") + vector("woman") lands near vector("queen"). Tractability comes from hierarchical softmax over a Huffman-coded vocabulary or from negative sampling, plus frequent-word subsampling with threshold t around 10^-5 (vault:20260416-234959-local-word2vec-mikolov-2013.md.extracted.md).

Word2Vec is the direct ancestor of the [[token-embedding]] tables used in every modern [[transformer]] stack. In "Attention Is All You Need," the input-to-model layer is still a learned embedding matrix with byte-pair-encoded vocabulary of ~37K tokens plus sinusoidal positional encodings added at the bottom of the encoder and decoder stacks (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). [[bert]] inherits this lineage too — its input is a sum of WordPiece token embeddings, segment embeddings, and learned positional embeddings, with a 15% masked-LM objective that can be viewed as a deep bidirectional successor to Skip-gram's shallow context-prediction task (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md).

Key contrast: Word2Vec vectors are static — one vector per word type — whereas [[contextual-embedding]] models like BERT produce per-token vectors conditioned on surrounding context. But the training recipe (predict-missing-word from context, self-supervised on raw text, scale with data and dimensionality) is unchanged. The 2013 paper's thesis that "quality depends primarily on data and dimensionality, not architectural sophistication" prefigures the [[scaling-laws]] narrative that dominates the transformer era.
