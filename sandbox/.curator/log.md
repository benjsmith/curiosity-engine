# Log

## ingest 2026-04-16T23:50Z
- 20 academic papers ingested into vault/raw/ → vault/ via local_ingest.py
- vault_index.py --rebuild: 20 documents in FTS5
- sweep.py fix-source-stubs: 20 source stubs created in wiki/sources/
- 6 concept pages bootstrapped via subagent
- 6 entity pages bootstrapped via subagent
- graph.py rebuild: 32 pages, 20 vault_sources, 85 wikilinks, 20 citations

## curate-epoch 1 2026-04-16T23:55Z
start_score: 0.2607
end_score: 0.2319
rate_per_accept: 0.00360
elapsed_minutes: ~3
accepted: 8 (editorial: 5, exploration: 0, frontier: 3, question: 0)
rejected: 0
flagged_for_human: 0
contradictions_auto_corrected: 0
contradictions_flagged: 0
curiosity_metrics:
  frontier_size: 6 → (to recompute) after
  cross_cluster_ratio: 0.XX (growing)
  questions_generated: 0
  source_wishlist: [empty]
suspect_citations: 0
brief_strategy: passage — gave workers 2-4 specific vault paths + specific task keywords
sweep_change: none
notes: Editorial targets clip + self-attention + transformer got dense caveman-ultra rewrite with per-line citations. Frontier targets created adam-optimizer, lora, word2vec pages. All passed scrub_check. New pages appear as top-5 orphans — need inbound links next epoch.

## curate-epoch 2 2026-04-16T23:58Z
start_score: 0.2319
end_score: 0.1997
rate_per_accept: 0.00322
elapsed_minutes: ~3
accepted: 10 (editorial: 1, exploration: 0, connection: 1 cross-link edit, frontier: 4, question: 2 analyses, inbound-link-additions: 3)
rejected: 0
flagged_for_human: 0
contradictions_auto_corrected: 0
contradictions_flagged: 0
curiosity_metrics:
  frontier_size: 3 (dropout, reformer, resnet, flashattention all now cited)
  cross_cluster_ratio: rising (edges entity↔concept↔analysis now)
  questions_generated: 2 (scaling-laws-kaplan-vs-chinchilla + alignment-methods analyses)
  source_wishlist: [distillation, constitutional-ai-entity-page, instructgpt-entity-page, dpo-entity-page]
suspect_citations: 0
brief_strategy: passage — included 3-4 vault paths + specific claims to cite
sweep_change: none
notes: Two analysis pages synthesizing cross-source tensions (scaling-laws, alignment methods) plus four new frontier entities (dropout, reformer, resnet, flashattention). Graph now 41 pages / 153 wikilinks / 90 citations.

## curate-epoch 3 2026-04-17T00:01Z
start_score: 0.1997
end_score: 0.1773
rate_per_accept: 0.00280
elapsed_minutes: ~3
accepted: 8 (editorial: 2, connection: 1 mixtral↔llama+MoE, frontier: 2 (chinchilla, instructgpt), question: 1 (efficient-transformer-variants), hub-concepts: 2 (pre-training, fine-tuning))
rejected: 0
flagged_for_human: 0
suspect_citations: 0
brief_strategy: passage
sweep_change: none
notes: Two new hub concepts (pre-training, fine-tuning) linking multiple papers. Third analysis (efficient-transformer-variants). New chinchilla + instructgpt entities. Graph 46 pages / 192 wikilinks / 115 citations.

## curate-epoch 4 2026-04-17T00:04Z
start_score: 0.1773
end_score: 0.1394
rate_per_accept: 0.00542
elapsed_minutes: ~2
accepted: 7 (editorial: 2 unsourced fixes on scaling-laws + alignment-analysis, connection: 3 cross-link adds, frontier: 2 new entities dpo + constitutional-ai)
rejected: 0
flagged_for_human: 0
suspect_citations: 0
curiosity_metrics:
  frontier_size: 0 (full utilization - all 20 vault sources cited)
  cross_cluster_ratio: 0.612 (123/201 edges cross-cluster)
  questions_generated: 3 total (scaling, alignment, efficient-transformer analyses)
  source_wishlist: [distillation, RLAIF benchmarks, attention-mechanism-taxonomy, emergent-capabilities-theory]
brief_strategy: passage + targeted orphan-link ops
sweep_change: none
notes: Cross-cluster ratio 0.612 means most wikilink edges now cross entity/concept/analysis boundaries — the wiki is behaving as an interlinked knowledge graph rather than a filing cabinet. Vault utilization reached 1.0. Final state: 48 pages / 212 wikilinks / 123 citations.

## curate-epoch 5 2026-04-17T00:06Z
start_score: 0.1394
end_score: 0.1307
rate_per_accept: 0.00124
elapsed_minutes: ~2
accepted: 7 (editorial: 1 rlhf full rewrite, connection: 3 orphan-link adds (flashattention/reformer/mixtral → efficient-transformer-variants), frontier: 3 citation-adds on adam-optimizer/bert/dpo)
rejected: 0
flagged_for_human: 0
suspect_citations: 0
curiosity_metrics:
  frontier_size: 0 (all sources cited)
  cross_cluster_ratio: 0.619
  questions_generated: 0 (saturation approaching for editorial)
  source_wishlist: [attention-mechanism-taxonomy, emergent-capabilities-theory, vision-transformer-timeline]
brief_strategy: passage + targeted orphan-link ops
sweep_change: none
notes: orphan_rate mean hit 0.0 — no orphans remaining. rate_per_accept dropped to 0.00124 (saturation threshold 0.001 not crossed but approaching). Final: 48 pages / 217 wikilinks / 128 citations. Total curate elapsed ~12.5 min; stopping per user 30-min cap with reserve for queries/report.
