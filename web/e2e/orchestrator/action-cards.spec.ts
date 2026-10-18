import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { initialOrchScreen, reduceOrchScreen } from "../../src/lib/orchestratorSession";
import {
  ACTION_CONTRACT_REF,
  ACTION_CONTRACT_VERSION,
  ACTION_SCHEMA_PATH,
  ACTION_SCHEMA_SHA256,
  EXAMPLE_CORE_APPROVAL,
  EXAMPLE_DELETE_OK,
  EXAMPLE_FAILED,
  EXAMPLE_FREE_APPROVAL,
  EXAMPLE_FRAME_DELETE,
  EXAMPLE_GROUP_DELETE,
  EXAMPLE_NODE_CREATE,
  EXAMPLE_NODE_EDIT,
  EXAMPLE_PARAMS_EDIT,
  EXAMPLE_PARTIAL_EDIT,
  EXAMPLE_PAID_APPROVAL,
  EXAMPLE_PROMPT_EDIT,
  EXAMPLE_R21_CONFLICT,
  EXAMPLE_R21_DELETE,
  EXAMPLE_VERSION_CONFLICT,
  EXAMPLE_REPORT,
  EXAMPLE_UNKNOWN_APPROVAL,
  conflictRetryBody,
  metadataFrom,
  metadataOf,
  needsApproval,
  readActionEnvelope,
  reportEditBody,
  unifiedLines,
} from "../../src/lib/orchestratorActionCards";

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

test("схема ws2-r2.3 закреплена sha256", () => {
  const specDir = path.dirname(fileURLToPath(import.meta.url));
  const dir = path.resolve(specDir, "../../src/lib");
  const file = path.join(specDir, "orchestratorActionResults.schema.json");
  const raw = readFileSync(file);
  const hash = createHash("sha256").update(raw).digest("hex");
  expect(hash).toBe(ACTION_SCHEMA_SHA256);
  expect(hash).toBe("76d2ec8cf8a0d4f17f9d7e43217978909b0511e25c94e7409c1ca957b1a54123");
  expect(ACTION_CONTRACT_VERSION).toBe("ws2-r2.3");
  expect(ACTION_CONTRACT_REF).toBe("2216f10e27eef6117e31e81d915824140135a145");
  expect(ACTION_SCHEMA_PATH).toBe("app/orchestrator/action_contracts");
  expect(JSON.parse(raw.toString("utf8")).version).toBe("ws2-r2.3");
  const source = readFileSync(path.join(dir, "orchestratorActionCards.ts"), "utf8");
  expect(source).toContain("app/orchestrator/action_contracts");
  expect(source).toContain("#378");
  expect(source).toContain("2216f10e27eef6117e31e81d915824140135a145");
  expect(source).toContain("76d2ec8cf8a0d4f17f9d7e43217978909b0511e25c94e7409c1ca957b1a54123");
  expect(source).toContain("^[a-z0-9_]+(\\.[a-z0-9_]+)*$");
  expect(source).not.toContain("create_node@new_group");
  expect(source).not.toContain("a26a0f02630cb1c8fa71dba10540ba74ccabdb7b532cfc7e4a4219dc090ab99c");

  const elevated = metadataFrom({
    name: "create_node",
    risk: "high",
    read_only: false,
    destructive: true,
    paid: false,
  });
  expect(elevated).toMatchObject({ name: "create_node", risk: "high", destructive: true, paid: false });
  expect(elevated && needsApproval(elevated)).toBe(true);
  const sameGroup = metadataFrom({
    name: "create_node",
    risk: "medium",
    read_only: false,
    destructive: false,
    paid: false,
  });
  expect(sameGroup && needsApproval(sameGroup)).toBe(false);
  expect(
    metadataFrom({ name: "create_node@new_group", risk: "high", read_only: false, destructive: true, paid: false }),
  ).toBeNull();

  const atName = structuredClone(EXAMPLE_NODE_CREATE);
  (atName.result as { action: string }).action = "create_node@new_group";
  expect(readActionEnvelope(atName, 8)).toBeNull();

  const hyphen = structuredClone(EXAMPLE_NODE_CREATE);
  (hyphen.result as { action: string }).action = "create-node";
  expect(readActionEnvelope(hyphen, 10)).toBeNull();
  const badEdit = structuredClone(EXAMPLE_REPORT);
  (badEdit.result as { edit_action: string }).edit_action = "Update_Report";
  expect(readActionEnvelope(badEdit, 11)).toBeNull();
  const badRestore = structuredClone(EXAMPLE_GROUP_DELETE);
  (badRestore.result as { restore_action: string }).restore_action = "group.restore!";
  expect(readActionEnvelope(badRestore, 12)?.kind).toBe("invalid");
  expect(readActionEnvelope(badRestore, 12)?.error).toBe("пометка не подтверждена");
  expect(
    metadataFrom({ name: "CreateNode", risk: "high", read_only: false, destructive: true, paid: false }),
  ).toBeNull();

  const policy = structuredClone(EXAMPLE_PAID_APPROVAL);
  const policyResult = policy.result as { action: string; metadata: Record<string, unknown> };
  policyResult.action = "create_node";
  policyResult.metadata = {
    name: "create_node",
    risk: "high",
    read_only: false,
    destructive: true,
    paid: false,
  };
  const card = readActionEnvelope(policy, 9);
  expect(card?.kind).toBe("waiting");
  expect(card?.action).toBe("create_node");
  expect(card?.metadata).toMatchObject({ risk: "high", destructive: true, paid: false });
  expect(card && needsApproval(card.metadata)).toBe(true);
  expect(metadataOf("create_node").destructive).toBe(false);

  const partial = EXAMPLE_PARTIAL_EDIT.result as { skipped?: string[]; partial?: boolean };
  expect(partial.skipped?.length).toBeGreaterThan(0);
  expect(partial.partial).toBe(true);
  const conflict = EXAMPLE_R21_CONFLICT.result as { skipped?: string[]; partial?: boolean };
  expect(conflict.skipped).toEqual(["group"]);
  expect(conflict.partial).toBe(true);

  expect(readActionEnvelope(EXAMPLE_PROMPT_EDIT, 1)?.version).toBe(2);
  expect(readActionEnvelope(EXAMPLE_REPORT, 2)?.kind).toBe("report");
});

