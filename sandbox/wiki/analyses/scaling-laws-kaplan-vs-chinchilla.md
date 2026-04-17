---
title: "[ana] Scaling laws: Kaplan vs Chinchilla"
type: analysis
created: 2026-04-16
updated: 2026-04-16
sources: [20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md, 20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md, 20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md]
---

# Scaling laws: Kaplan vs Chinchilla

Two foundational papers on [[scaling-laws]] reach different conclusions about how to spend a compute budget, and a third reframes the stakes by showing capabilities emerge with scale rather than appear linearly.

## Kaplan 2020: parameters dominate

Kaplan et al. fit power laws across seven orders of magnitude and found that cross-entropy loss follows `L(N) = (N_c / N)^0.076` for model size and `L(D) = (D_c / D)^0.095` for data size (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). The prescription for a fixed compute budget C was `N_opt ∝ C^0.73` and `D_opt ∝ C^0.27` — grow parameters roughly three times faster than tokens. The practical advice was blunt: "training very large models on a relatively modest amount of data and stopping significantly before convergence" (vault:20260416-234959-local-scaling-laws-kaplan-2020.md.extracted.md). This shaped the 175B-to-530B era. [[gpt3]] (Brown 2020) at 175B parameters on ~300B tokens, Gopher at 280B on 300B, and MT-NLG at 530B on 270B all followed the parameter-heavy recipe.

## Chinchilla 2022: data was undercounted

Hoffmann et al. trained over 400 models from 70M to 16B parameters and, via three independent methods (IsoFLOP profiles, envelope fits, and a parametric `L(N,D) = E + A/N^α + B/D^β` form), converged on exponents near 0.5 for both N and D (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md). The rule of thumb: `D ≈ 20 × N`. The authors trace Kaplan's error to a methodological subtlety — using a cosine learning-rate schedule tuned for a fixed iteration count inflated loss for short-data runs, which made data look less valuable than it actually was (vault:20260416-234959-local-chinchilla-hoffmann-2022.md.extracted.md). Training [[chinchilla-hoffmann-2022]] (70B on 1.4T tokens) with Gopher's compute produced a model that beat Gopher, GPT-3, Jurassic-1, and MT-NLG on MMLU, BIG-bench, and common-sense reasoning, and was 4× cheaper at inference. [[llama]] (Touvron 2023) operationalized this: train smaller [[transformer]] models on far more data than Kaplan would recommend.

## Chain of thought: scale unlocks new regimes

Wei et al. add a third axis. Raw loss scaling is smooth and predictable, but downstream capability is not. Chain-of-thought prompting lifts PaLM 540B on GSM8K from 17.9% to 56.9%, while the 400M variant shows essentially no gain (vault:20260416-234959-local-chain-of-thought-wei-2022.md.extracted.md). [[chain-of-thought]] reasoning is emergent above ~100B parameters. This reframes the Kaplan-vs-Chinchilla debate: if reasoning phase-transitions at scale, compute-optimal loss is only a proxy. A Chinchilla-optimal 70B may beat a 280B Gopher on average benchmarks, yet still sit below the threshold where certain reasoning abilities switch on.

## Synthesis

Kaplan answered "how does loss scale?" Chinchilla answered "how should we spend compute to minimize loss?" Wei answered "does minimizing loss give us the capabilities we want?" The honest reading is that `D ≈ 20 × N` is the right loss-minimizing allocation given current methodology, but practitioners targeting emergent reasoning may rationally overshoot on parameters even after Chinchilla, because capability thresholds do not align neatly with loss curves.
