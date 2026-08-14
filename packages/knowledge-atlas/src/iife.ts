/**
 * IIFE entry for the Curiosity Engine wiki-view embedding (PLAN §14.4):
 * `window.KnowledgeAtlas.mount(container, { data, initialFocus, … })`
 * — framework-free, self-contained, vendorable next to d3.min.js.
 */

import { AtlasEngine } from "./core/engine.ts";
import { CanvasRenderer } from "./renderer/canvas.ts";
import { AtlasMinimap } from "./renderer/minimap.ts";
import { resolveTheme } from "./renderer/theme.ts";
import { CuriosityDataSource, type CEData } from "./datasources/curiosity.ts";
import { coreRadius, isFullGraphScene, populatedShellBands } from "./core/layout/hybrid.ts";
import { hybridLayout } from "./core/layout/hybrid.ts";
import { clampCameraScale, projectCamera, responsiveNodeScale, wheelZoomFactor } from "./interaction/camera.ts";
import { boundaryHoverDelay, projectedBoundaryDepth } from "./interaction/hover.ts";
import { AggregateTooltip, LONG_PRESS_MS } from "./interaction/tooltip.ts";
import { DEFAULT_PHYSICS, type AtlasConfig, type AtlasEvent, type AtlasPhysics, type LayoutResult } from "./core/types.ts";
import type { Camera } from "./renderer/types.ts";

export type MountOptions = {
  data: CEData;
  initialFocus?: string;
  config?: AtlasConfig;
  /** Label policy (classic parity): auto (default) / on / off. */
  labelMode?: "auto" | "on" | "off";
  /** Types whose labels are eligible; null/undefined = all types. */
  labelTypes?: readonly string[] | null;
  onOpenItem?: (id: string) => void;
  onEvent?: (event: AtlasEvent) => void;
};

export type MountHandle = {
  engine: AtlasEngine;
  /** Update the label policy live (wired to the host's label picker). */
  setLabels: (mode: "auto" | "on" | "off", types?: readonly string[] | null) => void;
  /** Update the classic force controls and re-solve the central graph. */
  setPhysics: (physics: Partial<AtlasPhysics>) => void;
  destroy: () => void;
};

