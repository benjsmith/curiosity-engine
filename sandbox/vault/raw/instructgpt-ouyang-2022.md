# Training Language Models to Follow Instructions with Human Feedback

**Authors:** Long Ouyang, Jeff Wu, Xu Jiang, Diogo Almeida, Carroll L. Wainwright, Pamela Mishkin, Chong Zhang, Sandhini Agarwal, Katarina Slama, Alex Ray, John Schulman, Jacob Hilton, Fraser Kelton, Luke Miller, Maddie Simens, Amanda Askell, Peter Welinder, Paul Christiano, Jan Leike, Ryan Lowe (2022)
**arXiv:** 2203.02155

## Abstract

Making language models bigger does not inherently make them better at following a user's intent. For example, large language models can generate outputs that are untruthful, toxic, or simply not helpful to the user. In other words, these models are not aligned with their users. This paper shows an avenue for aligning language models with user intent on a wide range of tasks by fine-tuning with human feedback. Starting with a set of labeler-written prompts and prompts submitted through the OpenAI API, the authors collect a dataset of labeler demonstrations of the desired model behavior, which is used to fine-tune GPT-3 using supervised learning. A dataset of rankings of model outputs is then collected, which is used to further fine-tune this supervised model using reinforcement learning from human feedback (RLHF). The resulting models are called InstructGPT. In human evaluations on a distribution of prompts submitted to the OpenAI API, outputs from the 1.3B parameter InstructGPT model are preferred to outputs from the 175B parameter GPT-3, despite having 100x fewer parameters. Moreover, InstructGPT models show improvements in truthfulness and reductions in toxic output generation while showing minimal performance regressions on public NLP datasets. Even though InstructGPT still makes simple mistakes, the results show that fine-tuning with human feedback is a promising direction for aligning language models with human intent.

## Methods

The training procedure has three stages. Stage 1 is supervised fine-tuning (SFT). A team of 40 contractors is hired to write demonstrations of desired behavior given a prompt, producing approximately 13,000 training examples. Pre-trained GPT-3 models at the 1.3B, 6B, and 175B parameter scales are fine-tuned using standard supervised cross-entropy loss for 16 epochs. Overfitting at the token level is accepted because downstream reward modeling and RL steps re-optimize the model against human preferences.

Stage 2 is reward model (RM) training. For a sampled prompt, the SFT model generates K outputs, where K ranges from 4 to 9. Human labelers rank these outputs from best to worst. From these rankings, all C(K, 2) pairwise comparisons are extracted, and a 6B reward model (initialized from the 6B SFT model with the unembedding layer replaced by a scalar head) is trained with a pairwise ranking loss: -log(sigmoid(r_theta(x, y_w) - r_theta(x, y_l))), where y_w is the preferred completion and y_l is the dispreferred one. Approximately 33,000 prompts and their associated comparisons are used to train the reward model.

Stage 3 is reinforcement learning with Proximal Policy Optimization (PPO). The SFT model is fine-tuned as a policy to maximize the scalar reward produced by the RM, using PPO with a KL-divergence penalty against the SFT policy to prevent the RL policy from drifting too far from the demonstration distribution. A variant called PPO-ptx additionally mixes in pre-training gradients on a fraction of the data to prevent regression on standard NLP benchmarks. The objective can be written as E[r_theta(x, y) - beta * log(pi_RL(y|x) / pi_SFT(y|x))] + gamma * E[log(pi_RL(x_pretrain))]. The RL policy, reward model, and KL anchor are all 6B or 175B Transformer networks depending on the scale of the final InstructGPT model.

Prompts come from two sources: labeler-written prompts spanning plain generation, few-shot, and user-provided use cases, and real prompts submitted to the OpenAI API for a beta version of the models. Prompts are deduplicated and filtered to remove personally identifiable information and inputs that do not meet safety constraints. The labeler pool is selected through screening exercises to produce data broadly aligned with the preferences of the research team, and consistency between labelers is measured throughout data collection.

Evaluation combines human preference ratings on held-out API prompts, automatic metrics on public benchmarks (TruthfulQA, RealToxicityPrompts, Winogender, CrowS-Pairs), and traditional NLP evaluations (SQuAD, DROP, HellaSwag, WMT translation). Evaluators rate outputs along axes including helpfulness, honesty, harmfulness, and whether the output follows explicit instructions and adheres to safety guidelines.

## Key Results

Labelers significantly prefer InstructGPT outputs over GPT-3 outputs across model sizes. The 1.3B InstructGPT PPO model is preferred to the 175B GPT-3 model in approximately 85% of pairwise comparisons on held-out API prompts, and the 175B InstructGPT model is preferred over 175B GPT-3 approximately 71% of the time. Preferences hold even when GPT-3 is given a well-crafted few-shot prompt.

On TruthfulQA, 175B InstructGPT generates truthful and informative answers about twice as often as GPT-3, improving from roughly 0.22 to roughly 0.42 on the truthful-and-informative metric. On the RealToxicityPrompts benchmark, InstructGPT produces about 25% less toxic output than GPT-3 when instructed to be respectful, though it is comparable or slightly worse when no instruction is given. On the Winogender and CrowS-Pairs bias benchmarks, InstructGPT shows small improvements but does not eliminate biased outputs.

InstructGPT shows minor regressions on some public NLP benchmarks relative to GPT-3, a phenomenon the authors call the alignment tax. The PPO-ptx variant largely mitigates these regressions while maintaining preference improvements. The models also generalize to instructions from labelers not involved in training data collection, and they follow instructions in languages and coding tasks underrepresented in the instruction-tuning data, suggesting the learned behavior transfers beyond the narrow training distribution. However, InstructGPT still hallucinates facts, fails on certain multi-step reasoning tasks, and can be induced to produce harmful content given adversarial prompts.

## Conclusion

Fine-tuning large language models with human feedback using a combination of supervised learning on demonstrations and reinforcement learning on preference comparisons yields models that are substantially better aligned with user intent than base pretrained models, even at much smaller scales. The resulting InstructGPT models are more helpful, somewhat more truthful, and produce less toxic output, with modest regressions on standard benchmarks that can be largely recovered via mixing pre-training gradients. The methodology, now widely known as RLHF, establishes a foundation for subsequent alignment work on large language models.

## References

- Brown et al. 2020 (GPT-3 language models are few-shot learners)
- Christiano et al. 2017 (deep reinforcement learning from human preferences)
- Stiennon et al. 2020 (learning to summarize from human feedback)
- Schulman et al. 2017 (proximal policy optimization algorithms)
- Bai et al. 2022 (training a helpful and harmless assistant with RLHF)
- Ziegler et al. 2019 (fine-tuning language models from human preferences)
