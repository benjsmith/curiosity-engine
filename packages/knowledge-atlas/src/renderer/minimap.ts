/**
 * Flat-space overview for Atlas navigation.
 *
 * The minimap always draws the canonical whole-graph force layout with
 * no edges. Atlas's camera is represented as a viewport rectangle;
 * clicking or dragging here changes only the affine camera centre.
 */

import { coreRadiusAt } from "../core/geometry.ts";
import type { LayoutPoint, LayoutResult, SceneData } from "../core/types.ts";
import type { Camera } from "./types.ts";
import type { ResolvedTheme } from "./theme.ts";
import { typeColour } from "./theme.ts";

type MapState = {
  minX: number;
  minY: number;
  scale: number;
  pad: number;
};

export class AtlasMinimap {
  private readonly canvas: HTMLCanvasElement;
  private readonly base = document.createElement("canvas");
  private map: MapState | null = null;
  private onNavigate: (x: number, y: number) => void;
  private cachedLayout: LayoutResult | null = null;
  private cachedScene: SceneData | null = null;
  private cacheKey = "";

  constructor(host: HTMLElement, onNavigate: (x: number, y: number) => void) {
    this.onNavigate = onNavigate;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "atlas-minimap";
    this.canvas.tabIndex = 0;
    this.canvas.setAttribute("role", "application");
    this.canvas.setAttribute("aria-label", "Atlas overview map; click or drag to move the main view");
    this.canvas.title = "Whole-wiki overview — click or drag to navigate";
    this.canvas.style.cssText = [
      "position:absolute",
      "right:12px",
      "bottom:12px",
      "width:190px",
      "height:128px",
      "z-index:4",
      "border:1px solid rgba(127,127,127,.42)",
      "border-radius:8px",
      "box-shadow:0 3px 14px rgba(0,0,0,.24)",
      "cursor:crosshair",
      "touch-action:none",
    ].join(";");
    host.appendChild(this.canvas);

    const navigate = (ev: PointerEvent) => {
      if (!this.map) return;
      const rect = this.canvas.getBoundingClientRect();
      const x = (ev.clientX - rect.left - this.map.pad) / this.map.scale + this.map.minX;
      const y = (ev.clientY - rect.top - this.map.pad) / this.map.scale + this.map.minY;
      this.onNavigate(x, y);
    };
    this.canvas.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      this.canvas.setPointerCapture(ev.pointerId);
      navigate(ev);
    });
    this.canvas.addEventListener("pointermove", (ev) => {
      if (this.canvas.hasPointerCapture(ev.pointerId)) navigate(ev);
    });
    this.canvas.addEventListener("pointerup", (ev) => {
      if (this.canvas.hasPointerCapture(ev.pointerId)) this.canvas.releasePointerCapture(ev.pointerId);
    });
  }

  update(
    layout: LayoutResult | undefined,
    scene: SceneData | null,
    camera: Camera,
    viewport: { width: number; height: number },
    theme: ResolvedTheme,
    boundaryShape?: number,
  ): void {
    if (!layout || !scene || layout.positions.size < 2) {
      this.canvas.hidden = true;
      return;
    }
    this.canvas.hidden = false;
    const compact = Math.min(viewport.width, viewport.height) <= 520;
    const cssW = compact ? 126 : 190;
    const cssH = compact ? 88 : 128;
    this.canvas.style.width = `${cssW}px`;
    this.canvas.style.height = `${cssH}px`;
    const dpr = window.devicePixelRatio || 1;
    const pixelW = Math.round(cssW * dpr);
    const pixelH = Math.round(cssH * dpr);
    if (this.canvas.width !== pixelW) this.canvas.width = pixelW;
    if (this.canvas.height !== pixelH) this.canvas.height = pixelH;
    this.canvas.dataset.cameraX = String(camera.x);
    this.canvas.dataset.cameraY = String(camera.y);
    this.canvas.dataset.cameraScale = String(camera.scale);
    const ctx = this.canvas.getContext("2d");
    if (!ctx) return;
    const themeKey = `${theme.tokens.bg}|${Object.entries(theme.palette).join("|")}`;
    const nextCacheKey = `${pixelW}x${pixelH}|${themeKey}`;
    if (layout !== this.cachedLayout || scene !== this.cachedScene || nextCacheKey !== this.cacheKey) {
      this.rebuildBase(layout, scene, cssW, cssH, dpr, theme);
      this.cachedLayout = layout;
      this.cachedScene = scene;
      this.cacheKey = nextCacheKey;
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, pixelW, pixelH);
    ctx.drawImage(this.base, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!this.map) return;

    const { minX, minY, scale, pad } = this.map;
    const worldCx = -camera.x / Math.max(0.001, camera.scale);
    const worldCy = -camera.y / Math.max(0.001, camera.scale);
    const halfW = coreRadiusAt(0, viewport, 1, boundaryShape) / Math.max(0.001, camera.scale);
    const halfH = coreRadiusAt(Math.PI / 2, viewport, 1, boundaryShape) / Math.max(0.001, camera.scale);
    const x = pad + (worldCx - halfW - minX) * scale;
    const y = pad + (worldCy - halfH - minY) * scale;
    const w = halfW * 2 * scale;
    const h = halfH * 2 * scale;
    ctx.strokeStyle = theme.tokens.accent;
    ctx.lineWidth = 1.5;
    ctx.fillStyle = theme.tokens.accent;
    ctx.globalAlpha = 0.08;
    ctx.fillRect(x, y, w, h);
    ctx.globalAlpha = 0.95;
    ctx.strokeRect(x, y, w, h);
    ctx.globalAlpha = 1;
  }

  private rebuildBase(
    layout: LayoutResult,
    scene: SceneData,
    cssW: number,
    cssH: number,
    dpr: number,
    theme: ResolvedTheme,
  ): void {
    const points: Array<[string, LayoutPoint]> = [];
    for (const node of scene.nodes) {
      const point = layout.positions.get(node.id);
      if (point) points.push([node.id, point]);
    }
    if (points.length < 2) {
      this.map = null;
      return;
    }
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const [, p] of points) {
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, p.y);
    }
    const spanX = Math.max(1, maxX - minX);
    const spanY = Math.max(1, maxY - minY);
    const pad = 7;
    const scale = Math.min((cssW - pad * 2) / spanX, (cssH - pad * 2) / spanY);
    // Centre the shorter dimension while keeping a simple invertible map.
    minX -= ((cssW - pad * 2) / scale - spanX) / 2;
    minY -= ((cssH - pad * 2) / scale - spanY) / 2;
    this.map = { minX, minY, scale, pad };

    const pixelW = Math.round(cssW * dpr);
    const pixelH = Math.round(cssH * dpr);
    this.base.width = pixelW;
    this.base.height = pixelH;
    const ctx = this.base.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = theme.tokens.bg;
    ctx.globalAlpha = 0.94;
    ctx.fillRect(0, 0, cssW, cssH);

    const byId = new Map(scene.nodes.map((node) => [node.id, node]));
    const usableArea = Math.max(1, (cssW - pad * 2) * (cssH - pad * 2));
    const maxMarks = Math.max(1_500, Math.round(usableArea * 0.45));
    const stride = Math.max(1, Math.ceil(points.length / maxMarks));
    const dotR = Math.max(0.28, Math.min(2.2, Math.sqrt(usableArea / points.length) * 0.18));
    const density = points.length / usableArea;
    const alpha = Math.max(0.18, Math.min(0.86, 1.4 / Math.sqrt(Math.max(1, density))));
    for (let i = 0; i < points.length; i++) {
      const [id, p] = points[i];
      if (stride > 1 && stringHash(id) % stride !== 0) continue;
      const node = byId.get(id);
      if (!node) continue;
      ctx.fillStyle = typeColour(node.item.type, theme);
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.arc(
        pad + (p.x - minX) * scale,
        pad + (p.y - minY) * scale,
        dotR,
        0,
        Math.PI * 2,
      );
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  destroy(): void {
    this.canvas.remove();
    this.map = null;
    this.cachedLayout = null;
    this.cachedScene = null;
  }
}

function stringHash(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i++) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
