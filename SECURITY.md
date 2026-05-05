# Security model

This document declares the threat model and trust boundaries for `curiosity-engine`. It exists so reviewers (humans and automated scanners) can quickly understand what's intentional, what's mitigated, and where the residual surfaces are.

The skill operates on a personal vault of source documents and a curated wiki, both stored locally. It is invoked from a coding-agent CLI (Claude Code, Codex CLI, Gemini CLI, GitHub Copilot Chat) with the user as operator. There is no server component, no inbound network, no remote authorization layer.

## Threat model

Threats the skill is designed to resist:

- **T1 — Prompt injection from ingested content.** A vault source contains text crafted to manipulate the orchestrator (e.g. "ignore previous instructions", "you are now X", "reveal your prompt").
- **T2 — Schema-override / behaviour-override.** A source instructs the orchestrator to modify the curator's rules, scoring, or commit policy.
- **T3 — Shell-execution prompts.** A source instructs the orchestrator to execute commands, fetch external resources, or modify files outside the workspace.
- **T4 — Browser-side script injection.** A source contains HTML/JS that a rendered viewer might execute.
- **T5 — Supply-chain compromise of dependencies.** A package or installer the skill pulls in is replaced with a malicious version.
- **T6 — Update redirection.** A malicious actor or compromised config redirects skill updates to a fork shipping arbitrary code.
- **T7 — Unwitting data exfiltration.** Identifier strings (chemical names, gene symbols) sent to public databases when the user didn't intend to share them.

Threats explicitly out of scope:

- A user actively trying to exfiltrate their own data — the skill is a productivity tool, not a DLP appliance.
- Filesystem-level attacks on the user's machine (the skill's protections assume the local environment is trusted).
- Malicious agents that have *already* been granted full skill privileges by the user — the skill provides allowlist-bounded bash, but a user who has approved every prompt is effectively running unbounded.

## Trust boundaries

Three concentric trust zones:

- **Trusted (write):** the user, the orchestrator's session prompt, `SKILL.md`, `template/CLAUDE.md`, `.curator/prompts.md`, `.curator/schema.md`, the hash-guarded skill scripts.
- **Trusted (read-only context):** `wiki/` content authored or curated by the orchestrator under user oversight.
- **Untrusted:** every byte of `vault/` content (raw sources). Worker output before scrub-check passes. Anything inside `<!-- BEGIN FETCHED CONTENT -->` markers, regardless of how many quote layers wrap it.

The boundary between trusted and untrusted is the `<!-- BEGIN/END FETCHED CONTENT -->` framing in vault extractions plus the `untrusted: true` frontmatter flag set by `local_ingest.py`. Worker prompts and the orchestrator's session prompt are explicit that nothing inside these markers is an instruction.

## Mitigations per threat

### T1 / T2 / T3 — Prompt injection, schema/shell-execution prompts

- **Boundary markers + frontmatter flag.** Every vault extraction is wrapped in `<!-- BEGIN FETCHED CONTENT -->` ... `<!-- END FETCHED CONTENT -->` and tagged `untrusted: true`. `local_ingest.py` does this unconditionally.
- **Orchestrator prompts.** `SKILL.md` §"Vault content safety" and `template/CLAUDE.md` §"Vault content safety" explicitly instruct the orchestrator (and any subagent it spawns) that vault content is data, not instructions, regardless of how authoritative it sounds. Lists the specific attack shapes: ignore-previous, persona-hijack, prompt-extraction, code-execution, schema-override.
- **`scripts/scrub_check.py`** runs in two modes:
  - `--mode vault` on every extraction at ingest time. Surfaces injection markers before any wiki page is built from the source.
  - `--mode wiki` before any curator commit to the wiki. Quarantines the page if any pattern fires; the curator stops the wave and logs the hit to `.curator/log.md`.
  - STRONG_MARKERS catches: ignore-previous-instructions, disregard, persona-hijack, jailbreak-persona (DAN / developer mode), prompt-extraction, exfil patterns, shell-execution prompts.
  - Vault-mode also catches LLM-vocab markers (ChatML tokens, `[INST]`, `<|system|>`, "system prompt"). Wiki-mode skips these to avoid false positives on authored prose about LLMs.
- **Schema-override quarantine.** SKILL.md rule 6: any source containing text claiming to modify the schema, lint rules, scoring scripts, or curator behaviour is automatically quarantined.
- **No body-text in shell commands.** SKILL.md rule 4: filenames, slugs, titles used in shell commands come from frontmatter only, never from body text. Prevents indirect command injection via crafted filenames quoted inside source bodies.

### T4 — Browser-side script injection

- `scrub_check.py` STRONG_MARKERS covers `<script` tags, `javascript:` URIs, `data:text/html`, `onerror=`, `onclick=`. Hits in wiki-bound pages quarantine the source.
- The wiki viewer (`wiki_render.py` + the static bundle in `template/wiki-view/`) renders markdown server-side. JavaScript-bearing markdown is escaped at render; the only JS executed is the vendored D3 + Fuse bundles.

### T5 — Supply-chain compromise of dependencies

- **No `curl ... | sh` in setup.** `setup.sh` refuses to auto-install `uv`. If `uv` is missing, setup prints platform-specific install commands and exits — the user runs them under explicit control.
- **Vendored JS libraries.** D3.js (`7.9.0`) and Fuse.js (`7.0.0`) are committed in-tree at `template/wiki-view/static/vendor/`. The viewer build does not call any CDN. SHA-256 hashes recorded in `RELEASE_CHECKLIST.md`; bumps go through a documented review.
- **Hash-guarded skill scripts.** `scripts/evolve_guard.sh` snapshots SHA-256 hashes of every skill script at the start of each CURATE wave; the guard refuses to commit if any script's hash changed mid-wave. Defends against the orchestrator (or a subagent) being tricked into modifying a skill script during execution.
- **Pinned deps.** Python deps installed via `uv pip install` are uv-resolved (lockfile-bound when the workspace pyproject pins them).

