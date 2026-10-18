import { expect, test } from "@playwright/test";
import { EXAMPLE_PROMPT_EDIT, EXAMPLE_REPORT } from "../../src/lib/orchestratorActionCards";
import { installFrozenClock, settleVisual } from "./studio-mock";

const TOKEN = "orch-dev-token";
const MOCK = process.env.ORCH_MOCK_URL ?? "http://127.0.0.1:8799";

test.beforeEach(async ({ page }) => {
  await installFrozenClock(page);
});

async function show(page: import("@playwright/test").Page, session: string, payload: Record<string, unknown>) {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  const q = new URLSearchParams({ scenario: "idle", session });
  await page.goto(`/orchestrator?${q.toString()}`);
  await expect(page.getByTestId("orch-composer")).toBeVisible();
  const res = await page.request.post(`${MOCK}/api/orchestrator/test/emit`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: session, type: "step_finished", task_id: 1, payload },
  });
  expect(res.ok()).toBeTruthy();
  await expect(page.getByTestId("orch-action-card")).toBeVisible();
  await settleVisual(page);
}

test("снимок карточки промта", async ({ page }) => {
  await show(page, "visual-prompt", EXAMPLE_PROMPT_EDIT);
  await expect(page.getByTestId("orch-action-card")).toHaveScreenshot("action-prompt.png");
});

test("снимок карточки отчёта", async ({ page }) => {
  await show(page, "visual-report", EXAMPLE_REPORT);
  await expect(page.getByTestId("orch-action-card")).toHaveScreenshot("action-report.png");
});
