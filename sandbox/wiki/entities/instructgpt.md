---
title: "[ent] InstructGPT"
type: entity
created: 2026-04-17
updated: 2026-04-17
sources: [20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md, 20260416-234959-local-gpt3-brown-2020.md.extracted.md]
---

# InstructGPT

InstructGPT (Ouyang et al., 2022) is the OpenAI model family that introduced the now-canonical three-stage [[rlhf]] recipe used to align instruction-following language models, and it is the direct methodological foundation of ChatGPT. Starting from a pretrained [[gpt3]] base (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md), the authors fine-tune via: (1) **supervised fine-tuning (SFT)** on roughly 13k labeler-written and API-sourced prompt/response demonstrations; (2) **reward modeling (RM)** on a dataset of about 33k prompts with K=4 to K=9 ranked responses, trained by stripping the final embedding of the SFT model and attaching a scalar head; and (3) **reinforcement learning** against the RM using [[ppo]], with a PPO-ptx variant that mixes in a pretraining-gradient term to blunt the alignment tax on public NLP benchmarks (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md).

The headline result is that the **1.3B InstructGPT is preferred by human labelers over the 175B GPT-3** despite having roughly 100x fewer parameters, and the 175B InstructGPT wins against few-shot 175B GPT-3 about 85% of the time (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). Alignment gains are not limited to preference: on **TruthfulQA** InstructGPT produces truthful-and-informative answers roughly twice as often as GPT-3, and on **RealToxicityPrompts** it generates about 25% less toxic output when prompted to be respectful. Regressions on SQuAD, HellaSwag, and WinoGrande are minor, especially with PPO-ptx.

InstructGPT set the template that downstream work either refined or replaced. Anthropic's [[constitutional-ai]] keeps the SFT+RL skeleton but substitutes AI feedback (RLAIF) guided by written principles for much of the human harm labeling, explicitly citing Ouyang 2022 as the RLHF baseline it targets (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). [[dpo]] goes further and eliminates the RM + PPO stages altogether by deriving a closed-form policy-as-reward re-parameterization of the KL-constrained objective, collapsing InstructGPT's three stages into a single supervised-style preference loss (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). The broader landscape of post-training alignment variants is surveyed in [[alignment-methods-rlhf-cai-dpo]]. Limitations noted by the authors include labeler-agreement variance, residual alignment tax on some benchmarks, excessive hedging, and failure on some multi-step instructions.
