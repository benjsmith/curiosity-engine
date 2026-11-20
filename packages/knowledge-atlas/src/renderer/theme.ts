/**
 * Theme resolution (PLAN §9.4): AtlasTheme tokens → resolved paint
 * values. Hosts may override via the theme prop or --atlas-* CSS
 * custom properties (read by the adapters, not here — the renderer
 * itself never touches the DOM beyond its canvas).
 */

import type { AtlasTheme, AtlasThemeToken } from "../core/types.ts";

export type ResolvedTheme = {
  tokens: Record<AtlasThemeToken, string>;
  palette: Record<string, string>;
};

export const DARK_TOKENS: Record<AtlasThemeToken, string> = {
  bg: "#101014",
  text: "#e8e8ee",
  textMuted: "#9a9aa8",
  line: "#3a3a46",
  accent: "#7aa2ff",
  aggregateFill: "#23232d",
  horizonBg: "#16161d",
};

export const LIGHT_TOKENS: Record<AtlasThemeToken, string> = {
  bg: "#fafafa",
  text: "#1c1c22",
  textMuted: "#6b6b76",
  line: "#d4d4dc",
  accent: "#2f6fed",
  aggregateFill: "#ececf2",
  horizonBg: "#f1f1f6",
};

/** CE palette as the type-colour fallback of last resort. */
const FALLBACK_PALETTE: Record<string, string> = {
  project: "#4d1ae8", analysis: "#1d6996", concept: "#38a6a5",
  entity: "#0f8554", evidence: "#73af48", fact: "#edad08",
  figure: "#e17c05", table: "#cc503e", source: "#94346e",
  note: "#6f4070", "todo-list": "#9656a2", procedure: "#5e4fa2",
  execution: "#c51b8a", hypothesis: "#7570b3", unclassified: "#ffffff",
  default: "#bbbbbb",
};

const RESERVED_TYPE_COLORS = new Set([
  "#4d1ae8", "#1d6996", "#38a6a5", "#0f8554", "#73af48", "#edad08",
  "#e17c05", "#cc503e", "#94346e", "#6f4070", "#9656a2", "#5e4fa2",
  "#c51b8a", "#7570b3", "#ffffff", "#bbbbbb",
]);

/** Stable colour for a type that did not set one. Matches
 *  meta_ontology.deterministic_color and the wiki-view theme.js copy. */
export function deterministicTypeColor(typeName: string): string {
  const name = (typeName || "").trim().toLowerCase();
  let hue = fnv1a(name) % 360;
  for (let i = 0; i < 12; i++) {
    const color = hslHex(hue, 0.55, 0.46);
    if (!RESERVED_TYPE_COLORS.has(color)) return color;
    hue = (hue + 29) % 360;
  }
  return hslHex(hue, 0.55, 0.46);
}

function fnv1a(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

function hslHex(hue: number, sat: number, light: number): string {
  const c = (1 - Math.abs(2 * light - 1)) * sat;
  const hp = (hue % 360) / 60;
  const x = c * (1 - Math.abs(hp % 2 - 1));
  let r = 0, g = 0, b = 0;
  if (hp < 1) { r = c; g = x; }
  else if (hp < 2) { r = x; g = c; }
  else if (hp < 3) { g = c; b = x; }
  else if (hp < 4) { g = x; b = c; }
  else if (hp < 5) { r = x; b = c; }
  else { r = c; b = x; }
  const m = light - c / 2;
  const ch = (v: number) => Math.max(0, Math.min(255, Math.round((v + m) * 255)));
  const hex = (n: number) => n.toString(16).padStart(2, "0");
  return `#${hex(ch(r))}${hex(ch(g))}${hex(ch(b))}`;
}

export function resolveTheme(
  theme: AtlasTheme | undefined,
  dataPalette?: Record<string, string>,
  mode: "dark" | "light" = "dark",
): ResolvedTheme {
  const base = mode === "dark" ? DARK_TOKENS : LIGHT_TOKENS;
  return {
    tokens: { ...base, ...theme?.tokens },
    palette: { ...FALLBACK_PALETTE, ...dataPalette, ...theme?.palette },
  };
}

export function typeColour(type: string, theme: ResolvedTheme): string {
  const direct = theme.palette[type];
  if (direct) return direct;
  if (type && type !== "unclassified" && type !== "default") {
    return deterministicTypeColor(type);
  }
  return theme.palette.default ?? "#7a7a7a";
}
