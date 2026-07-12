---
description: Add a todo with intent-detected priority (default year).
---

Determine the priority bucket for the input using these rules:

1. **day** — input references today, tonight, now, or "right away" (e.g. "for today:", "today:", "call Jane now"). Or input references a specific date that falls within today or tomorrow.
2. **month** — input references a date within the current month that isn't day-level (e.g. "mid-May X" when it is currently April or May). Or input references "this week", "next week", "end of month".
3. **year** — input references a date within the current year that isn't month-level (e.g. "mid-May X" when it is currently January). Or input references "this quarter", "H2", "this year".
4. **default** — no temporal cue detected → year.

Append the todo under `## active` in the matching file:

- day → `wiki/todos/day.md`
- month → `wiki/todos/month.md`
- year → `wiki/todos/year.md`

Create the file with standard frontmatter if missing (`type: todo-list`, title matches the priority).

Format:

```
- [ ] <text> (created: <today-ISO-date>)
```

Don't mint a `(todo:TN)` ID — the next CURATE wave's sync-todos sweep handles ID minting and cross-mention reconciliation. If the input implied a relative date (e.g. "mid-May"), preserve it verbatim in the text so the eventual due-field extraction can parse it.

Todo content:

$ARGUMENTS
