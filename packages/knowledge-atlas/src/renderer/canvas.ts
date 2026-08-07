/**
 * Canvas 2D renderer (AD-2). Scene budgets cap draw items at ~10²–10³,
 * well inside Canvas 2D's 60fps envelope, with crisp text for free and
 * no GPU-driver variance (SwiftShader e2e). DPR-aware; label placement
 * is greedy AABB collision in score order (ports the CE viewer's
 * auto-label logic). Draw order per PLAN §9: horizon band → bundles →
 * edges → aggregates → nodes → labels → overlays.
 */

import { typeColour } from "./theme.ts";
import { coreRadiusAt, rimRadiusAt } from "../core/geometry.ts";
import type { DiscoveryClass, LayoutPoint } from "../core/types.ts";
import type { Frame, SceneRenderer } from "./types.ts";

const CLASS_ORDER: DiscoveryClass[] = ["direct", "adjacent", "bridge", "contrast", "surprise", "unexplored"];
const CLASS_LABEL: Record<DiscoveryClass, string> = {
  direct: "more like this",
  adjacent: "adjacent",
  bridge: "bridges",
  contrast: "contrasts",
  surprise: "surprises",
  unexplored: "unexplored",
};

type PlacedLabel = { x: number; y: number; w: number; h: number };

export class CanvasRenderer implements SceneRenderer {
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private measureCache = new Map<string, number>();

  mount(canvas: HTMLCanvasElement): void {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
  }

  private measure(ctx: CanvasRenderingContext2D, text: string): number {
    let w = this.measureCache.get(text);
    if (w === undefined) {
      w = ctx.measureText(text).width;
      this.measureCache.set(text, w);
    }
    return w;
  }

  destroy(): void {
    this.canvas = null;
    this.ctx = null;
    this.measureCache.clear();
  }

  /** Interpolated position: new items unfold from their aggregate. */
  private pos(frame: Frame, id: string): LayoutPoint | null {
    const target = frame.layout.positions.get(id);
    if (!target) return null;
    if (frame.progress >= 1 || !frame.prevLayout) return target;
    let from = frame.prevLayout.positions.get(id);
    if (!from && frame.scene.transitionMap) {
      // Object correspondence: a node expanding out of an aggregate
      // starts at the aggregate's previous position (PLAN §10.3).
      for (const [aggId, members] of Object.entries(frame.scene.transitionMap)) {
        if (members.includes(id)) {
          from = frame.prevLayout.positions.get(aggId);
          break;
        }
      }
    }
    if (!from) {
      // Fade-in in place: start at target with zero radius.
      from = { ...target, r: 0 };
    }
    const t = easeCubic(frame.progress);
    return {
      x: from.x + (target.x - from.x) * t,
      y: from.y + (target.y - from.y) * t,
      r: from.r + (target.r - from.r) * t,
    };
  }

