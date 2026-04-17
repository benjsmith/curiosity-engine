---
title: "[con] LoRA: Low-Rank Adaptation"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources: [20260416-234959-local-lora-hu-2021.md.extracted.md, 20260416-234959-local-gpt3-brown-2020.md.extracted.md, 20260416-234959-local-llama-touvron-2023.md.extracted.md]
---

# LoRA: Low-Rank Adaptation

LoRA freeze big weight. Add tiny delta (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Delta = B times A. B skinny tall d x r, A skinny wide r x k (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Rank r tiny, like 4 or 8. Only train B, A. Big [[transformer]] weight W0 stay cold, stone, untouched (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Forward pass: h = W0 x + B A x. Scale by alpha over r (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). A init Gaussian, B init zero, so Delta W = 0 at start (vault:20260416-234959-local-lora-hu-2021.md.extracted.md).

Why small matter. Full fine-tune of [[gpt3]] 175B need 350 GB per task checkpoint (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). [[gpt3]] itself = 175B param autoregressive decoder, 96 layer, d=12288, 96 head, context 2048 (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md). Store many task copy = bad. LoRA cut trainable param 10000x, cut GPU memory 3x, match or beat full fine-tune on GLUE, E2E NLG, WikiSQL, MNLI (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Hypothesis: update Delta W has low intrinsic rank (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Empirical test: r = 1 already strong. Rank sweep on [[gpt3]] MNLI-m from r=1 to r=64 all land 91.2 to 91.7. Flat. Low rank enough (vault:20260416-234959-local-lora-hu-2021.md.extracted.md).

Apply to [[transformer]] attention: put LoRA on W_q and W_v (query, value). Leave W_k, MLP frozen (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Ablation say Q+V best trade (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). For [[gpt3]] d=12288, 96 layer, r=4 gives 4.7M trainable vs 175B total (vault:20260416-234959-local-lora-hu-2021.md.extracted.md).

At inference merge. W_eff = W0 + B A once. Same shape, same latency, zero overhead (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Contrast with adapter layer that add 20-30% latency per forward (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Contrast with prefix tuning that eat context window (vault:20260416-234959-local-lora-hu-2021.md.extracted.md).

Connect to [[llama]]: open model family 7B to 65B trained on public data (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md). [[llama]]-13B beat [[gpt3]] 175B on most bench despite 10x smaller (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md). Community LoRA-tune [[llama]] constantly because cheap. Single V100 can hold 7B + tiny LoRA adapter (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md). Alpaca, Vicuna, LLaMA-Adapter all lean on LoRA-style [[parameter-efficient-fine-tuning]]. Fits [[llama]] emphasis on inference cost over train cost (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md).

Connect to [[instructgpt]] and alignment. Full RLHF fine-tune of 175B expensive given [[gpt3]] 175B scale (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md). LoRA enable cheaper alignment, cheaper [[parameter-efficient-fine-tuning]] of instruction data. QLoRA combine 4-bit quantize backbone + LoRA adapter for single-GPU fine-tune of 65B model (vault:20260416-234959-local-lora-hu-2021.md.extracted.md).

SVD analysis of learned Delta W show LoRA amplify task-relevant direction already in W0 with small singular value (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Not new feature — boost dormant feature. Match prior intuition that pretrain cover broad skill, fine-tune reweight. [[gpt3]] few-shot work already show prompt alone elicit hidden skill at scale (vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md).

Extension family: AdaLoRA (learned rank per layer), QLoRA (quantize + LoRA), DoRA (decompose magnitude + direction) (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Also diffusion model LoRA for image style adapter (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). LoRA the default knob for [[parameter-efficient-fine-tuning]] of frozen backbone (vault:20260416-234959-local-lora-hu-2021.md.extracted.md).

Caveman summary: freeze big, train small, merge at end, swap per task, pay nothing at inference. Club good.
