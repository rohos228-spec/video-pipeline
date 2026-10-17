import { expect, test, type Page } from "@playwright/test";
import { EXAMPLE_PAID_APPROVAL } from "../../src/lib/orchestratorActionCards";

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

test("partial_write показывает WARN и список GET partial-changes без отката", async ({ page }) => {
  const session = "partial-1";
  await openWithToken(page, session);
  const seeded = await page.request.post(`${MOCK}/api/orchestrator/test/partial-changes`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: session, task_id: 1, change_ids: ["chg-partial-a", "chg-partial-b"] },
  });
  expect(seeded.ok()).toBeTruthy();

  await emit(page, session, "partial_write", {
    level: "WARN",
    change_ids: ["only-in-event"],
    position: 2,
    task_id: 1,
  });

  const banner = page.getByTestId("orch-partial");
  await expect(banner).toBeVisible();
  await expect(page.getByTestId("orch-partial-icon")).toBeVisible();
  await expect(page.getByTestId("orch-partial-level")).toHaveText("WARN partial_write");
  await expect(page.getByTestId("orch-partial-honest")).toContainText("Откат не выполняется");
  await expect(banner.getByRole("button")).toHaveCount(0);
  const items = page.getByTestId("orch-partial-change");
  await expect(items).toHaveCount(2);
  await expect(items.nth(0)).toHaveText("chg-partial-a");
  await expect(items.nth(1)).toHaveText("chg-partial-b");
  await expect(banner).not.toContainText("only-in-event");
});

test("карточка paid_outcome_unknown показывает change_id шага, кнопки только из approval_required", async ({ page }) => {
  const session = "paid-kill";
  await openWithToken(page, session);

  await emit(page, session, "step_finished", EXAMPLE_PAID_APPROVAL);
  await expect(page.getByTestId("orch-action-wait")).toContainText("Ждёт подтверждения");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  await expect(page.getByTestId("orch-paid-outcome")).toHaveCount(0);

  await emit(page, session, "approval_required", {
    reason: "paid_outcome_unknown",
    change_id: "orch-appr-7-1",
    action: "charge",
    retry_armed: false,
    task_id: 1,
  });

  const card = page.getByTestId("orch-paid-outcome");
  await expect(card).toBeVisible();
  await expect(card).toContainText("waiting_approval");
  await expect(card).toContainText("paid_outcome_unknown");
  await expect(card).toContainText("Отката нет");
  await expect(card).toHaveAttribute("data-retry-armed", "false");
  await expect(page.getByTestId("orch-paid-step-missing")).toBeVisible();
  await expect(card.getByTestId("orch-approve")).toHaveCount(0);
  await expect(card.getByTestId("orch-reject")).toHaveCount(0);

  const approval = page.getByTestId("orch-approval");
  await expect(approval).toHaveAttribute("data-change-id", "orch-appr-7-1");
  await expect(approval.getByTestId("orch-approve")).toHaveCount(1);
  await expect(approval.getByTestId("orch-reject")).toHaveCount(1);
  await expect(page.getByTestId("orch-paid-reason")).toHaveText("Исход платного шага неизвестен");
  await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");

  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-paid",
    plan: [{ action: "charge", paid: true, risk: "none", estimated_cost: 1 }],
    position: 0,
    change_ids: ["chg-charge-9"],
    spend: { actions: 1 },
    context: "",
  });
  await expect(page.getByTestId("orch-paid-step-change")).toHaveText("chg-charge-9");
  await expect(page.getByTestId("orch-paid-step-missing")).toHaveCount(0);
  await expect(page.getByTestId("orch-approve")).toHaveCount(1);
});
