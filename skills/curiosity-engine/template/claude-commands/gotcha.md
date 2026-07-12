---
description: Capture a gotcha or surprising behaviour. Drains into an evidence page.
---

**Workspace context.** Apply the workspace-resolution rule from SKILL.md § Code-repo mode before doing anything. If you established `$WORKSPACE` (code-repo mode), every path below is rooted at `$WORKSPACE` instead of cwd, and add `project: <name>` to the entry's frontmatter (the value from `.curiosity/config.toml`).

A gotcha is something that *looks like a bug but isn't* — code or behaviour that surprises a fresh reader, where removing or "fixing" it would break things. Capture: what it looks like, what it actually is, what would break if a future agent or engineer "cleaned it up."

**Target file:** `wiki/notes/gotchas.md`. Create the file if missing with `type: note`, `topic: gotchas`, and a `"[note] gotchas"` title; carry a top `Part of [[notes]].` line so it joins the notes-hub graph.

**Append the entry as a new heading section:**

```
## <first-few-words-as-header> (created: <today-ISO-date>, kind: gotcha)
project: <project-name-if-code-repo-mode>

**Looks like.** <what a fresh reader would think>

**Actually is.** <the real behaviour or constraint>

**What breaks if "fixed".** <the consequence — incident, regression, downstream pipeline, etc.>

<code reference if applicable: (code:<project>:<path>:<line-range>)>
```

If the gotcha references a known entity / module / concept page, add `[[stem]]` wikilinks inline. Code citations use the `(code:project:path:line)` form, not `(vault:...)` — code is referenced, not cited as evidence (see SKILL.md § Code-repo mode).

The next CURATE wave will drain this entry into a dedicated evidence page citing the original source (PR, incident postmortem, session transcript) once that material lands in the vault.

Gotcha content:

$ARGUMENTS
