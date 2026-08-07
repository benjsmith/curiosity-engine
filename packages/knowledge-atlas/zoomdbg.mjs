import { chromium } from "@playwright/test";
const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto("http://localhost:5173/");
await page.waitForTimeout(2500);
const nodes = async () => Number(await page.getByTestId("hud-nodes").textContent());
console.log("nodes at scale 1:", await nodes());
const box = await page.getByTestId("atlas-canvas").boundingBox();
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, 120); await page.waitForTimeout(250); }
await page.waitForTimeout(2500);
console.log("nodes zoomed OUT ×6:", await nodes());
await page.screenshot({ path: "e2e/shots/dbg-zoomout.png" });
for (let i = 0; i < 12; i++) { await page.mouse.wheel(0, -120); await page.waitForTimeout(250); }
await page.waitForTimeout(2500);
console.log("nodes zoomed IN ×6:", await nodes());
await page.screenshot({ path: "e2e/shots/dbg-zoomin.png" });
await browser.close();
