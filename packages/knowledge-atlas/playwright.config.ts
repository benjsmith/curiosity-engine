import { defineConfig } from "@playwright/test";

// Unlike Switchbay's config, this one self-starts the harness dev
// server — the harness needs no daemon or workspace, so e2e is
// one-command reproducible.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5199",
    viewport: { width: 1280, height: 800 },
    trace: "off",
    // Use a pre-installed Chromium when the environment provides one
    // (e.g. Claude Code remote sets PLAYWRIGHT_CHROMIUM_PATH-style
    // wrappers under /opt/pw-browsers); fall back to the managed
    // download otherwise.
    launchOptions: process.env.PW_CHROMIUM_PATH
      ? { executablePath: process.env.PW_CHROMIUM_PATH }
      : {},
  },
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:5199",
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
