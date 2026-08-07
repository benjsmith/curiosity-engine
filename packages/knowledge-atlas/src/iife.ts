/**
 * IIFE entry for the Curiosity Engine wiki-view embedding (PLAN §14.4):
 * `window.KnowledgeAtlas.mount(container, { data, initialFocus, … })`
 * — framework-free, self-contained, vendorable next to d3.min.js.
 */

import { AtlasEngine } from "./core/engine.ts";
import { CanvasRenderer } from "./renderer/canvas.ts";
import { resolveTheme } from "./renderer/theme.ts";
import { CuriosityDataSource, type CEData } from "./datasources/curiosity.ts";
import { coreRadius } from "./core/layout/hybrid.ts";
import { applyLens, commitLensTarget, LENS_STEP, type LensState } from "./interaction/lens.ts";
import { AggregateTooltip, LONG_PRESS_MS } from "./interaction/tooltip.ts";
import type { AtlasConfig, AtlasEvent, LayoutResult } from "./core/types.ts";
import type { Camera } from "./renderer/types.ts";

export type MountOptions = {
  data: CEData;
  initialFocus?: string;
  config?: AtlasConfig;
  onOpenItem?: (id: string) => void;
  onEvent?: (event: AtlasEvent) => void;
};

export type MountHandle = {
  engine: AtlasEngine;
  destroy: () => void;
};

export function mount(container: HTMLElement, opts: MountOptions): MountHandle {
  const source = new CuriosityDataSource(opts.data, { seed: opts.config?.seed });
  const engine = new AtlasEngine(source, opts.config);
  const renderer = new CanvasRenderer();

  const canvas = document.createElement("canvas");
  canvas.style.cssText = "display:block;width:100%;height:100%;outline:none;touch-action:none;";
  canvas.tabIndex = 0;
  container.appendChild(canvas);
  renderer.mount(canvas);

  const mode = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const theme = resolveTheme(undefined, source.palette, mode);
  if (getComputedStyle(container).position === "static") container.style.position = "relative";
  const tooltip = new AggregateTooltip(container, theme);
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
  const camera: Camera = { x: 0, y: 0, scale: 1 };
  let viewport = { width: container.clientWidth || 800, height: container.clientHeight || 600 };
  const layoutKind = opts.config?.layout ?? "focus";
  const isHybrid = layoutKind === "hybrid" || layoutKind === "adaptive-hybrid";
  const lens: LensState = { pull: 0, angle: 0 };

  const draw = (progress: number) => {
    const snap = engine.snapshot();
    if (!snap.scene || !snap.layout) return;
    const layout = isHybrid ? applyLens(snap.layout, lens, coreRadius(viewport)) : snap.layout;
    renderer.render({
      scene: snap.scene,
      layout,
      prevLayout,
      progress,
      camera,
      viewport,
      dpr: window.devicePixelRatio || 1,
      theme,
      hoverId,
      selection: new Set(snap.state.selection),
      pinned: new Set(snap.state.pinned),
      maxLabels: opts.config?.budget?.maxLabels ?? 40,
      showHorizonRing: (opts.config?.layout ?? "focus") !== "force",
      coreRadius: isHybrid ? coreRadius(viewport) : undefined,
    });
  };
  const animate = () => {
    const t = Math.min(1, (performance.now() - animStart) / 300);
    draw(t);
    if (t < 1) raf = requestAnimationFrame(animate);
    else prevLayout = engine.snapshot().layout ?? undefined;
  };

  const off = engine.on((e) => {
    if (e.kind === "scene-ready") {
      lens.pull = 0;
      cancelAnimationFrame(raf);
      animStart = performance.now();
      raf = requestAnimationFrame(animate);
    }
    if (e.kind === "item-open-requested") opts.onOpenItem?.(e.id);
    opts.onEvent?.(e);
  });

  const toScene = (ev: MouseEvent) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (ev.clientX - rect.left - viewport.width / 2 - camera.x) / camera.scale,
      y: (ev.clientY - rect.top - viewport.height / 2 - camera.y) / camera.scale,
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
  const onDown = (ev: PointerEvent) => {
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
      if (ratio > 1.12) {
        engine.zoomTo(engine.getState().semanticScale + 0.2);
        pinchDist = d;
      } else if (ratio < 0.89) {
        engine.zoomTo(engine.getState().semanticScale - 0.2);
        pinchDist = d;
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
        last = { x: ev.clientX, y: ev.clientY };
        draw(1);
      }
      return;
    }
    const p = toScene(ev);
    const hit = engine.hitTester.pointAt(p.x, p.y);
    const id = hit?.id ?? null;
    if (id !== hoverId) {
      hoverId = id;
      canvas.style.cursor = id ? "pointer" : "default";
      if (ev.pointerType !== "touch") {
        maybeShowTooltip(hit?.kind === "aggregate" ? id : null, ev.clientX, ev.clientY);
      }
      draw(1);
    }
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
    if (wasDrag) return;
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
      camera.x = 0;
      camera.y = 0;
      lastClickFocus = { id: hit.id, t: performance.now() };
      engine.focus(hit.id, "user");
    } else {
      // Aggregates are selectable: click focuses the top member so the
      // region unfolds into the graph zone.
      const agg = engine.snapshot().scene?.aggregates.find((a) => a.id === hit.id);
      const member = agg?.memberIds[0];
      if (member) {
        camera.x = 0;
        camera.y = 0;
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
    }
  };
  const onWheel = (ev: WheelEvent) => {
    ev.preventDefault();
    if (isHybrid) {
      const p = toScene(ev);
      const rCore = coreRadius(viewport);
      if (Math.hypot(p.x, p.y) <= rCore) {
        camera.scale = Math.max(0.5, Math.min(3, camera.scale * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
        draw(1);
        return;
      }
      if (ev.deltaY < 0) {
        lens.angle = Math.atan2(p.y, p.x);
        lens.pull = Math.min(1, lens.pull + LENS_STEP);
        if (lens.pull >= 1) {
          lens.pull = 0;
          commitLensTarget(engine, { pull: 1, angle: lens.angle }, rCore);
        }
      } else {
        lens.pull = Math.max(0, lens.pull - LENS_STEP);
      }
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
    draw(1);
  });
  ro.observe(container);

  engine.resize(viewport.width, viewport.height);
  engine.start(opts.initialFocus);

  return {
    engine,
    destroy: () => {
      cancelAnimationFrame(raf);
      clearTimeout(longPressTimer);
      tooltip.destroy();
      ro.disconnect();
      off();
      renderer.destroy();
      engine.destroy();
      canvas.remove();
    },
  };
}
