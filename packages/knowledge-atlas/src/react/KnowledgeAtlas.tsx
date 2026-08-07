/**
 * React adapter (PLAN §13): a thin component over AtlasEngine +
 * CanvasRenderer. Owns the canvas, ResizeObserver, rAF transitions,
 * pointer/keyboard wiring, and nothing else — no context requirement,
 * no global state. StrictMode-safe (idempotent mount/destroy).
 */

import { forwardRef, useEffect, useRef } from "react";
import { AtlasEngine } from "../core/engine.ts";
import { CanvasRenderer } from "../renderer/canvas.ts";
import { resolveTheme } from "../renderer/theme.ts";
import { CuriosityDataSource } from "../datasources/curiosity.ts";
import { coreRadius, isFullGraphScene } from "../core/layout/hybrid.ts";
import { inCoreZone } from "../core/geometry.ts";
import { applyLens, commitLensTarget, LENS_STEP, type LensState } from "../interaction/lens.ts";
import { AggregateTooltip, LONG_PRESS_MS } from "../interaction/tooltip.ts";
import type { AtlasController, AtlasEvent, LayoutResult } from "../core/types.ts";
import type { KnowledgeAtlasProps } from "./props.ts";
import type { Camera } from "../renderer/types.ts";

const TRANSITION_MS = 300;
const REDUCED_MS = 100;

