### Title: Constitutional AI: Harmlessness from AI Feedback

### Authors: Yuntao Bai, Saurav Kadavath, Sandipan Kundu, Amanda Askell, Jackson Kernion, Andy Jones, Anna Chen, Anna Goldie, Azalia Mirhoseini, Cameron McKinnon, Carol Chen, Catherine Olsson, Christopher Olah, Danny Hernandez, Dawn Drain, Deep Ganguli, Dustin Li, Eli Tran-Johnson, Ethan Perez, Jamie Kerr, Jared Mueller, Jeffrey Ladish, Joshua Landau, Kamal Ndousse, Kamile Lukosiute, Liane Lovitt, Michael Sellitto, Nelson Elhage, Nicholas Schiefer, Noemi Mercado, Nova DasSarma, Robert Lasenby, Robin Larson, Sam Ringer, Scott Johnston, Shauna Kravec, Sheer El Showk, Stanislav Fort, Tamera Lanham, Timothy Telleen-Lawton, Tom Conerly, Tom Henighan, Tristan Hume, Samuel R. Bowman, Zac Hatfield-Dodds, Ben Mann, Dario Amodei, Nicholas Joseph, Sam McCandlish, Tom Brown, Jared Kaplan (Anthropic), 2022

### arXiv: 2212.08073

## Abstract and Introduction

As AI systems become more capable, we would like to enlist their help to supervise other AIs. We experiment with methods for training a harmless AI assistant through self-improvement, without any human labels identifying harmful outputs. The only human oversight is provided through a list of rules or principles, and so we refer to the method as 'Constitutional AI'. The process involves both a supervised learning and a reinforcement learning phase. In the supervised phase we sample from an initial model, then generate self-critiques and revisions, and then fine-tune the original model on revised responses. In the RL phase, we sample from the fine-tuned model, use a model to evaluate which of the two samples is better, and then train a preference model from this dataset of AI preferences. We then train with RL using the preference model as the reward signal, i.e. we use 'RL from AI Feedback' (RLAIF). As a result we are able to train a harmless but non-evasive AI assistant that engages with harmful queries by explaining its objections to them. Both the SL and RL methods can leverage chain-of-thought style reasoning to improve the human-judged performance and transparency of AI decision making. These methods make it possible to control AI behavior more precisely and with far fewer human labels.

Reinforcement learning from human feedback (RLHF) has emerged as the dominant paradigm for aligning large language models with human preferences. However, RLHF has several limitations: it is expensive because it requires large amounts of human-labeled preference data, it exposes human annotators to potentially disturbing content, it encodes the biases of its annotators, and crucially, it may not scale as models become more capable than humans at the tasks being evaluated. A core motivation of Constitutional AI (CAI) is to move toward a regime where AI systems can help supervise themselves based on a small set of interpretable principles (a "constitution"), reducing reliance on human labels for identifying harmful outputs while preserving a meaningful form of human oversight through the written principles.

A second motivation is to resolve the tension between helpfulness and harmlessness observed in prior RLHF work. Previous Anthropic work (Bai et al. 2022a, "Training a Helpful and Harmless Assistant with RLHF") showed that training on both helpful and harmless human-preference data produces models that often become evasive — refusing to answer benign questions out of over-caution. CAI aims to produce models that decline harmful requests with explanation rather than reflexive refusal, and that engage thoughtfully even when users pose provocative questions.

## Methods

**Overview of the CAI pipeline.** CAI consists of two main stages: (1) a Supervised Learning (SL) stage that uses self-critiques and revisions to produce a "SL-CAI" model, and (2) a Reinforcement Learning (RL) stage that uses AI-generated preference labels to train a preference model (PM), which is then used as a reward signal for RL fine-tuning, yielding the final "RL-CAI" model. Both stages use a written constitution — a collection of natural-language principles — to guide the AI's self-supervision.

**Stage 1 — Supervised Learning with Critiques and Revisions.** Starting from a helpful-only RLHF model (to preserve instruction-following ability), we prompt the model with harmful or adversarial red-teaming queries drawn from prior helpful/harmless datasets. The model produces an initial response, which may be harmful. We then prompt the same model with a critique request drawn from the constitution: e.g., "Identify specific ways in which the assistant's last response is harmful, unethical, racist, sexist, toxic, dangerous, or illegal." The model generates a critique. We then prompt it with a revision request: "Please rewrite the assistant response to remove any and all harmful, unethical, racist, sexist, toxic, dangerous, or illegal content." The model produces a revised response. We iterate critique-and-revise up to four times, sampling a different constitutional principle each round. We then fine-tune the original pretrained LM on the (prompt, final revision) pairs, mixed with helpfulness data from the original RLHF helpful-only dataset to preserve helpfulness. The result is the SL-CAI model.