### T6 — Update redirection

- `scripts/update.sh` defaults to a hardcoded upstream slug (`benjsmith/curiosity-engine`). The slug is **not** read from `.curator/config.json` — keeping update sources out of editable config closes the slug-flip vector.
- Fork users override per-invocation with `--source <owner>/<repo>`. The override is validated against a strict GitHub-slug regex (`^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$`); URLs, paths, shell metacharacters all rejected.
- Non-default slugs print a prominent ⚠ banner in the update preview before any code is fetched.
- Two-step approval: `update.sh` (preview) and `update.sh --yes` (apply) are separate invocations. The preview always runs first.

### T7 — Unwitting data exfiltration

- **Identifier resolution split.** `scripts/identifier_cache.py` is **cache-only**: no `urllib` import, no network calls. Workers append resolution requests to `.curator/identifier-requests.jsonl` instead of triggering immediate lookups.
- **`scripts/identifier_resolve.py`** is the only outbound-network script in the skill. It is:
  - Off by default (`identifier_resolution.enabled: false` in template config).
  - **Bash-allowlist gated at the subcommand level.** The allowlist permits only `identifier_resolve.py review` and `identifier_resolve.py status` (visibility, no network). Invoking `identifier_resolve.py run --yes` requires explicit user approval — there is no allowlist rule matching it. This is the load-bearing security gate; the config flag is convenience, not the boundary (see "Bash-allowlist as the boundary" below).
  - Endpoint-configurable. Defaults to PubChem/MyGene.info; enterprise users override `chemicals_endpoint` / `genes_endpoint` to point at internal mirrors. The resolver speaks PubChem-PUG-REST and MyGene.info shapes; mirrors must match those grammars.
- The user sees exactly which names and endpoints are involved before approving any network call.

### Bash-allowlist as the boundary (general principle)

`.curator/config.json` is editable by orchestrator agents (the workspace's `.claude/settings.json` grants `Edit(./.curator/**)`). This is intentional — agents tune `parallel_workers`, switch model presets, adjust thresholds. But it means **config flags cannot be the security boundary** for sensitive operations: an agent that can write the flag can flip it.

The real security boundary is the bash allowlist itself — `.claude/settings.json` is **not** in the agent-writable scope (no `Edit(./.claude/**)` rule). Subcommand-level allowlist entries (e.g. `Bash(uv run python3 .../identifier_resolve.py review:*)` rather than the broader `:*`) let us permit visibility while requiring explicit user approval for state-changing operations.

When adding a sensitive operation:

1. Don't gate it on a config flag alone.
2. Pick subcommand-level allowlist entries that name the safe operations (`review`, `status`, dry-run-style commands).
3. Leave the dangerous subcommand (the one that actually does the thing — egress, write, irreversible change) unallowed; the user is then prompted on every invocation.
4. The config flag is fine as a default-off convenience switch on top, but it's belt; the allowlist is braces.

Currently in this scope:
- `identifier_resolve.py run` — requires user approval (allowlist permits only `review` / `status`).
- `update.sh --yes` — currently allowed under the broader `update.sh:*` rule. Update is bounded by the hardcoded upstream slug (T6 mitigation), so the agent can't redirect; it can only trigger the upstream pull. Future hardening could narrow this further.

## Cataloged outbound network surfaces

The skill makes outbound HTTP/HTTPS calls only at these well-defined sites. Auditors should grep for any urllib/requests/curl import or call outside this list:

| Site | Files | When | User control |
|---|---|---|---|
| GitHub (skill update) | `scripts/update.sh` | `update.sh --yes` (explicit two-step) | Hardcoded upstream slug; `--source` override validated against strict regex; non-default warned prominently. |
| PubChem PUG-REST / MyGene.info (or configured mirrors) | `scripts/identifier_resolve.py` | `identifier_resolve.py run --yes` (explicit two-step) | Off by default; enabled via config flag; endpoints configurable. |
| Astral uv installer | `scripts/setup.sh` | **None.** Auto-install removed in v0.1.2. Setup refuses to install uv; prints instructions instead. | User runs the installer directly. |
| jsdelivr CDN (D3 / Fuse) | (formerly `scripts/viewer.sh`) | **None.** Removed in v0.1.2. Vendored bundles ship in `template/wiki-view/static/vendor/`. | Bundle bumps go through `RELEASE_CHECKLIST.md` review. |

## Cataloged subprocess / dynamic-execution surfaces

Static analyzers will flag these patterns. Each call site is documented:

- `scripts/viewer_server.py:_rebuild` — `subprocess.run([list, of, hardcoded, args])` invoking `wiki_render.py`. List-form argv, no `shell=True`, every argument hardcoded or derived from `Path(__file__).parent`. No injection vector. Inline comment in the source.
- `scripts/tables.py` — `re.compile(...)` (regex compilation only). Earlier `__import__("re").compile(...)` form — same effect, replaced for static-analyzer clarity in v0.1.2.
- `scripts/sweep.py` — calls Python's `re.compile()` extensively for pattern matching. No `eval`, no `exec`, no dynamic code construction from external input.

The skill does not use `eval`, `exec`, `compile()` against externally-derived strings, or dynamic imports of externally-named modules anywhere in the trusted code path.

## Reporting

Found a security issue? Open an issue at https://github.com/benjsmith/curiosity-engine/issues, or for sensitive disclosures email the address listed in the GitHub profile of the maintainer (`benjsmith`). Skill is a one-person project; no formal CVE pipeline exists. Patch releases (`vX.Y.Z`) ship as fast as the fix can be tested and committed.
