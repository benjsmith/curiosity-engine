/**
 * SceneRenderer contract (PLAN AD-2): frames in, pixels out. The
 * engine owns hit testing and semantics; renderers are swappable
 * (Canvas 2D now, WebGL later) without touching the core.
 */

import type { LayoutResult, SceneData } from "../core/types.ts";
import type { ResolvedTheme } from "./theme.ts";

export type Camera = { x: number; y: number; scale: number };

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
};

export interface SceneRenderer {
  mount(canvas: HTMLCanvasElement): void;
  render(frame: Frame): void;
  destroy(): void;
}
