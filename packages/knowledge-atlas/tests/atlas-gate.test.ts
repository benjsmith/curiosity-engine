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
  initChoice: (data: unknown, mode: string) => void;
};

type Probe = {
  viewer: AtlasViewer;
  hidden: () => boolean;
};

/** Load the wiki-view IIFE against a fresh localStorage and a chooser button. */
function loadAtlas(stored: string | null): Probe {
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
  const classes = new Set(["hidden"]);
  const button = {
    title: "",
    classList: {
      add: (name: string) => classes.add(name),
      remove: (name: string) => classes.delete(name),
      contains: (name: string) => classes.has(name),
    },
    addEventListener() {},
  };
  const state = { textContent: "classic" };
  const window: Record<string, unknown> = {
    localStorage,
    location: { search: "", href: "http://localhost/", hash: "" },
    KnowledgeAtlas: {},
  };
  window.window = window;
  const context = createContext({
    window,
    document: {
      getElementById: (id: string) =>
        id === "viewer-mode" ? button : id === "viewer-mode-state" ? state : null,
      documentElement: { dataset: {} },
    },
    localStorage,
    URL,
    URLSearchParams,
    console,
    setTimeout,
    clearTimeout,
  });
  runInContext(SOURCE, context, { filename: "atlas.js" });
  return {
    viewer: window.AtlasViewer as AtlasViewer,
    hidden: () => classes.has("hidden"),
  };
}

function wiki(pages: number): { pages: Record<string, { id: string }> } {
  const out: Record<string, { id: string }> = {};
  for (let i = 0; i < pages; i++) out[`p${i}`] = { id: `p${i}` };
  return { pages: out };
}

describe("Atlas is available at any wiki size", () => {
  const small = wiki(1);
  const floor = wiki(360);

  it("offers Atlas on a small wiki, and turns it on only when chosen", () => {
    const plain = loadAtlas(null);
    expect(plain.viewer.eligible(small)).toBe(true);
    expect(plain.viewer.enabled(small)).toBe(false);
    expect(plain.viewer.eligible(floor)).toBe(true);
    expect(plain.viewer.enabled(floor)).toBe(false);

    const stored = loadAtlas("atlas");
    expect(stored.viewer.eligible(small)).toBe(true);
    expect(stored.viewer.enabled(small)).toBe(true);
    expect(stored.viewer.enabled(floor)).toBe(true);
  });

  it("shows the chooser on a small wiki and hides it above 1000 pages", () => {
    const smallWiki = loadAtlas(null);
    smallWiki.viewer.initChoice(wiki(12), "classic");
    expect(smallWiki.hidden()).toBe(false);

    const huge = loadAtlas("classic");
    huge.viewer.initChoice(wiki(1001), "atlas");
    expect(huge.hidden()).toBe(true);
    expect(huge.viewer.enabled(wiki(1001))).toBe(true);
  });
});
