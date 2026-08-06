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
  note: "#6f4070", "todo-list": "#9656a2", unclassified: "#ffffff",
  default: "#bbbbbb",
};

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
  return theme.palette[type] ?? theme.palette.default ?? "#7a7a7a";
}
