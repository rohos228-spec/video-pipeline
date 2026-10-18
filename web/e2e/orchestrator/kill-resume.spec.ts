import { expect, test, type Page } from "@playwright/test";
import { EXAMPLE_PAID_APPROVAL } from "../../src/lib/orchestratorActionCards";
import { ORCH_EVENT_TYPES } from "../../src/lib/orchestratorTypes";

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

test("Продолжить слушает resume_allowed и прячется при false", async ({ page }) => {
  const session = "resume-flag";
  await openWithToken(page, session);
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);

  await emit(page, session, "agent_status", { agent: "supervisor", status: "waiting_approval" });
  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-flag",
    plan: [],
    position: 0,
    change_ids: ["chg-flag"],
    context: "",
  });
  await expect(page.getByTestId("orch-resume")).toBeEnabled();

  await emit(page, session, "agent_status", {
    agent: "supervisor",
    status: "waiting_approval",
    resume_allowed: false,
  });
  await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);

  await emit(page, session, "agent_status", {
    agent: "supervisor",
    status: "waiting_approval",
    resume_allowed: true,
  });
  await expect(page.getByTestId("orch-resume")).toBeEnabled();

  await emit(page, session, "agent_status", {
    agent: "supervisor",
    status: "failed",
    resume_allowed: false,
  });
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
});

test("снимок resume_allowed важнее чекпоинта", async ({ page }) => {
  const session = "resume-snap";
  const seeded = await page.request.post(`${MOCK}/api/orchestrator/test/status`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: session, task_id: 1, status: "waiting_approval", resume_allowed: false },
  });
  expect(seeded.ok()).toBeTruthy();
  await openWithToken(page, session);
  await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);

  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-snap",
    plan: [],
    position: 0,
    change_ids: ["chg-snap"],
    context: "",
  });
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);

  await emit(page, session, "agent_status", {
    agent: "supervisor",
    status: "waiting_approval",
    resume_allowed: true,
  });
  await expect(page.getByTestId("orch-resume")).toBeEnabled();
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

test("partial_write входит в известные типы событий", async ({ page }) => {
  expect(ORCH_EVENT_TYPES).toContain("partial_write");
  const session = "partial-known";
  await openWithToken(page, session);
  const seeded = await page.request.post(`${MOCK}/api/orchestrator/test/partial-changes`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: session, task_id: 1, change_ids: ["chg-known"] },
  });
  expect(seeded.ok()).toBeTruthy();
  await emit(page, session, "partial_write", {
    level: "WARN",
    change_ids: [],
    position: 0,
    task_id: 1,
  });
  const banner = page.getByTestId("orch-partial");
  await expect(banner).toHaveAttribute("data-known-event", "partial_write");
  await expect(page.getByTestId("orch-partial-level")).toHaveText("WARN partial_write");
  await expect(page.getByTestId("orch-partial-change")).toHaveText("chg-known");
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
  await expect(approval.getByTestId("orch-approve")).toHaveCount(0);
  await expect(approval.getByTestId("orch-retry")).toHaveCount(1);
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
  await expect(page.getByTestId("orch-retry")).toHaveCount(1);
});

test("paid_outcome_unknown берёт step_change_id и кнопки approve retry_step deny", async ({ page }) => {
  const session = "paid-actions";
  await openWithToken(page, session);
  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-actions",
    plan: [{ action: "charge", paid: true, risk: "high", estimated_cost: 1 }],
    position: 0,
    change_ids: ["chg-checkpoint-only"],
    context: "",
  });
  await emit(page, session, "approval_required", {
    reason: "paid_outcome_unknown",
    change_id: "orch-appr-9-1",
    action: "charge",
    retry_armed: false,
    task_id: 1,
    actions: ["approve", "retry_step", "deny"],
    step_change_id: "chg-step-9",
  });

  const card = page.getByTestId("orch-paid-outcome");
  await expect(card.getByTestId("orch-paid-step-changes")).toHaveAttribute("data-source", "step_change_id");
  await expect(card.getByTestId("orch-paid-step-change")).toHaveText("chg-step-9");
  await expect(card).not.toContainText("chg-checkpoint-only");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  const retry = page.getByTestId("orch-retry");
  const deny = page.getByTestId("orch-reject");
  await expect(retry).toHaveText("Перезапустить шаг");
  await expect(deny).toHaveText("Reject");
  await expect(page.getByTestId("orch-resume")).toBeEnabled();

  let posted = "";
  await page.route(`**/api/orchestrator/chat/${session}/approve`, async (route) => {
    posted = route.request().postData() ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        executed: false,
        decision: "deny",
        retry_armed: false,
        change_id: "orch-appr-9-1",
      }),
    });
  });
  await deny.click();
  await expect(page.getByTestId("orch-approval")).toHaveCount(0);
  await expect(page.getByTestId("orch-paid-outcome")).toHaveCount(0);
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
  expect(JSON.parse(posted)).toEqual({ change_id: "orch-appr-9-1", decision: "deny" });
});

