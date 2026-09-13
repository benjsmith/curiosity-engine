# Switchbay integration example

Switchbay mounts tabs through its registry
(`frontend/src/center/tabRegistry.ts`); its Graph tab receives
`{ data: GraphData | null; error: string | null }` where `GraphData`
is byte-compatible with this package's `CEData`. That makes the
adapter a ~40-line file: see `AtlasTab.tsx` here.

Wiring (in the Switchbay repo, not this one):

1. Vendor or install the package (`@curiosity/knowledge-atlas` — ESM
   + React entry, React 18 peer). The pack system (pre-built ESM
   served by the daemon) also works with zero build-system changes.
2. Register: in `center/builtinTabs.tsx`, either add a new kind
   (`registerTabKind("atlas", AtlasAdapter, { bare: true })`) for
   side-by-side comparison, or swap the `"graph"` registration to
   replace the current viewer.
3. Keep the shims: `AtlasTab` maps `item-open-requested` to the
   existing `#page=<id>` hash contract (so `window.Modal` and the
   `App.tsx` selection effects keep working), and exposes the
   controller for the `window.Graph.focus/clearFocus` call sites.
4. Theme: read Switchbay's `--type-*` CSS variables into the palette
   (shown in AtlasTab.tsx) or pass `data.palette` straight through.

Split mode stays host-side: the engine's `select()` +
`selection-changed` events carry multi-select; the rubber-band UI and
`POST /api/workspaces/split` remain Switchbay chrome (PLAN §2.2).

## Edges mode (auto / on / off)

Mirror the wiki-view `edges:` pill with a controlled prop — same
contract as `labelMode`:

```tsx
import { KnowledgeAtlas, cycleEdgeMode, type EdgeMode } from "@curiosity/knowledge-atlas/react";
import { useState } from "react";

const [edgeMode, setEdgeMode] = useState<EdgeMode>("auto");

<button type="button" onClick={() => setEdgeMode(cycleEdgeMode(edgeMode))}>
  edges: {edgeMode}
</button>
<KnowledgeAtlas edgeMode={edgeMode} /* … */ />
```

IIFE / graph-viewer hosts use `mount({ edgeMode: "auto" })` and
`handle.setEdges(mode)` (see wiki-view `static/atlas.js`). Drawing
only: do not drop edges from `/api/graph/data` or scene budgets when
toggling. Default `auto` is full draw below ~1200 edges and a sparse
hash-modulo sample above that. Ship the Switchbay pill + rail layout
on the Switchbay track after CE+Pages land — not in this package PR.