test("action create_node@new_group не рисует карточку", async ({ page }) => {
  const broken = structuredClone(EXAMPLE_NODE_CREATE);
  (broken.result as { action: string }).action = "create_node@new_group";
  expect(readActionEnvelope(broken, 1)).toBeNull();

  await openWithToken(page, "act-at-action");
  await emit(page, "act-at-action", "step_finished", broken);
  await emit(page, "act-at-action", "step_finished", EXAMPLE_PARAMS_EDIT);
  const params = page.getByTestId("orch-action-card").filter({ hasText: "Параметры" });
  await expect(params).toBeVisible();
  await expect(page.getByTestId("orch-action-card")).toHaveCount(1);
  await expect(page.getByTestId("orch-action-card").filter({ hasText: "create_node@new_group" })).toHaveCount(0);
  await expect(page.getByTestId("orch-action-card").filter({ hasText: "Новая нода" })).toHaveCount(0);
  await expect(page.getByTestId("orch-action-card").filter({ hasText: "Некорректный ответ действия" })).toHaveCount(0);
});

test("версия, снимок и marked обязательны", () => {
  const prompt = readActionEnvelope(EXAMPLE_PROMPT_EDIT, 1);
  expect(prompt?.version).toBe(2);
  expect(prompt?.baseVersion).toBe(1);
  expect(prompt?.metadata).toBeNull();
  expect(metadataOf("revert_node_to_git").destructive).toBe(true);
  expect(metadataOf("resume_node")).toMatchObject({ risk: "high", destructive: true, paid: true });

  const created = readActionEnvelope(EXAMPLE_NODE_CREATE, 2);
  expect(created?.snapshotId).toBe("snap-13-1");
  expect(created?.version).toBe(2);
  expect(created?.baseVersion).toBe(1);

  const marked = readActionEnvelope(EXAMPLE_DELETE_OK, 3);
  expect(marked?.marked).toBe(true);
  expect(marked?.restoreAction).toBe("prompt.restore_master");
  expect(marked?.snapshotId).toBe("snap-13-6");
  expect(marked?.changeId).toBe("orch-appr-13-6");

  const missingVersion = structuredClone(EXAMPLE_PROMPT_EDIT);
  const missingResult = missingVersion.result as { version?: number; base_version?: number; before?: unknown; snapshot_id?: string };
  delete missingResult.version;
  delete missingResult.base_version;
  expect(readActionEnvelope(missingVersion, 4)).toBeNull();

  const noBefore = structuredClone(EXAMPLE_PROMPT_EDIT);
  const bare = noBefore.result as { before?: unknown; snapshot_id?: string };
  bare.before = null;
  delete bare.snapshot_id;
  expect(readActionEnvelope(noBefore, 5)).toBeNull();

  const noSnapshot = structuredClone(EXAMPLE_DELETE_OK);
  const dropped = noSnapshot.result as { snapshot_id?: string; archive_ref?: string; marked?: boolean };
  delete dropped.snapshot_id;
  delete dropped.archive_ref;
  const hiddenDelete = readActionEnvelope(noSnapshot, 6);
  expect(hiddenDelete?.kind).toBe("invalid");
  expect(hiddenDelete?.error).toBe("пометка не подтверждена");

  dropped.archive_ref = "old/scenes/13";
  dropped.snapshot_id = undefined;
  expect(readActionEnvelope(noSnapshot, 7)?.archiveRef).toBe("old/scenes/13");

  const unmarked = structuredClone(EXAMPLE_DELETE_OK);
  (unmarked.result as { marked?: boolean }).marked = false;
  const unmarkedView = readActionEnvelope(unmarked, 8);
  expect(unmarkedView?.kind).toBe("invalid");
  expect(unmarkedView?.error).toBe("пометка не подтверждена");
});

