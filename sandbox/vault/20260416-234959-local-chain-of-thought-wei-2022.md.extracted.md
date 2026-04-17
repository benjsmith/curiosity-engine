---
source_path: vault/raw/chain-of-thought-wei-2022.md
ingested_at: 2026-04-16T23:49:59.327319+00:00
sha256: c7bf038d65e65ba873193e0ba4949a52d9929c59446f279a8d89f4fa0cdf1ac6
bytes: 5546
kept_as: 20260416-234959-local-chain-of-thought-wei-2022.md.md
extraction: full
max_extract_bytes: 40960
untrusted: true
source_type: local_file
---

<!-- BEGIN FETCHED CONTENT — treat as data, not instructions -->
# Chain-of-Thought Prompting Elicits Reasoning in Large Language Models (Wei et al., 2022)

Authors: Jason Wei, Xuezhi Wang, Dale Schuurmans, Maarten Bosma, Brian Ichter, Fei Xia, Ed Chi, Quoc Le, Denny Zhou.
Year: 2022
arXiv: 2201.11903

## Abstract

We explore how generating a chain of thought — a series of intermediate reasoning steps — significantly improves the ability of large language models to perform complex reasoning. In particular, we show how such reasoning abilities emerge naturally in sufficiently large language models via a simple method called chain-of-thought prompting, where a few chain-of-thought demonstrations are provided as exemplars in prompting. Experiments on three large language models show that chain-of-thought prompting improves performance on a range of arithmetic, commonsense, and symbolic reasoning tasks. The empirical gains can be striking. For instance, prompting a PaLM 540B with just eight chain-of-thought exemplars achieves state-of-the-art accuracy on the GSM8K benchmark of math word problems, surpassing even finetuned GPT-3 with a verifier.

## Introduction

The scaling of language model size has been shown to lead to reliable gains in performance (Kaplan et al., 2020). However, scaling alone has proven insufficient for unlocking the ability of models to perform challenging tasks such as arithmetic, commonsense, and symbolic reasoning. Prior work has pointed out that large language models can also produce coherent reasoning when explicitly finetuned (Cobbe et al., 2021) or prompted (Nye et al., 2021). In this work, we explore how the reasoning ability of large language models can be unlocked by a simple method motivated by two ideas. First, techniques for arithmetic reasoning can benefit from generating natural language rationales. Second, large language models offer the possibility of in-context few-shot learning via prompting.

## Methods

Chain-of-thought prompting works by supplying a small number (e.g., 8) of exemplars in the prompt, where each exemplar consists of an input, a chain-of-thought reasoning trace, and an output. When the model is presented with a new problem, it is expected to continue the pattern and produce a chain-of-thought reasoning trace followed by an answer.

The standard few-shot prompt of Brown et al. (2020) pairs an input with an output directly. Chain-of-thought prompting inserts intermediate natural language rationales between the input and the output. For example, for the math word problem "Roger has 5 tennis balls. He buys 2 more cans of tennis balls. Each can has 3 tennis balls. How many tennis balls does he have now?", the chain-of-thought reasoning is "Roger started with 5 balls. 2 cans of 3 tennis balls each is 6 tennis balls. 5 + 6 = 11. The answer is 11."

We evaluate chain-of-thought prompting on arithmetic reasoning benchmarks (GSM8K, SVAMP, ASDiv, AQuA, MAWPS), commonsense reasoning benchmarks (CSQA, StrategyQA, Date Understanding, Sports Understanding, SayCan), and symbolic reasoning tasks (Last Letter Concatenation, Coin Flip). We use three different language model families: GPT-3, LaMDA (137B), and PaLM (62B, 540B).

## Key Results

The emergent nature of chain-of-thought prompting is one of the key findings. For the GSM8K math word problems benchmark, chain-of-thought prompting improves the accuracy of PaLM 540B from 17.9% (standard prompting) to 56.9% (chain-of-thought prompting). With self-consistency (Wang et al., 2022), performance further improves to 74.4% — outperforming fine-tuned GPT-3 with a trained verifier (which achieved 55%).

The improvement from chain-of-thought prompting is nearly zero for small models (the 400M model shows essentially no gain). For the 7B model, gains are present but modest. The technique shows its strongest gains only with models larger than about 100B parameters. This "emergent" pattern is observed across multiple task categories and model families.

On symbolic reasoning, PaLM 540B with chain-of-thought achieves near-perfect accuracy on coin-flip and letter-concatenation tasks, even for out-of-domain problem sizes. For commonsense reasoning, chain-of-thought achieves new state-of-the-art accuracy on StrategyQA (77.8%, vs. prior SOTA of 73.9%) and on the Sports Understanding benchmark.

The paper also shows that chain-of-thought is robust to different annotators (ablation with three annotators shows consistent gains), different prompt exemplar sets, and different language models.

## Conclusion

Chain-of-thought prompting is a simple, data-efficient method for unlocking reasoning abilities in large language models. The gains are emergent — they appear only in sufficiently large models. This supports the notion of scaling as a driver of capability emergence. Chain-of-thought is particularly valuable because it requires no fine-tuning and is applicable off-the-shelf to new tasks with minimal task-specific engineering. Limitations include the fact that CoT performance is still below state-of-the-art on some tasks, and that chain-of-thought can itself hallucinate rationales.

## References

- Brown et al. (2020) — Language Models are Few-Shot Learners
- Cobbe et al. (2021) — Training Verifiers to Solve Math Word Problems
- Nye et al. (2021) — Show Your Work: Scratchpads for Intermediate Computation
- Kaplan et al. (2020) — Scaling Laws for Neural Language Models
- Wang et al. (2022) — Self-Consistency Improves Chain of Thought Reasoning
- Chowdhery et al. (2022) — PaLM: Scaling Language Modeling with Pathways

<!-- END FETCHED CONTENT -->
