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

      let prevLayout: LayoutResult | undefined;
      let animStart = 0;
      let raf = 0;
      let hoverId: string | null = null;
      const camera: Camera = { x: 0, y: 0, scale: 1 };
      let viewport = { width: host.clientWidth || 800, height: host.clientHeight || 600 };

      const draw = (progress: number) => {
        const snap = engine.snapshot();
        if (!snap.scene || !snap.layout) return;
        renderer.render({
          scene: snap.scene,
          layout: snap.layout,
          prevLayout,
          progress,
          camera,
          viewport,
          dpr: typeof devicePixelRatio === "number" ? devicePixelRatio : 1,
          theme,
          hoverId,
          selection: new Set(snap.state.selection),
          pinned: new Set(snap.state.pinned),
          maxLabels: props.config?.budget?.maxLabels ?? 40,
          showHorizonRing: (props.config?.layout ?? "focus") !== "force",
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
      const onPointerDown = (ev: PointerEvent) => {
        dragging = true;
        dragMoved = false;
        last = { x: ev.clientX, y: ev.clientY };
        canvas.setPointerCapture(ev.pointerId);
      };
      const onPointerMove = (ev: PointerEvent) => {
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
          draw(1);
        }
      };
      const onPointerUp = (ev: PointerEvent) => {
        const wasDrag = dragMoved;
        dragging = false;
        dragMoved = false;
        canvas.releasePointerCapture(ev.pointerId);
        if (wasDrag) return;
        const p = toScene(ev);
        const hit = engine.hitTester.pointAt(p.x, p.y);
        if (!hit) return;
        if (hit.kind === "node") {
          camera.x = 0;
          camera.y = 0;
          engine.focus(hit.id, "user");
        } else {
          // Expanding an aggregate = one semantic band deeper.
          engine.zoomTo(engine.getState().semanticScale + 1);
        }
      };
      const onDblClick = (ev: MouseEvent) => {
        const p = toScene(ev);
        const hit = engine.hitTester.pointAt(p.x, p.y);
        if (hit?.kind === "node") engine.openItem(hit.id);
      };
      const onWheel = (ev: WheelEvent) => {
        ev.preventDefault();
        // Wheel = semantic zoom (PLAN §10): resolution, not glyph size.
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

      canvas.addEventListener("pointerdown", onPointerDown);
      canvas.addEventListener("pointermove", onPointerMove);
      canvas.addEventListener("pointerup", onPointerUp);
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
        canvas.removeEventListener("dblclick", onDblClick);
        canvas.removeEventListener("wheel", onWheel);
        canvas.removeEventListener("keydown", onKeyDown);
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
      <div ref={hostRef} style={{ position: "relative", width: "100%", height: "100%" }}>
        <canvas
          ref={canvasRef}
          data-testid="atlas-canvas"
          style={{ display: "block", width: "100%", height: "100%", outline: "none" }}
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