export function mount(container: HTMLElement, opts: MountOptions): MountHandle {
  const source = new CuriosityDataSource(opts.data, { seed: opts.config?.seed });
  const engine = new AtlasEngine(source, opts.config);
  const renderer = new CanvasRenderer();

  const canvas = document.createElement("canvas");
  canvas.style.cssText =
    "display:block;width:100%;height:100%;outline:none;touch-action:none;" +
    "user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;";
  canvas.tabIndex = 0;
  container.appendChild(canvas);
  renderer.mount(canvas);

  const mode = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  let theme = resolveTheme(undefined, source.palette, mode);
  if (getComputedStyle(container).position === "static") container.style.position = "relative";
  const tooltip = new AggregateTooltip(container, theme, (aggId) => {
    const agg = engine.snapshot().scene?.aggregates.find((a) => a.id === aggId);
    const member = agg?.memberIds[0];
    tooltip.hide();
    if (member) {
      engine.focus(member, "user");
    }
  });
  let longPressTimer = 0;
  let longPressFired = false;
  const maybeShowTooltip = (id: string | null, clientX: number, clientY: number) => {
    const agg = id ? engine.snapshot().scene?.aggregates.find((a) => a.id === id) : undefined;
    if (agg) {
      const rect = container.getBoundingClientRect();
      tooltip.show(agg, clientX - rect.left, clientY - rect.top, rect);
    } else {
      tooltip.hide();
    }
  };

  let prevLayout: LayoutResult | undefined;
  let animStart = 0;
  let raf = 0;
  let hoverId: string | null = null;
  let hoverTimer = 0;
  let pendingHoverId: string | null = null;
  let projectedForHover: LayoutResult | undefined;
  let hoverScene = null as ReturnType<typeof engine.snapshot>["scene"];
  let hoverBands = 1;
  const hoverShell = new Map<string, number>();
  const camera: Camera = { x: 0, y: 0, scale: 1 };
  let viewport = { width: container.clientWidth || 800, height: container.clientHeight || 600 };
  const layoutKind = opts.config?.layout ?? "focus";
  const isHybrid = layoutKind === "hybrid" || layoutKind === "adaptive-hybrid";
  const boundaryShape = opts.config?.boundaryShape;
  let overviewLayout: LayoutResult | undefined;
  let overviewScene = null as ReturnType<typeof engine.snapshot>["scene"];
  let overviewTimer = 0;
  let overviewPending = false;
  let destroyed = false;
  let overviewPhysics = { ...DEFAULT_PHYSICS, ...opts.config?.physics };
  let densityTimer = 0;
  const scheduleDensity = () => {
    clearTimeout(densityTimer);
    densityTimer = window.setTimeout(() => engine.setViewScale(camera.scale), 120);
  };
  const minimap = new AtlasMinimap(container, (worldX, worldY) => {
    camera.x = -worldX * camera.scale;
    camera.y = -worldY * camera.scale;
    draw(1);
  });

  const labelState = {
    mode: opts.labelMode ?? "auto",
    types: opts.labelTypes ? new Set(opts.labelTypes) : null,
  } as { mode: "auto" | "on" | "off"; types: ReadonlySet<string> | null };

  const draw = (progress: number) => {
    const snap = engine.snapshot();
    if (!snap.scene || !snap.layout) return;
    const full = isFullGraphScene(snap.scene);
    const bands = Math.max(1, populatedShellBands(snap.scene));
    const layout = snap.layout;
    const nodeScale = responsiveNodeScale(viewport);
    const projected = projectCamera(
      layout,
      snap.scene,
      camera,
      isHybrid && !full,
      nodeScale,
      viewport,
      boundaryShape,
      isHybrid && full,
    );
    projectedForHover = projected;
    hoverBands = bands;
    if (hoverScene !== snap.scene) {
      hoverScene = snap.scene;
      hoverShell.clear();
      for (const node of snap.scene.nodes) if (node.shell) hoverShell.set(node.id, node.shell);
      for (const aggregate of snap.scene.aggregates) {
        if (aggregate.shell) hoverShell.set(aggregate.id, aggregate.shell);
      }
    }
    const projectedPrev = prevLayout
      ? projectCamera(
          prevLayout,
          snap.scene,
          camera,
          isHybrid && !full,
          nodeScale,
          viewport,
          boundaryShape,
          isHybrid && full,
        )
      : undefined;
    engine.hitTester.update(
      projected.positions,
      snap.scene.nodes.map((node) => node.id),
      snap.scene.aggregates.map((aggregate) => aggregate.id),
    );
    renderer.render({
      scene: snap.scene,
      layout: projected,
      prevLayout: projectedPrev,
      progress,
      camera: { x: 0, y: 0, scale: 1 },
      viewport,
      dpr: window.devicePixelRatio || 1,
      theme,
      hoverId,
      selection: new Set(snap.state.selection),
      pinned: new Set(snap.state.pinned),
      maxLabels: snap.stats?.labelCount ?? opts.config?.budget?.maxLabels ?? 60,
      showHorizonRing: (opts.config?.layout ?? "focus") !== "force" && !full,
      coreRadius: isHybrid ? coreRadius(viewport, bands) : undefined,
      shellBands: bands,
      boundaryShape,
      labelMode: labelState.mode,
      labelTypes: labelState.types,
    });
    canvas.dataset.hoverId = hoverId ?? "";
    if (full) {
      overviewLayout = snap.layout;
      overviewScene = snap.scene;
    }
    minimap.update(overviewLayout, overviewScene, camera, viewport, theme, boundaryShape);
  };
  const themeObserver = new MutationObserver(() => {
    const nextMode = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    theme = resolveTheme(undefined, source.palette, nextMode);
    tooltip.setTheme(theme);
    draw(1);
  });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const ensureOverview = () => {
    if (destroyed || overviewScene || overviewPending || !source.getOverviewScene) return;
    overviewPending = true;
    // Let the primary scene paint first; the overview solve is secondary
    // work and must not delay first interaction on a phone.
    overviewTimer = window.setTimeout(() => {
      Promise.resolve(source.getOverviewScene?.(engine.getState().focusId))
        .then((scene) => {
          if (!scene || destroyed) return;
          overviewScene = scene;
          overviewLayout = hybridLayout.layout(scene, {
            viewport,
            seed: opts.config?.seed ?? 42,
            boundaryShape,
            physics: overviewPhysics,
          });
          draw(1);
        })
        .finally(() => {
          overviewPending = false;
        });
    }, 0);
  };
  const animate = () => {
    const t = Math.min(1, (performance.now() - animStart) / 300);
    draw(t);
    if (t < 1) raf = requestAnimationFrame(animate);
    else prevLayout = engine.snapshot().layout ?? undefined;
  };
  const off = engine.on((e) => {
    if (e.kind === "scene-ready") {
      clearHoverIntent(true);
      cancelAnimationFrame(raf);
      animStart = performance.now();
      raf = requestAnimationFrame(animate);
      if (!isFullGraphScene(engine.snapshot().scene!)) ensureOverview();
    }
    if (e.kind === "item-open-requested") opts.onOpenItem?.(e.id);
    opts.onEvent?.(e);
  });

  const toScene = (ev: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ev.clientX - rect.left - viewport.width / 2,
      y: ev.clientY - rect.top - viewport.height / 2,
    };
  };
  let dragging = false;
  let dragMoved = false;
  let last = { x: 0, y: 0 };
  // Touch: pinch = semantic zoom, double-tap = open (see React adapter).
  const pointers = new Map<number, { x: number; y: number }>();
  let pinchDist = 0;
  let pinched = false;
  let lastTap = { t: 0, x: 0, y: 0 };
  let lastClickFocus = { id: "", t: 0 };
  const clearHoverIntent = (clearVisible = false) => {
    clearTimeout(hoverTimer);
    hoverTimer = 0;
    pendingHoverId = null;
    if (clearVisible && hoverId !== null) {
      hoverId = null;
      tooltip.hide();
      draw(1);
    }
  };
  const hoverDelay = (id: string): number => {
    const projected = projectedForHover;
    const scene = hoverScene;
    if (!projected || !scene) return 0;
    const shell = hoverShell.get(id);
    const inBoundary = Boolean(shell) || Boolean(projected.boundaryIds?.has(id));
    if (!inBoundary) return 0;
    const point = projected.positions.get(id);
    const depth = shell
      ? Math.max(0, Math.min(1, (shell - 1) / 3))
      : point
        ? projectedBoundaryDepth(point, viewport, boundaryShape, hoverBands)
        : 0;
    return boundaryHoverDelay(depth, scene.stats?.totalNodes ?? scene.nodes.length);
  };
  const scheduleHover = (hit: ReturnType<typeof engine.hitTester.pointAt>, ev: PointerEvent) => {
    const id = hit?.id ?? null;
    canvas.style.cursor = id ? "pointer" : "default";
    if (id === hoverId || id === pendingHoverId) return;
    clearHoverIntent(false);
    if (hoverId !== null) {
      hoverId = null;
      tooltip.hide();
      draw(1);
    }
    if (!hit || ev.pointerType === "touch") return;
    const hitId = hit.id;
    const { clientX, clientY } = ev;
    const commit = () => {
      if (pendingHoverId !== hitId || dragging) return;
      pendingHoverId = null;
      hoverTimer = 0;
      hoverId = hitId;
      maybeShowTooltip(hit.kind === "aggregate" ? hitId : null, clientX, clientY);
      draw(1);
    };
    const delay = hoverDelay(hitId);
    pendingHoverId = hitId;
    if (delay === 0) commit();
    else hoverTimer = window.setTimeout(commit, delay);
  };
  const onDown = (ev: PointerEvent) => {
    clearHoverIntent(true);
    pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
      pinched = true;
      dragging = false;
      return;
    }
    dragging = true;
    dragMoved = false;
    last = { x: ev.clientX, y: ev.clientY };
    if (ev.pointerType === "touch") {
      tooltip.hide();
      const p = toScene(ev);
      const hit = engine.hitTester.pointAt(p.x, p.y, 12);
      if (hit?.kind === "aggregate") {
        const { clientX, clientY } = ev;
        longPressTimer = window.setTimeout(() => {
          if (!dragMoved && !pinched) {
            maybeShowTooltip(hit.id, clientX, clientY);
            longPressFired = true;
          }
        }, LONG_PRESS_MS);
      }
    }
  };
  const onMove = (ev: PointerEvent) => {
    if (pointers.has(ev.pointerId)) {
      pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    }
    if (pointers.size === 2 && pinchDist > 0) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      const ratio = d / pinchDist;
      if (ratio > 1.025 || ratio < 0.975) {
        const rect = canvas.getBoundingClientRect();
        const anchor = {
          x: (a.x + b.x) / 2 - rect.left - viewport.width / 2,
          y: (a.y + b.y) / 2 - rect.top - viewport.height / 2,
        };
        const old = camera.scale;
        camera.scale = clampCameraScale(old * Math.max(0.94, Math.min(1.06, ratio)));
        const k = camera.scale / old;
        camera.x = anchor.x - (anchor.x - camera.x) * k;
        camera.y = anchor.y - (anchor.y - camera.y) * k;
        scheduleDensity();
        pinchDist = d;
        draw(1);
      }
      return;
    }
    if (dragging) {
      const dx = ev.clientX - last.x;
      const dy = ev.clientY - last.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) dragMoved = true;
      if (dragMoved) {
        camera.x += dx;
        camera.y += dy;
        draw(1);
        last = { x: ev.clientX, y: ev.clientY };
      }
      return;
    }
    const p = toScene(ev);
    const hit = engine.hitTester.pointAt(p.x, p.y);
    scheduleHover(hit, ev);
  };
  const onUp = (ev: PointerEvent) => {
    clearTimeout(longPressTimer);
    pointers.delete(ev.pointerId);
    if (longPressFired) {
      longPressFired = false;
      dragging = false;
      dragMoved = false;
      return;
    }
    if (pointers.size === 0 && pinched) {
      pinched = false;
      pinchDist = 0;
      dragging = false;
      dragMoved = false;
      return;
    }
    if (pinched) return;
    const wasDrag = dragMoved;
    dragging = false;
    dragMoved = false;
    if (wasDrag) {
      return;
    }
    const p = toScene(ev);
    const hit = engine.hitTester.pointAt(p.x, p.y, ev.pointerType === "touch" ? 12 : 4);
    if (ev.pointerType === "touch") {
      const now = performance.now();
      const isDouble =
        now - lastTap.t < 350 && Math.hypot(ev.clientX - lastTap.x, ev.clientY - lastTap.y) < 30;
      lastTap = { t: now, x: ev.clientX, y: ev.clientY };
      if (isDouble) {
        if (hit?.kind === "node") engine.openItem(hit.id);
        else if (performance.now() - lastClickFocus.t < 700 && lastClickFocus.id) {
          engine.openItem(lastClickFocus.id);
        }
        return;
      }
    }
    if (!hit) return;
    if (hit.kind === "node") {
      lastClickFocus = { id: hit.id, t: performance.now() };
      engine.focus(hit.id, "user");
    } else {
      // Aggregates are selectable: click focuses the top member so the
      // region unfolds into the graph zone.
      const agg = engine.snapshot().scene?.aggregates.find((a) => a.id === hit.id);
      const member = agg?.memberIds[0];
      if (member) {
        engine.focus(member, "user");
      } else {
        engine.zoomTo(engine.getState().semanticScale + 1);
      }
    }
  };
  const onCancel = (ev: PointerEvent) => {
    pointers.delete(ev.pointerId);
    if (pointers.size === 0) {
      pinched = false;
      pinchDist = 0;
      dragging = false;
      dragMoved = false;
    }
  };
  const onDbl = (ev: MouseEvent) => {
    const p = toScene(ev);
    const hit = engine.hitTester.pointAt(p.x, p.y);
    if (hit?.kind === "node") engine.openItem(hit.id);
    else if (performance.now() - lastClickFocus.t < 600 && lastClickFocus.id) {
      engine.openItem(lastClickFocus.id);
    } else if (engine.getState().focusId) {
      // A focus-driven scene transition may move the node between the
      // two clicks; opening the active focus preserves classic behavior.
      engine.openItem(engine.getState().focusId!);
    }
  };
  const onWheel = (ev: WheelEvent) => {
    ev.preventDefault();
    clearHoverIntent(true);
    if (isHybrid) {
      // One geometric zoom model across the plotting area. The graph
      // zooms around the pointer; the boundary remains fixed geography.
      const anchor = toScene(ev);
      const old = camera.scale;
      camera.scale = clampCameraScale(old * wheelZoomFactor(ev.deltaY, ev.deltaMode));
      const k = camera.scale / old;
      camera.x = anchor.x - (anchor.x - camera.x) * k;
      camera.y = anchor.y - (anchor.y - camera.y) * k;
      scheduleDensity();
      draw(1);
      return;
    }
    engine.zoomTo(engine.getState().semanticScale + (ev.deltaY < 0 ? 0.2 : -0.2));
  };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onCancel);
  canvas.addEventListener("dblclick", onDbl);
  canvas.addEventListener("wheel", onWheel, { passive: false });

  const ro = new ResizeObserver(() => {
    viewport = { width: container.clientWidth, height: container.clientHeight };
    engine.resize(viewport.width, viewport.height);
    engine.setViewScale(camera.scale);
    draw(1);
  });
  ro.observe(container);

  engine.resize(viewport.width, viewport.height);
  engine.setViewScale(camera.scale);
  engine.start(opts.initialFocus);

  return {
    engine,
    setLabels: (mode, types) => {
      labelState.mode = mode;
      if (types !== undefined) labelState.types = types ? new Set(types) : null;
      draw(1);
    },
    setPhysics: (physics) => {
      overviewPhysics = { ...overviewPhysics, ...physics };
      if (overviewScene) {
        overviewLayout = hybridLayout.layout(overviewScene, {
          viewport,
          seed: opts.config?.seed ?? 42,
          boundaryShape,
          physics: overviewPhysics,
        });
      }
      engine.setPhysics(physics);
    },
    destroy: () => {
      destroyed = true;
      cancelAnimationFrame(raf);
      clearTimeout(longPressTimer);
      clearTimeout(densityTimer);
      clearTimeout(overviewTimer);
      clearTimeout(hoverTimer);
      themeObserver.disconnect();
      tooltip.destroy();
      minimap.destroy();
      ro.disconnect();
      off();
      renderer.destroy();
      engine.destroy();
      canvas.remove();
    },
  };
}
