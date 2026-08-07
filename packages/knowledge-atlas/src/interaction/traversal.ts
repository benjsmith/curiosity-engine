/**
 * Lens traversal (iteration-8): dragging centerward from outside the
 * squircle moves the graph THROUGH the viewing lens. The further out
 * the drag starts, the more corpus each pixel represents — shell k
 * holds ~10× shell k−1, so drag speed converts to a docs/second flow
 * rate that grows exponentially with start depth, clamped by hard
 * SPEED LIMITS so the machine (and a cloud backend) can keep up.
 *
 * Real scene commits are rate-limited (COMMIT_INTERVAL_MS); between
 * commits the renderer shows a motion abstraction — type-tinted
 * streaks flowing through the core plus a docs odometer — because
 * animating hundreds of thousands of actual subgraphs per second is
 * neither feasible nor legible. On release, velocity decays with
 * iOS-flick-style exponential friction; when it drops below the
 * threshold the final scene resolves.
 *
 * Pure physics/state here; adapters own pointers and drawing.
 */

import { commitLensTarget } from "./lens.ts";
import { coreRadius, rimRadiusAt, type Viewport } from "../core/geometry.ts";
import type { AtlasEngine } from "../core/engine.ts";

/** Hard limits — the "scrolling speed limits". */
export const MAX_DOCS_PER_SECOND = 250_000; // absolute display-flow cap
export const COMMIT_INTERVAL_MS = 400; // ≤2.5 real scene commits/sec
export const FRICTION_PER_MS = 0.9965; // ≈0.81 per 60ms — iOS-ish decay
export const STOP_VELOCITY = 0.02; // normalized flow below which we settle

/**
 * The flow can never exceed what the corpus could plausibly stream: a
 * sub-1k wiki must not read "10k docs/sec". The cap is the smaller of
 * the absolute limit and "sweep the whole remaining corpus in about a
 * second" (iteration-9). Intensity normalizes against this same cap,
 * so a small wiki still reaches full streaks at its own top speed.
 */
export function flowCapFor(shellTotals: readonly number[]): number {
  let outside = 0;
  for (const t of shellTotals) outside += t ?? 0;
  return Math.max(30, Math.min(MAX_DOCS_PER_SECOND, outside));
}

export type TraversalFrame = {
  active: boolean;
  /** Direction of travel (radians; sector being pulled through). */
  angle: number;
  /** 0..1 motion-abstraction intensity (drives streaks + dimming). */
  intensity: number;
  /** Total docs streamed past this gesture (estimate, monotonic). */
  odometer: number;
  /** Streak animation phase (advances with flow). */
  phase: number;
};

/** How many docs one centerward pixel represents at this start depth. */
export function docsPerPixel(
  startRho: number,
  angle: number,
  viewport: Viewport,
  shellTotals: readonly number[], // index 1..4 → docs represented by shell
): number {
  const rCore = coreRadius(viewport);
  const wall = rimRadiusAt(angle, viewport);
  const gap = Math.max(24, wall - rCore * 1.12);
  const t = Math.min(1, Math.max(0, (startRho - rCore * 1.12) / gap));
  // Band edges match the layout's SHELL_CUM (50/30/15/5% of the gap).
  const cum = [0, 0.5, 0.8, 0.95, 1.0];
  let shell = 1;
  for (let k = 1; k < cum.length; k++) {
    if (t <= cum[k]) {
      shell = k;
      break;
    }
  }
  // Small corpora don't populate all four bands; an empty band gears
  // like the nearest populated shell inside it (else the deepest drag
  // would free-spin over empty space).
  if (!shellTotals[shell]) {
    let pick = 0;
    for (let k = shell; k >= 1; k--) {
      if (shellTotals[k]) {
        pick = k;
        break;
      }
    }
    if (!pick) {
      for (let k = shell + 1; k < cum.length; k++) {
        if (shellTotals[k]) {
          pick = k;
          break;
        }
      }
    }
    if (pick) shell = pick;
  }
  const total = shellTotals[shell] ?? 0;
  const bandDepthPx = Math.max(12, gap * (cum[shell] - cum[shell - 1]));
  return Math.max(0.05, total / bandDepthPx);
}

export class LensTraversal {
  private engine: AtlasEngine;
  private viewport: Viewport;
  private shellTotals: () => readonly number[];

