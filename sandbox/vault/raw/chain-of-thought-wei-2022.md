# Chain-of-Thought Prompting Elicits Reasoning in Large Language Models

**Authors:** Jason Wei, Xuezhi Wang, Dale Schuurmans, Maarten Bosma, Brian Ichter, Fei Xia, Ed Chi, Quoc Le, Denny Zhou (2022)
**arXiv:** 2201.11903

## Abstract

This paper explores how generating a chain of thought, a series of intermediate reasoning steps, significantly improves the ability of large language models to perform complex reasoning. In particular, it shows how such reasoning abilities emerge naturally in sufficiently large language models via a simple method called chain-of-thought prompting, where a few chain-of-thought demonstrations are provided as exemplars in prompting. Experiments on three large language models show that chain-of-thought prompting improves performance on a range of arithmetic, commonsense, and symbolic reasoning tasks. The empirical gains can be striking. For instance, prompting a 540B-parameter language model with just eight chain-of-thought exemplars achieves state-of-the-art accuracy on the GSM8K benchmark of math word problems, surpassing even fine-tuned GPT-3 with a verifier. Chain-of-thought prompting is model-agnostic in the sense that it works with any sufficiently large pre-trained language model without requiring additional training, additional data, or additional architectural modifications. The method is also general in the sense that it applies to a variety of reasoning tasks that benefit from decomposing a problem into intermediate steps. The paper documents that chain-of-thought is an emergent ability of model scale: it provides essentially no benefit for small models, and it begins to help only when models reach roughly 100B parameters or more. The analysis also explores why chain-of-thought prompting works, what kinds of errors it makes, and how robust it is to variations in prompt design.

## Methods

Chain-of-thought prompting is a simple few-shot prompting technique. Instead of prompting a language model with (input, output) exemplars, the prompt contains (input, chain-of-thought, output) triples, where the chain of thought is a natural language rationale that leads step by step from the input to the output. For example, for a math word problem, the chain of thought might describe the arithmetic steps required to solve the problem. The model is then asked to answer a new query in the same format: it generates its own chain of thought followed by the final answer, and the final answer is extracted for evaluation.

The authors evaluate chain-of-thought prompting on three classes of tasks. Arithmetic reasoning is evaluated on GSM8K (grade school math word problems), SVAMP (math word problems with varying structures), ASDiv (diverse math word problems), AQuA (algebraic multiple-choice problems), and MAWPS. Commonsense reasoning is evaluated on CSQA, StrategyQA, date understanding, and sports understanding from BIG-Bench. Symbolic reasoning is evaluated on last-letter concatenation and coin flip tasks, where generalization from in-distribution to out-of-distribution lengths is tested.

Five language models are evaluated: GPT-3 (in 350M, 1.3B, 6.7B, and 175B variants), LaMDA (in 422M, 2B, 8B, 68B, and 137B variants), PaLM (in 8B, 62B, and 540B variants), UL2 20B, and Codex. For each task, a fixed set of eight manually written chain-of-thought exemplars is used unless otherwise specified. Exemplars are written by the authors and are not tuned on the target test set. Standard prompting (input, output pairs only) is used as the baseline, and for fair comparison the exemplars match across conditions; only the chain-of-thought rationales are added.

Evaluation uses exact-match accuracy for arithmetic and symbolic tasks and task-specific metrics for commonsense tasks. When a task involves multiple-choice answers, the final answer is parsed from the last line of the generated text. The authors also evaluate self-consistency as a complementary technique: they sample multiple chains of thought and take the majority answer, but the main paper focuses on greedy decoding for comparability.

Additional analyses include ablations that replace the rationale with equation-only or variable-only forms, experiments that vary the number of exemplars, robustness studies across different annotators who write independent chain-of-thought prompts, and error analyses categorizing the failure modes of chain-of-thought outputs. The authors also investigate the effect of prompt ordering, prompt length, and the choice of which exemplars to include.

## Key Results

On GSM8K, PaLM 540B with chain-of-thought prompting and self-consistency decoding reaches approximately 57-58% solve rate, while greedy chain-of-thought prompting with PaLM 540B alone reaches about 56.9%, compared to about 17.9% with standard prompting. This surpasses the prior state-of-the-art set by a fine-tuned GPT-3 175B model augmented with a verifier, which achieved around 55%. GPT-3 175B improves from 15.6% with standard prompting to about 46.9% with chain of thought, and LaMDA 137B improves from about 6.5% to about 14.3%.

On SVAMP, chain-of-thought prompting with PaLM 540B achieves 79.0% accuracy versus 69.9% for standard prompting. On MAWPS, PaLM 540B reaches 93.3% with chain of thought versus 72.4% with standard prompting. On AQuA, PaLM 540B with chain of thought improves from about 25% (random baseline) to about 35% accuracy.

On commonsense reasoning, PaLM 540B achieves 79.9% on CSQA (versus 78.1% standard), 77.8% on StrategyQA (versus 68.6% standard), and 95% on sports understanding. Date understanding improves from 49.0% to 65.3%. On symbolic reasoning, chain-of-thought prompting enables length generalization: last-letter concatenation accuracy with PaLM 540B on four-word inputs improves from nearly zero with standard prompting to 99% with chain of thought, and strong out-of-distribution generalization is observed to longer lists.

A key finding is that chain-of-thought is an emergent ability. For small models (below about 10B parameters), chain-of-thought prompting often hurts accuracy because models produce fluent but logically incorrect rationales. Gains become positive and large only at model sizes at or beyond about 100B parameters for most tasks. Error analysis on GSM8K with PaLM 62B shows that approximately half of the errors are semantic-understanding errors, with the rest distributed across arithmetic errors, missing-step errors, and other categories; most of these are fixed by scaling to PaLM 540B.

## Conclusion

Chain-of-thought prompting is a remarkably simple technique that allows sufficiently large pre-trained language models to perform complex, multi-step reasoning by generating intermediate natural language rationales. It requires no gradient updates and only a handful of demonstration examples. Empirically, it delivers large gains across arithmetic, commonsense, and symbolic reasoning tasks, and it sets new state-of-the-art results on several benchmarks including GSM8K. The phenomenon is emergent with scale, illustrating that some capabilities of language models appear only past certain parameter thresholds.

## References

- Brown et al. 2020 (GPT-3 few-shot in-context learning)
- Cobbe et al. 2021 (GSM8K training verifiers for math word problems)
- Chowdhery et al. 2022 (PaLM scaling language modeling with pathways)
- Thoppilan et al. 2022 (LaMDA language models for dialog applications)
- Nye et al. 2021 (scratchpads for intermediate computation)
- Wang et al. 2022 (self-consistency improves chain-of-thought reasoning)