test("удаление без пометки или снимка не прячется", () => {
  const frame = structuredClone(EXAMPLE_FRAME_DELETE);
  const frameResult = frame.result as { marked?: boolean; snapshot_id?: string; archive_ref?: string };
  frameResult.marked = false;
  const unmarkedFrame = readActionEnvelope(frame, 1);
  expect(unmarkedFrame?.kind).toBe("invalid");
  expect(unmarkedFrame?.error).toBe("пометка не подтверждена");

  const group = structuredClone(EXAMPLE_GROUP_DELETE);
  const groupResult = group.result as { snapshot_id?: string; archive_ref?: string };
  delete groupResult.snapshot_id;
  delete groupResult.archive_ref;
  const bareGroup = readActionEnvelope(group, 2);
  expect(bareGroup?.kind).toBe("invalid");
  expect(bareGroup?.error).toBe("пометка не подтверждена");
});

test("невалидный ответ действия остаётся карточкой", async ({ page }) => {
  const stale = structuredClone(EXAMPLE_PROMPT_EDIT);
  const staleResult = stale.result as { version?: number; base_version?: number };
  staleResult.version = 2;
  staleResult.base_version = 2;
  expect(readActionEnvelope(stale, 1)?.kind).toBe("invalid");

  const blank = structuredClone(EXAMPLE_DELETE_OK);
  (blank.result as { snapshot_id?: string }).snapshot_id = " \n\t";
  expect(readActionEnvelope(blank, 2)?.kind).toBe("invalid");
  expect(readActionEnvelope(blank, 2)?.error).toBe("Некорректный ответ действия");

  await openWithToken(page, "act-invalid");
  await emit(page, "act-invalid", "step_finished", stale);
  const card = page.getByTestId("orch-action-card").filter({ hasText: "Некорректный ответ действия" });
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute("data-kind", "invalid");
  await expect(card.getByTestId("orch-diff")).toHaveCount(0);
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);

  await emit(page, "act-invalid", "step_finished", blank);
  await expect(page.getByTestId("orch-action-card").filter({ hasText: "Некорректный ответ действия" })).toHaveCount(2);
  await expect(page.getByTestId("orch-soft-delete")).toHaveCount(0);
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
});

test("skipped без partial считается некорректным ответом", async ({ page }) => {
  const broken = structuredClone(EXAMPLE_PARAMS_EDIT);
  const result = broken.result as { skipped?: string[]; partial?: boolean };
  result.skipped = ["shot-2"];
  result.partial = false;
  const view = readActionEnvelope(broken, 1);
  expect(view?.kind).toBe("invalid");
  expect(view?.error).toBe("Некорректный ответ действия");

  const missing = structuredClone(EXAMPLE_PARTIAL_EDIT);
  const missingResult = missing.result as { skipped?: string[]; partial?: boolean };
  expect(missingResult.skipped?.length).toBeGreaterThan(0);
  delete missingResult.partial;
  expect(readActionEnvelope(missing, 2)?.kind).toBe("invalid");
  expect(readActionEnvelope(missing, 2)?.error).toBe("Некорректный ответ действия");

  const ok = readActionEnvelope(EXAMPLE_PARTIAL_EDIT, 3);
  expect(ok?.kind).toBe("params");
  expect(ok?.partial).toBe(true);
  expect(ok?.skipped).toEqual(["shot-2"]);

  await openWithToken(page, "act-skip-partial");
  await emit(page, "act-skip-partial", "step_finished", broken);
  const card = page.getByTestId("orch-action-card").filter({ hasText: "Некорректный ответ действия" });
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute("data-kind", "invalid");
  await expect(card.getByTestId("orch-action-partial")).toHaveCount(0);
  await expect(page.getByTestId("orch-action-card").filter({ hasText: "Частично" })).toHaveCount(0);
});

