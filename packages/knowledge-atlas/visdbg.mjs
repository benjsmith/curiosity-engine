import { chromium } from "@playwright/test";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
// Landscape phone (matches the user's screenshots) + portrait + desktop.
for (const [name, vp] of [["land", { width: 844, height: 390 }], ["port", { width: 390, height: 780 }], ["desk", { width: 1280, height: 800 }]]) {
  const page = await browser.newPage({ viewport: vp });
  page.on("pageerror", (e) => console.log("[err]", e.message));
  await page.goto("http://localhost:5173/");
  await page.waitForTimeout(2800);
  console.log(name, "nodes:", await page.getByTestId("hud-nodes").textContent());
  await page.screenshot({ path: `e2e/shots/dbg-squircle-${name}.png` });
  await page.close();
}
// Absorption run on desktop: repeated drags at the facts sector until it drains.
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto("http://localhost:5173/");
await page.waitForTimeout(2500);
const box = await page.getByTestId("atlas-canvas").boundingBox();
const cy = box.y + box.height / 2;
for (let round = 1; round <= 6; round++) {
  const sx = box.x + box.width * 0.95;
  await page.mouse.move(sx, cy);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) { await page.mouse.move(sx - i * box.width * 0.04, cy); await page.waitForTimeout(16); }
  await page.mouse.up();
  await page.waitForTimeout(1300);
  console.log(`drag ${round}: focus=`, await page.getByTestId("focus-title").textContent());
}
await page.screenshot({ path: "e2e/shots/dbg-absorbed.png" });
await browser.close();
