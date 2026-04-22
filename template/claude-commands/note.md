---
description: Append a note. Routes to a topic file on explicit cue, else to wiki/notes/new.md.
---

Determine the target file from the input:

1. **Explicit topic cue** — the input begins with `topic: <name>`, `re: <name>`, `project <name>`, or a similarly clear leading phrase naming a subject. Extract the topic name, kebab-case it, and use `wiki/notes/<slug>.md` as the target. Create the file if missing with `type: note` frontmatter and a `"[note] <title>"` title.
2. **No explicit cue** → `wiki/notes/new.md`. The next CURATE wave's sync-notes sweep routes the note to a topic file (based on detected wikilinks or explicit markers) or to `wiki/notes/for-attention.md` if no topic can be inferred.

Append the content as an atomic note:

- **Single line** → `- <content> (created: <today-ISO-date>)`
- **Multi-line** → a heading section:

  ```
  ## <first-few-words-as-header> (created: <today-ISO-date>)
  <body>
  ```

Don't mint a `(note:NN)` ID — the sync-notes sweep mints IDs and populates the graph.

If the input wraps a mention of a known entity or concept (check wiki/entities/ and wiki/concepts/ if the reference is obvious), you may add `[[stem]]` wikilinks inline. Otherwise, keep the user's wording intact — the curator will add wikilinks later during its read of new.md.

Note content:

$ARGUMENTS
