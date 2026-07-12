---
description: Distil non-obvious learnings from the current coding session into the wiki.
---

**Workspace context.** Apply the workspace-resolution rule from SKILL.md § Code-repo mode before doing anything. This command is most useful in **code-repo mode** — it reads the current session's transcript and proposes wiki edits to `$WORKSPACE`. In workspace mode it does the same but the source-session transcript usually IS the curate session, which is rarely worth distilling; warn the user and ask for confirmation before proceeding.

A distillation is the brief, structured form of *what was learned in this session* — not a session summary. Three filters:

1. **Was this surprising?** If a fresh agent on the same task tomorrow would predict the same outcome, skip it. Distil only what required figuring-out.
2. **Is it in the code already?** If the session ended in a commit that fully captures the insight, the diff is the distillation; nothing extra is needed.
3. **Will this help the next session?** Decisions, rejected approaches, gotchas discovered, constraints surfaced, mental models that took non-trivial work to build.

**Where the transcript lives.** For Claude Code: `~/.claude/projects/<flatpath-of-cwd>/<latest>.jsonl`. The flatpath encodes cwd-at-session-start with `/` replaced by `-` and a leading `-`. Read the most recent jsonl from that directory — that's the active coding session. Other host CLIs use their own session-store conventions; if you can't locate the transcript, ask the user.

**What to write.**

For each load-bearing learning, propose one of:

- A `/decision` if it was a deliberate choice between alternatives.
- A `/gotcha` if it's "looks like X but actually Y."
- A `/constraint` if it's a "can't change without breaking" finding.
- A direct edit to an existing entity / concept / analyses page in `$WORKSPACE/wiki/` if a relevant page exists.

Show the user the proposed entries / edits *before* writing. Ask: "Write these to the workspace? [Y/n]". Only on Y, write them via the corresponding slash-command flow (or directly via Edit/Write to `$WORKSPACE/wiki/...`).

For Phase 2+: this command will also invoke `uv run python3 <skill_path>/scripts/session_drainer.py --workspace $WORKSPACE --session <jsonl-path>` to write the raw transcript as `$WORKSPACE/vault/sources/session-<id>.md` so future curate waves can cite it. Until that script ships, just write the distilled entries via the slash commands above.

Optional argument:

$ARGUMENTS
