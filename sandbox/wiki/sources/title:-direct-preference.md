---
title: [src] Title: Direct Preference Optimization: Your Language Model is Secretly a Reward Model
type: source
created: 2026-04-12
updated: 2026-04-12
sources: [20260416-234959-local-dpo-rafailov-2023.md.extracted.md]
vault_sha256: 50fe53c19c006379f71b07141fb4ba80a5979abab9ef32f6c8df86aa3d82d15f
---

Aligning large language models to human preferences has become a central problem in deploying capable, helpful, and harmless assistants. The standard pipeline, reinforcement learning from human feedback (RLHF), proceeds in three stages: (1) supervised fine-tuning (SFT) on instruction-following demonstrations, (2) training a reward model on pairwise human preference comparisons under a Bradley-Terry likelihood, and (3) optimizing the language model against the learned reward using policy-gradient (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md)
