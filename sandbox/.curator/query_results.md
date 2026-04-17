# Query test results

## Q1. What do I know about the Transformer architecture?

**Answer:** The [[transformer]] is a pure-attention seq-to-seq architecture from Vaswani 2017 (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md): encoder-decoder stacks of multi-head [[self-attention]] (h=8, d_model=512) plus position-wise FFN, residuals, layernorm, and sinusoidal position embeddings, trained with [[adam-optimizer]]. Parallelism over sequence replaces RNN recurrence, enabling scale to billions of parameters. Three flavors dominate: encoder-only ([[bert]]), decoder-only ([[gpt3]], [[llama]]), and encoder-decoder ([[t5]]). Quadratic attention drives efficiency work — [[flashattention]], [[reformer]], and the [[mixture-of-experts]] routing in [[mixtral]] (see [[efficient-transformer-variants]]).

**Wiki pages used:** transformer, self-attention, attention-all-you (source)

**Vault searches run:** none

**Quality metrics:** used-wiki=Y, used-vault=N, wiki-only=Y, latency=fast, completeness=5/5

**End-of-answer probe:** How did pre-LN vs post-LN variants change large-scale training stability?

---

## Q2. Which sources discuss RLHF and its alternatives?

**Answer:** Three source papers frame the modern [[rlhf]] landscape: Ouyang 2022 [[instructgpt]] (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md) established the canonical SFT + RM + PPO three-stage pipeline, Bai 2022 [[constitutional-ai]] (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md) substituted AI feedback (RLAIF) against written principles, and Rafailov 2023 [[dpo]] (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md) collapsed the RM+PPO stages via a closed-form policy-as-reward reparameterization. The [[alignment-methods-rlhf-cai-dpo]] analysis synthesizes all three.

**Wiki pages used:** rlhf, instructgpt, constitutional-ai, dpo, alignment-methods-rlhf-cai-dpo

**Vault searches run:** none

**Quality metrics:** used-wiki=Y, used-vault=N, wiki-only=Y, latency=fast, completeness=5/5

**End-of-answer probe:** Are there any sources on process-reward models or online iterative DPO variants in the vault?

---

## Q3. How does LoRA compare to full fine-tuning?

**Answer:** [[lora]] freezes the base [[transformer]] weight W0 and trains a low-rank update Delta = BA (rank r as low as 4-8), cutting trainable parameters ~10000x and GPU memory ~3x versus full [[fine-tuning]], while matching or beating it on GLUE, E2E NLG, WikiSQL, and MNLI (vault:20260416-234959-local-lora-hu-2021.md.extracted.md). Full FT of [[gpt3]] 175B needs ~350 GB per task checkpoint; LoRA ships tiny swappable adapters and merges at inference with zero latency overhead, unlike adapter-layer or prefix-tuning alternatives. Rank sweep on [[gpt3]] MNLI-m is flat from r=1 to r=64, supporting the low-intrinsic-rank hypothesis.

**Wiki pages used:** lora, fine-tuning, title:-lora:-low-rank (source)

**Vault searches run:** "LoRA rank adaptation" (confirmation only)

**Quality metrics:** used-wiki=Y, used-vault=Y, wiki-only=Y, latency=fast, completeness=5/5

**End-of-answer probe:** Where does LoRA's quality ceiling show up relative to full FT — any benchmarks where it clearly underperforms?

---

## Q4. What is the Chinchilla compute-optimal scaling rule?

**Answer:** [[chinchilla]] (Hoffmann 2022, vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md) prescribes scaling parameters N and training tokens D roughly equally for a fixed compute C, giving N proportional to C^0.5, D proportional to C^0.5, with rule-of-thumb D approx 20 x N. Chinchilla-70B trained on 1.4T tokens at the same compute as 280B Gopher beat Gopher, [[gpt3]], Jurassic-1, and MT-NLG — hitting 67.5% MMLU vs Gopher 60.0%. The result corrected Kaplan 2020's parameter-heavy prescription (N proportional to C^0.73), which Hoffmann traced to a cosine-LR schedule bug (see [[scaling-laws-kaplan-vs-chinchilla]] and [[scaling-laws]]).

**Wiki pages used:** chinchilla, scaling-laws, scaling-laws-kaplan-vs-chinchilla

**Vault searches run:** none

**Quality metrics:** used-wiki=Y, used-vault=N, wiki-only=Y, latency=fast, completeness=5/5

**End-of-answer probe:** Why do practitioners now routinely overshoot 20 tokens/param — does inference economics fully explain LLaMA-style overtraining?

