/**
 * Experiment harness (PLAN deliverable 4): fixture picker, layout mode
 * switcher, telemetry HUD, discovery-horizon inspector with reasons,
 * trail panel. Excluded from the published package. All controls carry
 * data-testid hooks for the Playwright specs.
 */

import { StrictMode, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { KnowledgeAtlas } from "../src/react.ts";
import { allFixtures, megaCorpus, type Fixture } from "../fixtures/index.ts";
import { ScaledDataSource, SCALED_TOTAL_LEAVES } from "../src/datasources/scaled.ts";
import { RemoteDataSource } from "../src/datasources/remote.ts";
import type {
  AtlasController,
  AtlasEvent,
  Explanation,
  HorizonGroup,
  LayoutKind,
  SceneStats,
  TrailState,
} from "../src/index.ts";

const SEED = 42;

function buildSources(): Fixture[] {
  const fixtures = allFixtures(SEED);
  const scaled: Fixture = {
    name: "scaled-1M",
    source: new ScaledDataSource({ seed: SEED }),
    defaultFocus: "s:7.42.13",
    expected: {},
  };
  const remote: Fixture = {
    name: "remote-sim (120ms)",
    source: RemoteDataSource.wrap(fixtures[0].source, 120),
    defaultFocus: fixtures[0].defaultFocus,
    expected: fixtures[0].expected,
  };
  return [...fixtures, megaCorpus(SEED), scaled, remote];
}

// Classic-viewer label defaults: concept + entity are the structural
// hubs, note + todo are user input; the rest label only when picked.
const ALL_LABEL_TYPES = [
  "project", "analysis", "concept", "entity", "evidence", "fact",
  "figure", "table", "source", "note", "todo-list", "unclassified",
];
const LABEL_TYPE_DEFAULTS = ["concept", "entity", "note", "todo-list"];

function App() {
  const fixtures = useMemo(buildSources, []);
  const [fixtureIdx, setFixtureIdx] = useState(0);
  const [layout, setLayout] = useState<LayoutKind>("hybrid");
  const [labelMode, setLabelMode] = useState<"auto" | "on" | "off">("auto");
  const [labelTypes, setLabelTypes] = useState<string[]>(LABEL_TYPE_DEFAULTS);
  const [showLabelPicker, setShowLabelPicker] = useState(false);
  const [stats, setStats] = useState<SceneStats | null>(null);
  const [horizon, setHorizon] = useState<HorizonGroup[]>([]);
  const [trail, setTrail] = useState<TrailState | null>(null);
  const [focusTitle, setFocusTitle] = useState("");
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const controllerRef = useRef<AtlasController>(null);
  const sceneRef = useRef<{ horizon: HorizonGroup[] }>({ horizon: [] });

  const fixture = fixtures[fixtureIdx];

  const onEvent = (e: AtlasEvent) => {
    if (e.kind === "scene-ready") {
      setStats(e.stats);
      const snap = (controllerRef.current as unknown as { snapshot?: () => { scene?: { horizon: HorizonGroup[]; focus?: { title: string } } | null } })?.snapshot?.();
      const scene = snap?.scene;
      if (scene) {
        sceneRef.current.horizon = scene.horizon;
        setHorizon(scene.horizon);
        setFocusTitle(scene.focus?.title ?? "(overview)");
      }
    } else if (e.kind === "trail-changed") {
      setTrail(e.trail);
    } else if (e.kind === "explanation-ready") {
      setExplanation(e.explanation);
    }
  };

  // Key by fixture + layout so the component fully remounts per mode
  // (identical scene data through different geometry = the comparison).
  const atlasKey = `${fixture.name}:${layout}`;
  const config = useMemo(() => ({ seed: SEED, layout }), [layout]);

  return (
    <div className="app">
      <div className="toolbar">
        <strong>Knowledge Atlas</strong>
        <select
          data-testid="fixture-select"
          value={fixtureIdx}
          onChange={(e) => {
            setFixtureIdx(Number(e.target.value));
            setExplanation(null);
            setOpened(null);
          }}
        >
          {fixtures.map((f, i) => (
            <option key={f.name} value={i}>
              {f.name}
            </option>
          ))}
        </select>
        <select
          data-testid="layout-select"
          value={layout}
          onChange={(e) => setLayout(e.target.value as LayoutKind)}
        >
          <option value="hybrid">hybrid (P6: force core + type rim)</option>
          <option value="adaptive-hybrid">adaptive hybrid (P7: gridlike core when it fits)</option>
          <option value="focus">focus (P1)</option>
          <option value="force">force (P0 baseline)</option>
          <option value="hyperbolic">hyperbolic (P3)</option>
          <option value="adaptive">adaptive (P5)</option>
        </select>
        <button data-testid="btn-back" onClick={() => controllerRef.current?.back()}>← back</button>
        <button data-testid="btn-forward" onClick={() => controllerRef.current?.forward()}>fwd →</button>
        <button data-testid="btn-pin" onClick={() => {
          const st = controllerRef.current?.getState();
          if (st?.focusId) controllerRef.current?.pin(st.focusId);
        }}>pin focus</button>
        <button data-testid="btn-branch" onClick={() => controllerRef.current?.branch()}>branch</button>
        <button
          data-testid="btn-zoom-out"
          onClick={() => controllerRef.current?.zoomTo((controllerRef.current?.getState().semanticScale ?? 2) - 1)}
        >zoom −</button>
        <button
          data-testid="btn-zoom-in"
          onClick={() => controllerRef.current?.zoomTo((controllerRef.current?.getState().semanticScale ?? 2) + 1)}
        >zoom +</button>
        <select
          data-testid="label-mode"
          value={labelMode}
          onChange={(e) => setLabelMode(e.target.value as "auto" | "on" | "off")}
          title="Label mode (classic viewer parity)"
        >
          <option value="auto">labels: auto</option>
          <option value="on">labels: on</option>
          <option value="off">labels: off</option>
        </select>
        <button data-testid="btn-label-types" onClick={() => setShowLabelPicker((v) => !v)}>
          types…
        </button>
        <div className="spacer" />
        <span data-testid="focus-title">{focusTitle}</span>
      </div>

      <div className="stage" data-testid="stage">
        <KnowledgeAtlas
          key={atlasKey}
          ref={controllerRef}
          dataSource={fixture.source}
          initialFocus={fixture.defaultFocus}
          config={config}
          labelMode={labelMode}
          labelTypes={labelTypes}
          onEvent={onEvent}
          onOpenItem={(id) => setOpened(id)}
        />
        {showLabelPicker && (
          <div className="explain" style={{ position: "absolute", top: 8, right: 8, maxWidth: 240 }} data-testid="label-picker">
            <div style={{ marginBottom: 4, fontWeight: 600 }}>
              label types <button style={{ float: "right" }} onClick={() => setShowLabelPicker(false)}>×</button>
            </div>
            {ALL_LABEL_TYPES.map((t) => (
              <label key={t} style={{ display: "inline-flex", alignItems: "center", gap: 3, marginRight: 8 }}>
                <input
                  type="checkbox"
                  checked={labelTypes.includes(t)}
                  onChange={(e) =>
                    setLabelTypes((prev) => (e.target.checked ? [...prev, t] : prev.filter((x) => x !== t)))
                  }
                />
                {t}
              </label>
            ))}
          </div>
        )}
        {opened && (
          <div
            className="explain"
            style={{ position: "absolute", top: 8, left: 8 }}
            data-testid="opened-item"
          >
            open: {opened} <button onClick={() => setOpened(null)}>×</button>
          </div>
        )}
      </div>

      <div className="side">
        <div className="panel">
          <h3>Telemetry</h3>
          <div className="hud" data-testid="hud">
            <span>nodes</span><b data-testid="hud-nodes">{stats?.nodeCount ?? "–"}</b>
            <span>aggregates</span><b>{stats?.aggregateCount ?? "–"}</b>
            <span>edges</span><b>{stats?.edgeCount ?? "–"}</b>
            <span>horizon</span><b data-testid="hud-horizon">{stats?.horizonCount ?? "–"}</b>
            <span>scene build</span><b data-testid="hud-build">{stats ? `${stats.sceneBuildMs.toFixed(1)}ms` : "–"}</b>
            <span>layout</span><b>{stats ? `${stats.layoutMs.toFixed(1)}ms` : "–"}</b>
            <span>displacement</span><b>{stats ? Math.round(stats.layoutDisplacement) : "–"}</b>
            {fixture.name === "scaled-1M" && (
              <>
                <span>corpus</span><b>{SCALED_TOTAL_LEAVES.toLocaleString()}</b>
              </>
            )}
          </div>
        </div>

        <div className="panel" data-testid="horizon-panel">
          <h3>Discovery horizon</h3>
          {horizon.length === 0 && <div className="why">no candidates (zoom in?)</div>}
          {horizon.map((grp) => (
            <div key={grp.cls}>
              <div className="cls-head" data-testid={`cls-${grp.cls}`}>
                {grp.cls}
                {grp.omittedCount > 0 && <span className="omit"> · {grp.omittedCount} more</span>}
              </div>
              {grp.candidates.map((c) => {
                const explain = () =>
                  controllerRef.current?.requestExplanation({
                    kind: "candidate",
                    id: c.id,
                    focusId: controllerRef.current?.getState().focusId ?? "",
                    cls: grp.cls,
                  });
                return (
                  <div key={c.id} style={{ display: "flex", gap: 4, alignItems: "flex-start" }}>
                    <button
                      className="candidate"
                      data-testid="candidate"
                      style={{ flex: 1, width: "auto" }}
                      onClick={() => controllerRef.current?.focus(c.id)}
                      onContextMenu={(ev) => {
                        ev.preventDefault();
                        explain();
                      }}
                    >
                      {c.item.title}
                      <div className="why">{c.reason.text}</div>
                    </button>
                    <button
                      className="candidate"
                      data-testid="candidate-why"
                      style={{ flex: "0 0 auto", width: "auto", padding: "4px 7px" }}
                      title="Why does this appear?"
                      aria-label={`Explain ${c.item.title}`}
                      onClick={explain}
                    >
                      ?
                    </button>
                  </div>
                );
              })}
            </div>
          ))}
        </div>

        {explanation && (
          <div className="panel">
            <h3>Explanation</h3>
            <div className="explain" data-testid="explanation">
              {explanation.summary.text}
              {explanation.evidence?.map((ev, i) => (
                <div key={i} className="why">• {ev.text}</div>
              ))}
            </div>
          </div>
        )}

        <div className="panel" data-testid="trail-panel">
          <h3>Trail</h3>
          {trail && (
            <>
              <div className="why">
                branch {trail.activeBranchId} of {trail.branches.length} · pinned {trail.pinned.length}
              </div>
              {trail.branches
                .find((b) => b.id === trail.activeBranchId)
                ?.steps.map((s, i) => (
                  <div key={s.id} className={`trail-step${i === trail.cursor ? " current" : ""}`}>
                    {i === trail.cursor ? "▸ " : "  "}
                    {s.focusId}
                    {s.via?.cls ? ` (via ${s.via.cls})` : ""}
                  </div>
                ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
