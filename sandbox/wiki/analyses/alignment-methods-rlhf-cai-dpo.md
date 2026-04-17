---
title: "[ana] Alignment methods: RLHF, Constitutional AI, DPO"
type: analysis
created: 2026-04-16
updated: 2026-04-16
sources: [20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md, 20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md, 20260416-234959-local-dpo-rafailov-2023.md.extracted.md]
---

# Alignment methods: RLHF, Constitutional AI, DPO

Three papers frame the modern landscape for aligning [[large-language-models]] with human intent: [[instructgpt]] (Ouyang 2022) (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md), [[constitutional-ai]] (Bai 2022) (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md), and [[dpo]] (Rafailov 2023) (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). Each inherits the pairwise-preference framing from Christiano 2017, but they differ sharply in who provides the signal and how the optimizer consumes it (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md).

## How each method works

**RLHF via InstructGPT.** The canonical recipe runs three stages: supervised fine-tuning on labeler demonstrations, a reward model trained on ranked comparisons, then PPO against the learned reward with a KL penalty (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). Ouyang et al. report the 1.3B InstructGPT beats 175B [[gpt3]] on human preference judgments, a 100x compression from alignment alone (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). A pretraining-mixture gradient (PPO-ptx) patches the alignment tax on benchmarks like SQuAD and HellaSwag (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). The SFT stage used ~13k prompts; the RM stage used ~33k ranked comparisons from ~40 contractors (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md).

**Constitutional AI.** Bai et al. replace human harm labels with a short written constitution of ~16 principles and let the model critique and revise its own outputs (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). The SL-CAI stage iterates critique/revise up to four times, sampling a different principle each round; the RL stage trains a preference model on AI-generated labels (RLAIF) and feeds it into PPO (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). A key trick is chain-of-thought reasoning inside the feedback model — harmless PM accuracy jumps from ~68% to ~77% with CoT (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). The resulting [[rl-cai]] model refuses evasively on ~4-5% of red-team prompts versus ~43% for the helpful+harmless [[rlhf]] baseline (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md).

**DPO.** Rafailov et al. derive a closed-form map between a KL-constrained reward-maximizing policy and the reward function itself: `pi*(y|x) = (1/Z(x)) * pi_ref(y|x) * exp(r(x,y)/beta)` (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). Inverting gives `r(x,y) = beta * log(pi*(y|x)/pi_ref(y|x)) + beta*log Z(x)`; substituting into the Bradley-Terry likelihood cancels `Z(x)` and yields a binary cross-entropy loss on log-probability ratios against a frozen reference (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). No reward model, no sampling from the policy, no RL loop (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md).

## What they share

All three assume a [[bradley-terry]]-style pairwise preference signal plus a KL tether to an SFT reference — implicit in DPO's beta, explicit in PPO's penalty (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). All three begin from an SFT checkpoint; DPO explicitly uses that checkpoint as `pi_ref` (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). All three ultimately optimize a scalar-reward surrogate — parametric in InstructGPT and CAI (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md), implicit in DPO (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md).

## Where they differ

The axes of variation are data source, optimizer, and transparency.

- **Data cost.** InstructGPT required ~40 contractors, ~33k ranked comparisons, and continuous labeler calibration (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). CAI reduces this to ~16 principles plus a helpful-only seed model (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). DPO keeps the human preference dataset but cuts the reward-model step entirely (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md).
- **Training stability.** PPO in both InstructGPT and CAI needs careful KL coefficients, advantage normalization, and rollout schedules, with reward hacking as a common failure mode (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). DPO reports stable fine-tuning on a single 8xA100 node in hours, roughly 3-5x faster than comparable PPO (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md).
- **Transparency.** The CAI constitution is human-readable (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md); RLHF reward models are opaque scalar predictors (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md); DPO's reward is an implicit log-ratio, auditable but tied to the current policy (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md).

## The evolving landscape

RLHF established the three-stage pipeline and powered [[chatgpt]] (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). CAI showed AI feedback can substitute for most human harm labels and even Pareto-dominate helpful+harmless RLHF on the helpfulness-vs-harmlessness tradeoff (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). DPO then collapsed the pipeline itself, showing the policy is secretly the reward model, and has since become default for open-weights systems like Zephyr, Tulu 2, and Mixtral-Instruct, spawning successors like IPO, KTO, ORPO, and SimPO (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). The trajectory is clear: fewer human labels, fewer moving parts, more interpretable oversight — with CAI-style AI feedback and DPO-style closed-form losses converging as complementary axes on the post-RLHF design space for aligning models including [[llama]] and successors.
