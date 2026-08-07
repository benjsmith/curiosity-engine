/**
 * Core navigation e2e (PLAN §18): the P1 loop — focus → open → back →
 * forward — plus pins, keyboard, zoom bands, mode toggles, and the
 * discovery horizon. Screenshots land in e2e/shots/ for the visual
 * evaluation pass recorded in docs/results.md.
 */

import { expect, test, type Page } from "@playwright/test";
import * as fs from "node:fs";

const SHOTS = "e2e/shots";
test.beforeAll(() => fs.mkdirSync(SHOTS, { recursive: true }));

async function ready(page: Page): Promise<void> {
  await expect(page.getByTestId("hud-nodes")).not.toHaveText("–", { timeout: 15_000 });
  // Let the 300ms transition settle before screenshots/interactions.
  await page.waitForTimeout(450);
}

async function focusTitle(page: Page): Promise<string> {
  return (await page.getByTestId("focus-title").textContent()) ?? "";
}

test("loads the workspace fixture with a bounded scene", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  const nodes = Number(await page.getByTestId("hud-nodes").textContent());
  expect(nodes).toBeGreaterThan(5);
  expect(nodes).toBeLessThanOrEqual(470);
  // At CE-viewer core scale most nearby material is VISIBLE, so which
  // discovery classes populate depends on the wiki; any class counts.
  await expect(page.locator('[data-testid^="cls-"]').first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-workspace-focus.png` });
});

test("navigation loop: candidate focus → back → forward", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  const t0 = await focusTitle(page);

  const candidate = page.getByTestId("candidate").first();
  const candidateText = (await candidate.textContent()) ?? "";
  await candidate.click();
  await ready(page);
  const t1 = await focusTitle(page);
  expect(t1).not.toBe(t0);
  expect(candidateText).toContain(t1);

  await page.getByTestId("btn-back").click();
  await ready(page);
  expect(await focusTitle(page)).toBe(t0);

  await page.getByTestId("btn-forward").click();
  await ready(page);
  expect(await focusTitle(page)).toBe(t1);

  // The trail records the discovery origin of the transition.
  await expect(page.getByTestId("trail-panel")).toContainText("via");
  await page.screenshot({ path: `${SHOTS}/02-after-navigation.png` });
});

test("canvas click focuses a node; double-click opens it", async ({ page }) => {
  test.setTimeout(90_000); // dense scenes need more grid probing
  await page.goto("/");
  await ready(page);
  const canvas = page.getByTestId("atlas-canvas");
  const box = (await canvas.boundingBox())!;
  const t0 = await focusTitle(page);

  // Probe a grid of points; click pointer-cursor hits until one
  // changes the focus (a hit may be an aggregate, which zooms instead).
  let focused = false;
  outer: for (let dx = -0.3; dx <= 0.3; dx += 0.06) {
    for (let dy = -0.3; dy <= 0.3; dy += 0.06) {
      if (Math.abs(dx) < 0.04 && Math.abs(dy) < 0.04) continue; // skip focus itself
      const x = box.x + box.width / 2 + dx * box.width;
      const y = box.y + box.height / 2 + dy * box.height;
      await page.mouse.move(x, y);
      await page.waitForTimeout(16);
      const cursor = await canvas.evaluate((el) => getComputedStyle(el).cursor);
      if (cursor !== "pointer") continue;
      await page.mouse.click(x, y);
      await ready(page);
      if ((await focusTitle(page)) !== t0) {
        focused = true;
        break outer;
      }
    }
  }
  expect(focused, "a canvas click re-focused the atlas").toBe(true);

  // Double-click any node (the focus is not pinned at centre) → open.
  let opened = false;
  outer2: for (let dx = -0.3; dx <= 0.3; dx += 0.06) {
    for (let dy = -0.3; dy <= 0.3; dy += 0.06) {
      const x = box.x + box.width / 2 + dx * box.width;
      const y = box.y + box.height / 2 + dy * box.height;
      await page.mouse.move(x, y);
      await page.waitForTimeout(16);
      const cursor = await canvas.evaluate((el) => getComputedStyle(el).cursor);
      if (cursor !== "pointer") continue;
      await page.mouse.dblclick(x, y);
      if (await page.getByTestId("opened-item").isVisible({ timeout: 1500 }).catch(() => false)) {
        opened = true;
        break outer2;
      }
      await ready(page); // the first click of the dblclick may have re-focused
    }
  }
  expect(opened, "double-click opened an item").toBe(true);
});

test("pin focus survives navigation", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  await page.getByTestId("btn-pin").click();
  await ready(page);
  await expect(page.getByTestId("trail-panel")).toContainText("pinned 1");
  await page.getByTestId("candidate").first().click();
  await ready(page);
  await expect(page.getByTestId("trail-panel")).toContainText("pinned 1");
});

test("semantic zoom out aggregates; hysteresis holds band", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  const nodesAtBand2 = Number(await page.getByTestId("hud-nodes").textContent());
  await page.getByTestId("btn-zoom-out").click();
  await ready(page);
  const nodesAtBand1 = Number(await page.getByTestId("hud-nodes").textContent());
  expect(nodesAtBand1).toBeLessThan(nodesAtBand2);
  await page.screenshot({ path: `${SHOTS}/03-zoomed-out.png` });
  await page.getByTestId("btn-zoom-in").click();
  await ready(page);
  expect(Number(await page.getByTestId("hud-nodes").textContent())).toBeGreaterThan(nodesAtBand1);
});

test("keyboard: arrows + Enter navigate, Backspace goes back", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  const t0 = await focusTitle(page);
  await page.getByTestId("atlas-canvas").click({ position: { x: 10, y: 10 } }); // focus canvas, no node
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  await ready(page);
  const t1 = await focusTitle(page);
  expect(t1).not.toBe(t0);
  await page.keyboard.press("Backspace");
  await ready(page);
  expect(await focusTitle(page)).toBe(t0);
});

test("layout modes render on identical scene data", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  for (const mode of ["hybrid", "adaptive-hybrid", "focus", "hyperbolic", "force", "adaptive"] as const) {
    await page.getByTestId("layout-select").selectOption(mode);
    await ready(page);
    const nodes = Number(await page.getByTestId("hud-nodes").textContent());
    expect(nodes, mode).toBeGreaterThan(5);
    await page.screenshot({ path: `${SHOTS}/04-layout-${mode}.png` });
  }
});

test("explanation on right-click of a candidate", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  await page.getByTestId("candidate").first().click({ button: "right" });
  await expect(page.getByTestId("explanation")).toBeVisible();
  const text = (await page.getByTestId("explanation").textContent()) ?? "";
  expect(text.length).toBeGreaterThan(20);
});

test("scaled 1M-node corpus stays bounded and responsive", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  await page.getByTestId("fixture-select").selectOption({ label: "scaled-1M" });
  await ready(page);
  const nodes = Number(await page.getByTestId("hud-nodes").textContent());
  expect(nodes).toBeGreaterThan(5);
  expect(nodes).toBeLessThanOrEqual(470);
  const build = (await page.getByTestId("hud-build").textContent()) ?? "";
  expect(parseFloat(build)).toBeLessThan(500);
  await page.screenshot({ path: `${SHOTS}/05-scaled-1m.png` });
  // Navigate within the corpus.
  const candidate = page.getByTestId("candidate").first();
  if (await candidate.isVisible()) {
    await candidate.click();
    await ready(page);
  }
});

test("remote-sim source drives the same viewer", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  await page.getByTestId("fixture-select").selectOption({ label: "remote-sim (120ms)" });
  await ready(page);
  expect(Number(await page.getByTestId("hud-nodes").textContent())).toBeGreaterThan(5);
});

test("other fixtures render across layouts", async ({ page }) => {
  await page.goto("/");
  await ready(page);
  for (const fixture of ["ontology-tree", "dense-smallworld", "mixed-multiscale"]) {
    await page.getByTestId("fixture-select").selectOption({ label: fixture });
    await ready(page);
    expect(Number(await page.getByTestId("hud-nodes").textContent()), fixture).toBeGreaterThan(3);
    await page.screenshot({ path: `${SHOTS}/06-fixture-${fixture}.png` });
  }
});
