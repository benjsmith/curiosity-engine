/** Package root: core engine + renderer + data sources (no React). */

export * from "./core/types.ts";
export { AtlasEngine, type EngineSnapshot } from "./core/engine.ts";
export { GraphIndex } from "./core/graphindex.ts";
export { Trails } from "./core/trails.ts";
export { HitTester } from "./core/hittest.ts";
export { buildScene, bandOf } from "./core/scene/builder.ts";
export { computeHorizon } from "./core/scene/discovery.ts";
export { buildLandmarks, anchorAngle, DEFAULT_TYPE_ORDER } from "./core/scene/landmarks.ts";
export { clampScale, nextBand } from "./core/zoom.ts";
export { focusLayout } from "./core/layout/focus.ts";
export { forceLayout } from "./core/layout/force.ts";
export { hyperbolicLayout } from "./core/layout/hyperbolic.ts";
export { hybridLayout, coreRadius, rimRadiusAt, CORE_SHARE } from "./core/layout/hybrid.ts";
export { adaptiveLayout, classifyTopology } from "./core/layout/adaptive.ts";
export { adaptiveHybridLayout } from "./core/layout/adaptiveHybrid.ts";
export type { LayoutAdapter, LayoutContext } from "./core/layout/types.ts";
export { CanvasRenderer } from "./renderer/canvas.ts";
export { resolveTheme, typeColour } from "./renderer/theme.ts";
export type { Frame, SceneRenderer, Camera } from "./renderer/types.ts";
export {
  CuriosityDataSource,
  canonicalType,
  normalizeId,
  indexFromCEData,
  type CEData,
} from "./datasources/curiosity.ts";
export { LocalSceneSource } from "./datasources/local.ts";
export { ScaledDataSource, SCALED_TOTAL_LEAVES } from "./datasources/scaled.ts";
export { RemoteDataSource } from "./datasources/remote.ts";
