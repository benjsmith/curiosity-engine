/**
 * Edge drawing policy (mirrors labelMode auto/on/off).
 *
 * Edges always remain in the scene / force graph / link counts; this
 * module only decides which strokes to paint. Hover and selection
 * highlight edges are drawn in every mode.
 */

export type EdgeMode = "auto" | "on" | "off";

export type EdgeDrawKind = "highlight" | "normal" | "skip";

/** Stable 32-bit hash so auto sampling does not flicker across frames. */
export function edgeSampleKey(source: string, target: string): number {
  const a = source < target ? source : target;
  const b = source < target ? target : source;
  let h = 2166136261;
  for (let i = 0; i < a.length; i++) {
    h ^= a.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  h ^= 0x1f;
  for (let i = 0; i < b.length; i++) {
    h ^= b.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/**
 * Auto budget: below ~1200 edges draw everything; above that keep a
 * sparse, slowly-growing cap so large overviews stay readable without
 * zoom hairline tricks.
 */
export function autoEdgeBudget(edgeCount: number): number {
  if (edgeCount <= 1200) return edgeCount;
  return Math.min(edgeCount, Math.round(900 + Math.sqrt(edgeCount) * 12));
}

export function edgeIsHighlighted(
  e: { source: string; target: string; priority: number },
  hoverId: string | null,
  selection: ReadonlySet<string>,
): boolean {
  if (e.priority === 1) return true;
  if (hoverId !== null && (e.source === hoverId || e.target === hoverId)) return true;
  if (selection.has(e.source) || selection.has(e.target)) return true;
  return false;
}

/**
 * Decide whether an edge stroke should paint this frame.
 * Highlights always win; off skips the rest; on draws all; auto keeps
 * a deterministic sparse subset sized by {@link autoEdgeBudget}.
 */
export function classifyEdgeDraw(
  e: { source: string; target: string; priority: number },
  mode: EdgeMode,
  opts: {
    hoverId: string | null;
    selection: ReadonlySet<string>;
    edgeCount: number;
    budget?: number;
  },
): EdgeDrawKind {
  if (edgeIsHighlighted(e, opts.hoverId, opts.selection)) return "highlight";
  if (mode === "off") return "skip";
  if (mode === "on") return "normal";
  const budget = opts.budget ?? autoEdgeBudget(opts.edgeCount);
  if (opts.edgeCount <= budget) return "normal";
  // Hash-modulo keeps ~budget edges without sorting every frame.
  if (edgeSampleKey(e.source, e.target) % opts.edgeCount < budget) return "normal";
  return "skip";
}

export function cycleEdgeMode(mode: EdgeMode): EdgeMode {
  const order: EdgeMode[] = ["auto", "on", "off"];
  return order[(order.indexOf(mode) + 1) % order.length]!;
}

/** Default: auto (sparse on large corpora; full draw when small). */
export function defaultEdgeMode(_corpusSize?: number): EdgeMode {
  return "auto";
}