  render(frame: Frame): void {
    const ctx = this.ctx;
    const canvas = this.canvas;
    if (!ctx || !canvas) return;
    const { width, height } = frame.viewport;
    const dpr = frame.dpr;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const T = frame.theme.tokens;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = T.bg;
    ctx.fillRect(0, 0, width, height);

    // World transform: origin at viewport centre + camera.
    ctx.save();
    ctx.translate(width / 2 + frame.camera.x, height / 2 + frame.camera.y);
    ctx.scale(frame.camera.scale, frame.camera.scale);

    const positions = new Map<string, LayoutPoint>();
    for (const n of frame.scene.nodes) {
      const p = this.pos(frame, n.id);
      if (p) positions.set(n.id, p);
    }
    for (const a of frame.scene.aggregates) {
      const p = this.pos(frame, a.id);
      if (p) positions.set(a.id, p);
    }

    // ── hybrid core boundary (lens zone hint) ───────────────────────
    // Same squircle family as the rim — a circle here read as a
    // mismatch against the squircle wall (iteration-6 feedback).
    if (frame.coreRadius) {
      ctx.strokeStyle = T.line;
      ctx.globalAlpha = 0.3;
      ctx.beginPath();
      const STEPS = 72;
      for (let i = 0; i <= STEPS; i++) {
        const th = (i / STEPS) * 2 * Math.PI;
        const r = coreRadiusAt(th, frame.viewport);
        const x = Math.cos(th) * r;
        const y = Math.sin(th) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // ── horizon band ────────────────────────────────────────────────
    if (frame.showHorizonRing && frame.scene.horizon.length) {
      const circleR = Math.min(width, height) * 0.485;
      // Hybrid mode: the rim is a squircle reaching into the corners;
      // other modes keep the inscribed circle.
      const boundaryAt = (angle: number) =>
        frame.coreRadius ? rimRadiusAt(angle, frame.viewport) : circleR;
      ctx.strokeStyle = T.line;
      ctx.globalAlpha = 0.35;
      ctx.setLineDash([2, 6]);
      ctx.beginPath();
      const STEPS = 96;
      for (let i = 0; i <= STEPS; i++) {
        const th = (i / STEPS) * 2 * Math.PI;
        const r = boundaryAt(th);
        const x = Math.cos(th) * r;
        const y = Math.sin(th) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle = T.textMuted;
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      const arc = (2 * Math.PI) / CLASS_ORDER.length;
      // Captions sit just INSIDE the boundary — outside clips at the
      // canvas edge on narrow (portrait/phone) viewports.
      for (const grp of frame.scene.horizon) {
        const angle = CLASS_ORDER.indexOf(grp.cls) * arc + arc / 2 - Math.PI / 2;
        const r = boundaryAt(angle) - 16;
        const lx = Math.cos(angle) * r;
        const ly = Math.sin(angle) * r;
        const omitted = grp.omittedCount > 0 ? ` (+${grp.omittedCount})` : "";
        ctx.fillText(`${CLASS_LABEL[grp.cls]}${omitted}`, lx, ly);
      }
    }

    // ── bundles ─────────────────────────────────────────────────────
    ctx.lineCap = "round";
    for (const b of frame.scene.bundles) {
      const s = positions.get(b.source);
      const t = positions.get(b.target);
      if (!s || !t) continue;
      ctx.strokeStyle = T.line;
      ctx.globalAlpha = 0.5;
      ctx.lineWidth = Math.min(6, 1 + Math.log2(1 + b.count));
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // ── edges ───────────────────────────────────────────────────────
    for (const e of frame.scene.edges) {
      const s = positions.get(e.source);
      const t = positions.get(e.target);
      if (!s || !t) continue;
      const isFocusEdge = e.priority === 1;
      const hovered = frame.hoverId === e.source || frame.hoverId === e.target;
      ctx.strokeStyle = isFocusEdge || hovered ? T.accent : T.line;
      ctx.globalAlpha = (isFocusEdge ? 0.9 : hovered ? 0.8 : 0.35) * (e.confidence ?? 1);
      ctx.lineWidth = isFocusEdge ? 1.4 : 1;
      ctx.setLineDash(e.type === "depicts" ? [3, 3] : e.type === "co-cited" ? [1.5, 3] : []);
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(t.x, t.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    // ── aggregates ──────────────────────────────────────────────────
    // Far shells smear: high-shell groups stretch tangentially into
    // arcs (the gravitational-lensing feel) and fade, while near
    // groups stay crisp chips.
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const a of frame.scene.aggregates) {
      const p = positions.get(a.id);
      if (!p) continue;
      const shell = a.shell ?? 0;
      const angle = Math.atan2(p.y, p.x);
      const stretch = 1 + shell * 0.9;
      const squash = Math.max(0.4, 1 - shell * 0.16);
      ctx.save();
      ctx.translate(p.x, p.y);
      if (shell > 0) ctx.rotate(angle + Math.PI / 2);
      ctx.globalAlpha = Math.max(0.35, 1 - shell * 0.16);
      ctx.fillStyle = T.aggregateFill;
      ctx.strokeStyle = typeColour(a.type, frame.theme);
      ctx.lineWidth = shell >= 3 ? 1 : 1.5;
      ctx.beginPath();
      ctx.ellipse(0, 0, p.r * stretch, p.r * squash, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = T.text;
      ctx.font = `600 ${Math.max(8, Math.min(13, p.r * squash * 0.8))}px system-ui, sans-serif`;
      if (shell > 0) {
        ctx.rotate(-(angle + Math.PI / 2)); // keep the count upright
      }
      ctx.fillText(a.count > 9999 ? `${Math.round(a.count / 1000)}k` : String(a.count), 0, 0);
      ctx.restore();
    }

    // ── nodes ───────────────────────────────────────────────────────
    for (const n of frame.scene.nodes) {
      const p = positions.get(n.id);
      if (!p || p.r <= 0) continue;
      const colour = typeColour(n.item.type, frame.theme);
      ctx.fillStyle = colour;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
      if (n.item.type === "unclassified") {
        ctx.strokeStyle = "#000";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      const isFocus = n.role === "focus";
      const isSelected = frame.selection.has(n.id);
      const isPinned = frame.pinned.has(n.id);
      const isHover = frame.hoverId === n.id;
      if (isFocus || isSelected || isHover) {
        ctx.strokeStyle = T.accent;
        ctx.lineWidth = isFocus ? 2.5 : 1.8;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r + 3, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (isPinned) {
        ctx.strokeStyle = T.text;
        ctx.setLineDash([2, 2]);
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r + 6, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      if (n.role === "bridge") {
        ctx.strokeStyle = typeColour(n.item.type, frame.theme);
        ctx.setLineDash([3, 2]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r + 4, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // ── labels (greedy AABB in score order) ─────────────────────────
    const placed: PlacedLabel[] = [];
    const labelFont = "11px system-ui, sans-serif";
    ctx.font = labelFont;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    const byScore = [...frame.scene.nodes].sort((a, b) =>
      a.role === "focus" ? -1 : b.role === "focus" ? 1 : b.score - a.score || (a.id < b.id ? -1 : 1),
    );
    let labelCount = 0;
    for (const n of byScore) {
      if (labelCount >= frame.maxLabels) break;
      const p = positions.get(n.id);
      if (!p) continue;
      const text = truncate(n.item.title, 28);
      const w = this.measure(ctx, text);
      // Flip to the node's left when the label would clip the right
      // wall (rim shelves sit close to the viewport edge).
      const prefixW = n.item.meta.titlePrefix ? this.measure(ctx, n.item.meta.titlePrefix) + 3 : 0;
      const total = w + prefixW;
      const flip = p.x + p.r + 4 + total > width / 2 - 6;
      const lx = flip ? p.x - p.r - 4 - total : p.x + p.r + 4;
      const ly = p.y;
      const box: PlacedLabel = { x: lx - 2, y: ly - 8, w: total + 4, h: 16 };
      if (placed.some((q) => overlaps(q, box))) continue;
      placed.push(box);
      labelCount++;
      // Prefix + title on one line reads better on canvas than the
      // SVG two-liner; keep it simple.
      if (n.item.meta.titlePrefix) {
        ctx.fillStyle = T.textMuted;
        ctx.fillText(n.item.meta.titlePrefix, lx, ly);
      }
      ctx.fillStyle = n.role === "focus" ? T.text : T.textMuted;
      ctx.fillText(text, lx + prefixW, ly);
    }

    // ── aggregate labels ────────────────────────────────────────────
    ctx.fillStyle = frame.theme.tokens.textMuted;
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "center";
    for (const a of frame.scene.aggregates) {
      const p = positions.get(a.id);
      if (!p) continue;
      const text = truncate(a.label, 30);
      const box: PlacedLabel = { x: p.x - 60, y: p.y + p.r + 2, w: 120, h: 14 };
      if (placed.some((q) => overlaps(q, box))) continue;
      placed.push(box);
      ctx.fillText(text, p.x, p.y + p.r + 10);
    }

    ctx.restore();

    // ── lens-traversal motion abstraction (iteration-8) ─────────────
    // When the corpus streams through the lens faster than real
    // subgraphs can be drawn, dim the scene and overlay directional
    // streaks + a docs odometer. Streak state is derived purely from
    // the gesture (phase advances with flow), so frames are
    // deterministic — no wall clock, no Math.random.
    if (frame.motion && frame.motion.intensity > 0.01) {
      this.drawMotion(ctx, frame);
    }
  }

  private drawMotion(ctx: CanvasRenderingContext2D, frame: Frame): void {
    const m = frame.motion!;
    const { width, height } = frame.viewport;
    const T = frame.theme.tokens;
    ctx.save();
    ctx.translate(width / 2, height / 2);
    // Dim the underlying scene in proportion to flow.
    ctx.fillStyle = T.bg;
    ctx.globalAlpha = 0.5 * m.intensity;
    ctx.fillRect(-width / 2, -height / 2, width, height);
    // Streaks travel along the traversal axis: in from the drag sector,
    // through the core, out the far side.
    const ux = Math.cos(m.angle);
    const uy = Math.sin(m.angle);
    const nx = -uy;
    const ny = ux;
    const span = Math.hypot(width, height) / 2;
    const count = Math.round(12 + 72 * m.intensity);
    ctx.lineCap = "round";
    for (let i = 0; i < count; i++) {
      const lane = (hash01(i * 7 + 1) - 0.5) * Math.min(width, height) * 0.92;
      const speed = 0.35 + hash01(i * 13 + 5) * 0.9;
      const s = (hash01(i * 29 + 11) + m.phase * speed * 0.22) % 1;
      const pos = span - s * 2 * span; // +span (drag side) → −span
      const len = (16 + 64 * m.intensity) * speed;
      ctx.strokeStyle = i % 5 === 0 ? T.accent : T.textMuted;
      ctx.lineWidth = i % 5 === 0 ? 1.6 : 1;
      ctx.globalAlpha = (0.14 + 0.5 * m.intensity) * (0.4 + 0.6 * hash01(i * 3 + 2));
      ctx.beginPath();
      ctx.moveTo(ux * pos + nx * lane, uy * pos + ny * lane);
      ctx.lineTo(ux * (pos - len) + nx * lane, uy * (pos - len) + ny * lane);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    if (m.odometer >= 1) {
      ctx.fillStyle = T.text;
      ctx.font = "600 12px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(`≈ ${formatDocs(m.odometer)} docs streaming past`, 0, height / 2 - 18);
    }
    ctx.restore();
  }
}

/** Deterministic 0..1 hash for streak lanes/speeds (splitmix-ish). */
function hash01(i: number): number {
  let h = (i + 0x9e3779b9) | 0;
  h = Math.imul(h ^ (h >>> 16), 0x21f0aaad);
  h = Math.imul(h ^ (h >>> 15), 0x735a2d97);
  h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}

function formatDocs(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${Math.round(n / 1000)}k`;
  return String(Math.round(n));
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : `${s.slice(0, n - 1)}…`;
}

function overlaps(a: PlacedLabel, b: PlacedLabel): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

function easeCubic(t: number): number {
  const u = 1 - t;
  return 1 - u * u * u;
}
