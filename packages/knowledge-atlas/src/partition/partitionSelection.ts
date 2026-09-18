/**
 * Workspace-partition selection helpers (Phase 2b+ split spike).
 *
 * Atlas already exposes multi-select (`select` / `selection-changed`).
 * This module owns the move|copy policy map Switchbay's GraphTab used,
 * so CE can drive POST /api/split without shell-specific state.
 */

export type PartitionPolicy = "move" | "copy";

export type PartitionEntry = { id: string; policy: PartitionPolicy };

export type PartitionSeed =
  | string
  | { id: string; policy?: PartitionPolicy };

/** Normalize seeds into a de-duplicated selection (first policy wins). */
export function buildPartitionSelection(
  seeds: PartitionSeed[],
  defaultPolicy: PartitionPolicy = "move",
): PartitionEntry[] {
  const out: PartitionEntry[] = [];
  const seen = new Set<string>();
  for (const seed of seeds) {
    const id = typeof seed === "string" ? seed.trim() : String(seed.id || "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const policy =
      typeof seed === "string"
        ? defaultPolicy
        : seed.policy === "copy" || seed.policy === "move"
          ? seed.policy
          : defaultPolicy;
    out.push({ id, policy });
  }
  return out;
}

/** Toggle or set policy for one id. */
export function setPartitionPolicy(
  selection: PartitionEntry[],
  id: string,
  policy: PartitionPolicy,
): PartitionEntry[] {
  const clean = id.trim();
  if (!clean) return selection.slice();
  let found = false;
  const next = selection.map((e) => {
    if (e.id !== clean) return e;
    found = true;
    return { id: e.id, policy };
  });
  if (!found) next.push({ id: clean, policy });
  return next;
}

/** Add ids (default policy) without dropping existing policies. */
export function addToPartitionSelection(
  selection: PartitionEntry[],
  ids: string[],
  defaultPolicy: PartitionPolicy = "move",
): PartitionEntry[] {
  let next = selection.slice();
  for (const raw of ids) {
    const id = raw.trim();
    if (!id) continue;
    if (next.some((e) => e.id === id)) continue;
    next.push({ id, policy: defaultPolicy });
  }
  return next;
}

/** Remove one id. */
export function removeFromPartitionSelection(
  selection: PartitionEntry[],
  id: string,
): PartitionEntry[] {
  const clean = id.trim();
  return selection.filter((e) => e.id !== clean);
}

/** Split selection into move/copy ref lists for POST /api/split. */
export function partitionPayload(selection: PartitionEntry[]): {
  move: string[];
  copy: string[];
} {
  const move: string[] = [];
  const copy: string[] = [];
  for (const e of selection) {
    if (e.policy === "copy") copy.push(e.id);
    else move.push(e.id);
  }
  return { move, copy };
}
