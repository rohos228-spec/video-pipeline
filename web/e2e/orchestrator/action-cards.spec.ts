import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import {
  ACTION_CONTRACT_REF,
  ACTION_CONTRACT_VERSION,
  ACTION_SCHEMA_SHA256,
  EXAMPLE_NODE_CREATE,
  EXAMPLE_NODE_EDIT,
  EXAMPLE_PARAMS_EDIT,
  EXAMPLE_PROMPT_EDIT,
  EXAMPLE_REPORT,
  metadataOf,
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

test("схема ws2-r2.0 закреплена sha256", () => {
  const file = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/lib/orchestratorActionResults.schema.json");
  const hash = createHash("sha256").update(readFileSync(file)).digest("hex");
  expect(hash).toBe(ACTION_SCHEMA_SHA256);
  expect(hash).toBe("850ad63329195bd6d58b7b313550e2cc3994521616c57476df655ddd7c1337aa");
  expect(ACTION_CONTRACT_VERSION).toBe("ws2-r2.0");
  expect(ACTION_CONTRACT_REF).toBe("ae950ce6c81892c9b8b3fc07b095780b5f26c075");
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
