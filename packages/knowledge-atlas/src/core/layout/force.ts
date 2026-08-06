/**
 * Baseline force layout (P0) — d3-force with the constants of the
 * current Curiosity Engine viewer (charge −420, link 110, collide 10,
 * 350 pre-warm ticks), so the P0 harness is visually comparable to
 * today's graph. Deterministic: d3-force ≥ v2 seeds its internal LCG,
 * and input order is stable.
 */

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
} from "d3-force";
import { aggregateRadius, meanDisplacement, nodeRadius, type LayoutAdapter, type LayoutContext } from "./types.ts";
import type { LayoutPoint, LayoutResult, SceneData } from "../types.ts";

const PHYSICS = { charge: -420, link: 110, collide: 10 };
const PREWARM_TICKS = 350;

type SimNode = { id: string; r: number; x?: number; y?: number };

export const forceLayout: LayoutAdapter = {
  id: "force",
  layout(scene: SceneData, ctx: LayoutContext): LayoutResult {
    const simNodes: SimNode[] = [
      ...scene.nodes.map((n) => ({ id: n.id, r: nodeRadius(n.item.meta.degree) })),
      ...scene.aggregates.map((a) => ({ id: a.id, r: aggregateRadius(a.count) })),
    ];
    // Continuity: seed positions from the previous layout where known.
    for (const sn of simNodes) {
      const prev = ctx.previous?.positions.get(sn.id);
      if (prev) {
        sn.x = prev.x;
        sn.y = prev.y;
      }
    }
    const links = [
      ...scene.edges.map((e) => ({ source: e.source, target: e.target })),
      ...scene.bundles.map((b) => ({ source: b.source, target: b.target })),
    ].filter((l) => simNodes.some((n) => n.id === l.source) && simNodes.some((n) => n.id === l.target));

    const sim = forceSimulation(simNodes as never[])
      .force(
        "link",
        forceLink(links as never[])
          .id((d) => (d as unknown as SimNode).id)
          .distance(PHYSICS.link)
          .strength(0.55),
      )
      .force("charge", forceManyBody().strength(PHYSICS.charge).distanceMax(500))
      .force("center", forceCenter(0, 0).strength(0.04))
      .force("collide", forceCollide((d) => (d as unknown as SimNode).r + PHYSICS.collide))
      .stop();
    for (let i = 0; i < PREWARM_TICKS; i++) sim.tick();

    const positions = new Map<string, LayoutPoint>();
    for (const sn of simNodes) {
      positions.set(sn.id, { x: sn.x ?? 0, y: sn.y ?? 0, r: sn.r });
    }
    return { positions, displacement: meanDisplacement(positions, ctx.previous) };
  },
};
