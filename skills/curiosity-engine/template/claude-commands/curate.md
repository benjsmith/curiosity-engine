---
description: Run the CURATE loop. Detached against the workspace if invoked from a code repo; in-session if cwd is the workspace.
---

**Workspace context.** Apply the workspace-resolution rule from SKILL.md § Code-repo mode.

**If cwd IS the workspace** (workspace mode): run the standard CURATE loop in-session as documented in SKILL.md § Operations → CURATE. No detach. Done.

**If cwd is a code repo** (code-repo mode): never run curate inline. Spawn a detached session against the workspace so the engineer's coding-session context isn't burned by curate's operational chatter and the transcript stays useful for `/distill`.

```bash
uv run python3 <skill_path>/scripts/curate_launch.py --workspace "$WORKSPACE"
```

Read the JSON output. Three possible shapes:

1. **`status: launched`** — detach worked. Tell the user:

   > Curate launched against `<workspace>` (session `<id>`, pid `<pid>`).
   > Logs at `<log path>`. Ask `/curate status` to check progress.

   Do NOT wait for it to finish. The engineer's session continues immediately.

2. **`status: fallback-in-session`** — host doesn't support headless launch (Gemini, Copilot today). Print the banner and run curate inline:

   > Detached curate not yet supported on this host (`<host>`). Falling back to in-session curate — your context will fill with curate operational chatter. Recommend finishing your coding work first or switching to Claude Code for code-repo curate.

   Then proceed with the standard CURATE loop, but treating `$WORKSPACE` as the operational root for all paths (`Edit("$WORKSPACE/wiki/...")`, `git -C $WORKSPACE/wiki ...`, etc., per SKILL.md § Code-repo mode).

3. **`status: launch-failed`** — `claude` binary not on PATH, or some OS error. Surface the error to the user; do not fall back without their say-so.

**Status check.** When the user types `/curate status` (or asks "how's curate going?"):

```bash
uv run python3 <skill_path>/scripts/curate_status.py --workspace "$WORKSPACE"
```

The script prints JSON: `alive`, `elapsed_seconds`, `log.tail`, `log.waves_seen`, `log.accepts`, `log.rejects`. Summarise to the user — surface the wave count, recent log tail (only the last few lines, not the whole log), and accept/reject counts. If `alive: false`, tell them the session has finished and offer to surface the final summary from the log's last lines.

Optional argument:

$ARGUMENTS
