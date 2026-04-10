# Curiosity Engine

A self-improving knowledge wiki. Uses the `curiosity-engine` skill.

## Layout
- `vault/` — raw source files + FTS5 search index. Append-only.
- `wiki/` — git-tracked markdown. The agent maintains this.
- `wiki/schema.md` — operating protocol. Read before any operation.

## Quick commands
- "Add <file> to the vault" — ingest a source
- "What do I know about X?" — query the wiki
- "Lint" — check wiki health
- "Run the curator for N cycles" — autonomous improvement