  private active = false;
  private angle = 0;
  private dpp = 1; // docs per centerward pixel at gesture start
  private flowCap = MAX_DOCS_PER_SECOND; // corpus-scaled at start()
  /** Current flow in docs/second (post-cap). */
  private flow = 0;
  private odometer = 0;
  private phase = 0;
  private released = false;
  private lastCommit = 0;
  private lastTick = 0;

  constructor(engine: AtlasEngine, viewport: Viewport, shellTotals: () => readonly number[]) {
    this.engine = engine;
    this.viewport = viewport;
    this.shellTotals = shellTotals;
  }

  setViewport(v: Viewport): void {
    this.viewport = v;
  }

  /** Begin a gesture at layout-space (x, y); returns false inside the core. */
  start(x: number, y: number, now: number): boolean {
    const rho = Math.hypot(x, y);
    const rCore = coreRadius(this.viewport);
    if (rho <= rCore * 1.05) return false;
    this.active = true;
    this.released = false;
    this.angle = Math.atan2(y, x);
    const totals = this.shellTotals();
    this.dpp = docsPerPixel(rho, this.angle, this.viewport, totals);
    this.flowCap = flowCapFor(totals);
    this.flow = 0;
    this.odometer = 0;
    this.phase = 0;
    this.lastCommit = now;
    this.lastTick = now;
    return true;
  }

  /** Feed a drag delta (screen px). Centerward motion drives the flow. */
  drag(dx: number, dy: number, dtMs: number): void {
    if (!this.active || this.released) return;
    // Centerward = opposite the gesture's radial direction.
    const centerward = -(dx * Math.cos(this.angle) + dy * Math.sin(this.angle));
    if (centerward <= 0 || dtMs <= 0) return;
    const rate = (centerward / dtMs) * 1000 * this.dpp; // docs/sec
    // Blend toward the instantaneous rate; cap = the corpus-scaled limit.
    this.flow = Math.min(this.flowCap, this.flow * 0.6 + rate * 0.4);
  }

  release(): void {
    this.released = true; // momentum takes over in tick()
  }

  cancel(): void {
    this.active = false;
    this.flow = 0;
  }

  /** Advance physics; call once per animation frame. */
  tick(now: number, commit = true): TraversalFrame {
    const dt = Math.max(0, Math.min(100, now - this.lastTick));
    this.lastTick = now;
    if (!this.active) {
      return { active: false, angle: this.angle, intensity: 0, odometer: this.odometer, phase: this.phase };
    }
    if (this.released) {
      this.flow *= Math.pow(FRICTION_PER_MS, dt);
    }
    this.odometer += (this.flow * dt) / 1000;
    this.phase += (dt / 1000) * (2 + 30 * this.intensityOf(this.flow));

    // Rate-limited real commits: the graph actually steps through the
    // lens at most every COMMIT_INTERVAL_MS while flow persists.
    if (commit && this.flow > 0 && now - this.lastCommit >= COMMIT_INTERVAL_MS) {
      this.lastCommit = now;
      commitLensTarget(this.engine, { pull: 1, angle: this.angle }, coreRadius(this.viewport));
    }

    const normalized = this.flow / this.flowCap;
    if (this.released && normalized < STOP_VELOCITY) {
      this.active = false;
      this.flow = 0;
    }
    return {
      active: this.active,
      angle: this.angle,
      intensity: this.intensityOf(this.flow),
      odometer: this.odometer,
      phase: this.phase,
    };
  }

  private intensityOf(flow: number): number {
    // Log response against the corpus-scaled cap so a 400-doc wiki and
    // a 100M corpus both reach full streaks at their own top speed.
    if (flow <= 0) return 0;
    return Math.min(1, Math.log10(1 + flow) / Math.log10(1 + this.flowCap));
  }
}

/** Shell totals from the current scene (docs represented per shell). */
export function shellTotalsFromScene(scene: {
  nodes: Array<{ shell?: number }>;
  aggregates: Array<{ shell?: number; count: number }>;
} | null): number[] {
  const totals = [0, 0, 0, 0, 0];
  if (!scene) return totals;
  for (const n of scene.nodes) if (n.shell) totals[Math.min(4, n.shell)] += 1;
  for (const a of scene.aggregates) if (a.shell) totals[Math.min(4, a.shell)] += a.count;
  return totals;
}
