---
title: "[con] Chain-of-Thought"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md
  - vault/20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md
  - vault/20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md
---

Chain-of-thought (CoT) = prompt trick. Put rationale between input and output. Model copy pattern, spit steps, then answer. Wei 2022 primary (vault:20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md).

Method: ~8 few-shot exemplars, each with natural-language reasoning trace. PaLM 540B goes 17.9% -> 56.9% on GSM8K; +self-consistency -> 74.4%, beats fine-tuned [[gpt3]] with verifier (vault:20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md). Zero-shot variant: "let's think step by step" (Kojima).

Emergent. Dead below ~100B params; 400M model zero gain, 7B modest. Only wakes up at big scale. Ties to [[scaling-laws]]: loss power-laws in N, D, C (Kaplan 2020, alpha_N~0.076) explain why capability lives on a compute curve, and Wei explicitly cites Kaplan as the scaling-drives-capability premise (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). Small model + CoT = worse. Big model + CoT = phase change.

Runs atop [[transformer]] decoder-only LMs. No weight update, pure inference. Compatible with self-consistency (sample many chains, majority vote), tree-of-thought, program-of-thought.

But raw pretraining objective ≠ "follow user helpfully." CoT alone can hallucinate plausible-wrong chains. [[instructgpt]] / [[rlhf]] bridges the gap: SFT on demos, reward model on rankings, PPO on RM; 1.3B InstructGPT beats 175B GPT-3 on human preference, 100x fewer params, less toxic, more truthful (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). Modern reasoning models fuse both: scale (Kaplan) + CoT elicitation (Wei) + instruction/process reward tuning (Ouyang). Sparked o1/R1-style deliberative systems.

Limits: faithfulness gap (chain ≠ true computation), extra token budget, multi-step instruction following still breaks (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md).
