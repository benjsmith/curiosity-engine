---
title: "[ent] BERT"
type: entity
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-bert-devlin-2018.md.extracted.md
  - vault/20260416-234959-local-gpt3-brown-2020.md.extracted.md
  - vault/20260416-234959-local-adam-kingma-2014.md.extracted.md
  - vault/20260416-234959-local-word2vec-mikolov-2013.md.extracted.md
  - vault/20260416-234959-local-t5-raffel-2019.md.extracted.md
---

BERT = Bidirectional Encoder Representations from Transformers. Devlin et al. 2018 @ Google (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md). Encoder-only [[transformer]]. Two sizes: base 110M, large 340M params.

Pre-train objectives: masked language model (MLM) + next-sentence prediction (NSP). Mask 15% tokens. Bidirectional context via [[self-attention]] across full sequence. Contrast [[gpt3]] decoder-only left-to-right.

Corpus: BooksCorpus + English Wikipedia, 3.3B tokens. Trained via [[adam]] / [[adam-optimizer]] (vault:20260416-234959-local-adam-kingma-2014.md.extracted.md). WordPiece tokenizer, 30K vocab. Supersedes static [[word2vec]] predecessor embeddings with contextual representations (vault:20260416-234959-local-word2vec-mikolov-2013.md.extracted.md).

Fine-tune per downstream task: add task head, update all params. SOTA GLUE 80.5, SQuAD v1.1 F1 93.2, MultiNLI 86.7. Beat prior SOTA 11 NLP tasks.

[CLS] token for classification, [SEP] separates segments. Pre-LN variant. Influenced RoBERTa, DistilBERT, ALBERT. Seminal for [[transfer-learning]] NLP. Predecessor paradigm to [[scaling-laws]] era few-shot (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md). Foundation for [[sentence-embeddings]] + retrieval.

Pre-trains via MLM+NSP, central to [[pre-training]] taxonomy (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md). Task-specific [[fine-tuning]] set GLUE SOTA (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md).

Benchmarked against later encoder-decoder [[t5]]: T5's systematic sweep compared BERT-style masked LM vs span-corruption, span-corruption wins slightly (83.28 vs 82.87 GLUE), establishing BERT's MLM as the reference objective in the transfer-learning landscape (vault:20260416-234959-local-t5-raffel-2019.md.extracted.md).
