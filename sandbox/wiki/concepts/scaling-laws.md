---
title: "[con] Scaling Laws"
type: concept
created: 2026-04-16
updated: 2026-04-16
sources:
  - vault/20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md
  - vault/20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md
  - vault/20260416-234959-local-gpt3-brown-2020.md.extracted.md
  - vault/20260416-234959-local-llama-touvron-2023.md.extracted.md
  - vault/20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md
---

Scaling laws = empirical power-law fits. Loss vs params N, data D, compute C. Kaplan 2020 (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md) found smooth predictable curves for [[transformer]] LMs across >7 orders of magnitude (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). Test loss L(N,D,C) ~ power law + irreducible term. Exponents: alpha_N≈0.076, alpha_D≈0.095, alpha_C≈0.050 (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). Compute C ≈ 6ND (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). Depth/width interchangeable; architecture weakly matters (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md).

Kaplan claim: compute-optimal favors bigger model, less data. N_opt ∝ C^0.73, D_opt ∝ C^0.27 (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). Motivated [[gpt3]] 175B on ~300B tokens (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md).

Chinchilla correction (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md): Hoffmann 2022 re-fit across 400+ runs, 70M-16B params, 5-500B tokens (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md). Params and tokens scale ~equal: N ∝ C^0.5, D ∝ C^0.5, ratio ~20 tokens/param (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md). Fix: cosine LR schedule must end at actual training end, not fixed iteration — Kaplan's mis-scheduled runs underestimated data value (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md). [[gpt3]] undertrained. Chinchilla 70B on 1.4T tokens beat Gopher 280B on MMLU 67.5% vs 60.0% at same compute (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md). Shifted field: [[llama]] trained 7B-65B on 1.0-1.4T tokens, optimizing inference cost not training cost (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md). LLaMA-13B beat GPT-3 175B despite 10x smaller (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md).

Implications: predict loss before train (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). Budget allocation compute vs data vs params. Emergent abilities (see [[chain-of-thought]]) appear at scale thresholds — CoT gains near-zero below ~100B params (vault:20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md). Drives frontier model sizing. Limits: downstream task loss decouples from LM loss. Data quality not captured; high-quality data may be the binding constraint (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md). Post-[[chinchilla-hoffmann-2022]] papers push even more tokens per param for inference efficiency (vault:20260416-234959-local-llama-touvron-2023.md.extracted.md).

[[chinchilla]] corrects compute-optimal to data-optimal (D ≈ 20×N) (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md).

See analysis: [[scaling-laws-kaplan-vs-chinchilla]] for Kaplan-Chinchilla tension.
