# Curiosity Engine for codebases

This document describes how curiosity-engine is used to capture and curate
the knowledge that surrounds an engineering team's codebases — the
decisions, constraints, gotchas, mental models, and agent-discovered
findings that don't live in the code itself.

For the broader skill design (vault / wiki / curator, the citation ratchet,
the CURATE loop, multi-project tagging) see [`architecture.md`](architecture.md)
and [`multi-project.md`](multi-project.md). This doc adds a thin code-repo
surface on top of that foundation; nothing in the existing model changes.

## The problem this solves

A frontier coding agent dropped into an unfamiliar repository can
re-derive most of its architecture in seconds. Read the entry points,
follow the imports, summarise the layers, answer "how does X work" by
reading the code. There is genuinely no value in a curiosity-engine wiki
that re-states what the AST already exposes.

What an agent **cannot** recover from the code:

- **Decisions and rejected alternatives.** "We tried Redis here, it
  failed for reason X, that's why it's Postgres." The code shows the
  decision; the *why* is in PR threads from 18 months ago, scattered
  across hundreds of merges.
- **Constraints you can't change.** "Don't reorder these columns — the
  export pipeline depends on positional ordering." Not in code, not in
  comments; lives in someone's head.
- **Gotchas that look like bugs.** "This null check looks redundant; it
  exists because the deserialiser yields None on EOF and we hit a prod
  incident in 2024." Comments rot; the next agent "cleans it up" and
  breaks things.
- **Mental models for unusual code.** "The streaming layer looks
  event-driven but is actually polling — read it that way." The kind of
  thing a senior engineer says in onboarding.
- **Agent-discovered findings across sessions.** A coding agent in
  session 1 figures out something subtle about the deploy pipeline; the
  agent in session 2 has no idea. Today this knowledge evaporates.

This is exactly the shape curiosity-engine is built for: a *compounding
artefact* with provenance, where the unit of value is something the code
can't state for itself. The page-type taxonomy
(`sources` / `entities` / `concepts` / `analyses` / `evidence` / `facts` /
`tables` / `figures`) absorbs every input class we care about; the
citation ratchet keeps claims grounded; multi-project tagging gives us
"many code repos, one wiki" almost for free.

## When this fits and when it doesn't

**Fits well when:**

- The codebase is more than 12 months old and has had multiple engineers
  pass through it.
- Decisions are made in PR threads, Slack, design docs, and incident
  postmortems — and they're hard to find later.
- Engineers use a coding-agent CLI (Claude Code, Codex, Gemini, Copilot)
  and would benefit from yesterday's session context being available
  today.
- You can afford one shared workspace path on every engineer's machine.

**Doesn't fit when:**

