import { randomBytes } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const port = process.env.ORCH_R6_PORT ?? "8877";
if (port === "8765" || port === "8766") {
  throw new Error("ORCH_R6_PORT не может быть 8765 или 8766");
}

if (!process.env.ORCH_R6_TOKEN) {
  process.env.ORCH_R6_TOKEN = randomBytes(24).toString("hex");
}
if (!process.env.ORCH_R6_BASE_URL) {
  process.env.ORCH_R6_BASE_URL = `http://127.0.0.1:${port}`;
}

const external = process.env.ORCH_R6_EXTERNAL === "1";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    locale: "ru-RU",
    trace: "retain-on-failure",
    screenshot: "off",
  },
  webServer: external
    ? undefined
    : {
        command: "node e2e/orchestrator/r6/serve.mjs",
        cwd: webRoot,
        port: Number(port),
        reuseExistingServer: false,
        timeout: 180_000,
        env: {
          ...process.env,
          ORCH_R6_PORT: port,
          ORCH_R6_TOKEN: process.env.ORCH_R6_TOKEN,
        },
      },
});
