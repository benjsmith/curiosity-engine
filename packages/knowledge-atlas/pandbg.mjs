import { chromium } from "@playwright/test";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 844, height: 390 } }); // landscape phone
page.on("pageerror", (e) => console.log("[err]", e.message));
await page.goto("http://localhost:5173/");
await page.waitForTimeout(2600);
const box = await page.getByTestId("atlas-canvas").boundingBox();
const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
console.log("focus before:", await page.getByTestId("focus-title").textContent());
await page.screenshot({ path: "e2e/shots/pan-0-before.png" });
// Slow drag INSIDE the core, rightward (pull content in from the left).
await page.mouse.move(cx, cy);
await page.mouse.down();
for (let i = 1; i <= 14; i++) {
  await page.mouse.move(cx + i * 14, cy, { steps: 1 });
  await page.waitForTimeout(30);
}
await page.screenshot({ path: "e2e/shots/pan-1-middrag.png" });
await page.mouse.up();
await page.waitForTimeout(900); // spring back + commits settle
await page.screenshot({ path: "e2e/shots/pan-2-released.png" });
console.log("focus after:", await page.getByTestId("focus-title").textContent());
await browser.close();