- The repo is small enough for a single engineer to hold in their head.
- Knowledge is already captured in well-disciplined ADRs and the team
  reads them. (curiosity-engine isn't replacing that — it's filling the
  gap when it isn't being done.)
- The team won't run `/distill` or react to PR-merge capture, and there's
  no CI surface to automate it. Without ingested material, the workspace
  stays empty.
- You need the wiki to live inside the code repo (open-source projects
  with public decision histories). For that case, run setup.sh with the
  `--in-repo` flag — it's the existing behaviour and unchanged.

## Architecture

### One workspace, many code repos

A single curiosity-engine workspace lives **outside** any code repo, on
each engineer's machine, at a path the team agrees on. Inside the
workspace, the existing three-object model is unchanged: `vault/` for
raw sources, `wiki/` for the curated markdown (with its own git remote),
`.curator/` for operational state.

Each code repo registers against the workspace via a small **pointer
file** committed at `.curiosity/config.toml` in the repo root. The
pointer names the workspace path and declares the project tag this repo
contributes to. CE's existing multi-project model handles the rest —
pages tagged with the project flow through the recency-weighted planner,
cross-project bridges surface naturally, and archival mode applies if
the project goes dormant.

```
~/Documents/curiosity-workspace/      ← one shared workspace
  vault/                               ← PR threads, transcripts, design docs
  wiki/                                ← team wiki (its own git repo)
  .curator/

~/work/myapp/                          ← code repo
  .curiosity/
    config.toml                        ← pointer (committed)
  .git/hooks/post-merge                ← capture hook (per-machine)
  .claude/settings.local.json          ← CE allowlist (per-machine)

~/work/auth-service/                   ← another code repo
  .curiosity/config.toml               ← points at the same workspace
  ...
```

We deliberately do **not** use the `curiosity-merge` companion skill for
this. `curiosity-merge` exists for occasional sharing or augmentation of
a curated wiki — different trust model, heavier reconciliation, smaller
audience. The team-wiki shape is *one* workspace with multiple write
paths into it, not multiple workspaces being merged.

### Mapping inputs to existing page types

The eight CE page types are preserved unchanged. Inputs from a code-repo
context map cleanly:

| Input | Vault entry | Wiki destinations |
|---|---|---|
| PR thread + diff summary | `sources/pr-<repo>-<n>.md` | analyses (decision), evidence (atomic gotcha), entities (touched modules) |
| Coding-agent session transcript | `sources/session-<id>.md` | evidence (surprising finding), analyses (cross-session pattern) |
| Design doc | `sources/design-<slug>.md` | concepts (introduced terms), entities (proposed components), analyses |
| ADR | `sources/adr-<n>.md` | analyses, concepts |
| Incident postmortem | `sources/incident-<date>.md` | evidence (root cause), analyses (lessons), concepts (new constraints) |
| Linear / Jira ticket on close | `sources/lin-<n>.md` | evidence (problem), analyses (resolution) |
| CHANGELOG.md entry | `sources/changelog-<sha>.md` | facts, evidence |
| Whiteboard photo / diagram | `sources/board-<date>.png` | figures + analyses |

Code modules and services map to **entities**. Architectural patterns,
invariants, and conventions map to **concepts**. Cross-cutting
narratives ("the auth flow", "why postgres over redis", "how to read
the streaming layer") map to **analyses**. Atomic findings ("X breaks
under load Y") map to **evidence**. Concrete invariants and numbers map
to **facts**. Feature-flag / environment / deploy registers map to
**tables**. Architecture diagrams map to **figures**.

### Citation form for code

Code references gain a repo qualifier so the wiki can cite multiple
repos unambiguously:

```
(code:myapp:src/auth/middleware.py:42-78)
```

The qualifier (`myapp` here) resolves via the pointer file's `project`
field. Code citations are **references**, not the source-of-truth role
that `(vault:...)` plays — code changes daily, vault is append-only.
Treat code citations as "as of recent ingest" pointers; the drift
mechanism is described under [Capture surfaces](#capture-surfaces-in-v1)
below.

The existing `(vault:path)` DSL is unchanged, and `score_diff.py`'s
mechanical citation gate continues to operate against `(vault:...)`
exclusively. `(code:...)` citations are tracked by the graph builder for
backlinks and drift but do not gate on the citation-preserving ratchet —
they're more like wikilinks-with-anchors than evidentiary citations.

## Setup

### Cross-platform path defaults

Setup resolves the default workspace path in this order, in portable
bash (macOS / Linux / Git Bash / WSL):

1. `$CURIOSITY_WORKSPACE` env var, if set.
2. `xdg-user-dir DOCUMENTS` if available, suffixed with
   `/curiosity-workspace`.
3. `$HOME/Documents/curiosity-workspace` if `$HOME/Documents` exists.
4. `$HOME/curiosity-workspace` as a last-resort fallback.

Native Windows PowerShell setup is out of scope for v1 — same as the
existing skill. On Windows use Git Bash or WSL.

### Registering a code repo

From inside the code repo:

```bash
bash <skill_path>/scripts/setup.sh --register-code-repo
```

Setup detects that cwd is a code repo (by presence of `.git/` plus a
source-marker file like `pyproject.toml`, `package.json`, `Cargo.toml`,
`go.mod`, `Gemfile`, `pom.xml`, `build.gradle`, `Makefile`,
`CMakeLists.txt`, `composer.json`, or `*.sln`), resolves the default
workspace path, and confirms before proceeding:

```
~/Documents/curiosity-workspace already exists as a CE workspace.
Use it for this code repo? [Y/n]
```

- Default `Y` registers against the existing workspace.
- `n` prompts for an alternative path.
- A workspace at the proposed path that *isn't* a CE workspace prompts
  separately ("Bootstrap one here? [y/N]" — default N to avoid name
  collision with unrelated directories).

For non-interactive automation:

```bash
# Skip the confirm prompt, accept the resolved default
setup.sh --register-code-repo --yes

# Explicit workspace path (skips prompt entirely)
setup.sh --register-code-repo --ce-workspace-path ~/work/team-knowledge

# Override the auto-derived project tag (defaults to repo basename)
setup.sh --register-code-repo --ce-project myapp-backend

# Bootstrap a workspace and register this repo against it in one call
setup.sh --register-code-repo --init-workspace ~/Documents/curiosity-workspace

# Solo / OSS: create the workspace inside this code repo (legacy behaviour)
setup.sh --in-repo

# Also drop a GitHub Action workflow template into .github/workflows/
setup.sh --register-code-repo --ci-mode
```

What `--register-code-repo` writes:

- `.curiosity/config.toml` — pointer file, **committed** so all
  engineers on the team share routing.
- `.claude/settings.local.json` — per-machine, gitignored. CE allowlist
  patterns scoped to the resolved workspace's absolute path.
- `.git/hooks/post-merge` — per-machine, not committed. The default
  capture hook.
- (with `--ci-mode`) `.github/workflows/ce-capture.yml` — committed; team
  reviews and merges.

Setup never creates `vault/`, `wiki/`, or `.curator/` inside a code
repo unless `--in-repo` is passed explicitly.

### Pointer file format

```toml
# .curiosity/config.toml — committed; routes CE-aware commands run in
# this repo to the named workspace
workspace = "~/Documents/curiosity-workspace"  # ~ expands; absolute also fine
project = "myapp"
code_citation_root = "myapp"

[ingest]
enabled = true
paths = ["docs/adr/", "CHANGELOG.md", "README.md"]
pr_capture = true
commit_capture = true
transcript_capture = true

[brief]
auto = true                  # generate session-brief on agent session start
regenerate_on_pull = false   # also regenerate after `git pull` (off by default)
```

`workspace` resolves with `~`-expansion. `$CURIOSITY_WORKSPACE` env var
overrides the file value, useful for engineers who keep their workspace
in a non-default location without editing the committed pointer.

If the resolved workspace path doesn't exist on a given engineer's
machine — common when they've cloned the code repo but not yet cloned
the workspace — hooks no-op silently. They never break `git pull`.

### Re-running setup is idempotent

Re-running `setup.sh --register-code-repo` on an already-registered repo
diffs the config and produces no change unless something needs updating
(e.g., the user passed a different workspace path). The allowlist file,
hook, and pointer file are all overwrite-safe; setup refuses to clobber
a customised `.github/workflows/ce-capture.yml` if `--ci-mode` is re-run
on a repo that already has one.

For existing CE users with a research workspace at, say, `~/research/`:
nothing changes. Setup invocations inside `~/research/` see the existing
workspace markers and follow today's path. The code-repo detection only
fires when (a) cwd has no workspace markers, and (b) cwd is a code repo.

## Capture surfaces in v1

v1 captures from four sources: agent session transcripts, commit
messages, PR threads, and the changelog. Slack / email / Confluence /
Linear / IDE connectors are deferred to v1.5+ — they're high-value but
each carries privacy and integration cost that doesn't fit a v1 surface.

### Agent session transcripts

`scripts/session_drainer.py` reads the host CLI's session-store
directory (for Claude Code: `~/.claude/projects/<flatpath>/*.jsonl`),
identifies completed sessions, and writes one
`vault/sources/session-<id>.md` entry per session, with the project tag
inferred from the session's working directory.

It runs in one of three modes:

1. **One-shot** — invoked manually or by `/distill`. Drains any complete
   sessions newer than the last drain marker.
2. **Daemon** — installed as a launchd / systemd unit on opt-in via
   `setup.sh --register-code-repo --install-drainer`. Watches the
   session directory; drains as sessions complete.
3. **Per-session via `/distill`** — agent invokes the drainer scoped to
   the current session, then proposes wiki edits from it.

A critical filter rule prevents recursion: sessions whose flatpath
matches the workspace itself are skipped. Detached `/curate` sessions
run in the workspace's project dir (see [Detached /curate](#detached-curate)
below); without this rule each curate run would generate a transcript
that the next curate run would re-ingest.

### Commits, PR threads, changelog

The local `post-merge` hook calls `scripts/code_capture.py` with the
range of commits just merged. It reads:

- Commit messages in the new range.
- The associated PR via `gh pr view --json` if `gh` is on PATH and
  authed (degrades gracefully without `gh` to commit-only capture).
- `CHANGELOG.md` if it changed in the merged range.

Each input becomes a vault entry with sha256-based deduplication, so
re-running the capture (or running it from both a local hook and a
GitHub Action) is idempotent — no duplicate entries.

### The local git hook (default)

For solo developers and small teams, the local hook is the recommended
default. Zero CI dependency, no deploy keys, no shared secrets.

```bash
#!/usr/bin/env bash
# .git/hooks/post-merge — installed by setup.sh --register-code-repo
WORKSPACE="$(<skill_path>/scripts/code_repo.py resolve-workspace)"
[ -d "$WORKSPACE" ] || exit 0   # workspace not on this machine; no-op
uv run python3 <skill_path>/scripts/code_capture.py commits \
  --workspace "$WORKSPACE" \
  --since-marker
```

The hook is installed per-machine; an engineer who doesn't want capture
can simply disable it, or set `[ingest] enabled = false` in the
committed pointer file (which the hook respects).

### The optional GitHub Action (team scale)

For larger teams, a centralised Action removes the requirement that
every engineer have the workspace cloned locally. `setup.sh
--register-code-repo --ci-mode` drops a workflow template into
`.github/workflows/ce-capture.yml`:

```yaml
# .github/workflows/ce-capture.yml — committed by setup.sh --ci-mode
name: ce-capture
on:
  pull_request: { types: [closed] }
  push: { branches: [main], paths: [CHANGELOG.md] }
  workflow_dispatch:
jobs:
  capture:
    if: github.event.pull_request.merged == true || github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Checkout wiki workspace
        uses: actions/checkout@v4
        with:
          repository: ${{ vars.CE_WIKI_REPO }}
          ssh-key: ${{ secrets.CE_WIKI_DEPLOY_KEY }}
          path: ce-workspace
      - name: Capture
        run: |
          uv run python3 ${{ github.workspace }}/.curiosity/scripts/code_capture.py \
            ${{ github.event_name == 'pull_request' && 'pr' || 'commits' }} \
            --workspace ${{ github.workspace }}/ce-workspace \
            --pr-number ${{ github.event.pull_request.number }}
      - name: Push to wiki
        working-directory: ce-workspace
        run: |
          git config user.name "ce-capture[bot]"
          git config user.email "ce-capture@users.noreply.github.com"
          git add vault/
          git diff --cached --quiet || \
            git commit -m "capture: PR #${{ github.event.pull_request.number }}

Co-Authored-By: Claude <noreply@anthropic.com>"
          git push
```

Setup writes the workflow file but does **not** wire secrets. The team
owner adds:

- A deploy key to the wiki repo with write access.
- The private key as the `CE_WIKI_DEPLOY_KEY` secret in each code repo.
- A `CE_WIKI_REPO` repository variable naming the wiki repo
  (`org/team-curiosity-wiki`).

The Action commits with `Co-Authored-By: Claude` so existing CE activity
filtering correctly classifies these as agent edits, not user signal.

**Coexistence with the local hook.** Both can run; the
sha256-content-addressed vault entries dedupe automatically. Recommended
configuration when both are installed: set
`[ingest] commit_capture = false` in the pointer file so engineers'
local hooks don't double-capture the same PRs that the Action handles
canonically.

### Why these and not others

The four chosen sources have a property the deferred ones lack: they're
**already produced as part of normal engineering work, in a structured
format, on a deterministic trigger.** A PR merge always has a PR
thread; an agent session always writes a jsonl; a `git pull` always has
a commit range; CHANGELOG entries always commit through the same hook.

Slack / email / Confluence / Linear / IDE captures are individually
high-value but each requires:

- An auth surface (OAuth, API tokens, IMAP credentials).
- A polling or webhook deployment.
- A privacy story (Slack threads can contain sensitive content; many
  enterprises have policies).
- An ingestion-format adapter.

Bundling them into v1 multiplies the deployment surface and slows
adoption. v1.5+ ships them as discrete connectors with explicit opt-in
per source.

## In-session UX

### Workspace resolution

When the curiosity-engine skill loads in an agent session, it resolves
the active workspace by:

1. **Is cwd itself a workspace?** (cwd has `vault/` + `wiki/` +
   `.curator/`) — yes: operate on cwd, today's behaviour, no change.
2. **Walk up bounded by git-root** looking for `.curiosity/config.toml`.
   If found, route to the named workspace; the cwd repo's `project`
   field becomes the auto-applied project tag for any captures or
   wiki writes in this session.
3. **Otherwise prompt**, never resolve a workspace by directory
   proximity. The prompt offers three choices: register against an
   existing workspace, bootstrap a new workspace at a given path, or
   `--in-repo` for the OSS / solo case.

Walk-up never crosses out of the git repo — it's "find the pointer file
at this code repo's root from a subdirectory of it," which is safe
because the pointer always lives at the repo root. Workspace
*discovery* by walk-up across arbitrary directories is deliberately not
supported, because nesting a code repo inside an unrelated workspace
would otherwise cause silent mis-routing.

### Slash commands

The slash commands available in code-repo mode mirror the existing
workspace ones, with a workspace-resolution preamble that routes writes
to the named workspace rather than cwd:

| Command | Behaviour from a code-repo cwd |
|---|---|
| `/note <text>` | Writes to `<workspace>/wiki/notes/new.md` with the project tag autotag. (Existing command, extended.) |
| `/decision <text>` | Writes to notes with a `kind: decision` cue so CURATE drains it into an analyses page. |
| `/gotcha <text>` | Writes to notes with a `kind: gotcha` cue; drains into evidence. |
| `/constraint <text>` | Writes to notes with a `kind: constraint` cue; drains into a concept page. |
| `/distill` | Reads the current session transcript, proposes wiki edits, awaits confirm. Always operates on the engineer's current session, never on a curate session. |
| `/brief` | Generates / refreshes the session brief (see below). |
| `/curate` | Spawns a detached curate session against the workspace; returns immediately. |

When cwd is itself a workspace, every command behaves exactly as it does
today. The resolution preamble is inert in that case.

### Allowlist injection

`setup.sh --register-code-repo` writes CE's bash-allowlist patterns to
`.claude/settings.local.json` in the code repo. The patterns are
scoped to the resolved workspace's absolute path so an engineer's code
repo doesn't inherit allow-everything:

```json
{
  "permissions": {
    "allow": [
      "Bash(uv run python3 <skill_path>/scripts/*:*)",
      "Bash(bash <skill_path>/scripts/evolve_guard.sh:*)",
      "Bash(git -C /Users/<user>/Documents/curiosity-workspace/wiki:*)"
    ]
  }
}
```

`<skill_path>` is substituted by hosts that support it (Claude Code), or
falls back to `$CURIOSITY_ENGINE_SCRIPTS_DIR` for hosts that don't
(Codex, Gemini). For non–Claude-Code hosts, the existing one-time
approval-gated allowlist installer fires once per (host, code repo);
the marker file is `.curiosity/.allowlist-installed-<host>` (gitignored).

`.claude/settings.local.json` is per-machine and not committed, matching
Claude Code's existing convention. Each engineer's machine carries its
own resolved workspace path in the allowlist; the committed pointer
file carries only the abstract workspace-relative path.

## The session brief

The brief is what closes the loop the engineer feels. A coding agent in
a fresh session needs five minutes' worth of context — what we've
recently decided, what's in flight, what gotchas affect the files in
the current branch's diff. The brief surfaces exactly that, in under
100 lines, generated lazily.

### What it contains

```markdown
# Session brief — myapp / branch: feat/audit-log-postgres
Generated 2026-05-08 15:42 UTC.

## In flight (touched files vs main)
- src/audit_log/{writer,reader}.py
- migrations/0042_audit_postgres.sql

## Recent decisions affecting these
- [[Audit log: Postgres over Mongo]] — analysis, 2026-04-21,
  citing PR #847 + incident-2026-04-15
- [[Mongo replica-lag invariant]] — concept, 2026-04-22

## Known gotchas in touched files
- [[Audit writer: idempotency on retry]] — evidence,
  citing (code:myapp:src/audit_log/writer.py:88-104)

## Constraints
- [[Audit columns: positional ordering]] — concept, flagged constraint;
  reorder breaks downstream export pipeline

## Open questions for this project
- notes/audit-postgres-cutover.md (unresolved)

## Cross-project bridges
- [[Auth Flow]] (project: auth-service) — referenced by audit-log writer
```

The brief is a digest, not a doc. It surfaces wikilinks to the actual
pages; the agent navigates them on demand.

### When it generates

- **On agent session start** in a code repo with a pointer file, if
  `[brief] auto = true` (default true). SKILL.md instructs the agent to
  read the brief before starting work; if missing or stale (older than
  the most recent commit on the current branch), regenerate first.
- **On `/brief`** — manual refresh, regardless of auto setting.
- **On `git pull`** if `[brief] regenerate_on_pull = true` (default
  false). The post-merge hook calls `session_brief.py` after capture.

### Storage

`<code-repo>/.curiosity/session-brief.md` — per-machine, gitignored.
Cheap to overwrite. Different engineers see different briefs because
their branches differ.

The brief is generated by `scripts/session_brief.py` reading:

- The pointer file (`project` tag, workspace path).
- `git diff main...HEAD --name-only` for files in flight.
- The workspace's graph (`graph.kuzu`) for entities corresponding to
  those files plus 1-hop neighbourhood, filtered to project tag.
- Recent activity in `<workspace>/.curator/activity.log` for this
  project (last 14 days by default).
- Cross-project analyses where this project appears as a secondary tag
  (the "you might also care about X" section).

`session_brief.py` is hash-guarded.

## Detached /curate

### Why detach

`/curate` is a long-running multi-agent operation: planner, workers,
reviewer, ratchet, dozens to hundreds of tool calls per wave, multiple
waves per run. Running it inline in the engineer's coding session would:

- Burn the session's context budget on operational chatter.
- Make the transcript useless for `/distill` (mostly curate noise, not
  engineering work).
- Risk rate-limit collisions with the engineer's interactive use.

Detaching solves all three.

### How it works

`/curate` from a code-repo cwd calls `scripts/curate_launch.py`, which
spawns the active host CLI in headless mode with cwd set to the
workspace. For Claude Code:

```bash
setsid claude -p "<curate-session-prompt>" \
  --workspace "$WORKSPACE" \
  > "$WORKSPACE/.curator/sessions/$ID.log" 2>&1 &
```

The launcher writes a status file at
`<workspace>/.curator/sessions/<id>.status.json` and returns to the
engineer's session immediately:

```
> /curate
Launching curate against ~/Documents/curiosity-workspace.
Session id: curate-2026-05-08-1542.
Logs: ~/Documents/curiosity-workspace/.curator/sessions/curate-2026-05-08-1542.log
Up to wallclock_max_hours; ask `/curate status` to check progress.
```

The detached session runs the same curate loop the workspace runs
today — same prompts, same gates, same hash-guard, same review process.

### Host CLI compatibility

| Host | Detach support | v1 behaviour |
|---|---|---|
| Claude Code | `claude -p` headless | Detached |
| Codex CLI | headless equivalent | Detached |
| Gemini CLI | no clean headless | In-session with banner warning |
| Copilot Chat | no headless | In-session with banner warning |

For hosts without headless support, `/curate` falls back to running the
loop in-session, with an explicit banner warning the engineer their
context will fill. We don't try to fake detach where the host doesn't
support it.

### Transcript isolation

The detached session's `cwd` is the workspace, so its jsonl lands at
`~/.claude/projects/<flatpath-of-workspace>/<curate-session>.jsonl`.
The engineer's coding session lives at
`~/.claude/projects/<flatpath-of-code-repo>/<coding-session>.jsonl`.
Different project dirs, different transcripts, no pollution.

`session_drainer.py`'s skip rule excludes the workspace's flatpath from
ingestion candidates, so curate sessions don't get re-ingested as
engineering work.

## Idempotency for existing CE users

Existing curiosity-engine users see no change in their day-to-day flow.
The contract:

- Any directory with `vault/` + `wiki/` + `.curator/` continues to
  behave exactly as today. `setup.sh` re-runs are no-ops on existing
  workspaces, as today.
- Slash commands, when cwd is itself a workspace, behave identically.
- No schema migrations to `vault.db`, `tables.db`, `graph.kuzu`, or
  `.curator/log.md`. New state lives in new files.
- Setup invocations without flags in non-code-repo directories are
  unchanged. The code-repo flow only fires on the heuristic (`.git/` +
  source-marker file) AND when no workspace markers are present.
- The `--in-repo` flag preserves the legacy "create the workspace right
  here in cwd" behaviour for solo / OSS / monorepo cases.

The only existing-user-visible behaviour change is for users who, in
the past, would run `setup.sh` inside a code repo to bootstrap a fresh
workspace there. That flow now prompts (or accepts `--in-repo` to skip
the prompt). The CHANGELOG entry documents this and the escape hatch.

## When this doesn't fit

- **Single-engineer or short-lived projects.** The compounding artefact
  doesn't compound over a 6-week side project; the overhead of capture
  isn't repaid.
- **Privacy-strict environments where transcripts can't be ingested.**
  Set `[ingest] transcript_capture = false` and rely on PR / commit
  capture only — but expect lower-quality coverage.
- **Open-source projects with public decision histories.** Use
  `--in-repo` instead; the code repo IS the wiki home.
- **Monorepos where every project lives in one git repo.** Use
  `--in-repo` and rely on multi-project tagging within the wiki to
  separate concerns. The "one workspace per code repo" framing doesn't
  add value when "one repo" already covers everything.
- **Teams that already have a working ADR + design-doc culture** and
  read what they write. CE doesn't compete with that — it fills the gap
  when the discipline isn't there.

The biggest failure mode is **empty workspace**: capture surfaces are
on, but `/distill` is never run and PR descriptions are one-liners. The
wiki stays sparse, queries return little, the brief is empty. The
mitigation is leaning hard on passive surfaces (PR threads via the
Action, commit messages, agent transcripts via the daemon) and making
`/distill` a near-zero-cost prompt at session end. Discipline is a v2
problem if v1 capture isn't working; pretend it isn't and ship the
passive surfaces first.

## Roadmap (v1.5+)

Deferred to keep v1 a clean ship:

- **Code-comment scraping** for `WHY:` / `GOTCHA:` / `INVARIANT:`
  patterns. Promotes commented intent into evidence pages with code
  citations. Cheap technically; deferred because it asks engineers to
  adopt a comment convention.
- **Slack / Teams capture** via slash commands, emoji reactions, and
  channel listeners. Highest-value v1.5 connector — most decisions
  happen there.
- **Email forwarding alias** for stakeholder threads. Low integration
  cost, deferred for privacy review.
- **Confluence / Notion / Google Docs** ingestion of tagged design
  docs.
- **Linear / Jira webhook** on ticket close.
- **IDE extensions** (VS Code / Cursor "Send to CE" command on
  selection).
- **Native Windows PowerShell setup**.
- **`wiki/.recent.md` auto-feed** — workspace-wide rolling digest, a
  generalisation of the per-repo session brief.
- **Drift audit for code citations** — apply the existing
  `table_citation_risk` pattern to `(code:project:path:line)` ranges,
  flagging wiki claims whose backing code has churned.

The session brief, the pointer file, the local hook, the optional
Action, and the detached `/curate` are the irreducible v1 surface.
Everything else is layered on top once the loop is demonstrated to be
worth the integration cost.
