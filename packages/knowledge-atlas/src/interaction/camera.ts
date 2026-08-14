/**
 * Camera projection for Atlas's hybrid surface.
 *
 * The central graph behaves like the classic viewer: pan/zoom changes
 * node positions and sizes. The boundary is screen geography, so its
 * shell nodes and aggregates stay fixed while the graph streams through
 * it. Keeping this projection outside the renderer also lets hit testing
 * use the exact pixels that were drawn.
 */

import type { LayoutPoint, LayoutResult, SceneData } from "../core/types.ts";
import type { Camera } from "../renderer/types.ts";
import { coreRadiusAt, rimRadiusAt } from "../core/geometry.ts";

export const MIN_CAMERA_SCALE = 0.05;
export const MAX_CAMERA_SCALE = 4;

export function clampCameraScale(scale: number): number {
  return Math.max(MIN_CAMERA_SCALE, Math.min(MAX_CAMERA_SCALE, scale));
}

/**
 * Continuous, deliberately low-gain wheel zoom. Trackpads emit many
 * small pixel deltas; treating each as a full zoom notch caused the
 * camera to outrun scene updates. Line/page modes are normalised first.
 */
export function wheelZoomFactor(deltaY: number, deltaMode = 0): number {
  const pixels = deltaY * (deltaMode === 1 ? 16 : deltaMode === 2 ? 800 : 1);
  const bounded = Math.max(-240, Math.min(240, pixels));
  return Math.exp(-bounded * 0.0006);
}

/** Smaller touch targets look oversized on a narrow phone graph pane. */
export function responsiveNodeScale(viewport: { width: number; height: number }): number {
  const short = Math.min(viewport.width, viewport.height);
  if (short <= 420) return 0.62;
  if (short >= 700) return 1;
  return 0.62 + ((short - 420) / 280) * 0.38;
}

export function projectCamera(
  layout: LayoutResult,
  scene: SceneData,
  camera: Camera,
  fixedBoundary: boolean,
  nodeScale = 1,
  viewport?: { width: number; height: number },
  boundaryShape?: number,
  warpFullGraph = false,
): LayoutResult {
  const nodeById = new Map(scene.nodes.map((node) => [node.id, node]));
  const aggregateIds = new Set(scene.aggregates.map((aggregate) => aggregate.id));
  const positions = new Map<string, LayoutPoint>();
  const boundaryIds = new Set<string>();
  const fullGraph = aggregateIds.size === 0 && !scene.nodes.some((node) => node.shell);

  for (const [id, point] of layout.positions) {
    const node = nodeById.get(id);
    const inBoundary = fixedBoundary && (aggregateIds.has(id) || Boolean(node?.shell));
    if (inBoundary) boundaryIds.add(id);
    const cameraScale = inBoundary ? 1 : camera.scale;
    const radiusScale = node ? nodeScale : 1;
    let x = point.x * cameraScale + (inBoundary ? 0 : camera.x);
    let y = point.y * cameraScale + (inBoundary ? 0 : camera.y);
    let r = point.r * cameraScale * radiusScale;

    // A resident whole graph stays in Classic coordinates forever.
    // Only its screen projection bends: material outside the central
    // squircle is compressed monotonically into a fixed rim. Because
    // camera state remains affine, equal opposite pans return exactly
    // to the same view even though the displayed edge geography bends.
    if (!inBoundary && warpFullGraph && fullGraph && viewport && node) {
      const rho = Math.hypot(x, y);
      const angle = Math.atan2(y, x);
      const core = coreRadiusAt(angle, viewport, 1, boundaryShape);
      if (rho > core) {
        const rim = rimRadiusAt(angle, viewport, boundaryShape) - 4;
        const gap = Math.max(1, rim - core);
        const excess = rho - core;
        const t = 1 - Math.exp(-excess / Math.max(32, core * 0.55));
        const mapped = core + gap * t;
        const k = mapped / Math.max(1, rho);
        x *= k;
        y *= k;
        // Hyperbolic distance cue: apparent size falls exponentially
        // through the compressed region. Far-rim points become tiny
        // survey marks instead of competing with the central graph.
        r *= Math.max(0.18, Math.exp(-1.7 * t));
        boundaryIds.add(id);
      }
    }
    positions.set(id, { x, y, r });
  }

  return { positions, displacement: layout.displacement, boundaryIds };
}
