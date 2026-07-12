# Skill design rationale — selected notes

Internal design notes that don't belong in the runtime SKILL.md but
are useful for contributors and people forking the project.

## Compression rules in the worker prompt (formerly "caveman integration")

Write-time compression is implemented as a small ruleset **inlined
verbatim into the worker template** in `template/prompts.md` (the
"Compression" block in the worker section). The ruleset is two
levels — `lite` (used for `analyses/` pages) and `ultra` (used for
every other page type) — and configures register tightness, article
elision, abbreviation usage, and fragment style. Workers apply the
rules as they generate page text; no separate compression pass.

### Why inlined and not a companion skill

Earlier versions of the skill cited
[JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) as
the source of the ruleset and invoked it as a companion skill via
`Skill(caveman, ...)`. Two issues surfaced during testing:

1. **Auto-Clarity short-circuit.** The caveman skill has an
   Auto-Clarity clause that disables compression on code / JSON
   output. The worker's return is a JSON object
   (`{"page":..., "new_text":...}`), so any in-worker
   `Skill(caveman, ...)` invocation saw structured output and
   declined to compress — silent no-op, verified empirically across
   dozens of epochs.

2. **Hot-loop cold-start tax.** A dedicated compressor subagent per
   page (or even batched per level per wave) paid a per-spawn cost
   — tool schema load, skill search, system prompt, caveman skill
   read — that dominated the actual compression work in a loop
   firing waves every minute.

3. **Caveman-style prompts cost MORE tokens, not fewer.** The
   round-1 V02 test (worker prompt rewritten in caveman-ultra
   register) measured **+28.5% effective tokens** vs baseline
   despite **−8% character count**. The model's BPE tokeniser is
   trained on standard English; telegraphic register and dropped
   articles tokenise less efficiently than the verbose forms they
   compress.

Together, these findings made the companion-skill path a worse
choice than inlining the ruleset directly. The skill no longer
installs the companion at setup time; the ruleset itself remains
inlined in the worker template.

### What we kept

- The two-level compression scheme (`lite` for analyses, `ultra`
  for other page types).
- The bloat-cap exception in `score_diff.py` for hydration
  (`prose-v1` target legitimately expands compressed pages ~1.5×).
- The RESTYLE operation's `caveman-lite-v1` / `caveman-ultra-v1`
  target style names — kept for backward compatibility with any
  existing wiki pages that have `style: caveman-ultra-v1` in their
  frontmatter. Treat the names as historical-only labels for "lite"
  and "ultra" compression intensities.

### What we removed

- `Skill(caveman, ...)` invocations at write or read time (silent
  no-op).
- The setup.sh prompt to install the companion skill.
- The `caveman: {...}` block in `template/config.json` (renamed to
  `compression: {...}`).
- The caveman attribution paragraph from the worker template's
  Compression section (rules retained, attribution dropped).
- The README's standalone caveman section.

### When to revisit

If the BPE-tokenisation pattern changes (e.g., a model trained with
a tokeniser that handles dropped-article register efficiently), the
caveman-style register could become net-positive on tokens — at
which point dialing up the `ultra` intensity further would be worth
testing again. As of 2026-05, with the production model presets
(`claude-sonnet-4-6`, `claude-opus-4-6`, `gpt-5`, `gemini-2.5-pro`),
the current `lite/ultra` levels are the right tradeoff.
