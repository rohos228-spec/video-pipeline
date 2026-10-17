import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const baseURL = process.env.ORCH_WEB_URL ?? "http://127.0.0.1:3100";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  retries: 2,
  reporter: [["list"]],
  expect: {
    timeout: 20_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
      animations: "disabled",
    },
  },
  use: {
    baseURL,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
      },
    },
  ],
  webServer: [
    {
      command: "node e2e/orchestrator/mock-server.mjs",
      cwd: webRoot,
      port: Number(process.env.ORCH_MOCK_PORT ?? 8799),
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: "pnpm exec next dev --turbopack --port 3100",
      cwd: webRoot,
      port: 3100,
      reuseExistingServer: !process.env.CI,
      timeout: 240_000,
      env: { ...process.env, NEXT_PUBLIC_ORCH_TASK_MODE: "1" },
    },
  ],
});
