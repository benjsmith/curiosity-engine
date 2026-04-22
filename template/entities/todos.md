---
title: "[ent] Todos"
type: entity
created: 2026-04-22
updated: 2026-04-22
table:
  name: todos
  primary_key: id
  columns:
    - name: id
      type: string
    - name: text
      type: string
    - name: status
      type: enum
      values: [open, done, deleted, delegated]
      default: open
    - name: priority
      type: enum
      values: [day, month, year]
      default: year
    - name: created
      type: date
    - name: done_at
      type: date
      optional: true
    - name: due
      type: date
      optional: true
    - name: origin
      type: string
---

The `todos` class table is the canonical store for to-do items across the wiki. The `table:` frontmatter above declares its schema; `tables.py sync wiki/entities/todos.md` creates / migrates the SQLite table in `.curator/tables.db`.

Todos appear as checkbox bullets in any wiki page:

- `- [ ] <text> (todo:T<id>)` — open todo
- `- [x] <text> (todo:T<id>)` — completed todo

Priority-bucket pages under `wiki/todos/` (`day.md`, `month.md`, `year.md`, `unfiled.md`, `topic-<stem>.md`) are mention sites; the sync-todos sweep reconciles checkbox state to this table as the single source of truth and propagates changes to every mention. Completed todos additionally append to the yearly archive at `wiki/todos/YYYY.md` with `created:` and `completed:` dates for lookback queries.

See SKILL.md §Operations → TODOS for the full flow.