test("построчный дифф промта не теряет строки", () => {
  const lines = unifiedLines("--- a/prompt\n+++ b/prompt\n@@\n-было\n+стало\n");
  expect(lines.map((line) => `${line.kind}:${line.text}`)).toEqual([
    "same:--- a/prompt",
    "same:+++ b/prompt",
    "same:@@",
    "remove:было",
    "add:стало",
  ]);
});

test("неизвестное имя считается платным и деструктивным", () => {
  const meta = metadataOf("widget.spin", {
    name: "widget.spin",
    risk: "none",
    read_only: true,
    destructive: false,
    paid: false,
  });
  expect(meta.paid).toBe(true);
  expect(meta.destructive).toBe(true);
  expect(meta.read_only).toBe(false);
  expect(reportEditBody(7, "  сверь кадры  ")).toEqual({
    text: "сверь кадры",
    action: { name: "update_report", report_id: 7 },
  });
});

test("карточка правки: дифф текстом, создание без было, открыть ноду", async ({ page }) => {
  await page.addInitScript(() => {
    const bag = window as unknown as { __opened: string[] };
    bag.__opened = [];
    window.addEventListener("studio-open-node-prompts", (event) => {
      const detail = (event as CustomEvent<{ nodeKey?: string }>).detail;
      bag.__opened.push(detail?.nodeKey ?? "");
    });
  });
  await openWithToken(page, "act-diff");
  await emit(page, "act-diff", "step_finished", EXAMPLE_PROMPT_EDIT);
  const prompt = page.getByTestId("orch-action-card").filter({ hasText: "Правка промта" });
  await expect(prompt).toBeVisible();
  await expect(prompt.getByTestId("orch-diff-line").filter({ hasText: "было" })).toHaveAttribute("data-kind", "remove");
  await expect(prompt.getByTestId("orch-diff-line").filter({ hasText: "стало" })).toHaveAttribute("data-kind", "add");
  await prompt.getByTestId("orch-open-node").click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __opened: string[] }).__opened)).toEqual(["n_scene_writer"]);

  await emit(page, "act-diff", "step_finished", EXAMPLE_NODE_CREATE);
  const created = page.getByTestId("orch-action-card").filter({ hasText: "Новая нода" });
  await expect(created).toBeVisible();
  await expect(created.getByTestId("orch-field-before")).toHaveCount(0);
  await expect(created).toContainText("стало Сцены");
  await expect(created).not.toContainText("было");

  await emit(page, "act-diff", "step_finished", EXAMPLE_NODE_EDIT);
  const node = page.getByTestId("orch-action-card").filter({ hasText: "Правка ноды" });
  await expect(node.getByTestId("orch-field-row").filter({ hasText: "source" })).toContainText("было db");
  await expect(node.getByTestId("orch-field-row").filter({ hasText: "source" })).toContainText("стало git");

  await emit(page, "act-diff", "step_finished", EXAMPLE_PARAMS_EDIT);
  const params = page.getByTestId("orch-action-card").filter({ hasText: "Параметры" });
  const temperature = params.getByTestId("orch-field-row").filter({ hasText: "temperature" });
  await expect(temperature).toContainText("было 0.2");
  await expect(temperature).toContainText("стало 0.4");
});

test("удалённый ключ в диффе не выглядит как null", async ({ page }) => {
  await openWithToken(page, "act-removed");
  await emit(page, "act-removed", "step_finished", EXAMPLE_NODE_EDIT);
  const node = page.getByTestId("orch-action-card").filter({ hasText: "Правка ноды" });
  const source = node.getByTestId("orch-field-row").filter({ hasText: "source" });
  await expect(source).toHaveAttribute("data-op", "set");
  await expect(source).toContainText("стало git");
  const removed = node.getByTestId("orch-field-row").filter({ hasText: "db_version" });
  await expect(removed).toHaveAttribute("data-op", "removed");
  await expect(removed.getByTestId("orch-field-removed")).toContainText("удалён");
  await expect(removed.getByTestId("orch-field-after")).toHaveCount(0);
  await expect(removed).toContainText("было 4");
});