---

## Q5. Which papers use the Adam optimizer for training?

**Answer:** Per [[adam-optimizer]] and vault evidence, Adam or AdamW appears in: Vaswani 2017 [[transformer]] (beta_1=0.9, beta_2=0.98), [[bert]] (lr=1e-4, beta_2=0.999, weight-decay 0.01), [[gpt3]] (beta_2=0.95), [[llama]] (AdamW beta_2=0.95, weight-decay 0.1), [[clip]] (AdamW + cosine decay), and Kaplan 2020 [[scaling-laws]] (vault:20260416-234959-local-adam-kingma-2014.md.extracted.md, vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md, vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md, vault:20260416-234959-local-clip-radford-2021.md.extracted.md, vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). Notable holdouts: [[resnet]] used SGD+momentum, [[t5]] used AdaFactor.

**Wiki pages used:** adam-optimizer, title:-adam:-method (source)

**Vault searches run:** "Adam optimizer training"

**Quality metrics:** used-wiki=Y, used-vault=Y, wiki-only=N, latency=medium, completeness=5/5

**End-of-answer probe:** Did any paper explicitly justify the beta_2=0.95 drop (vs 0.999) for pretraining stability?

---

## Q6. What's the connection between ResNet and the Transformer architecture?

**Answer:** [[resnet]] introduced the residual block y = F(x) + x in 2015 to defeat the degradation problem in deep CNNs, allowing 152-layer ImageNet training via skip connections with zero extra parameters (vault:20260416-234959-local-resnet-he-2015.md.extracted.md). The [[transformer]] directly imports this scaffolding: every self-attention and FFN sub-layer is wrapped in a residual connection followed by layer normalization (vault:20260416-234959-local-attention-is-all-you-need-vaswani-2017.md.extracted.md). So residual learning is the common scaffold that makes both deep vision CNNs and deep attention stacks optimizable — [[clip]]'s image tower even retains literal ResNet-50 variants alongside its ViT encoders.

**Wiki pages used:** resnet, transformer, self-attention

**Vault searches run:** none (graph.py shared-sources returned empty)

**Quality metrics:** used-wiki=Y, used-vault=N, wiki-only=Y, latency=fast, completeness=5/5

**End-of-answer probe:** Does the pre-LN vs post-LN placement in Transformers trace back to any specific ResNet-era residual-order study?

---

## Q7. What evidence shows capabilities emerge at scale?

**Answer:** Three wiki pieces assemble the emergence evidence. [[chain-of-thought]] (Wei 2022, vault:20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md) shows CoT prompting is flat below ~100B parameters and jumps PaLM 540B on GSM8K from 17.9% to 56.9% — a phase change. [[gpt3]] (Brown 2020, vault:20260416-234959-local-gpt3-brown-2020.md.extracted.md) documents in-context few-shot learning and arithmetic emerging above ~13B. [[instructgpt]] and [[constitutional-ai]] both report AI-feedback / preference-model reliability scaling with model size (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). [[scaling-laws-kaplan-vs-chinchilla]] frames these against smooth loss curves — loss scales predictably while downstream capability phase-transitions.

**Wiki pages used:** chain-of-thought, gpt3, scaling-laws-kaplan-vs-chinchilla, constitutional-ai

**Vault searches run:** "emergent capabilities scale"

**Quality metrics:** used-wiki=Y, used-vault=Y, wiki-only=N, latency=medium, completeness=5/5

**End-of-answer probe:** Is there any critique source in the vault (e.g., Schaeffer "Are Emergent Abilities a Mirage?") that challenges the phase-transition framing?

---

## Q8. Compare efficient attention methods: FlashAttention vs Reformer vs Mixtral.

**Answer:** Per [[efficient-transformer-variants]], the three attack different layers of the cost hierarchy. [[flashattention]] is exact and IO-aware: tiles Q/K/V into SRAM with an online softmax, dropping HBM traffic from O(N^2+Nd) to O(N^2 d^2/M) for 2-4x speedup and 10-100x memory savings (vault:20260416-234959-local-flashattention-dao-2022.md.extracted.md). [[reformer]] is approximate and sub-quadratic: LSH bucketing gives O(L log L) attention plus reversible residuals cut depth memory, fitting 64k tokens on one 16GB GPU (vault:20260416-234959-local-reformer-kitaev-2020.md.extracted.md). [[mixtral]] is orthogonal: sparse top-2 routing over 8 experts activates only 12.9B of 46.7B params per token, cutting FFN compute not attention (vault:20260416-234959-local-mixtral-jiang-2024.md.extracted.md). FlashAttention wins 1k-16k exact; Reformer wins at 64k+; Mixtral composes with either.

