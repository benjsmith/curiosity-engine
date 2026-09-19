# Migration intake map — Switchbay → CE (Phase 2a / 2b / 2b++)

Charter north star: **CE** owns graph, atlas, wiki, search, source viewer, **filebrowser**, **graph animation + split**. Switchbay keeps PWA shell (workspaces, watcher, tabs chrome, rail UI) and reverse-proxies CE under `/embed/ce/`.

**Do not delete** Switchbay implementations until `/workspace/skill-shell-rationalization/docs/PARITY-CHECKLIST.md` (umbrella) is checked and a feature flag gates the cutover.

## Target homes in CE

| Capability | CE destination | Notes |
|------------|----------------|-------|
| Atlas / graph engine | `packages/knowledge-atlas` | Already the shared engine; Switchbay Graph tab adapts via examples/switchbay |
| Classic wiki viewer + APIs | `skills/curiosity-engine/template/wiki-view` + `scripts/viewer_server.py` | Phase 2a: `CE_PUBLIC_BASE` / hosted hook |
| Filebrowser | wiki-view sidebar **Files** mode + `GET /api/tree` + `/api/fs/*` | Phase 2b browse; 2b++ FS mutate + file-routes stub |
| Graph opening animation | knowledge-atlas timeline + wiki-view SVG replay | Phase 2b hook; 2b+++ minimal SVG play/pause/scrub/step |
| Workspace split | CE API + Classic rubber-band UI + atlas multi-select; shell may keep tab-open chrome | Phase 2b++++++ (partition API + registry + rubber-band) |

## Switchbay source map (intake)

Paths relative to the Switchbay repo root (branch `feat/skill-shell-rationalization`).

### Filebrowser

| Artifact | Role |
|----------|------|
| `frontend/src/sidebar/FileBrowser.tsx` (~1.3k LOC) | Tree, sort, search/regex, context menu, pack file-routes |
| `frontend/src/sidebar/Sidebar.tsx` | Host chrome wrapping browser + selection |
| `frontend/src/sidebar/SourceBrowser.tsx` | Source-oriented browse |
| `frontend/src/sidebar/WikiPane.tsx` | Wiki pane adjacent to browser |
| `frontend/src/sidebar/ingestDrop.ts` | Drop-to-ingest bridge |

**CE intake notes:** Prefer CE vault/wiki path semantics (`vault/`, `wiki/`) over Switchbay workspace-relative pack routes. Right-click / highlight / search parity is on the umbrella checklist. Pack-specific `file_routes` may stay shell-side or become CE extension hooks later.

#### Phase 2b landed (CE)

| Artifact | Role |
|----------|------|
| `scripts/filebrowser_tree.py` | Walk `vault/` + `wiki/` only; prune SKIP_DIRS / dot dirs |
| `scripts/filebrowser_match.py` | Substring / `/re/flags` / `*.ext` matchers (Switchbay parity) |
| `GET /api/tree` in `viewer_server.py` | `{ ok, roots, files, count }` — honors `CE_PUBLIC_BASE` strip |
| `template/wiki-view/static/filebrowser.js` | Pages\|Files toggle, tree, filter, highlight, context menu |
| Tests | `tests/test_filebrowser.py` |

**Parity vs Switchbay (filebrowser):**

| Behavior | Status |
|----------|--------|
| Browse vault + wiki tree | ✅ CE (`/api/tree`) |
| Search substring / regex / `*.ext` | ✅ |
| Highlight from graph search | ✅ (`FileBrowser.setSearchHits`) |
| Right-click Open / Copy / Reveal | ✅ (minimal) |
| Sort A↔Z | ✅ |
| Open wiki page → modal | ✅ |
| Open `*.extracted.md` via vault API | ✅ (`VaultSources.open`) |
| Pack `file_routes` / ext handlers | ≈ CE discovery + dispatch/install/enable (`GET /api/file-routes`, `/api/packs/*`); agent/LLM skill exec still shell |
| FS mutate (create/rename/move/delete/dup) | ✅ CE (`/api/fs/*`, vault/wiki sandbox) |
| Reveal-in-OS / open-external | ❌ deferred (shell / OS helpers) |
| Drop-to-ingest | ❌ deferred |
| SourceBrowser / WikiPane dual pane | ❌ deferred |
| Ext filter chip UI | ❌ deferred |

#### Phase 2b++ landed (CE) — FS mutate + pack routes stub

| Artifact | Role |
|----------|------|
| `scripts/filebrowser_fs.py` | Sandboxed create / mkdir / rename / move / delete / duplicate / stat under `vault/` + `wiki/` only |
| `scripts/filebrowser_packs.py` | Read-only `pack.json` `file_routes` from `.workbench/packs/`, `packs/`, or `CE_PACKS_DIR` |
| `POST /api/fs/{create,mkdir,rename,move,delete,duplicate}` + `GET /api/fs/stat` | Honors `CE_PUBLIC_BASE`; wiki writes trigger viewer rebuild |
| `GET /api/file-routes` | `{ ok, routes, count }` — enabled packs only |
| `template/wiki-view/static/filebrowser.js` | Context menu: New file/folder, Rename, Duplicate, Delete |
| Tests | `tests/test_filebrowser_fs.py` (sandbox escape + API) |

