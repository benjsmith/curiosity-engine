---
title: "[ent] Constitutional AI"
type: entity
created: 2026-04-17
updated: 2026-04-17
sources: [20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md, 20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md]
---

Constitutional AI = CAI. Anthropic 2022 (Bai et al). Train harmless assistant without human harm labels. Only human oversight = short list of written principles ("constitution"). AI supervises AI. Two stages: SL-CAI then RL-CAI (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md).

Stage 1 SL-CAI = critique-revise self-loop. Start from helpful-only [[rlhf]] model. Feed red-team prompt. Model emits initial (possibly harmful) reply. Same model critiques own reply using sampled constitutional principle ("identify ways response is harmful, unethical..."). Same model revises. Iterate up to 4 rounds, different principle each round. Fine-tune base LM on (prompt, final revision) pairs, mixed with helpfulness data to prevent helpfulness collapse. Pure [[fine-tuning]] on self-generated revisions, no RL yet.

Stage 2 RLAIF = RL from AI Feedback. SL-CAI generates response pairs to red-team prompts. Separate feedback LM picks winner per constitutional principle — log-probs over "(A)" vs "(B)" give soft preference label. AI labels + human helpfulness labels train preference model. PM becomes reward for PPO fine-tuning of SL-CAI. Result: RL-CAI. Replaces human harm labeling of [[instructgpt]]-style [[rlhf]] (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md).

Key trick: [[chain-of-thought]] in feedback model. Feedback LM reasons step-by-step before picking (A) or (B). Harmless PM accuracy jumps 68% → 77%. Downstream RL quality improves correspondingly.

Outcome: RL-CAI Pareto-dominates helpful+harmless RLHF baseline. More harmless (Elo gap 150-200), roughly equally helpful. Dramatically less evasive: baseline refuses ~43% of red-team prompts (often benign ones), RL-CAI ~4-5%. Engages with hard queries, explains objection, offers alternatives instead of reflexive refusal. Sits in [[alignment-methods-rlhf-cai-dpo]] cluster alongside [[dpo]] — CAI keeps RL/PPO but swaps human labels for AI labels, while DPO keeps human labels but drops RL entirely (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). Scales with model capability: bigger feedback model = better AI labels.
