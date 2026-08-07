/**
 * SceneRenderer contract (PLAN AD-2): frames in, pixels out. The
 * engine owns hit testing and semantics; renderers are swappable
 * (Canvas 2D now, WebGL later) without touching the core.
 */

import type { LayoutResult, SceneData } from "../core/types.ts";
import type { ResolvedTheme } from "./theme.ts";

export type Camera = { x: number; y: number; scale: number };

/**
 * Lens-traversal motion abstraction (iteration-8): while the corpus
 * streams through the lens faster than real subgraphs can be drawn,
 * the renderer shows directional streaks + a docs odometer instead.
 * All fields are gesture-derived (no wall clock) so frames stay
 * deterministic for a given traversal state.
 */
export type MotionOverlay = {
  /** Direction of travel (radians; the sector being pulled through). */
  angle: number;
  /** 0..1 — drives streak count/length and scene dimming. */
  intensity: number;
  /** Estimated docs streamed past so far in this gesture. */
  odometer: number;
  /** Streak animation phase (advances with flow, not time). */
  phase: number;
};

export type Frame = {
  scene: SceneData;
  layout: LayoutResult;
  /** Previous layout for transition interpolation. */
  prevLayout?: LayoutResult;
  /** Animation progress 0..1 (1 = settled). */
  progress: number;
  camera: Camera;
  viewport: { width: number; height: number };
  dpr: number;
  theme: ResolvedTheme;
  hoverId: string | null;
  selection: ReadonlySet<string>;
  pinned: ReadonlySet<string>;
  maxLabels: number;
  /** Show per-class horizon arc captions. */
  showHorizonRing: boolean;
  /** Hybrid mode: radius of the central force zone (faint boundary). */
  coreRadius?: number;
  /** Active lens-traversal motion abstraction (drawn over the scene). */
  motion?: MotionOverlay;
};

export interface SceneRenderer {
  mount(canvas: HTMLCanvasElement): void;
  render(frame: Frame): void;
  destroy(): void;
}