**API notes for Switchbay embed:** call CE under `/embed/ce/api/fs/*` with the same JSON bodies as Switchbay Step E where overlapping (`delete` `{path}`, `duplicate` `{path}`). CE-only: `create` `{path, kind?, content?}`, `mkdir` `{path}`, `rename`/`move` `{path, to}`. Trash → OS trash when available, else `.workbench/trash/`.

#### Phase 2b++++ landed (CE) — pack action dispatch

| Artifact | Role |
|----------|------|
| `scripts/filebrowser_packs.py` | list / enable / local install / uninstall + sandboxed `dispatch_action` (same `pack.json` Manifest) |
| `GET /api/packs`, `GET /api/packs/<name>/actions` | Pack registry + per-pack file_routes |
| `POST /api/packs/<pack>/action/<action>` | `{path}` under vault/wiki → `{run_id, pack, action}` queue (`.workbench/pack-runs/`) |
| `POST /api/packs/toggle`, `POST /api/packs/install`, `DELETE /api/packs?name=` | Enable state + local-path install into `.workbench/packs/` (no git) |
| `template/wiki-view/static/filebrowser.js` | Context menu pack actions → dispatch API |
| Tests | `tests/test_filebrowser_packs.py` (sandbox escape + embed-prefix API) |

**Still Switchbay-only (filebrowser):** reveal-in-OS / open-external, drop-to-ingest, git pack install, pip `requires_extra`, agent/LLM skill execution for queued runs, SourceBrowser/WikiPane dual pane, ext filter chips.

Deep-link: `?filebrowser=1` opens Files mode.

### Graph animation (curation replay)

| Artifact | Role |
|----------|------|
| `frontend/src/widgets/graph/CurationReplay.tsx` | Graph-tab opening animation UI |
| `frontend/src/widgets/graph/curationReplayAnim.ts` | Shared rAF animation (~389 LOC) |
| `src/switchbay/curation_history.py` | History data behind replay |

**CE intake notes:** Animation should consume CE graph/atlas data (or OKF/CE `data.json`), not Switchbay-only tab state. Ship behind a viewer flag; keep Switchbay button until parity.

#### Phase 2b landed (CE)

| Artifact | Role |
|----------|------|
| `packages/knowledge-atlas/src/animation/replayTimeline.ts` | Pure `playReplayTimeline` + `ReplayEvent` / `HistoryDoc` types |
| `packages/knowledge-atlas/tests/replayTimeline.test.ts` | Scheduler unit tests |

#### Phase 2b+++ landed (CE)

| Artifact | Role |
|----------|------|
| `packages/knowledge-atlas/src/animation/historyFromGraph.ts` | Pure `buildHistoryFromGraph` / `snapshotAt` / `indexAtTime` |
| `packages/knowledge-atlas/tests/historyFromGraph.test.ts` | Helper unit tests |
| `scripts/curation_history.py` | HistoryDoc from `data.json` (+ optional `.workbench/curation-history.json` cache) |
| `GET /api/curation/history` | Switchbay-compatible HistoryDoc JSON; honors `CE_PUBLIC_BASE` |
| `template/wiki-view/static/replay.js` | Vanilla SVG overlay: play/pause/scrub/step (`?replay=1`) |
| Tests | `tests/test_curation_history.py` |

**Parity vs Switchbay (CurationReplay):**

| Behavior | Status |
|----------|--------|
| HistoryDoc + timed play | ✅ (`playReplayTimeline` contract + vanilla port) |
| Play / pause / scrub / step chrome | ✅ CE (Switchbay is autoplay-only + ↻) |
| SVG force grow animation | ≈ slim d3 host (no settle/fade/auto-fit polish) |
| `CE_PUBLIC_BASE` / `ceApi` | ✅ |
| `GET /api/curation/history` | ≈ from `data.json` synthetic order; git first-seen optional via shell cache |
| Git-log chronology rebuild | ❌ deferred (use Switchbay cache if present) |
| Opening autoplay on Graph tab mount | ❌ deferred (opt-in button / `?replay=1`) |
| Atlas canvas binding | ❌ deferred |
| Fade crossfade onto live graph | ❌ deferred |
| Top-label degree ranking + zoom autfit | ≈ partial (top-24 labels; manual zoom only) |


### Split (subgraph → new workspace)

| Artifact | Role |
|----------|------|
| `frontend/src/widgets/graph/GraphTab.tsx` | Split mode UI, rubber-band / proposal listener (`sy:split-proposal`) |
| `frontend/src/widgets/graph/atlas.ts` | Atlas stubs `splitEnter` / `splitExit` |
| `src/switchbay/splitting.py` | `split_workspace` implementation |
| `src/switchbay/daemon.py` | `POST /api/workspaces/split`, status, `POST /api/split/proposal` |

**CE intake notes:** Engine multi-select already exists in knowledge-atlas (`select` / `selection-changed`). Move **page move/copy + wiki partition** logic into CE; Switchbay may retain workspace registry / tab open after split. Feature-flag dual-stack until checklist green.

