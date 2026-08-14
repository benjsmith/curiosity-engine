/**
 * P0 embedding proof (PLAN §14.4): the vendored IIFE bundle +
 * wiki-view glue drive a genuine CE data.json payload in a bare page —
 * no vite, no React, no dev server modules. This is exactly what the
 * Curiosity Engine viewer loads when ?viewer=atlas is on.
 */

import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const PKG = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const IIFE = path.join(PKG, "dist", "knowledge-atlas.iife.js");
const GLUE = path.resolve(PKG, "../../skills/curiosity-engine/template/wiki-view/static/atlas.js");
const WIKI_MAIN = path.resolve(PKG, "../../skills/curiosity-engine/template/wiki-view/static/main.js");
const SIDEBAR = path.resolve(PKG, "../../skills/curiosity-engine/template/wiki-view/static/sidebar.js");
const CLASSIC_GRAPH = path.resolve(PKG, "../../skills/curiosity-engine/template/wiki-view/static/graph.js");
const D3 = path.resolve(PKG, "../../skills/curiosity-engine/template/wiki-view/static/vendor/d3.min.js");

test.beforeAll(() => {
  if (!fs.existsSync(IIFE)) {
    execSync("pnpm run build", { cwd: PKG, stdio: "inherit" });
  }
});

test("IIFE + wiki-view glue mount against a CE payload", async ({ page }) => {
  // Generate the CE-shaped fixture payload in the browser context is
  // not possible (fixtures are TS) — evaluate it via the harness dev
  // server instead? No: keep this hermetic. Serialise it in Node.
  const { workspaceSmallData } = await import("../fixtures/index.ts");
  const data = workspaceSmallData(42);

  // Load the dev-server origin (about:blank denies localStorage), then
  // replace the document with a bare wiki-view-style shell.
  await page.goto("/");
  await page.setContent(`
    <style>.hidden { display:none }</style>
    <div id="graph" style="width:900px;height:600px"></div>
    <button id="label-mode"><span id="label-mode-state">auto</span></button>
    <button id="label-types"><span id="label-types-state">4/12</span></button>
    <div id="label-types-panel" class="hidden"></div>
    <button id="settings-trigger">physics</button>
    <div id="settings-panel" class="hidden">
      <input id="phys-charge" type="range" min="-1200" max="-50" value="-420"><span id="phys-charge-val">-420</span>
      <input id="phys-link" type="range" min="20" max="300" value="110"><span id="phys-link-val">110</span>
      <input id="phys-collide" type="range" min="0" max="40" value="10"><span id="phys-collide-val">10</span>
      <button id="phys-reset">Reset</button>
    </div>
  `);
  await page.addScriptTag({ path: IIFE });
  await page.addScriptTag({ path: GLUE });

  const result = await page.evaluate((payload) => {
    // Simulate main.js's flag path.
    localStorage.setItem("curiosity-engine.viewer", "atlas");
    const w = window as unknown as {
      AtlasViewer: { enabled: (d: unknown) => boolean; init: (d: unknown) => { focus: (id: string) => void } | null };
      KnowledgeAtlas: unknown;
    };
    if (!w.AtlasViewer.enabled(payload)) return { ok: false, why: "eligible stored choice not detected" };
    const api = w.AtlasViewer.init(payload);
    if (!api) return { ok: false, why: "init returned null" };
    (window as unknown as { __atlasApi: unknown }).__atlasApi = api;
    return { ok: true, why: "" };
  }, data as unknown as Record<string, unknown>);
  expect(result.ok, result.why).toBe(true);

  // A canvas appears and paints pixels.
  const canvas = page.locator("#graph > canvas:not(.atlas-minimap)");
  await expect(canvas).toBeVisible();
  // The main 900x600 scene is density-limited, but local CE data still
  // supplies the whole-wiki canonical field to the overview.
  await expect(page.locator(".atlas-minimap")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(600);
  const painted = await canvas.evaluate((el) => {
    const c = el as HTMLCanvasElement;
    const ctx = c.getContext("2d")!;
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    let nonBg = 0;
    for (let i = 0; i < px.length; i += 4) {
      if (px[i] !== 16 || px[i + 1] !== 16 || px[i + 2] !== 20) nonBg++;
    }
    return nonBg;
  });
  expect(painted).toBeGreaterThan(1000);

  // Theme changes repaint the mounted canvas and cached minimap live;
  // no Classic → Atlas remount is required.
  const darkCornerBrightness = await canvas.evaluate((el) => {
    const px = (el as HTMLCanvasElement).getContext("2d")!.getImageData(0, 0, 1, 1).data;
    return px[0] + px[1] + px[2];
  });
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  await expect.poll(async () => canvas.evaluate((el) => {
    const px = (el as HTMLCanvasElement).getContext("2d")!.getImageData(0, 0, 1, 1).data;
    return px[0] + px[1] + px[2];
  })).toBeGreaterThan(darkCornerBrightness + 250);
  await expect.poll(async () => page.locator(".atlas-minimap").evaluate((el) => {
    const px = (el as HTMLCanvasElement).getContext("2d")!.getImageData(1, 1, 1, 1).data;
    return [px[0], px[1], px[2]];
  })).toEqual([250, 250, 250]);
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });

  // Atlas keeps the host's label and physics controls live.
  await page.locator("#label-mode").click();
  await expect(page.locator("#label-mode-state")).toHaveText("on");
  await page.locator("#settings-trigger").click();
  await expect(page.locator("#settings-panel")).toBeVisible();
  await page.locator("#phys-charge").evaluate((el) => {
    (el as HTMLInputElement).value = "-700";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#phys-charge-val")).toHaveText("-700");

  // The Graph-facade contract: focus() re-centres without throwing,
  // and open events route through the hash (main.js contract).
  await page.evaluate(() => {
    (window as unknown as { __atlasApi: { focus: (id: string) => void } }).__atlasApi.focus(
      "concepts/transformers",
    );
  });
  await page.waitForTimeout(500);
  await canvas.dblclick({ position: { x: 450, y: 300 } });
  await expect
    .poll(async () => page.evaluate(() => window.location.hash))
    .toContain("#page=");
});

test("Classic exposes the same density-adaptive navigation minimap", async ({ page }) => {
  const { workspaceSmallData } = await import("../fixtures/index.ts");
  const data = workspaceSmallData(42);
  await page.goto("/");
  await page.setContent(`
    <style>#graph { position:relative;width:900px;height:600px } #graph svg { width:100%;height:100%;display:block }</style>
    <div id="graph"></div>
    <button id="label-mode"><span id="label-mode-state">auto</span></button>
    <button id="label-types"><span id="label-types-state">4/12</span></button>
    <div id="label-types-panel" class="hidden"></div>
    <button id="settings-trigger">physics</button>
    <div id="settings-panel" class="hidden"></div>
  `);
  await page.addScriptTag({ path: D3 });
  await page.addScriptTag({ path: CLASSIC_GRAPH });
  await page.evaluate((payload) => {
    (window as unknown as { Graph: { init(data: unknown): void } }).Graph.init(payload);
  }, data as unknown as Record<string, unknown>);

  const map = page.locator(".classic-minimap");
  await expect(page.locator("#graph > svg")).toBeVisible();
  await expect(map).toBeVisible();
  const before = await map.getAttribute("data-camera-x");
  await map.click({ position: { x: 170, y: 28 } });
  await expect.poll(() => map.getAttribute("data-camera-x")).not.toBe(before);
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  await expect.poll(async () => map.evaluate((el) => {
    const px = (el as HTMLCanvasElement).getContext("2d")!.getImageData(0, 0, 1, 1).data;
    return [px[0], px[1], px[2]];
  })).toEqual([250, 250, 250]);
});

test("Atlas preference and chooser are offered only above 360 wiki pages", async ({ page }) => {
  const { workspaceSmallData } = await import("../fixtures/index.ts");
  const large = workspaceSmallData(42);
  const keep = new Set(Object.keys(large.pages).slice(0, 360));
  const small = {
    ...large,
    pages: Object.fromEntries(Object.entries(large.pages).filter(([id]) => keep.has(id))),
    nodes: large.nodes.filter((node) => keep.has(node.id)),
    edges: large.edges.filter((edge) => {
      const source = typeof edge.source === "string" ? edge.source : edge.source.id;
      const target = typeof edge.target === "string" ? edge.target : edge.target.id;
      return keep.has(source) && keep.has(target);
    }),
  };

  await page.goto("/");
  await page.setContent(`
    <button id="viewer-mode" class="hidden">
      <span id="viewer-mode-state">classic</span>
    </button>
  `);
  await page.addScriptTag({ path: GLUE });

  const result = await page.evaluate(({ largePayload, smallPayload }) => {
    localStorage.setItem("curiosity-engine.viewer", "atlas");
    const w = window as unknown as {
      AtlasViewer: {
        minPages: number;
        eligible: (d: unknown) => boolean;
        enabled: (d: unknown) => boolean;
        initChoice: (d: unknown, mode: "classic" | "atlas") => void;
      };
    };
    const button = document.getElementById("viewer-mode")!;
    const state = document.getElementById("viewer-mode-state")!;

    w.AtlasViewer.initChoice(smallPayload, "classic");
    const smallHidden = button.classList.contains("hidden");
    w.AtlasViewer.initChoice(largePayload, "atlas");

    return {
      threshold: w.AtlasViewer.minPages,
      smallEligible: w.AtlasViewer.eligible(smallPayload),
      smallEnabled: w.AtlasViewer.enabled(smallPayload),
      smallHidden,
      largeEligible: w.AtlasViewer.eligible(largePayload),
      largeEnabled: w.AtlasViewer.enabled(largePayload),
      largeHidden: button.classList.contains("hidden"),
      state: state.textContent,
    };
  }, { largePayload: large, smallPayload: small });

  expect(result).toEqual({
    threshold: 360,
    smallEligible: false,
    smallEnabled: false,
    smallHidden: true,
    largeEligible: true,
    largeEnabled: true,
    largeHidden: false,
    state: "atlas",
  });
});

test("wiki-view orchestrator selects Atlas for an eligible stored choice", async ({ page }) => {
  const { workspaceSmallData } = await import("../fixtures/index.ts");
  const data = workspaceSmallData(42);

  await page.route("**/data.json*", async (route) => {
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await page.setContent(`
    <div id="graph" style="width:900px;height:600px"></div>
    <button id="viewer-mode" class="hidden">
      <span id="viewer-mode-state">classic</span>
    </button>
  `);
  await page.evaluate(() => {
    localStorage.setItem("curiosity-engine.viewer", "atlas");
    Object.assign(window, {
      Theme: { init() {} },
      Sidebar: { init() {}, setActive() {} },
      Subgraph: { init() {} },
      Modal: {
        init() {},
        open() { return true; },
        close() {},
        setOnClose() {},
      },
      Graph: {
        init() { (window as unknown as { __classicInit: boolean }).__classicInit = true; },
        focus() {},
        clearFocus() {},
      },
    });
  });
  await page.addScriptTag({ path: IIFE });
  await page.addScriptTag({ path: GLUE });
  await page.addScriptTag({ path: WIKI_MAIN });

  await expect(page.locator("#graph > canvas:not(.atlas-minimap)")).toBeVisible();
  await expect(page.locator("#viewer-mode")).not.toHaveClass(/hidden/);
  await expect(page.locator("#viewer-mode-state")).toHaveText("atlas");
  await expect.poll(() => page.evaluate(() => document.body.dataset.viewer)).toBe("atlas");
  expect(await page.evaluate(() => Boolean((window as unknown as { __classicInit?: boolean }).__classicInit))).toBe(false);
});

test("wiki browser can collapse and expand every page type", async ({ page }) => {
  await page.goto("/");
  await page.setContent(`
    <input id="sidebar-search">
    <button data-action="toggle-all-groups">toggle all</button>
    <div id="sidebar-list"></div>
    <div id="graph-pane"></div>
  `);
  await page.evaluate(() => {
    class FuseStub {
      constructor(_records: unknown[], _options: unknown) {}
      search() { return []; }
    }
    (window as unknown as { Fuse: typeof FuseStub }).Fuse = FuseStub;
  });
  await page.addScriptTag({ path: SIDEBAR });
  await page.evaluate(() => {
    (window as unknown as {
      Sidebar: { init(data: unknown): void };
    }).Sidebar.init({
      pages: {},
      edges: [],
      nodes: [
        { id: "concept/a", title: "A", type: "concept" },
        { id: "source/b", title: "B", type: "source" },
      ],
    });
  });

  const groups = page.locator(".type-group");
  await expect(groups).toHaveCount(2);
  await page.locator('[data-action="toggle-all-groups"]').click();
  expect(await groups.evaluateAll((els) => els.every((el) => (el as HTMLElement).dataset.collapsed === "true"))).toBe(true);
  await page.locator('[data-action="toggle-all-groups"]').click();
  expect(await groups.evaluateAll((els) => els.every((el) => (el as HTMLElement).dataset.collapsed === "false"))).toBe(true);
});
