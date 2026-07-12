---
description: Capture a constraint or invariant that can't be changed without breaking something.
---

**Workspace context.** Apply the workspace-resolution rule from SKILL.md § Code-repo mode before doing anything. If you established `$WORKSPACE` (code-repo mode), every path below is rooted at `$WORKSPACE` instead of cwd, and add `project: <name>` to the entry's frontmatter (the value from `.curiosity/config.toml`).

A constraint is a fact about the system that can't change without breaking something downstream — column ordering depended on by an export pipeline, an undocumented invariant another service relies on, an external contract baked into the schema. Constraints are easy to violate by accident because they're rarely visible from the local code alone.

**Target file:** `wiki/notes/constraints.md`. Create the file if missing with `type: note`, `topic: constraints`, and a `"[note] constraints"` title; carry a top `Part of [[notes]].` line so it joins the notes-hub graph.

**Append the entry as a new heading section:**

```
## <first-few-words-as-header> (created: <today-ISO-date>, kind: constraint)
project: <project-name-if-code-repo-mode>

**Constraint.** <what cannot change>

**Why.** <what depends on it — the downstream consumer, contract, or invariant>

**Failure mode if violated.** <how the breakage would manifest>

<code reference if applicable: (code:<project>:<path>:<line-range>)>
```

If the constraint references a known entity / concept / module, add `[[stem]]` wikilinks. The next CURATE wave will drain this into a concept page (constraints are typically *concepts*, not evidence — they're rules of the system).

Constraint content:

$ARGUMENTS
