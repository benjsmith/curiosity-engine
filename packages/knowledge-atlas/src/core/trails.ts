/**
 * Inquiry trails (PLAN §12): a branching tree of focus steps, not a
 * flat history. Truncated forward steps are preserved as an auto-
 * branch — no detour is ever lost. Serialisable and restorable.
 */

import type { TrailBranch, TrailState, TrailStep } from "./types.ts";

const TRAIL_VERSION = 1;

export class Trails {
  private branches: TrailBranch[] = [];
  private activeBranchId: string;
  private cursor = -1;
  private counter = 0;
  pinned: string[] = [];

  constructor() {
    this.activeBranchId = "b0";
    this.branches.push({ id: "b0", steps: [] });
  }

  private active(): TrailBranch {
    return this.branches.find((b) => b.id === this.activeBranchId)!;
  }

  get currentStep(): TrailStep | undefined {
    const b = this.active();
    return this.cursor >= 0 ? b.steps[this.cursor] : undefined;
  }

  /** Focus ids along the active branch up to the cursor (for exposure). */
  historyIds(): string[] {
    return this.active().steps.slice(0, this.cursor + 1).map((s) => s.focusId);
  }

  push(step: Omit<TrailStep, "id" | "t">): TrailStep {
    const b = this.active();
    // Preserve any forward steps as an automatic branch before
    // truncating (PLAN: no detour is ever lost).
    if (this.cursor < b.steps.length - 1) {
      const orphaned = b.steps.slice(this.cursor + 1);
      const branchId = `b${this.branches.length}`;
      this.branches.push({
        id: branchId,
        name: `detour from ${b.steps[this.cursor]?.focusId ?? "start"}`,
        steps: orphaned,
        parent: { branchId: b.id, stepId: b.steps[this.cursor]?.id ?? "" },
      });
      b.steps = b.steps.slice(0, this.cursor + 1);
    }
    const full: TrailStep = { ...step, id: `s${this.counter}`, t: this.counter };
    this.counter++;
    b.steps.push(full);
    this.cursor = b.steps.length - 1;
    return full;
  }

  back(): TrailStep | undefined {
    if (this.cursor <= 0) return undefined;
    this.cursor--;
    return this.currentStep;
  }

  forward(): TrailStep | undefined {
    const b = this.active();
    if (this.cursor >= b.steps.length - 1) return undefined;
    this.cursor++;
    return this.currentStep;
  }

  /** Fork a new branch at the cursor (or a named step) and switch to it. */
  branch(fromStepId?: string): string {
    const b = this.active();
    const idx = fromStepId ? b.steps.findIndex((s) => s.id === fromStepId) : this.cursor;
    const at = idx >= 0 ? idx : this.cursor;
    const branchId = `b${this.branches.length}`;
    this.branches.push({
      id: branchId,
      steps: b.steps.slice(0, at + 1),
      parent: { branchId: b.id, stepId: b.steps[at]?.id ?? "" },
    });
    this.activeBranchId = branchId;
    this.cursor = at;
    return branchId;
  }

  switchTo(branchId: string): boolean {
    const b = this.branches.find((x) => x.id === branchId);
    if (!b) return false;
    this.activeBranchId = branchId;
    this.cursor = b.steps.length - 1;
    return true;
  }

  /** Shared / unique focus ids across branches (compare support). */
  compare(branchIds: string[]): { shared: string[]; unique: Map<string, string[]> } {
    const sets = branchIds
      .map((id) => this.branches.find((b) => b.id === id))
      .filter((b): b is TrailBranch => !!b)
      .map((b) => ({ id: b.id, ids: new Set(b.steps.map((s) => s.focusId)) }));
    if (!sets.length) return { shared: [], unique: new Map() };
    const shared = [...sets[0].ids].filter((id) => sets.every((s) => s.ids.has(id)));
    const sharedSet = new Set(shared);
    const unique = new Map<string, string[]>();
    for (const s of sets) unique.set(s.id, [...s.ids].filter((id) => !sharedSet.has(id)));
    return { shared, unique };
  }

  state(): TrailState {
    return {
      branches: this.branches.map((b) => ({ ...b, steps: [...b.steps] })),
      activeBranchId: this.activeBranchId,
      cursor: this.cursor,
      pinned: [...this.pinned],
    };
  }

  serialize(): string {
    return JSON.stringify({ v: TRAIL_VERSION, counter: this.counter, ...this.state() });
  }

  restore(json: string): void {
    const data = JSON.parse(json) as { v: number; counter: number } & TrailState;
    if (data.v !== TRAIL_VERSION) throw new Error(`unsupported trail version ${data.v}`);
    this.branches = data.branches.map((b) => ({ ...b, steps: [...b.steps] }));
    this.activeBranchId = data.activeBranchId;
    this.cursor = data.cursor;
    this.pinned = [...data.pinned];
    this.counter = data.counter;
  }
}