test("html-отчёт: sandbox без скриптов, правка шлёт один запрос", async ({ page }) => {
  await openWithToken(page, "act-report");
  await emit(page, "act-report", "step_finished", EXAMPLE_REPORT);
  const card = page.getByTestId("orch-action-card");
  const frame = card.getByTestId("orch-action-report");
  await expect(frame).toBeVisible();
  const access = await frame.evaluate((el) => {
    const iframe = el as HTMLIFrameElement;
    return {
      sandbox: iframe.getAttribute("sandbox"),
      readable: iframe.contentDocument !== null,
    };
  });
  expect(access.sandbox ?? "").not.toContain("allow-scripts");
  expect(access.sandbox ?? "").not.toContain("allow-same-origin");
  expect(access.readable).toBe(false);
  const posts: { text?: string; action?: { name?: string; report_id?: number } }[] = [];
  page.on("request", (req) => {
    if (req.method() !== "POST" || !req.url().includes("/message")) return;
    posts.push(req.postDataJSON());
  });
  await page.getByTestId("orch-report-edit").click();
  expect(posts).toHaveLength(0);
  await page.getByTestId("orch-report-instruction").fill("сверь кадры");
  await page.getByTestId("orch-report-send").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({
    text: "сверь кадры",
    action: { name: "update_report", report_id: 7 },
  });
  await expect(page.getByTestId("orch-report-send")).toBeDisabled();
});

test("сбой и мягкое удаление рисуются карточками", async ({ page }) => {
  await openWithToken(page, "act-fail");
  await emit(page, "act-fail", "step_finished", EXAMPLE_FAILED);
  const failed = page.getByTestId("orch-action-card").filter({ hasText: "Ошибка" });
  await expect(failed.getByTestId("orch-action-error")).toContainText("нода n_scene_writer уже есть");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);

  await emit(page, "act-fail", "step_finished", EXAMPLE_DELETE_OK);
  const mark = page.getByTestId("orch-action-card").filter({ hasText: "Пометка" });
  await expect(mark.getByTestId("orch-soft-delete")).toContainText("Помечено, файл на месте");
  const posts: { text?: string; action?: { name?: string; change_id?: string } }[] = [];
  page.on("request", (req) => {
    if (req.method() !== "POST" || !req.url().includes("/message")) return;
    posts.push(req.postDataJSON());
  });
  await mark.getByTestId("orch-restore").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({
    text: "Восстановить",
    action: { name: "prompt.restore_master", snapshot_id: "snap-13-6" },
  });
});

test("ApprovalRequired без события ядра не даёт кнопки", async ({ page }) => {
  await openWithToken(page, "act-wait");
  await emit(page, "act-wait", "step_finished", EXAMPLE_PAID_APPROVAL);
  const waiting = page.getByTestId("orch-action-wait");
  await expect(waiting).toContainText("Ждёт подтверждения");
  await expect(waiting).toContainText("платно");
  await expect(waiting).not.toContainText("orch-appr-13-7");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);

  await emit(page, "act-wait", "step_finished", EXAMPLE_UNKNOWN_APPROVAL);
  const unknown = page.getByTestId("orch-action-card").filter({ hasText: "Некорректный ответ действия" });
  await expect(unknown).toBeVisible();
  await expect(unknown).toHaveAttribute("data-kind", "invalid");
  await expect(page.getByTestId("orch-action-wait").filter({ hasText: "widget.spin" })).toHaveCount(0);
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);

  await emit(page, "act-wait", "approval_required", EXAMPLE_CORE_APPROVAL);
  const core = page.getByTestId("orch-approval");
  await expect(core).toBeVisible();
  await expect(core).toHaveAttribute("data-change-id", "orch-appr-99-3");
  await expect(core.getByTestId("orch-approve")).toBeVisible();
});

