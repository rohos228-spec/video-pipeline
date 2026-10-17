import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import {
  ACTION_CONTRACT_REF,
  ACTION_CONTRACT_VERSION,
  ACTION_SCHEMA_SHA256,
  EXAMPLE_CORE_APPROVAL,
  EXAMPLE_DELETE_OK,
  EXAMPLE_FAILED,
  EXAMPLE_NODE_CREATE,
  EXAMPLE_NODE_EDIT,
  EXAMPLE_PARAMS_EDIT,
  EXAMPLE_PAID_APPROVAL,
  EXAMPLE_PROMPT_EDIT,
  EXAMPLE_R21_CONFLICT,
  EXAMPLE_R21_DELETE,
  EXAMPLE_VERSION_CONFLICT,
  EXAMPLE_REPORT,
  EXAMPLE_UNKNOWN_APPROVAL,
  metadataOf,
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

test("схема ws2-r2.1 закреплена sha256", () => {
  const file = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/lib/orchestratorActionResults.schema.json");
  const hash = createHash("sha256").update(readFileSync(file)).digest("hex");
  expect(hash).toBe(ACTION_SCHEMA_SHA256);
  expect(hash).toBe("e4eb29277f0d2128fcae5ec1006c7b95ba5522529e362129a90d39884c1fab9d");
  expect(ACTION_CONTRACT_VERSION).toBe("ws2-r2.1");
  expect(ACTION_CONTRACT_REF).toBe("ed35599772d3280760c405c4c8236db7833dd58e");
});

test("версия, снимок и marked обязательны", () => {
  const prompt = readActionEnvelope(EXAMPLE_PROMPT_EDIT, 1);
  expect(prompt?.version).toBe(2);
  expect(prompt?.baseVersion).toBe(1);
  expect(prompt?.metadata.destructive).toBe(true);
  expect(metadataOf("revert_node_to_git").destructive).toBe(true);
  expect(metadataOf("resume_node")).toMatchObject({ risk: "high", destructive: true, paid: true });

  const created = readActionEnvelope(EXAMPLE_NODE_CREATE, 2);
  expect(created?.snapshotId).toBe("snap-13-1");
  expect(created?.version).toBe(1);
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
  expect(readActionEnvelope(noSnapshot, 6)).toBeNull();

  dropped.archive_ref = "old/scenes/13";
  dropped.snapshot_id = undefined;
  expect(readActionEnvelope(noSnapshot, 7)?.archiveRef).toBe("old/scenes/13");

  const unmarked = structuredClone(EXAMPLE_DELETE_OK);
  (unmarked.result as { marked?: boolean }).marked = false;
  expect(readActionEnvelope(unmarked, 8)).toBeNull();
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
    action: { name: "prompt.restore_master", change_id: "orch-appr-13-6", snapshot_id: "snap-13-6" },
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
  const unknown = page.getByTestId("orch-action-wait").filter({ hasText: "widget.spin" });
  await expect(unknown).toContainText("платно");
  await expect(unknown).toContainText("деструктивно");
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
    action: { name: "edit_node_prompt", base_version: 4 },
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
    change_id: "orch-appr-13-6",
    snapshot_id: "snap-r21-6",
  });
});
