# ADR-001 — Proxy embed prefix + hosted-shell contract (Phase 2a)

**Status:** Accepted (2026-09-18, Europe/Zurich)  
**Charter:** umbrella `/workspace/skill-shell-rationalization/docs/CHARTER.md`  
**Related:** locked decisions #1 (no iframes), #2 (migrate Switchbay features into CE before thinning), #6 (hosted mode — okstratr settings; CE mirrors the hook).

## Context

Switchbay and okbay must **same-origin reverse-proxy** CE at `127.0.0.1:8766` under `/embed/ce/` (not iframes). Absolute frontend fetches (`/api/page`, `/api/vault/…`) break under a path prefix unless the server and static bundle share a public base. Shells also need a `host=switchbay|okbay` signal so CE can later defer workspace/harness chrome to the shell.

## Decision

1. **Configurable public base** via env `CE_PUBLIC_BASE` (normalized, no trailing slash; typical value `/embed/ce`). `viewer_server.py` strips that prefix from request paths before routing/static, and injects:

   - `window.CE_PUBLIC_BASE`
   - `window.CE_HOSTED` (`""` | `"switchbay"` | `"okbay"`)
   - `window.ceApi(path)` → `CE_PUBLIC_BASE + path`

   Wiki-view `edit.js` / `vault.js` call `ceApi` (with a local fallback) so API URLs stay under the embed prefix when proxied.

2. **Hosted-shell detection** (header wins over query):

   - Header: `X-CE-Host: switchbay|okbay`
   - Query: `?host=switchbay|okbay`
   - Probe: `GET /api/hosted` → stub policy JSON (`hosted_settings_policy`)

3. **Loopback bind unchanged:** always `127.0.0.1`. Bare `viewer.sh` default port remains **8090**. Embed/host daemons commonly use **8766** (charter). Setting `CE_PUBLIC_BASE` does not change bind host or imply a port change.

4. **Settings split stub:** when hosted, `shell_owns_workspace_settings: true` but `ce_viewer_knobs_enabled: true` until a parity-gated follow-up. Full HTML settings ownership split is large and deferred (2b+); this ADR only locks the wire contract.

## Consequences

- Proxies may forward either stripped paths (`/api/…`) or prefixed paths (`/embed/ce/api/…`); CE handles the latter when `CE_PUBLIC_BASE` is set. JS always needs the injected base for browser same-origin fetches.
- Migration of Switchbay filebrowser / graph animation / split into CE is tracked in [`MIGRATION-INTAKE.md`](MIGRATION-INTAKE.md); do not delete Switchbay surfaces until the umbrella parity checklist is green.
- okbay’s current `:8766` Atlas host and Switchbay’s tab chrome remain dual-stack until Phase 4/5 thinning.

## Alternatives considered

- **`<base href>` only** — fragile with absolute `/api` and mixed relative assets; rejected.
- **Iframe embed** — forbidden by charter #1.
- **Full settings disable in 2a** — too large a UX split; stub + document instead.