test("конфликт версии 409 предлагает обновить и повторить", async ({ page }) => {
  await openWithToken(page, "act-409");
  await emit(page, "act-409", "step_finished", EXAMPLE_R21_CONFLICT);
  const edit = page.getByTestId("orch-action-card").filter({ hasText: "Правка промта" });
  await expect(edit).toHaveAttribute("data-conflict", "true");
  await expect(edit.getByTestId("orch-version-conflict")).toContainText("Конфликт версий");
  await expect(edit.getByTestId("orch-version-conflict")).toContainText("409");
  await expect(edit.getByTestId("orch-version-conflict")).toContainText("Данные не изменены");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  const posts: { text?: string; action?: { name?: string; base_version?: number } }[] = [];
  page.on("request", (req) => {
    if (req.method() !== "POST" || !req.url().includes("/message")) return;
    posts.push(req.postDataJSON());
  });
  await edit.getByTestId("orch-conflict-retry").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({
    text: "Обновить и повторить",
    action: { name: "edit_node_prompt" },
  });

  await emit(page, "act-409", "step_finished", EXAMPLE_VERSION_CONFLICT);
  const bare = page.getByTestId("orch-action-card").filter({ hasText: "Данные не изменены" }).last();
  await expect(bare.getByTestId("orch-version-conflict")).toContainText("409");
  await bare.getByTestId("orch-conflict-retry").click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]).toEqual({
    text: "Обновить и повторить",
    action: { name: "conflict" },
  });
});

test("восстановление мягкого удаления идёт по kind и снимку", async ({ page }) => {
  await openWithToken(page, "act-restore");
  await emit(page, "act-restore", "step_finished", EXAMPLE_GROUP_DELETE);
  const group = page.getByTestId("orch-action-card").filter({ hasText: "group.delete" });
  await expect(group.getByTestId("orch-archive")).toContainText("old/scenes/13");
  await emit(page, "act-restore", "step_finished", EXAMPLE_FRAME_DELETE);
  const frame = page.getByTestId("orch-action-card").filter({ hasText: "montage.delete_frame" });
  await expect(frame.getByTestId("orch-snapshot")).toContainText("snap-frame-9");
  const posts: { text?: string; action?: { name?: string; snapshot_id?: string; archive_ref?: string; change_id?: string } }[] = [];
  page.on("request", (req) => {
    if (req.method() !== "POST" || !req.url().includes("/message")) return;
    posts.push(req.postDataJSON());
  });
  await group.getByTestId("orch-restore").click();
  await expect.poll(() => posts.length).toBe(1);
  await frame.getByTestId("orch-restore").click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[0]).toEqual({
    text: "Восстановить",
    action: { name: "group.restore", archive_ref: "old/scenes/13" },
  });
  expect(posts[1]).toEqual({
    text: "Восстановить",
    action: { name: "frame.restore", snapshot_id: "snap-frame-9" },
  });
  expect(JSON.stringify(posts)).not.toContain("history.rollback");
  expect(JSON.stringify(posts)).not.toContain("change_id");
});

test("предупреждения и пропущенное на частичной правке", async ({ page }) => {
  const broken = structuredClone(EXAMPLE_PARTIAL_EDIT);
  (broken.result as { skipped?: string[] }).skipped = [];
  expect(readActionEnvelope(broken, 1)?.kind).toBe("invalid");

  await openWithToken(page, "act-partial");
  await emit(page, "act-partial", "step_finished", EXAMPLE_PARTIAL_EDIT);
  const card = page.getByTestId("orch-action-card").filter({ hasText: "Параметры" }).last();
  await expect(card.getByTestId("orch-action-warnings")).toContainText("shot 2 пропущен");
  await expect(card.getByTestId("orch-action-partial")).toContainText("Частично");
  await expect(card.getByTestId("orch-action-partial")).toContainText("пропущено");
  await expect(card.getByTestId("orch-action-skipped")).toContainText("shot-2");
});

test("пустая оценка approval не показывается нулём", async ({ page }) => {
  await openWithToken(page, "act-cost");
  await emit(page, "act-cost", "step_finished", EXAMPLE_PAID_APPROVAL);
  const paid = page.getByTestId("orch-action-wait").filter({ hasText: "montage.suggest_scene_variants" });
  await expect(paid.getByTestId("orch-approval-estimate")).toHaveText("стоимость неизвестна");
  await expect(paid).not.toContainText("стоимость 0");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);

  await emit(page, "act-cost", "step_finished", EXAMPLE_FREE_APPROVAL);
  const free = page.getByTestId("orch-action-wait").filter({ hasText: "prompt.delete_master" });
  await expect(free.getByTestId("orch-approval-estimate")).toHaveText("стоимость 0");
  await expect(free).not.toContainText("стоимость неизвестна");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
});

