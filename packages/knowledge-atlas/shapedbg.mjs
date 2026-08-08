import { chromium } from "@playwright/test";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.on("pageerror", (e) => console.log("[err]", e.message));
await page.goto("http://localhost:5173/");
await page.waitForTimeout(2500);
for (const [name, val] of [["circle", "0"], ["rect", "1"]]) {
  await page.getByTestId("shape-slider").fill(val);
  await page.waitForTimeout(2500);
  console.log(name, "nodes:", await page.getByTestId("hud-nodes").textContent());
  await page.screenshot({ path: `e2e/shots/dbg-shape-${name}.png` });
}
await browser.close();