**Wiki pages used:** efficient-transformer-variants, flashattention, reformer, mixtral

**Vault searches run:** none

**Quality metrics:** used-wiki=Y, used-vault=N, wiki-only=Y, latency=fast, completeness=5/5

**End-of-answer probe:** How does a Mixtral-style MoE interact with FlashAttention when attention is already fused-kernel — any composition penalty?

---

## Q9. How did alignment methods evolve from InstructGPT through Constitutional AI to DPO?

**Answer:** See [[alignment-methods-rlhf-cai-dpo]]. [[instructgpt]] (Ouyang 2022) fixed the three-stage recipe — SFT on ~13k demos, RM on ~33k ranked comparisons, PPO with KL tether — and showed 1.3B InstructGPT beats 175B [[gpt3]] on human preference (vault:20260416-234959-local-instructgpt-ouyang-2022.md.extracted.md). [[constitutional-ai]] (Bai 2022) kept SFT+RL but swapped human harm labels for AI critiques guided by ~16 written principles (RLAIF), Pareto-dominating helpful+harmless [[rlhf]] and dropping evasive refusals from ~43% to ~4-5% (vault:20260416-234959-local-constitutional-ai-bai-2022.md.extracted.md). [[dpo]] (Rafailov 2023) derived a closed-form policy-as-reward mapping that collapses RM+PPO into a single Bradley-Terry cross-entropy loss — no RM, no sampling, 3-5x faster, now default for Zephyr/Tulu/Mixtral-Instruct (vault:20260416-234959-local-dpo-rafailov-2023.md.extracted.md). Trajectory: fewer human labels, fewer moving parts, more interpretable oversight.

**Wiki pages used:** alignment-methods-rlhf-cai-dpo, instructgpt, constitutional-ai, dpo, rlhf

**Vault searches run:** none

**Quality metrics:** used-wiki=Y, used-vault=N, wiki-only=Y, latency=fast, completeness=5/5

**End-of-answer probe:** Does CAI's AI-feedback pipeline combine cleanly with a DPO loss (skipping PPO), and is that combination attested in the vault?

---

## Q10. What do I know about word embeddings before BERT?

**Answer:** The primary pre-[[bert]] reference is [[word2vec]] (Mikolov 2013, vault:20260416-234959-local-word2vec-mikolov-2013.md.extracted.md): two log-bilinear architectures (CBOW predicting center from context, Skip-gram predicting context from center) trained on 6B tokens yielding 300-d static vectors where vector("king") - vector("man") + vector("woman") ~ vector("queen"). Tractability came from hierarchical softmax and negative sampling. These static embeddings are the direct ancestor of the learned token-embedding tables in [[transformer]] stacks, and [[bert]]'s deep bidirectional MLM is a contextual successor to Skip-gram's shallow context-prediction task. Wiki gap: no dedicated source page for GloVe, ELMo, or fastText.

**Wiki pages used:** word2vec, bert

**Vault searches run:** "word2vec"

**Quality metrics:** used-wiki=Y, used-vault=Y, wiki-only=Y, latency=fast, completeness=4/5

**End-of-answer probe:** Are GloVe or ELMo present anywhere in the vault, or is pre-BERT context exclusively Word2Vec?

---

## Summary
- 10/10 queries answered: Y
- Mean completeness score: 4.9/5
- Queries answered wiki-only: 8/10
- Queries that needed vault fallback: 2/10 (Q5 for full Adam-user list, Q7 for emergence cross-paper evidence) — both confirmatory, not gap-filling
- Notable wiki gaps exposed:
  - No source pages for GloVe, ELMo, fastText (Q10)
  - No "emergent abilities" critique source (e.g., Schaeffer 2023) (Q7)
  - graph.py shared-sources/neighbors returned empty — graph.kuzu may need rebuild after CURATE (Q6 worked around via frontmatter sources lists)
  - No iterative/online DPO or process-reward-model source (Q2 follow-up)
- Strongest pages for queries:
  - [[alignment-methods-rlhf-cai-dpo]] — carried Q2 and Q9 cleanly
  - [[efficient-transformer-variants]] — single-page answer for Q8
  - [[scaling-laws-kaplan-vs-chinchilla]] — grounded Q4 and Q7
  - [[transformer]] — dense hub for Q1, Q6, Q8
  - [[lora]] — self-contained for Q3
