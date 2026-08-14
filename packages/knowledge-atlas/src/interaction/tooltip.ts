/**
 * Aggregate tooltip (iteration-5 feedback): the numbered grouping
 * bubbles need to explain themselves. Shown on hover (mouse) or
 * touch-and-hold (~500 ms); lists the group label, a sample of member
 * titles, the residual count, and what a click will do. Shared by the
 * React adapter and the IIFE mount — plain DOM, styled from the
 * resolved theme so it works in both hosts and both colour modes.
 */

import { pluralize } from "../core/scene/aggregate.ts";
import type { RenderAggregate } from "../core/types.ts";
import type { ResolvedTheme } from "../renderer/theme.ts";

export const LONG_PRESS_MS = 500;

export class AggregateTooltip {
  private el: HTMLDivElement;
  private currentAggId: string | null = null;

  constructor(host: HTMLElement, theme: ResolvedTheme, onActivate?: (aggId: string) => void) {
    this.el = document.createElement("div");
    this.el.dataset.testid = "aggregate-tooltip";
    this.el.style.cssText = [
      "position:absolute",
      "z-index:30",
      "max-width:260px",
      "pointer-events:auto",
      "cursor:pointer",
      "user-select:none",
      "-webkit-user-select:none",
      "-webkit-touch-callout:none",
      "display:none",
      "padding:8px 10px",
      "border-radius:8px",
      "font:12px/1.45 system-ui, sans-serif",
      "box-shadow:0 4px 14px rgba(0,0,0,0.35)",
    ].join(";");
    this.setTheme(theme);
    host.appendChild(this.el);
    if (onActivate) {
      this.el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (this.currentAggId) onActivate(this.currentAggId);
      });
    }
  }

  setTheme(theme: ResolvedTheme): void {
    this.el.style.background = theme.tokens.aggregateFill;
    this.el.style.border = `1px solid ${theme.tokens.line}`;
    this.el.style.color = theme.tokens.text;
  }

  show(agg: RenderAggregate, x: number, y: number, hostRect: { width: number; height: number }): void {
    this.currentAggId = agg.id;
    // Haptic tick where supported (Android); silently absent elsewhere.
    (navigator as { vibrate?: (ms: number) => void }).vibrate?.(10);
    const titles = (agg.memberTitles ?? agg.memberIds).slice(0, 5);
    const more = agg.count - titles.length;
    const rows = titles
      .map((t) => `<div style="opacity:.85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">• ${escapeHtml(t)}</div>`)
      .join("");
    this.el.innerHTML =
      `<div style="font-weight:600;margin-bottom:4px">${escapeHtml(agg.label)}</div>` +
      rows +
      (more > 0 ? `<div style="opacity:.6;margin-top:2px">…and ${more} more ${escapeHtml(pluralize(agg.type, more))}</div>` : "") +
      `<div style="opacity:.6;margin-top:6px;font-style:italic">tap here to bring this group into the graph</div>`;
    this.el.style.display = "block";
    // Keep inside the host: flip left/up near the far edges.
    const w = 260;
    const h = this.el.offsetHeight || 120;
    const left = x + 14 + w > hostRect.width ? Math.max(4, x - w - 10) : x + 14;
    const top = y + h + 10 > hostRect.height ? Math.max(4, y - h - 10) : y + 10;
    this.el.style.left = `${left}px`;
    this.el.style.top = `${top}px`;
  }

  hide(): void {
    this.currentAggId = null;
    this.el.style.display = "none";
  }

  destroy(): void {
    this.el.remove();
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
