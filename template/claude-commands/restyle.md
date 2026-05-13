---
description: One-shot wave that rewrites every wiki page in a target style (prose / caveman-lite / caveman-ultra).
---

Restyle is bidirectional: hydrate a caveman-written wiki to readable prose, or compress a prose wiki to caveman if you prefer the denser register. Resumable + idempotent via a per-page `style:` frontmatter marker.

**Target** (one required, in `$ARGUMENTS`):

- `prose` (aliases: `prose-v1`, `hydrate`, `readable`) → `prose-v1`. Succinct readable English. The default schema rule.
- `caveman-lite` (aliases: `lite`, `caveman-lite-v1`) → `caveman-lite-v1`. Terse, full sentences with articles.
- `caveman-ultra` (aliases: `ultra`, `caveman`, `caveman-ultra-v1`) → `caveman-ultra-v1`. Telegraphic.

If `$ARGUMENTS` is empty or ambiguous, ask the user which target before doing anything else.

**Workspace context.** Apply the workspace-resolution rule from SKILL.md § Code-repo mode. Restyle operates on the workspace's wiki, whether you're in workspace mode or code-repo mode. In code-repo mode, prefix paths with `$WORKSPACE/`.

**Action.** Follow SKILL.md § RESTYLE step by step:

1. **Check `caveman.enabled`** in `.curator/config.json`. If `true` AND the target is prose, warn the user and offer to flip the config first. Wait for confirmation.

2. **Plan**:
   ```bash
   uv run python3 <skill_path>/scripts/restyle.py plan wiki --target <target-id>
   ```
   Surface to the user: pages to restyle, pages already at target, cost estimate range. Ask to proceed. If the wiki is large (>50 candidates) suggest a `--limit 20` validation pass first.

3. **Per-candidate loop**: dispatch a worker Agent (sonnet) with the `restyle_worker` template from `.curator/prompts.md`, fill `<TARGET_STYLE>`, `<PAGE_PATH>`, `<PAGE_TEXT>`. Pipe `new_text` to:
   ```bash
   uv run python3 <skill_path>/scripts/restyle.py score-check wiki/<page> --target <target-id> --new-text-stdin
   ```
   Accept → write the page, run `restyle.py mark`, commit per-page. Reject → log to `## restyle-rejections` in `.curator/log.md`, skip.

4. **Spot-audit every 5th accepted page**. Dispatch a reviewer Agent (opus, fresh context) with the `restyle_reviewer` template. `accept`: continue. `revert`: git-revert the page's commit. `needs-work`: log + skip next 4 spot-audits.

5. **End-of-wave**: `restyle.py progress wiki` → summarise to user.

**Resumable.** If interrupted (rate limit, manual stop), restart the same way — already-marked pages are filtered out by `restyle.py plan`.

Optional argument (target):

$ARGUMENTS
