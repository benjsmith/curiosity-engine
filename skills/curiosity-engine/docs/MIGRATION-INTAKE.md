# Migration intake map — Switchbay → CE (Phase 2a)

Charter north star: **CE** owns graph, atlas, wiki, search, source viewer, **filebrowser**, **graph animation + split**. Switchbay keeps PWA shell (workspaces, watcher, tabs chrome, rail UI) and reverse-proxies CE under `/embed/ce/`.

**Do not delete** Switchbay implementations until `/workspace/skill-shell-rationalization/docs/PARITY-CHECKLIST.md` (umbrella) is checked and a feature flag gates the cutover.

## Target homes in CE

| Capability | CE destination | Notes |
|------------|----------------|-------|
| Atlas / graph engine | `packages/knowledge-atlas` | Already the shared engine; Switchbay Graph tab adapts via examples/switchbay |
| Classic wiki viewer + APIs | `skills/curiosity-engine/template/wiki-view` + `scripts/viewer_server.py` | Phase 2a: `CE_PUBLIC_BASE` / hosted hook |
| Filebrowser | New CE surface (likely wiki-view sidebar or `packages/` module) | Intake from Switchbay sidebar |
| Graph opening animation | knowledge-atlas and/or wiki-view | Intake from curation replay |
| Workspace split | CE API + atlas multi-select; shell may keep workspace registry UX | Intake from Switchbay split API |

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

### Graph animation (curation replay)

| Artifact | Role |
|----------|------|
| `frontend/src/widgets/graph/CurationReplay.tsx` | Graph-tab opening animation UI |
| `frontend/src/widgets/graph/curationReplayAnim.ts` | Shared rAF animation (~389 LOC) |
| `src/switchbay/curation_history.py` | History data behind replay |

**CE intake notes:** Animation should consume CE graph/atlas data (or OKF/CE `data.json`), not Switchbay-only tab state. Ship behind a viewer flag; keep Switchbay button until parity.

### Split (subgraph → new workspace)

| Artifact | Role |
|----------|------|
| `frontend/src/widgets/graph/GraphTab.tsx` | Split mode UI, rubber-band / proposal listener (`sy:split-proposal`) |
| `frontend/src/widgets/graph/atlas.ts` | Atlas stubs `splitEnter` / `splitExit` |
| `src/switchbay/splitting.py` | `split_workspace` implementation |
| `src/switchbay/daemon.py` | `POST /api/workspaces/split`, status, `POST /api/split/proposal` |

**CE intake notes:** Engine multi-select already exists in knowledge-atlas (`select` / `selection-changed`). Move **page move/copy + wiki partition** logic into CE; Switchbay may retain workspace registry / tab open after split. Feature-flag dual-stack until checklist green.

## Embed contract (for Switchbay Phase 4a)

- Upstream: `http://127.0.0.1:8766` (loopback only)
- Public prefix: `/embed/ce` → set `CE_PUBLIC_BASE=/embed/ce` on the CE process
- Forward `X-CE-Host: switchbay` (okbay: `okbay`) or `?host=`
- No iframes — in-app panels load first-party proxied routes

## Sequencing

1. **2a (this branch):** proxy prefix + hosted stub + this map + ADR  
2. **2b+:** land filebrowser / animation / split behind flags in CE  
3. **4b:** Switchbay tabs thin to `/embed/ce/` once parity checklist passes  
