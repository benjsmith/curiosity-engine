/**
 * Seeded randomness (PLAN AD-7). One splitmix32 generator everywhere —
 * identical (data, config, command sequence) must yield identical
 * scenes and positions. Wall-clock never enters layout or ranking.
 */

export type Rng = () => number;

/** splitmix32 — deterministic, fast, good enough for jitter/sampling. */
export function splitmix32(seed: number): Rng {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x9e3779b9) | 0;
    let t = a ^ (a >>> 16);
    t = Math.imul(t, 0x21f0aaad);
    t = t ^ (t >>> 15);
    t = Math.imul(t, 0x735a2d97);
    t = t ^ (t >>> 15);
    return (t >>> 0) / 4294967296;
  };
}

/** FNV-1a 32-bit string hash — stable ids -> stable numbers. */
export function hashString(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Derive a child seed from a parent seed and a label. */
export function childSeed(seed: number, label: string): number {
  return (seed ^ hashString(label)) >>> 0;
}

/** Deterministic sample of k items (without replacement). */
export function sample<T>(items: readonly T[], k: number, rng: Rng): T[] {
  if (k >= items.length) return [...items];
  const pool = [...items];
  const out: T[] = [];
  for (let i = 0; i < k; i++) {
    const j = Math.floor(rng() * pool.length);
    out.push(pool[j]);
    pool[j] = pool[pool.length - 1];
    pool.pop();
  }
  return out;
}