test("поля r2.1: предупреждения, пропуск, конфликт и снимок", async ({ page }) => {
  await openWithToken(page, "act-r21");
  await emit(page, "act-r21", "step_finished", EXAMPLE_R21_CONFLICT);
  const conflict = page.getByTestId("orch-action-card").filter({ hasText: "Правка промта" });
  await expect(conflict).toHaveAttribute("data-conflict", "true");
  await expect(conflict.getByTestId("orch-version-conflict")).toContainText("Конфликт версий");
  await expect(conflict.getByTestId("orch-version-conflict")).toContainText("409");
  await expect(conflict.getByTestId("orch-action-warnings")).toContainText("черновик устарел");
  await expect(conflict.getByTestId("orch-action-partial")).toContainText("Частично");
  await expect(conflict.getByTestId("orch-action-skipped")).toContainText("group");

  await emit(page, "act-r21", "step_finished", EXAMPLE_R21_DELETE);
  const mark = page.getByTestId("orch-action-card").filter({ hasText: "Пометка" });
  await expect(mark.getByTestId("orch-snapshot")).toContainText("snap-r21-6");
  const posts: { action?: { name?: string; change_id?: string; snapshot_id?: string } }[] = [];
  page.on("request", (req) => {
    if (req.method() !== "POST" || !req.url().includes("/message")) return;
    posts.push(req.postDataJSON());
  });
  await mark.getByTestId("orch-restore").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]?.action).toEqual({
    name: "prompt.restore_master",
    snapshot_id: "snap-r21-6",
  });
});

test("повтор конфликта не шлёт прежний base_version", () => {
  expect(conflictRetryBody("edit_node_prompt", 4)).toEqual({
    text: "Обновить и повторить",
    action: { name: "edit_node_prompt" },
  });
  expect(conflictRetryBody("edit_node_prompt", 4).action).not.toHaveProperty("base_version");
});

test("лента карточек держит предел", () => {
  let state = initialOrchScreen();
  for (let seq = 1; seq <= 41; seq += 1) {
    const payload = structuredClone(EXAMPLE_FAILED);
    payload.error = `сбой ${seq}`;
    state = reduceOrchScreen(state, {
      type: "event",
      event: { type: "message", seq, task_id: 1, payload },
    });
  }
  expect(state.actionResults).toHaveLength(40);
  expect(state.actionResults[0]?.error).toBe("сбой 2");
  expect(state.actionResults[39]?.error).toBe("сбой 41");
});

test("повторы предупреждений не делят один key", () => {
  const source = readFileSync(
    path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/components/orchestrator/action-result-card.tsx"),
    "utf8",
  );
  expect(source).not.toMatch(/key=\{item\}/);
  const payload = structuredClone(EXAMPLE_PARTIAL_EDIT);
  payload.warnings = ["тот же", "тот же"];
  const view = readActionEnvelope(payload, 1);
  expect(view?.warnings).toEqual(["тот же", "тот же"]);
});

test("ссылка снимка только внутри /api/orchestrator", () => {
  const payload = structuredClone(EXAMPLE_DELETE_OK);
  const result = payload.result as { snapshot_url?: string };
  result.snapshot_url = "javascript:alert(1)";
  expect(readActionEnvelope(payload, 1)?.snapshotUrl).toBeNull();
  result.snapshot_url = "https://evil.example/api/orchestrator/x";
  expect(readActionEnvelope(payload, 2)?.snapshotUrl).toBeNull();
  result.snapshot_url = "//evil.example/api/orchestrator/x";
  expect(readActionEnvelope(payload, 3)?.snapshotUrl).toBeNull();
  result.snapshot_url = "/api/orchestrator/snapshots/snap-13-6";
  expect(readActionEnvelope(payload, 4)?.snapshotUrl).toBe("/api/orchestrator/snapshots/snap-13-6");
});

test("отчёт без edit_action принимается как update_report", () => {
  const payload = structuredClone(EXAMPLE_REPORT);
  const result = payload.result as { edit_action?: string; report_id?: number };
  delete result.edit_action;
  const view = readActionEnvelope(payload, 1);
  expect(view?.kind).toBe("report");
  expect(view?.reportId).toBe(7);
  expect(view?.editAction).toBe("update_report");

  result.report_id = 1.5;
  expect(readActionEnvelope(payload, 2)?.kind).not.toBe("report");
  result.report_id = 0;
  expect(readActionEnvelope(payload, 3)?.kind).not.toBe("report");
  result.report_id = 7;
  expect(readActionEnvelope(payload, 4)?.reportId).toBe(7);
});

