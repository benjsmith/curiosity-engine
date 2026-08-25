/**
 * React adapter (PLAN §13): a thin component over AtlasEngine +
 * CanvasRenderer. Owns the canvas, ResizeObserver, rAF transitions,
 * pointer/keyboard wiring, and nothing else — no context requirement,
 * no global state. StrictMode-safe (idempotent mount/destroy).
 */

import { forwardRef, useEffect, useRef } from "react";
import { AtlasEngine } from "../core/engine.ts";
import { CanvasRenderer } from "../renderer/canvas.ts";
import { AtlasMinimap } from "../renderer/minimap.ts";
import { resolveTheme } from "../renderer/theme.ts";
import { coreRadius, isFullGraphScene, populatedShellBands } from "../core/layout/hybrid.ts";
import { viewScaleToFit } from "../core/scene/shells.ts";
import { clampCameraScale, projectCamera, responsiveNodeScale, wheelZoomFactor } from "../interaction/camera.ts";
import { boundaryHoverDelay, projectedBoundaryDepth } from "../interaction/hover.ts";
import { AggregateTooltip, LONG_PRESS_MS } from "../interaction/tooltip.ts";
import {
  LensTraversal,
  shellTotalsFromLayout,
  shellTotalsFromScene,
} from "../interaction/traversal.ts";
import type { MotionOverlay } from "../renderer/types.ts";
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
    // Label policy is read at draw time via refs so changing it never
    // recreates the engine; redrawRef lets the label effect repaint.
    const labelRef = useRef<{ mode: "auto" | "on" | "off"; types: ReadonlySet<string> | null }>({
      mode: "auto",
      types: null,
    });
    labelRef.current = {
      mode: props.labelMode ?? "auto",
      types: props.labelTypes ? new Set(props.labelTypes) : null,
    };
    const redrawRef = useRef<(() => void) | null>(null);
    useEffect(() => {
      redrawRef.current?.();
    }, [props.labelMode, props.labelTypes]);

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

      // Palette comes through the AtlasDataSource contract, not a
      // concrete source class — any source can bring its own colours.
      const dataPalette = props.dataSource.palette;
      const currentMode = () =>
        typeof document !== "undefined" &&
        (document.documentElement.dataset.theme === "light" ||
          (document.documentElement.dataset.theme === undefined &&
            typeof matchMedia !== "undefined" &&
            matchMedia("(prefers-color-scheme: light)").matches))
          ? "light"
          : "dark";
      let theme = resolveTheme(props.theme, dataPalette, currentMode());
      const tooltip = new AggregateTooltip(host, theme, (aggId) => {
        // Keep the camera stable while the chosen community unfolds.
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
      let hoverTimer = 0;
      let pendingHoverId: string | null = null;
      let projectedForHover: LayoutResult | undefined;
      let hoverScene = null as ReturnType<typeof engine.snapshot>["scene"];
      let hoverBands = 1;
      const hoverShell = new Map<string, number>();
      const camera: Camera = { x: 0, y: 0, scale: 1 };
      let viewport = { width: host.clientWidth || 800, height: host.clientHeight || 600 };
      const layoutKind = props.config?.layout ?? "focus";
      const isHybrid = layoutKind === "hybrid" || layoutKind === "adaptive-hybrid";
      const boundaryShape = props.config?.boundaryShape;
      let motion: MotionOverlay | undefined;
      let traverseRaf = 0;
      let lastTraverseAt = 0;
      let traversing = false;
      const traversal = new LensTraversal(
        engine,
        viewport,
        () => {
          const snap = engine.snapshot();
          if (snap.scene && isFullGraphScene(snap.scene) && projectedForHover) {
            return shellTotalsFromLayout(
              projectedForHover.positions.values(),
              viewport,
              boundaryShape,
            );
          }
          return shellTotalsFromScene(snap.scene);
        },
        () => {
          camera.scale = clampCameraScale(camera.scale * 0.88);
          scheduleDensity();
        },
        boundaryShape,
      );
      const tickTraversal = (now: number) => {
        traverseRaf = 0;
        const frame = traversal.tick(now);
        motion = frame.active && frame.intensity > 0.01
          ? {
              angle: frame.angle,
              intensity: frame.intensity,
              odometer: frame.odometer,
              rate: frame.rate,
              phase: frame.phase,
            }
          : undefined;
        draw(1);
        if (frame.active) traverseRaf = requestAnimationFrame(tickTraversal);
      };
      let overviewLayout: LayoutResult | undefined;
      let overviewScene = engine.snapshot().scene;
      let densityTimer = 0;
      const scheduleDensity = () => {
        clearTimeout(densityTimer);
        densityTimer = window.setTimeout(() => engine.setViewScale(camera.scale), 120);
      };
      const minimap = new AtlasMinimap(host, (worldX, worldY) => {
        camera.x = -worldX * camera.scale;
        camera.y = -worldY * camera.scale;
        draw(1);
      });
      const draw = (progress: number) => {
        const snap = engine.snapshot();
        if (!snap.scene || !snap.layout) return;
        const bands = Math.max(1, populatedShellBands(snap.scene));
        const rCore = coreRadius(viewport, bands);
        const full = isFullGraphScene(snap.scene);
        traversal.setCoreBands(Math.max(1, bands));
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
          dpr: typeof devicePixelRatio === "number" ? devicePixelRatio : 1,
          theme,
          hoverId,
          selection: new Set(snap.state.selection),
          pinned: new Set(snap.state.pinned),
          maxLabels: snap.stats?.labelCount ?? props.config?.budget?.maxLabels ?? 60,
          showHorizonRing: (props.config?.layout ?? "focus") !== "force" && !full,
          coreRadius: isHybrid ? rCore : undefined,
          shellBands: bands,
          boundaryShape,
          labelMode: labelRef.current.mode,
          labelTypes: labelRef.current.types,
          motion,
        });
        canvas.dataset.hoverId = hoverId ?? "";
        if (full) {
          overviewLayout = snap.layout;
          overviewScene = snap.scene;
        }
        minimap.update(overviewLayout, overviewScene, camera, viewport, theme, boundaryShape);
      };
      redrawRef.current = () => draw(1);
      const themeObserver = new MutationObserver(() => {
        theme = resolveTheme(props.theme, dataPalette, currentMode());
        tooltip.setTheme(theme);
        draw(1);
      });
      themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

      const animate = () => {
        const t = Math.min(1, (performance.now() - animStart) / transitionMs);
        draw(t);
        if (t < 1) raf = requestAnimationFrame(animate);
        else prevLayout = engine.snapshot().layout ?? undefined;
      };

      const off = engine.on((e: AtlasEvent) => {
        if (e.kind === "scene-ready") {
          clearHoverIntent(true);
          if (traversal.isActive()) {
            draw(1);
          } else {
            cancelAnimationFrame(raf);
            animStart = performance.now();
            raf = requestAnimationFrame(animate);
          }
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
          x: ev.clientX - rect.left - viewport.width / 2,
          y: ev.clientY - rect.top - viewport.height / 2,
        };
      };
      let dragging = false;
      let dragMoved = false;
      let last = { x: 0, y: 0 };
      let traversalChecked = false;
      let pressPos = { x: 0, y: 0 };
      // traversing lives next to LensTraversal above
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
      let userZoomed = false;
      const clearHoverIntent = (clearVisible = false) => {
        clearTimeout(hoverTimer);
        hoverTimer = 0;
        pendingHoverId = null;
        if (clearVisible && hoverId !== null) {
          hoverId = null;
          engine.hover(null);
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
          engine.hover(null);
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
          engine.hover(hitId);
          maybeShowTooltip(hit.kind === "aggregate" ? hitId : null, clientX, clientY);
          draw(1);
        };
        const delay = hoverDelay(hitId);
        pendingHoverId = hitId;
        if (delay === 0) commit();
        else hoverTimer = window.setTimeout(commit, delay);
      };
      const onPointerDown = (ev: PointerEvent) => {
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
        traversing = false;
        traversalChecked = false;
        last = { x: ev.clientX, y: ev.clientY };
        pressPos = toScene(ev);
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
          if (ratio > 1.025 || ratio < 0.975) {
            const zoomIn = ratio > 1;
            pinchDist = d;
            if (isHybrid) {
              const rect = canvas.getBoundingClientRect();
              const anchor = {
                x: (a.x + b.x) / 2 - rect.left - viewport.width / 2,
                y: (a.y + b.y) / 2 - rect.top - viewport.height / 2,
              };
              const old = camera.scale;
              userZoomed = true;
              camera.scale = clampCameraScale(old * Math.max(0.94, Math.min(1.06, ratio)));
              const k = camera.scale / old;
              camera.x = anchor.x - (anchor.x - camera.x) * k;
              camera.y = anchor.y - (anchor.y - camera.y) * k;
              scheduleDensity();
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
          if (dragMoved && isHybrid && !traversalChecked) {
            traversalChecked = true;
            traversal.setViewport(viewport);
            if (traversal.start(pressPos.x, pressPos.y, performance.now())) {
              traversing = true;
              lastTraverseAt = performance.now();
            }
          }
          if (dragMoved) {
            camera.x += dx;
            camera.y += dy;
            last = { x: ev.clientX, y: ev.clientY };
            if (traversing) {
              const now = performance.now();
              traversal.drag(dx, dy, Math.max(8, now - lastTraverseAt));
              lastTraverseAt = now;
              if (!traverseRaf) traverseRaf = requestAnimationFrame(tickTraversal);
            } else {
              draw(1);
            }
          }
          return;
        }
        const p = toScene(ev);
        const hit = engine.hitTester.pointAt(p.x, p.y);
        scheduleHover(hit, ev);
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
        const wasTraverse = traversing;
        dragging = false;
        dragMoved = false;
        traversing = false;
        traversalChecked = false;
        if (canvas.hasPointerCapture(ev.pointerId)) canvas.releasePointerCapture(ev.pointerId);
        if (wasTraverse) {
          traversal.release();
          if (!traverseRaf) traverseRaf = requestAnimationFrame(tickTraversal);
          return;
        }
        if (wasDrag) {
          return;
        }
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
        } else if (engine.getState().focusId) {
          engine.openItem(engine.getState().focusId!);
        }
      };
      const onWheel = (ev: WheelEvent) => {
        ev.preventDefault();
        clearHoverIntent(true);
        if (isHybrid) {
          const p = toScene(ev);
          const old = camera.scale;
          userZoomed = true;
          camera.scale = clampCameraScale(old * wheelZoomFactor(ev.deltaY, ev.deltaMode));
          const k = camera.scale / old;
          camera.x = p.x - (p.x - camera.x) * k;
          camera.y = p.y - (p.y - camera.y) * k;
          scheduleDensity();
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
              clearHoverIntent(false);
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
          traversalChecked = false;
          if (traversing) {
            traversing = false;
            traversal.cancel();
            motion = undefined;
            if (traverseRaf) cancelAnimationFrame(traverseRaf);
            traverseRaf = 0;
            draw(1);
          }
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
        const n = props.config?.corpusSize ?? 0;
        if (!userZoomed && n > 0 && viewport.width > 0 && viewport.height > 0) {
          const fitted = viewScaleToFit(n, viewport, props.config?.maxVisibleNodes);
          if (fitted > 0) camera.scale = fitted;
        }
        engine.resize(viewport.width, viewport.height);
        traversal.setViewport(viewport);
        engine.setViewScale(camera.scale);
        draw(1);
      });
      ro.observe(host);

      engine.resize(viewport.width, viewport.height);
      engine.start(props.initialFocus);
      const fitted = engine.getState().viewScale;
      if (fitted > 0) camera.scale = fitted;
      engine.setViewScale(camera.scale);

      return () => {
        cancelAnimationFrame(raf);
        cancelAnimationFrame(traverseRaf);
        traversal.cancel();
        redrawRef.current = null;
        ro.disconnect();
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerup", onPointerUp);
        canvas.removeEventListener("pointercancel", onPointerCancel);
        canvas.removeEventListener("dblclick", onDblClick);
        canvas.removeEventListener("wheel", onWheel);
        canvas.removeEventListener("keydown", onKeyDown);
        clearTimeout(longPressTimer);
        clearTimeout(densityTimer);
        clearTimeout(hoverTimer);
        themeObserver.disconnect();
        tooltip.destroy();
        minimap.destroy();
        off();
        renderer.destroy();
        engine.destroy();
        engineRef.current = null;
        if (typeof ref === "function") ref(null);
        else if (ref) ref.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [props.dataSource, props.config?.layout, props.config?.seed, props.config?.boundaryShape]);

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