export const KnowledgeAtlas = forwardRef<AtlasController, KnowledgeAtlasProps>(
  function KnowledgeAtlas(props, ref) {
    const hostRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const liveRef = useRef<HTMLDivElement>(null);
    const engineRef = useRef<AtlasEngine | null>(null);

    // Recreate the engine when the data source changes (workspace swap).
    useEffect(() => {
      const host = hostRef.current;
      const canvas = canvasRef.current;
      if (!host || !canvas) return;

      const engine = new AtlasEngine(props.dataSource, props.config);
      engineRef.current = engine;
      // Populate the forwarded controller ref here rather than via
      // useImperativeHandle: that hook is a LAYOUT effect and would run
      // before this passive effect, capturing null.
      if (typeof ref === "function") ref(engine);
      else if (ref) ref.current = engine;
      const renderer = new CanvasRenderer();
      renderer.mount(canvas);

      const reducedMotion =
        props.config?.reducedMotion ??
        (typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches);
      const transitionMs = reducedMotion ? REDUCED_MS : TRANSITION_MS;

      const dataPalette =
        props.dataSource instanceof CuriosityDataSource ? props.dataSource.palette : undefined;
      const mode =
        typeof document !== "undefined" &&
        (document.documentElement.dataset.theme === "light" ||
          (document.documentElement.dataset.theme === undefined &&
            typeof matchMedia !== "undefined" &&
            matchMedia("(prefers-color-scheme: light)").matches))
          ? "light"
          : "dark";
      const theme = resolveTheme(props.theme, dataPalette, mode);
      const tooltip = new AggregateTooltip(host, theme, (aggId) => {
        // Clicking the tooltip centres the community (iteration-6).
        const agg = engine.snapshot().scene?.aggregates.find((a) => a.id === aggId);
        const member = agg?.memberIds[0];
        tooltip.hide();
        if (member) {
          camera.x = 0;
          camera.y = 0;
          engine.focus(member, "user");
        }
      });
      let longPressTimer = 0;
      let longPressFired = false;

      const maybeShowTooltip = (id: string | null, clientX: number, clientY: number) => {
        const agg = id ? engine.snapshot().scene?.aggregates.find((a) => a.id === id) : undefined;
        if (agg) {
          const rect = host.getBoundingClientRect();
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
      let viewport = { width: host.clientWidth || 800, height: host.clientHeight || 600 };
      const layoutKind = props.config?.layout ?? "focus";
      const isHybrid = layoutKind === "hybrid" || layoutKind === "adaptive-hybrid";
      const lens: LensState = { pull: 0, angle: 0 };

      const isFull = () => {
        const sc = engine.snapshot().scene;
        return sc ? isFullGraphScene(sc) : false;
      };
      const draw = (progress: number) => {
        const snap = engine.snapshot();
        if (!snap.scene || !snap.layout) return;
        const rCore = coreRadius(viewport);
        const full = isFullGraphScene(snap.scene);
        const layout = isHybrid && !full ? applyLens(snap.layout, lens, rCore) : snap.layout;
        renderer.render({
          scene: snap.scene,
          layout,
          prevLayout,
          progress,
          camera,
          viewport,
          dpr: typeof devicePixelRatio === "number" ? devicePixelRatio : 1,
          theme,
          hoverId,
          selection: new Set(snap.state.selection),
          pinned: new Set(snap.state.pinned),
          maxLabels: props.config?.budget?.maxLabels ?? 60,
          showHorizonRing: (props.config?.layout ?? "focus") !== "force" && !full,
          coreRadius: isHybrid && !full ? coreRadius(viewport) : undefined,
        });
      };

      const animate = () => {
        const t = Math.min(1, (performance.now() - animStart) / transitionMs);
        draw(t);
        if (t < 1) raf = requestAnimationFrame(animate);
        else prevLayout = engine.snapshot().layout ?? undefined;
      };

      const off = engine.on((e: AtlasEvent) => {
        if (e.kind === "scene-ready") {
          lens.pull = 0; // a new scene resets any in-flight lens pull
          cancelAnimationFrame(raf);
          animStart = performance.now();
          raf = requestAnimationFrame(animate);
          const focus = engine.snapshot().scene?.focus;
          if (liveRef.current && focus) {
            liveRef.current.textContent = `Focused: ${focus.title} (${focus.type})`;
          }
        }
        if (e.kind === "item-open-requested") props.onOpenItem?.(e.id);
        props.onEvent?.(e);
      });

      // ── pointer ─────────────────────────────────────────────────
      const toScene = (ev: PointerEvent | MouseEvent | WheelEvent) => {
        const rect = canvas.getBoundingClientRect();
        return {
          x: (ev.clientX - rect.left - viewport.width / 2 - camera.x) / camera.scale,
          y: (ev.clientY - rect.top - viewport.height / 2 - camera.y) / camera.scale,
        };
      };
      let dragging = false;
      let dragMoved = false;
      let last = { x: 0, y: 0 };
      // Touch: two-finger pinch drives SEMANTIC zoom (the wheel
      // equivalent) and double-tap opens (the dblclick equivalent).
      const pointers = new Map<number, { x: number; y: number }>();
      let pinchDist = 0;
      let pinched = false;
      let lastTap = { t: 0, x: 0, y: 0 };
      // A click-focus makes the layout shift under the cursor, so the
      // second click of a double-click can land on empty canvas. Track
      // the last click-focused node and let dblclick fall back to it.
      let lastClickFocus = { id: "", t: 0 };
      const onPointerDown = (ev: PointerEvent) => {
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
        canvas.setPointerCapture(ev.pointerId);
        // Touch-and-hold on a grouping bubble shows its annotation.
        if (ev.pointerType === "touch") {
          tooltip.hide(); // a new tap dismisses any held-open tooltip
          const p = toScene(ev);
          const hit = engine.hitTester.pointAt(p.x, p.y, 12);
          if (hit?.kind === "aggregate") {
            const { clientX, clientY } = ev;
            longPressTimer = window.setTimeout(() => {
              if (!dragMoved && !pinched) {
                maybeShowTooltip(hit.id, clientX, clientY);
                longPressFired = true; // the hold consumed this gesture
              }
            }, LONG_PRESS_MS);
          }
        }
      };
      const onPointerMove = (ev: PointerEvent) => {
        if (pointers.has(ev.pointerId)) {
          pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
        }
        if (pointers.size === 2 && pinchDist > 0) {
          const [a, b] = [...pointers.values()];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          const ratio = d / pinchDist;
          if (ratio > 1.12 || ratio < 0.89) {
            const zoomIn = ratio > 1;
            pinchDist = d;
            if (isHybrid) {
              // Same lens model as the wheel, anchored at the pinch
              // midpoint: core = geometric zoom, rim = sector pull.
              const rect = canvas.getBoundingClientRect();
              const mx = ((a.x + b.x) / 2 - rect.left - viewport.width / 2 - camera.x) / camera.scale;
              const my = ((a.y + b.y) / 2 - rect.top - viewport.height / 2 - camera.y) / camera.scale;
              const rCore = coreRadius(viewport);
              if (isFull() || inCoreZone(mx, my, viewport)) {
                camera.scale = Math.max(0.5, Math.min(3, camera.scale * (zoomIn ? 1.12 : 1 / 1.12)));
              } else if (zoomIn) {
                lens.angle = Math.atan2(my, mx);
                lens.pull = Math.min(1, lens.pull + LENS_STEP);
                if (lens.pull >= 1) {
                  lens.pull = 0;
                  commitLensTarget(engine, { pull: 1, angle: lens.angle }, rCore);
                }
              } else {
                lens.pull = Math.max(0, lens.pull - LENS_STEP);
              }
              draw(1);
            } else {
              engine.zoomTo(engine.getState().semanticScale + (zoomIn ? 0.2 : -0.2));
            }
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
          engine.hover(id);
          canvas.style.cursor = id ? "pointer" : "default";
          // Grouping bubbles annotate themselves on hover.
          if (ev.pointerType !== "touch") {
            maybeShowTooltip(hit?.kind === "aggregate" ? id : null, ev.clientX, ev.clientY);
          }
          draw(1);
        }
      };
      const onPointerUp = (ev: PointerEvent) => {
        clearTimeout(longPressTimer);
        pointers.delete(ev.pointerId);
        if (longPressFired) {
          // The hold already showed the tooltip; the release is not a tap.
          longPressFired = false;
          dragging = false;
          dragMoved = false;
          return;
        }
        if (pointers.size === 0 && pinched) {
          // A pinch just ended — its final lift must not count as a tap.
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
        if (canvas.hasPointerCapture(ev.pointerId)) canvas.releasePointerCapture(ev.pointerId);
        if (wasDrag) return;
        const p = toScene(ev);
        const hit = engine.hitTester.pointAt(p.x, p.y, ev.pointerType === "touch" ? 12 : 4);
        // Double-tap opens (touch has no reliable dblclick).
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
          // Aggregates are selectable (iteration-2 feedback): clicking
          // pulls the region into the graph zone by focusing its
          // top-ranked member — the unfold animation starts at the
          // bubble's position via transitionMap.
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
      const onDblClick = (ev: MouseEvent) => {
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
          // Lens model (iteration-2 feedback): inside the core zone the
          // wheel is plain geometric zoom, like the classic viewer;
          // over the rim it pulls that sector inward until it commits.
          // Whole-wiki mode has no rim: the entire surface zooms.
          const p = toScene(ev);
          const rCore = coreRadius(viewport);
          if (isFull() || inCoreZone(p.x, p.y, viewport)) {
            const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
            camera.scale = Math.max(0.5, Math.min(3, camera.scale * factor));
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
        // Non-hybrid modes: wheel = semantic zoom (PLAN §10).
        const delta = ev.deltaY < 0 ? 0.2 : -0.2;
        engine.zoomTo(engine.getState().semanticScale + delta);
      };

      // ── keyboard ────────────────────────────────────────────────
      const onKeyDown = (ev: KeyboardEvent) => {
        if (props.config?.keyboard === false) return;
        const state = engine.getState();
        const anchor = hoverId ?? state.focusId;
        switch (ev.key) {
          case "ArrowUp":
          case "ArrowDown":
          case "ArrowLeft":
          case "ArrowRight": {
            if (!anchor) return;
            const dir = ev.key.slice(5).toLowerCase() as "up" | "down" | "left" | "right";
            const next = engine.hitTester.nearestInDirection(anchor, dir);
            if (next) {
              hoverId = next;
              engine.hover(next);
              draw(1);
            }
            ev.preventDefault();
            break;
          }
          case "Enter":
            if (hoverId) {
              if (ev.shiftKey) engine.openItem(hoverId);
              else engine.focus(hoverId, "user");
              ev.preventDefault();
            }
            break;
          case "Backspace":
            engine.back();
            ev.preventDefault();
            break;
          case "p":
            if (hoverId ?? state.focusId) engine.pin((hoverId ?? state.focusId)!);
            break;
          default:
            break;
        }
      };

      const onPointerCancel = (ev: PointerEvent) => {
        pointers.delete(ev.pointerId);
        if (pointers.size === 0) {
          pinched = false;
          pinchDist = 0;
          dragging = false;
          dragMoved = false;
        }
      };
      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerup", onPointerUp);
      canvas.addEventListener("pointercancel", onPointerCancel);
      canvas.addEventListener("dblclick", onDblClick);
      canvas.addEventListener("wheel", onWheel, { passive: false });
      canvas.addEventListener("keydown", onKeyDown);
      canvas.tabIndex = 0;

      const ro = new ResizeObserver(() => {
        viewport = { width: host.clientWidth, height: host.clientHeight };
        engine.resize(viewport.width, viewport.height);
        draw(1);
      });
      ro.observe(host);

      engine.resize(viewport.width, viewport.height);
      engine.start(props.initialFocus);

      return () => {
        cancelAnimationFrame(raf);
        ro.disconnect();
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerup", onPointerUp);
        canvas.removeEventListener("pointercancel", onPointerCancel);
        canvas.removeEventListener("dblclick", onDblClick);
        canvas.removeEventListener("wheel", onWheel);
        canvas.removeEventListener("keydown", onKeyDown);
        clearTimeout(longPressTimer);
        tooltip.destroy();
        off();
        renderer.destroy();
        engine.destroy();
        engineRef.current = null;
        if (typeof ref === "function") ref(null);
        else if (ref) ref.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [props.dataSource, props.config?.layout, props.config?.seed]);

    return (
      <div ref={hostRef} style={{ position: "relative", width: "100%", height: "100%", userSelect: "none", WebkitUserSelect: "none", WebkitTouchCallout: "none" } as React.CSSProperties}>
        <canvas
          ref={canvasRef}
          data-testid="atlas-canvas"
          style={{ display: "block", width: "100%", height: "100%", outline: "none", touchAction: "none" }}
          aria-label="Knowledge atlas"
          role="application"
        />
        <div
          ref={liveRef}
          aria-live="polite"
          style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clipPath: "inset(50%)" }}
        />
      </div>
    );
  },
);
