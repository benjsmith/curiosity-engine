---
title: "[con] Todos"
type: concept
created: 2026-04-22
updated: 2026-04-22
---

# Todos

Priority-bucket to-do surface. Canonical state lives in the `todos` class table (`.curator/tables.db`); pages under `wiki/todos/` are mention-site views — readable in Obsidian, editable by hand, synced to the table by `sweep.py sync-todos` on each CURATE sweep.

## Priority buckets (active)

- **[[day]]** — day-priority bucket (things you want to do today or in the next few days).
- **[[month]]** — month-priority bucket.
- **[[year]]** — year-priority bucket. Default destination for `/todo` when no temporal cue is detected in the input.
- **[[unfiled]]** — staging for freshly-added todos pending priority assignment. Curator drains each sweep.

Priority is derived from which bucket the todo's line lives in — moving a line from `year.md` to `day.md` IS the priority change. No separate override concept.

## Completion archive

- **[[2026]]** — todos completed in 2026, appended with `(created: YYYY-MM-DD, completed: YYYY-MM-DD)` date pair.

Lookback queries ("what did I finish in April?") read the archive's `## completed` section directly.

## Syntax

```
- [ ] <text> [[wikilinks]] (created: YYYY-MM-DD) (todo:T<id>)
- [x] <text> [[wikilinks]] (created: YYYY-MM-DD) (todo:T<id>)
```

On each sweep:
- Un-IDed checkboxes get a minted `(todo:T<id>)` suffix.
- Ticking `[x]` anywhere propagates the status to every other mention-site of the same id.
- Newly-completed todos append to the current year's archive with the date pair.

## Slash commands (Claude Code only)

`/day`, `/month`, `/year` — append to the matching priority bucket.
`/todo` — agent-judged priority from content (today/tonight → day, current-month date → month, else year default).
`/note` — append to the [[notes]] surface.

Non-Claude-Code CLIs ignore `.claude/commands/`; users invoke the same operations via natural language with equivalent results.

## See also

- [[notes]] — personal note surface.
- [[index|Wiki index]] — browse by page type.
