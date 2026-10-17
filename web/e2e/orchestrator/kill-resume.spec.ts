import { expect, test, type Page } from "@playwright/test";

const TOKEN = "orch-dev-token";
const MOCK = process.env.ORCH_MOCK_URL ?? "http://127.0.0.1:8799";

function orchUrl(session: string): string {
  const q = new URLSearchParams({ scenario: "idle", session });
  return `/orchestrator?${q.toString()}`;
}

async function openWithToken(page: Page, session: string) {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  await page.goto(orchUrl(session));
  await expect(page.getByTestId("orch-composer")).toBeVisible();
}

async function emit(page: Page, session: string, type: string, payload: Record<string, unknown>) {
  const res = await page.request.post(`${MOCK}/api/orchestrator/test/emit`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: session, type, task_id: 1, payload },
  });
  expect(res.ok()).toBeTruthy();
}

test("Продолжить шлёт POST resume и после kill в planning молчит", async ({ page }) => {
  const session = "resume-plan";
  await openWithToken(page, session);
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);

  await emit(page, session, "agent_status", { agent: "supervisor", status: "cancelled" });
  await expect(page.getByTestId("orch-task-status")).toContainText("отменено");
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);

  await emit(page, session, "agent_status", { agent: "supervisor", status: "paused" });
  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-pause",
    plan: [],
    position: 0,
    change_ids: [],
    spend: { actions: 0, llm: 0 },
    context: "",
  });
  const button = page.getByTestId("orch-resume");
  await expect(button).toBeEnabled();

  let refused = 0;
  await page.route(`**/api/orchestrator/chat/${session}/resume`, async (route) => {
    refused += 1;
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "not found" }),
    });
  });
  await button.evaluate((el: HTMLButtonElement) => {
    el.click();
    el.click();
  });
  await expect(page.getByTestId("orch-resume-error")).toContainText("not found");
  await expect(button).toBeDisabled();
  expect(refused).toBe(1);

  await page.unroute(`**/api/orchestrator/chat/${session}/resume`);
  await emit(page, session, "agent_status", { agent: "supervisor", status: "critic_review" });
  await expect(button).toBeEnabled();

  let continued = 0;
  let body: string | null = "unset";
  await page.route(`**/api/orchestrator/chat/${session}/resume`, async (route) => {
    continued += 1;
    body = route.request().postData();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, session, task_id: 1, status: "executing" }),
    });
  });
  await button.evaluate((el: HTMLButtonElement) => {
    el.click();
    el.click();
  });
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("выполнение");
  expect(continued).toBe(1);
  expect(body ?? "").toBe("");
});