test("partial_write: approve идёт дальше, deny ставит failed и оставляет строку", async ({ page }) => {
  const session = "partial-decide";
  await openWithToken(page, session);
  const seeded = await page.request.post(`${MOCK}/api/orchestrator/test/partial-changes`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: session, task_id: 1, change_ids: ["chg-partial-row"] },
  });
  expect(seeded.ok()).toBeTruthy();
  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-partial",
    plan: [{ action: "write", paid: false, risk: "none", estimated_cost: 0 }],
    position: 0,
    change_ids: ["chg-partial-row"],
    context: "",
  });
  await emit(page, session, "partial_write", {
    level: "WARN",
    change_ids: ["chg-partial-row"],
    position: 0,
    task_id: 1,
  });
  await emit(page, session, "approval_required", {
    reason: "partial_write",
    change_id: "orch-appr-5-1",
    action: "write",
    actions: ["approve"],
    task_id: 1,
  });

  await expect(page.getByTestId("orch-partial-change")).toHaveText("chg-partial-row");
  await expect(page.getByTestId("orch-approve")).toHaveCount(1);
  await expect(page.getByTestId("orch-reject")).toHaveCount(1);
  await expect(page.getByTestId("orch-retry")).toHaveCount(0);
  await expect(page.getByTestId("orch-resume")).toBeEnabled();

  let posted = "";
  await page.route(`**/api/orchestrator/chat/${session}/approve`, async (route) => {
    posted = route.request().postData() ?? "";
    const body = JSON.parse(posted) as { decision?: string };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        executed: false,
        decision: body.decision,
        retry_armed: false,
        change_id: "orch-appr-5-1",
      }),
    });
  });
  await page.getByTestId("orch-reject").click();
  await expect(page.getByTestId("orch-approval")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
  await expect(page.getByTestId("orch-partial-change")).toHaveText("chg-partial-row");
  expect(JSON.parse(posted)).toEqual({ change_id: "orch-appr-5-1", decision: "deny" });

  const accept = "partial-accept";
  await openWithToken(page, accept);
  const seededAccept = await page.request.post(`${MOCK}/api/orchestrator/test/partial-changes`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: accept, task_id: 1, change_ids: ["chg-partial-row"] },
  });
  expect(seededAccept.ok()).toBeTruthy();
  await emit(page, accept, "partial_write", {
    level: "WARN",
    change_ids: ["chg-partial-row"],
    position: 0,
    task_id: 1,
  });
  await emit(page, accept, "approval_required", {
    reason: "partial_write",
    change_id: "orch-appr-5-2",
    action: "write",
    actions: ["approve"],
    task_id: 1,
  });
  let accepted = "";
  await page.route(`**/api/orchestrator/chat/${accept}/approve`, async (route) => {
    accepted = route.request().postData() ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        executed: false,
        decision: "approve",
        retry_armed: false,
        change_id: "orch-appr-5-2",
      }),
    });
  });
  await page.getByTestId("orch-approve").click();
  await expect(page.getByTestId("orch-approval")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
  await expect(page.getByTestId("orch-partial-change")).toHaveText("chg-partial-row");
  expect(JSON.parse(accepted)).toEqual({ change_id: "orch-appr-5-2", decision: "approve" });
});