test("новая версия отчёта заменяет карточку", () => {
  const older = structuredClone(EXAMPLE_REPORT);
  const newer = structuredClone(EXAMPLE_REPORT);
  const olderResult = older.result as { version?: number; html?: string };
  const newerResult = newer.result as { version?: number; html?: string };
  olderResult.version = 1;
  olderResult.html = "<p>старый</p>";
  newerResult.version = 2;
  newerResult.html = "<p>новый</p>";
  const event = (seq: number, payload: Record<string, unknown>) =>
    ({
      type: "event" as const,
      event: { type: "step_finished", task_id: 1, seq, payload },
    });
  let state = reduceOrchScreen(initialOrchScreen(), event(1, older));
  state = reduceOrchScreen(state, event(2, newer));
  expect(state.actionResults).toHaveLength(1);
  expect(state.actionResults[0]?.id).toBe("report-7-v2");
  expect(state.actionResults[0]?.html).toContain("новый");
  expect(state.actionResults[0]?.version).toBe(2);
  const stale = structuredClone(EXAMPLE_REPORT);
  const staleResult = stale.result as { version?: number; html?: string };
  staleResult.version = 1;
  staleResult.html = "<p>старее</p>";
  state = reduceOrchScreen(state, event(3, stale));
  expect(state.actionResults).toHaveLength(1);
  expect(state.actionResults[0]?.html).toContain("новый");
});

test("ok-карточки edit, delete и report не содержат риск", async ({ page }) => {
  await openWithToken(page, "act-norisk");
  await emit(page, "act-norisk", "step_finished", EXAMPLE_PROMPT_EDIT);
  await emit(page, "act-norisk", "step_finished", EXAMPLE_DELETE_OK);
  await emit(page, "act-norisk", "step_finished", EXAMPLE_REPORT);
  const cards = page.getByTestId("orch-action-card");
  await expect(cards).toHaveCount(3);
  await expect(cards.filter({ hasText: "Правка промта" })).toHaveCount(1);
  await expect(cards.filter({ hasText: "Пометка" })).toHaveCount(1);
  await expect(cards.filter({ hasText: "Отчёт" })).toHaveCount(1);
  await expect(cards.filter({ hasText: "риск" })).toHaveCount(0);
  await expect(cards.filter({ hasText: "платно" })).toHaveCount(0);
  await expect(cards.filter({ hasText: "деструктивно" })).toHaveCount(0);
  await expect(cards.filter({ hasText: "Некорректный ответ действия" })).toHaveCount(0);
});

test("approval без needsApproval даёт invalid", () => {
  const view = readActionEnvelope(EXAMPLE_UNKNOWN_APPROVAL, 1);
  expect(view?.kind).toBe("invalid");
});

test("action с хвостовым пробелом отвергается", () => {
  const payload = structuredClone(EXAMPLE_NODE_CREATE);
  (payload.result as { action: string }).action = "create_node ";
  expect(readActionEnvelope(payload, 1)).toBeNull();
});

test("result.action сверяется с metadata.name", () => {
  const payload = structuredClone(EXAMPLE_PAID_APPROVAL);
  const result = payload.result as { action: string };
  result.action = "create_node";
  const view = readActionEnvelope(payload, 1);
  expect(view?.kind).toBe("invalid");
  expect(view?.error).toBe("Некорректный ответ действия");
});

test("partial true без skipped невалиден", () => {
  const broken = structuredClone(EXAMPLE_PARTIAL_EDIT);
  (broken.result as { skipped?: string[] }).skipped = [];
  const view = readActionEnvelope(broken, 1);
  expect(view?.kind).toBe("invalid");
  expect(view?.error).toBe("Некорректный ответ действия");
});

test("плохой restore мягкого удаления даёт invalid", () => {
  const bang = structuredClone(EXAMPLE_GROUP_DELETE);
  (bang.result as { restore_action: string }).restore_action = "group.restore!";
  const bangView = readActionEnvelope(bang, 1);
  expect(bangView?.kind).toBe("invalid");
  expect(bangView?.error).toBe("пометка не подтверждена");

  const pair = structuredClone(EXAMPLE_FRAME_DELETE);
  (pair.result as { restore_action: string }).restore_action = "group.restore";
  const pairView = readActionEnvelope(pair, 2);
  expect(pairView?.kind).toBe("invalid");
  expect(pairView?.error).toBe("пометка не подтверждена");
});
