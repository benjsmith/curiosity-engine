---
description: Capture a decision (with rationale, alternatives considered). Drains into an analyses page.
---

**Workspace context.** Apply the workspace-resolution rule from SKILL.md § Code-repo mode before doing anything. If you established `$WORKSPACE` (code-repo mode), every path below is rooted at `$WORKSPACE` instead of cwd, and add `project: <name>` to the entry's frontmatter (the value from `.curiosity/config.toml`).

A decision is a deliberate choice between alternatives — the kind of thing that ends up affecting code structure for years and is rarely re-explained later. Capture: the decision itself, the alternatives considered, and the reason this one won.

**Target file:** `wiki/notes/decisions.md`. Create the file if missing with `type: note`, `topic: decisions`, and a `"[note] decisions"` title; carry a top `Part of [[notes]].` line so it joins the notes-hub graph.

**Append the entry as a new heading section:**

```
## <first-few-words-as-header> (created: <today-ISO-date>, kind: decision)
project: <project-name-if-code-repo-mode>

**Decision.** <what was decided>

**Alternatives considered.** <what was rejected and why>

**Why this won.** <the load-bearing reason>

<additional context — links to PR, ADR, incident — if known>
```

If the decision references a known entity / concept / source page (check `$WORKSPACE/wiki/entities/`, `$WORKSPACE/wiki/concepts/`, `$WORKSPACE/wiki/sources/` if obvious from context), add `[[stem]]` wikilinks inline.

Don't mint a `(note:NN)` ID — the curator's `sync-notes` sweep mints IDs. The next CURATE wave will drain this entry from `decisions.md` into a dedicated analyses page once enough material accumulates around the topic.

Decision content:

$ARGUMENTS
