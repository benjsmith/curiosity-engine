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
    <div id="graph" style="width:900px;height:600px"></div>
  `);
  await page.addScriptTag({ path: IIFE });
  await page.addScriptTag({ path: GLUE });

  const result = await page.evaluate((payload) => {
    // Simulate main.js's flag path.
    localStorage.setItem("curiosity-engine.viewer", "atlas");
    const w = window as unknown as {
      AtlasViewer: { enabled: () => boolean; init: (d: unknown) => { focus: (id: string) => void } | null };
      KnowledgeAtlas: unknown;
    };
    if (!w.AtlasViewer.enabled()) return { ok: false, why: "flag not detected" };
    const api = w.AtlasViewer.init(payload);
    if (!api) return { ok: false, why: "init returned null" };
    (window as unknown as { __atlasApi: unknown }).__atlasApi = api;
    return { ok: true, why: "" };
  }, data as unknown as Record<string, unknown>);
  expect(result.ok, result.why).toBe(true);

  // A canvas appears and paints pixels.
  const canvas = page.locator("#graph canvas");
  await expect(canvas).toBeVisible();
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
