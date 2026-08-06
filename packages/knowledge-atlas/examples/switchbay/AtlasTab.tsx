/**
 * Switchbay tab adapter for the Knowledge Atlas (example — lives in
 * the Switchbay repo when wired for real; kept here as deliverable 9).
 *
 * Registers via Switchbay's tab registry and honours its Graph-tab
 * contract: `data` is the daemon's /api/graph/data payload (identical
 * to CEData), item-open routes through the `#page=<id>` hash so the
 * existing modal keeps working, and the controller is exposed for the
 * `window.Graph.focus/clearFocus` shim sites in App.tsx.
 */

import { useMemo, useRef } from "react";
import { KnowledgeAtlas } from "@curiosity/knowledge-atlas/react";
import {
  CuriosityDataSource,
  type AtlasController,
  type CEData,
} from "@curiosity/knowledge-atlas";

type Props = {
  data: CEData | null;
  error: string | null;
  suppressDocModal?: boolean;
};

/** Read Switchbay's --type-* CSS variables as the atlas palette. */
function paletteFromCss(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement);
  const palette: Record<string, string> = {};
  for (const t of [
    "project", "analysis", "concept", "entity", "evidence", "fact",
    "figure", "table", "source", "note", "todo-list", "unclassified",
  ]) {
    const v = styles.getPropertyValue(`--type-${t}`).trim();
    if (v) palette[t] = v;
  }
  return palette;
}

export default function AtlasTab({ data, error, suppressDocModal }: Props) {
  const controllerRef = useRef<AtlasController>(null);
  const dataSource = useMemo(
    () => (data ? new CuriosityDataSource(data) : null),
    [data],
  );
  const theme = useMemo(() => ({ palette: paletteFromCss() }), []);

  if (error) return <div className="sy-placeholder">graph error: {error}</div>;
  if (!dataSource) return <div className="sy-placeholder">loading graph…</div>;

  return (
    <div className="sy-graph-host" style={{ position: "relative", width: "100%", height: "100%" }}>
      <KnowledgeAtlas
        ref={controllerRef}
        dataSource={dataSource}
        theme={theme}
        onOpenItem={(id) => {
          if (!suppressDocModal) {
            window.location.hash = `#page=${encodeURIComponent(id)}`;
          }
        }}
      />
    </div>
  );
}

// In center/builtinTabs.tsx (Switchbay repo):
//   const AtlasTab = lazy(() => import("../widgets/atlas/AtlasTab"));
//   registerTabKind("atlas", ({ graphData, graphError }) => (
//     <AtlasTab data={graphData as never} error={graphError} />
//   ), { bare: true });