#### Phase 2b+ landed (CE)

| Artifact | Role |
|----------|------|
| `scripts/wiki_partition.py` | Page resolve, copy wiki + vault citations + figures, manifests, MOVE → `wiki/.deleted/<stamp>/` |
| `POST /api/split` + `GET /api/split` in `viewer_server.py` | Sync partition; `target` or `CE_SPLIT_HOME/<name>`; honors `CE_PUBLIC_BASE` |
| `template/wiki-view/static/split.js` | Minimal panel (`?split=1` / split control); move\|copy policies |
| `packages/knowledge-atlas` `partitionSelection` | Pure move\|copy selection helpers for atlas multi-select |
| `scripts/workspace_registry.py` | CE-owned workspace registry (`CE_WORKSPACE_REGISTRY` / XDG); sandbox under `$HOME` or `CE_WORKSPACE_HOME` |
| `scripts/cm_export.py` | Optional curiosity-merge `subgraph_export` subprocess hook (dry-run + sandbox) |
| `GET/POST/DELETE /api/workspaces` | Registry list / register / unregister |
| `POST /api/cm-export` | CM export for selected pages (`dry_run` supported) |
| `template/wiki-view/static/graph.js` + `split.js` | Classic rubber-band (Ctrl/⌘-drag), Shift-click multi-select, click toggle, Alt/right-click policy flip; `ce:split-proposal` / `sy:split-proposal` |
| `packages/knowledge-atlas` `rubberBand` | Pure rect/hit helpers + `defaultPartitionPolicyForType` |
| Tests | `tests/test_wiki_partition.py` + `tests/test_workspace_registry_cm_export.py` + `partitionSelection.test.ts` + `rubberBand.test.ts` |

#### Phase 2b++++++ landed (CE) — rubber-band split targeting

| Artifact | Role |
|----------|------|
| `template/wiki-view/static/graph.js` | `splitEnter` / `splitExit`; Ctrl/⌘-drag rubber-band; Shift-click add; click toggle; Alt/right-click flip move↔copy |
| `template/wiki-view/static/split.js` | Syncs panel ↔ graph selection; `POST /api/split` via `ceApi`; listens `ce:split-proposal` + `sy:split-proposal` |
| `packages/knowledge-atlas/src/partition/rubberBand.ts` | Pure `idsInRubberBand` / policy defaults (unit-tested) |
| Atlas facade | `splitEnter`/`splitExit` seed engine multi-select (no marquee on canvas yet) |

Smoke: open wiki-view with `CE_PUBLIC_BASE=/embed/ce`, `?split=1`, Ctrl-drag over nodes, name workspace, Split.

**Parity vs Switchbay (split):**

| Behavior | Status |
|----------|--------|
| Export selected pages to new workspace | ✅ CE (`wiki_partition`) |
| MOVE vs COPY policy | ✅ |
| Transitive vault citations | ✅ |
| Wiki-relative figure embeds | ✅ |
| Dual-side `.curator/splits/` manifests | ✅ |
| Recoverable source prune (MOVE) | ✅ (`wiki/.deleted/`) |
| `CE_PUBLIC_BASE` embed paths | ✅ |
| Atlas multi-select policy helpers | ✅ |
| Workspace registry persistence | ✅ CE (`workspace_registry` + `GET/POST/DELETE /api/workspaces`; auto-register on split) |
| Tab open / chrome after split | ❌ shell (Switchbay) |
| Rubber-band / `sy:split-proposal` UI | ✅ CE Classic (`graph.js` + `split.js`; `ce:`/`sy:split-proposal`); Atlas: selection sync only (no canvas rubber-band yet) |
| CM `subgraph_export` hook (license modes) | ✅ CE (`cm_export` + `POST /api/cm-export`, dry-run supported; optional CM install) |
| Workspace-root figures / `.workbench` sketches | ❌ deferred |
| Async curator link-heal agents | ❌ deferred (shell) |
| OS Trash prune | ❌ deferred (CE uses `.deleted/`) |


## Embed contract (for Switchbay Phase 4a)

- Upstream: `http://127.0.0.1:8766` (loopback only)
- Public prefix: `/embed/ce` → set `CE_PUBLIC_BASE=/embed/ce` on the CE process
- Forward `X-CE-Host: switchbay` (okbay: `okbay`) or `?host=`
- No iframes — in-app panels load first-party proxied routes

## Sequencing

1. **2a (landed):** proxy prefix + hosted stub + this map + ADR
2. **2b (landed):** filebrowser shell API + minimal UI; animation timeline hook
3. **2b+ (this spike):** wiki partition / split API + minimal UI + atlas selection helpers
4. **2b++ / 2b+++ / 2b++++ / 2b+++++ / 2b++++++ (partial):** FS mutate + pack list/dispatch/install + SVG replay UI + workspace registry + CM export + **Classic rubber-band split UI** landed; Atlas canvas rubber-band / heal agents / git-history rebuild / reveal-in-OS / drop-ingest / tab-open chrome still deferred
5. **4b:** Switchbay tabs thin to `/embed/ce/` once parity checklist passes
