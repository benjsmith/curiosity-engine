---
title: "[con] Fine-tuning"
type: concept
created: 2026-04-17
updated: 2026-04-17
sources: [20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md, 20260416-234959-local-lora-hu-2021.md.extracted.md, 20260416-234959-local-dpo-rafailov-2023.md.extracted.md, 20260416-234959-local-bert-devlin-2018.md.extracted.md]
---

Fine-tuning = umbrella. Take model from [[pre-training]], keep training on narrower data/objective. Many flavors, same recipe: init from checkpoint, grad-descent on new loss. Flavors diverge on what data, what loss, what params move.

(a) Supervised FT, classical task-specific. [[bert]] era: freeze arch, add one output head per task, backprop through all params, 2-4 epochs lr 2e-5 to 5e-5 — GLUE, SQuAD, NER all fit this mold (vault:20260416-234959-local-bert-devlin-2018.md.extracted.md). [[t5]] generalizes: cast every task as text-to-text, same xent loss, task prefix disambiguates (vault:20260416-234959-local-t5-raffel-2019.md.extracted.md). Fine-tune once per task, ship a copy per task. Works but stores full model per task.

(b) Instruction tuning + [[rlhf]]. [[instructgpt]] three-stage: (1) SFT on labeler demos of desired behavior, 13k prompts; (2) reward model on 33k ranked comparisons; (3) PPO against RM with KL-to-SFT penalty (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). Result: 1.3B InstructGPT beats 175B GPT-3 on human prefs. Instruction tuning alone (stage 1) already huge jump; RLHF polishes alignment.

(c) Preference FT. [[dpo]] collapses stages 2+3: derive closed-form map between reward and KL-constrained optimal policy, substitute into Bradley-Terry likelihood, get a plain binary cross-entropy on log-prob ratios vs a frozen reference — no RM, no sampling, no PPO (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). Matches or beats PPO-RLHF on summarization and dialogue, far more stable. Spawned IPO/KTO/ORPO family.

(d) Parameter-efficient FT. [[lora]]: freeze W0, learn low-rank B A update such that effective weight is W0 + BA, rank r << min(d,k) (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). For GPT-3 175B, 10000x fewer trainable params, 3x less GPU memory, matches full FT. BA merges into W0 at inference — zero latency penalty. Swap tiny (B,A) pairs over a shared frozen backbone to serve many tasks. Foundation of PEFT, QLoRA, diffusion adapters.

Tradeoffs: full FT maximal capacity but storage-heavy per task. SFT+RLHF strong alignment but expensive label pipeline, reward hacking risk. DPO cheaper, stabler, but tied to offline pref data. LoRA cheap + composable, slight ceiling vs full FT on some regimes. Modern stack usually combines: pre-train, SFT, then DPO or RLHF, often with LoRA at each stage.
