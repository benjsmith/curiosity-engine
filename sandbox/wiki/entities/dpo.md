---
title: "[ent] DPO"
type: entity
created: 2026-04-17
updated: 2026-04-17
sources: [20260416-234959-local-dpo-rafailov-2023.md.extracted.md, 20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md, 20260416-234959-local-llama-touvron-2023.md.extracted.md]
---

DPO = Direct Preference Optimization. Big alignment shortcut. Skip reward model. Skip RL loop. Skip sampling. Just supervised-style loss on preference pairs. Paper by Rafailov et al 2023.

Core trick: closed-form link between reward r and KL-constrained optimal policy pi^*. Optimal policy is Boltzmann tilt of reference: pi^*(y|x) proportional to pi_ref(y|x) * exp(r(x,y)/beta). Invert this: r(x,y) = beta * log(pi(y|x)/pi_ref(y|x)) + beta * log Z(x). Policy IS reward model. "The language model is secretly a reward model" (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md).

Plug re-parameterization into Bradley-Terry likelihood. Partition Z(x) cancels (depends only on x, not y). Result: binary cross-entropy on log-ratio differences between winner y_w and loser y_l. No reward model trained. No on-policy sampling. No PPO (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). Contrast with standard [[rlhf]] pipeline where stage 3 PPO needs value functions, advantage estimation, KL coefficient tuning, rollout scheduling — caveman complicated.

Replaces the unstable PPO stage used by [[instructgpt]] (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). Also part of the broader [[alignment-methods-rlhf-cai-dpo]] family alongside [[constitutional-ai]]. Training starts from SFT checkpoint that doubles as pi_ref (frozen). Two forward+backward passes per example plus two frozen-reference forwards. Much cheaper than PPO: 3-5x less wall-clock on 7B [[llama]]-scale models over ~100k pairs on 8xA100.

Stability: no reward hacking loop, no policy collapse, gradient naturally margin-weighted (sigmoid term shrinks when winner already ranked above loser). Matches or beats PPO on IMDB sentiment Pareto frontier, Reddit TL;DR summarization (61% vs 57% win rate), Anthropic HH dialogue. PPO was unstable on HH at matched compute. Contrast with RLHF pipelines that built on [[gpt3]] via [[fine-tuning]] (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). DPO now default for Zephyr, Tulu 2, Mixtral-Instruct.

Commonly applied to [[llama]] fine-tunes: the open 7B-65B LLaMA base checkpoints released by Touvron et al. 2023 became the de-facto substrate for DPO post-training, with the authors explicitly flagging instruction fine-tuning as promising follow-up work (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md). Zephyr, Tulu 2, and derivatives all chain SFT -> DPO on top of LLaMA-family weights.
