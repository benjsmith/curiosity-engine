/**
 * Curation-replay timeline hook (Phase 2b spike).
 *
 * Pure scheduler extracted from Switchbay `curationReplayAnim.ts` event
 * model — no SVG/d3. Hosts (wiki-view / Graph tab) own rendering; CE owns
 * the timing contract so animation can later drive atlas/canvas.
 */

export type ReplayEvent =
  | { t: number; op: "node"; id: string; title: string; type: string }
  | { t: number; op: "edge"; source: string; target: string };

export type HistoryDoc = {
  duration: number;
  events: ReplayEvent[];
  source: string;
  generated_at?: number;
  node_count?: number;
  degree?: Record<string, number>;
};

export type ReplayHandlers = {
  onEvent: (ev: ReplayEvent, index: number) => void;
  onDone?: () => void;
  /** Wall-clock multiplier; 1 = real time vs HistoryDoc.t units. */
  rate?: number;
  /** Inject clock for tests (defaults to performance/Date). */
  now?: () => number;
  /** Inject timer APIs for tests. */
  schedule?: (fn: () => void, ms: number) => number;
  cancel?: (id: number) => void;
};

/**
 * Play events in ascending `t` order. Returns a cancel function.
 * Empty/missing history completes on the next microtask-equivalent tick.
 */
export function playReplayTimeline(
  history: HistoryDoc | null | undefined,
  handlers: ReplayHandlers,
): () => void {
  const events = (history?.events ?? []).slice().sort((a, b) => a.t - b.t);
  const rate = handlers.rate && handlers.rate > 0 ? handlers.rate : 1;
  const now = handlers.now ?? (() => (typeof performance !== "undefined" ? performance.now() : Date.now()));
  const schedule =
    handlers.schedule ??
    ((fn, ms) => setTimeout(fn, ms) as unknown as number);
  const cancel =
    handlers.cancel ??
    ((id) => {
      clearTimeout(id as unknown as ReturnType<typeof setTimeout>);
    });

  let cancelled = false;
  let timer: number | null = null;
  let index = 0;
  const t0 = now();

  const tick = () => {
    if (cancelled) return;
    if (index >= events.length) {
      handlers.onDone?.();
      return;
    }
    const wall = (now() - t0) * rate;
    while (index < events.length && events[index]!.t <= wall) {
      handlers.onEvent(events[index]!, index);
      index += 1;
    }
    if (index >= events.length) {
      handlers.onDone?.();
      return;
    }
    const nextT = events[index]!.t;
    const delay = Math.max(0, (nextT - wall) / rate);
    timer = schedule(tick, delay);
  };

  timer = schedule(tick, 0);

  return () => {
    cancelled = true;
    if (timer != null) cancel(timer);
  };
}