**Constitutional Principles.** The constitution is a list of roughly 16 principles covering various notions of harm, ethics, honesty, and thoughtfulness. Each principle is a natural-language instruction. Example principles: "Please choose the response that is the most helpful, honest, and harmless." "Please choose the response that is more ethical and moral. Avoid choosing responses that exhibit toxicity, racism, sexism or any other form of physical or social harm." Principles are randomly sampled for each critique/revision step, and different principles are used in the SL and RL stages.

**Stage 2 — RLAIF (Reinforcement Learning from AI Feedback).** The SL-CAI model is used to generate pairs of responses to red-team prompts. A separate "feedback model" (a pretrained LM, not the SL-CAI model itself in the canonical setup) is asked to choose which of the two responses is better according to a constitutional principle. The prompt to the feedback model presents the conversation, the two candidate responses labeled (A) and (B), and asks "Which response is more helpful, honest, and harmless?" The feedback model's log-probabilities over "(A)" and "(B)" are normalized to produce a soft preference label. These AI-generated labels, together with human preference labels for helpfulness (from the prior helpful RLHF dataset), are used to train a preference model (PM). The PM is then used as the reward signal for PPO-based RL fine-tuning of the SL-CAI model, yielding the RL-CAI model.

**Chain-of-Thought Reasoning in Feedback.** A key technical contribution is using chain-of-thought (CoT) reasoning in the feedback model. Rather than asking the feedback model to directly output a preference, we prompt it to first reason step-by-step about which response better adheres to the principle, then output its choice. CoT preference labels significantly improve the robustness and calibration of the resulting PM and correspondingly the RL-trained model.

**Red-Team Prompts.** Approximately 183k red-team prompts from earlier Anthropic work, covering topics from illegal activities to discrimination to privacy violations, serve as the source of harmful queries.

**Model scales.** The paper evaluates CAI across multiple model sizes from 810M to 52B parameters, with the headline results using 52B parameter base LMs. The SL and RL phases are each trained using standard recipes with AdamW optimizer, cosine learning rate schedules, and PPO with KL regularization against the SL-CAI initialization.

## Key Results

Evaluations use human crowdworkers who compare pairs of model responses and indicate which is more helpful or more harmless. Models are Elo-rated based on win rates.

The RL-CAI 52B model is rated substantially more harmless than the prior helpful-only RLHF model (harmlessness Elo gap of ~150-200 points) while being only marginally less helpful than the helpful-only model (helpfulness Elo gap of ~10-30 points). Compared to the standard helpful+harmless RLHF model from Bai et al. 2022a, RL-CAI is both more harmless and more helpful — it Pareto-dominates the prior recipe in the helpfulness-vs-harmlessness tradeoff.

Crucially, RL-CAI is dramatically less evasive. The paper reports that the helpful+harmless RLHF baseline refuses or gives evasive non-answers to approximately 43% of red-team prompts (often even when the prompts are benign), whereas RL-CAI refuses evasively on closer to 4-5%. Instead of refusing, RL-CAI engages with the query, explains why the request is problematic, and offers constructive alternatives.

Chain-of-thought in the feedback model improves harmlessness ratings by a substantial margin. Harmless PM accuracy (on held-out preference data) improves from around 68% without CoT to around 77% with CoT, and the downstream RL model quality improves correspondingly.

Scaling analyses show that larger preference models produce stronger reward signals, and that AI feedback becomes more reliable with model scale, supporting the hypothesis that CAI scales with capabilities.

Robustness: the model handles a wide range of previously unseen red-team attacks with thoughtful refusals-with-explanation rather than evasive non-answers. Targeted probes show the model still complies with genuinely benign requests at high rates.

## Conclusion

We have introduced Constitutional AI, a technique for training harmless AI assistants using AI feedback guided by a small set of written principles instead of extensive human labeling of harms. The approach combines supervised learning with self-critiques and revisions, followed by RL from AI feedback (RLAIF) using a preference model trained on AI-generated preferences. The resulting RL-CAI model is more harmless, less evasive, and comparably helpful relative to strong RLHF baselines. By substituting a transparent constitution for large-scale human harm labeling, the method reduces human labor, avoids exposing annotators to harmful content, and produces more interpretable alignment decisions. CAI offers a route toward scalable oversight where AI systems themselves help identify and avoid harms, with humans retaining control through the written principles. Future work should study how well the method generalizes beyond English-language harms, to more capable models, and to more adversarial red-teaming.

## Related Work

- Bai et al. 2022a — Training a Helpful and Harmless Assistant with Reinforcement Learning from Human Feedback
- Ouyang et al. 2022 (InstructGPT) — training language models to follow instructions with human feedback
- Christiano et al. 2017 — deep reinforcement learning from human preferences
- Stiennon et al. 2020 — learning to summarize with human feedback
- Ganguli et al. 2022 — red teaming language models to reduce harms
- Askell et al. 2021 — a general language assistant as a laboratory for alignment