test("deletion_forbidden объясняет отказ без soft_delete и гранта", async ({ page }) => {
  const session = "del-forbidden";
  await openWithToken(page, session);
  await emit(page, session, "task_failed", { error: "deletion_forbidden" });
  const card = page.getByTestId("orch-approval");
  await expect(page.getByTestId("orch-deletion-forbidden")).toHaveText("Удаление кадра запрещено");
  await expect(card).toContainText("soft_delete");
  await expect(card).toContainText("грант");
  await expect(card).toContainText("deletion_forbidden");
  await expect(card).toHaveAttribute("data-terminal", "true");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  await expect(page.getByTestId("orch-reject")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
});

test("not_in_whitelist: экран отказывает до карточки, grant 403 пишет понятный текст", async ({ page }) => {
  const session = "wl-deny";
  await openWithToken(page, session);

  await emit(page, session, "task_failed", { error: "not_in_whitelist" });
  const screen = page.getByTestId("orch-approval");
  await expect(page.getByTestId("orch-whitelist")).toHaveText("Действия нет в белом списке");
  await expect(screen).toContainText("Одобрить и выполнить его нельзя");
  await expect(screen).toHaveAttribute("data-terminal", "true");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  await expect(page.getByTestId("orch-reject")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");

  await emit(page, session, "approval_required", {
    reason: "risk",
    change_id: "orch-appr-1-1",
    action: "exec",
    task_id: 1,
  });
  await page.route("**/api/orchestrator/chat/*/approve", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "not_in_whitelist" }),
    });
  });
  const grant = page.getByTestId("orch-approval").filter({ hasText: "exec" });
  await grant.getByTestId("orch-approve").click();
  await expect(page.getByTestId("orch-whitelist").first()).toHaveText("Действия нет в белом списке");
  await expect(page.getByTestId("orch-approval").filter({ hasText: "exec" })).toHaveCount(0);
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
});

test("grant not_in_whitelist закрывает карточку и ставит failed", async ({ page }) => {
  const session = "grant-unlisted";
  const marked = await page.request.post(`${MOCK}/api/orchestrator/test/unlisted`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: session, unlisted: true },
  });
  expect(marked.ok()).toBeTruthy();
  await openWithToken(page, session);
  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-unlisted",
    plan: [{ action: "bash", paid: true, risk: "high", estimated_cost: 1 }],
    position: 0,
    change_ids: ["chg-bash"],
    context: "",
  });
  await emit(page, session, "approval_required", {
    reason: "paid_outcome_unknown",
    change_id: "orch-appr-8-1",
    action: "bash",
    retry_armed: false,
    task_id: 1,
    actions: ["approve", "retry_step", "deny"],
    step_change_id: "chg-bash",
  });
  await expect(page.getByTestId("orch-resume")).toBeEnabled();
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  await expect(page.getByTestId("orch-retry")).toHaveCount(1);

  await page.getByTestId("orch-retry").click();
  const screen = page.getByTestId("orch-approval");
  await expect(page.getByTestId("orch-whitelist")).toHaveText("Действия нет в белом списке");
  await expect(screen).toContainText("Одобрить и выполнить его нельзя");
  await expect(screen).toHaveAttribute("data-terminal", "true");
  await expect(page.getByTestId("orch-retry")).toHaveCount(0);
  await expect(page.getByTestId("orch-paid-outcome")).toHaveCount(0);
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
});

test("deny закрывает карточку, ставит failed и прячет Продолжить", async ({ page }) => {
  const session = "deny-fail";
  await openWithToken(page, session);
  await emit(page, session, "checkpoint", {
    checkpoint_id: "cp-deny",
    plan: [{ action: "charge", paid: true, risk: "high", estimated_cost: 1 }],
    position: 0,
    change_ids: ["chg-charge-1"],
    context: "",
  });
  await emit(page, session, "approval_required", {
    reason: "paid_outcome_unknown",
    change_id: "orch-appr-4-1",
    action: "charge",
    retry_armed: false,
    task_id: 1,
  });
  await expect(page.getByTestId("orch-resume")).toBeEnabled();
  await expect(page.getByTestId("orch-approval")).toHaveAttribute("data-change-id", "orch-appr-4-1");
  await expect(page.getByTestId("orch-paid-outcome")).toBeVisible();

  await emit(page, session, "approval_decided", {
    change_id: "orch-appr-4-1",
    decision: "deny",
    task_id: 1,
  });
  await expect(page.getByTestId("orch-approval")).toHaveCount(0);
  await expect(page.getByTestId("orch-paid-outcome")).toHaveCount(0);
  await expect(page.getByTestId("orch-resume")).toHaveCount(0);
  await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
});
