# Migration intake map — Switchbay → CE (Phase 2a / 2b)

Charter north star: **CE** owns graph, atlas, wiki, search, source viewer, **filebrowser**, **graph animation + split**. Switchbay keeps PWA shell (workspaces, watcher, tabs chrome, rail UI) and reverse-proxies CE under `/embed/ce/`.

**Do not delete** Switchbay implementations until `/workspace/skill-shell-rationalization/docs/PARITY-CHECKLIST.md` (umbrella) is checked and a feature flag gates the cutover.

## Target homes in CE

| Capability | CE destination | Notes |
|------------|----------------|-------|
| Atlas / graph engine | `packages/knowledge-atlas` | Already the shared engine; Switchbay Graph tab adapts via examples/switchbay |
| Classic wiki viewer + APIs | `skills/curiosity-engine/template/wiki-view` + `scripts/viewer_server.py` | Phase 2a: `CE_PUBLIC_BASE` / hosted hook |
| Filebrowser | wiki-view sidebar **Files** mode + `GET /api/tree` | Phase 2b spike landed (browse/search/highlight/ctx) |
| Graph opening animation | knowledge-atlas `animation/replayTimeline` (+ later wiki-view UI) | Phase 2b: pure timeline hook; SVG/d3 replay UI deferred |
| Workspace split | CE API + atlas multi-select; shell may keep workspace registry UX | Phase 2b+ spike landed (partition API + minimal UI) |

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
| Pack `file_routes` / ext handlers | ❌ deferred (shell) |
| FS mutate (delete/dup/reveal-in-OS) | ❌ deferred |
| Drop-to-ingest | ❌ deferred |
| SourceBrowser / WikiPane dual pane | ❌ deferred |
| Ext filter chip UI | ❌ deferred |

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

**Deferred:** SVG/d3 force replay UI, history JSON API from CE curator state, wiki-view chrome button, atlas canvas binding.

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
| Tests | `tests/test_wiki_partition.py` + `partitionSelection.test.ts` |

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
| Workspace registry / tab open | ❌ shell (Switchbay) |
| Rubber-band / `sy:split-proposal` UI | ❌ deferred (shell or later CE) |
| CM `subgraph_export` license modes | ❌ deferred (CE-native copy; no CM dep) |
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
4. **2b++:** pack routes / FS ops / SVG replay UI / rubber-band split / CM export parity
5. **4b:** Switchbay tabs thin to `/embed/ce/` once parity checklist passes
