import { chromium } from "@playwright/test";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.on("pageerror", (e) => console.log("[err]", e.message));
await page.goto("http://localhost:5173/");
await page.waitForTimeout(2500);
const box = await page.getByTestId("atlas-canvas").boundingBox();
const cy = box.y + box.height / 2;
// Repeated centerward drags from the RIGHT boundary (the "facts" side
// in the screenshot). After each, report aggregate labels.
const counts = async () => await page.evaluate(() => {
  // grab canvas-adjacent state via the HUD instead: nodes + aggregates
  return {
    nodes: document.querySelector('[data-testid="hud-nodes"]')?.textContent,
    focus: document.querySelector('[data-testid="focus-title"]')?.textContent,
  };
});
console.log("start:", await counts());
for (let round = 1; round <= 4; round++) {
  const sx = box.x + box.width * 0.93;
  await page.mouse.move(sx, cy);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) {
    await page.mouse.move(sx - i * box.width * 0.04, cy);
    await page.waitForTimeout(16);
  }
  await page.mouse.up();
  await page.waitForTimeout(1400); // settle + commits
  console.log(`after drag ${round}:`, await counts());
  await page.screenshot({ path: `e2e/shots/dbg-absorb-${round}.png` });
}
await browser.close();
