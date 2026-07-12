# This skill has moved (repo layout only — nothing else changed)

The curiosity-engine skill now lives at [`skills/curiosity-engine/SKILL.md`](skills/curiosity-engine/SKILL.md), with its `scripts/` and `template/` beside it.

Why: the `skills` CLI (≥ 1.5.13) treats a repo with a root-level SKILL.md as a *single-file* skill and installs only this file, silently dropping `scripts/` and `template/`. The subdirectory layout is handled correctly by every CLI version, and a plain `npx skills add benjsmith/curiosity-engine` or `npx skills update` now installs/repairs the complete skill.

This stub deliberately has **no frontmatter**, so skill-discovery tools ignore it. Do not add frontmatter here — a valid root SKILL.md would re-trigger the single-file install path.

Installed copies of the skill are unaffected by this layout: the install directory always contains SKILL.md, `scripts/`, and `template/` at its top level, exactly as before.
