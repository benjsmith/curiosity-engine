import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const ATLAS_JS = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../skills/curiosity-engine/template/wiki-view/static/atlas.js",
);
const SOURCE = readFileSync(ATLAS_JS, "utf8");
const STORAGE_KEY = "curiosity-engine.viewer";

type AtlasViewer = {
  eligible: (data: unknown) => boolean;
  enabled: (data: unknown) => boolean;
};

/** Load the wiki-view IIFE against a fresh localStorage. */
function loadAtlas(stored: string | null): AtlasViewer {
  const store = new Map<string, string>();
  if (stored !== null) store.set(STORAGE_KEY, stored);
  const localStorage = {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
  };
  const window: Record<string, unknown> = {
    localStorage,
    location: { search: "", href: "http://localhost/", hash: "" },
  };
  window.window = window;
  const context = createContext({
    window,
    document: { getElementById: () => null, documentElement: { dataset: {} } },
    localStorage,
    URL,
    URLSearchParams,
    console,
    setTimeout,
    clearTimeout,
  });
  runInContext(SOURCE, context, { filename: "atlas.js" });
  return window.AtlasViewer as AtlasViewer;
}

function wiki(pages: number): { pages: Record<string, { id: string }> } {
  const out: Record<string, { id: string }> = {};
  for (let i = 0; i < pages; i++) out[`p${i}`] = { id: `p${i}` };
  return { pages: out };
}

describe("Atlas enabled() agrees with eligible() on the 360-page floor", () => {
  const below = wiki(360);
  const above = wiki(361);

  it("does not enable Atlas at or below the floor, even with a stored atlas preference", () => {
    const plain = loadAtlas(null);
    expect(plain.eligible(below)).toBe(false);
    expect(plain.enabled(below)).toBe(false);

    const stored = loadAtlas("atlas");
    expect(stored.eligible(below)).toBe(false);
    expect(stored.enabled(below)).toBe(false);
  });

  it("honors a stored atlas preference only above the floor", () => {
    const plain = loadAtlas(null);
    expect(plain.eligible(above)).toBe(true);
    expect(plain.enabled(above)).toBe(false);

    const stored = loadAtlas("atlas");
    expect(stored.eligible(above)).toBe(true);
    expect(stored.enabled(above)).toBe(true);
  });
});
