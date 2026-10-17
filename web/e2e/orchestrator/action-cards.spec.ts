import { expect, test, type Page } from "@playwright/test";
import {
  EXAMPLE_NODE_CREATE,
  EXAMPLE_NODE_EDIT,
  EXAMPLE_PARAMS_EDIT,
  EXAMPLE_PROMPT_EDIT,
  EXAMPLE_REPORT,
  lineDiff,
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

test("построчный дифф промта не теряет строки", () => {
  const lines = lineDiff("кадр у окна\nгерой молчит", "кадр у окна\nгерой говорит");
  expect(lines.map((line) => `${line.kind}:${line.text}`)).toEqual(["same:кадр у окна", "remove:герой молчит", "add:герой говорит"]);
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
  await expect(prompt.getByTestId("orch-diff-line").filter({ hasText: "герой молчит" })).toHaveAttribute("data-kind", "remove");
  await expect(prompt.getByTestId("orch-diff-line").filter({ hasText: "герой говорит" })).toHaveAttribute("data-kind", "add");
  await prompt.getByTestId("orch-open-node").click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __opened: string[] }).__opened)).toEqual(["n_excel_gpt"]);

  await emit(page, "act-diff", "step_finished", EXAMPLE_NODE_CREATE);
  const created = page.getByTestId("orch-action-card").filter({ hasText: "Новая нода" });
  await expect(created).toBeVisible();
  await expect(created.getByTestId("orch-field-before")).toHaveCount(0);
  await expect(created).toContainText("стало Проверка");
  await expect(created).not.toContainText("было");

  await emit(page, "act-diff", "step_finished", EXAMPLE_NODE_EDIT);
  const node = page.getByTestId("orch-action-card").filter({ hasText: "Правка ноды" });
  await expect(node.getByTestId("orch-field-row").filter({ hasText: "title" })).toContainText("было Старая");
  await expect(node.getByTestId("orch-field-row").filter({ hasText: "title" })).toContainText("стало Новая");

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
  const posts: { text?: string; action?: { name?: string; report_id?: string; change_id?: string } }[] = [];
  page.on("request", (req) => {
    if (req.method() !== "POST" || !req.url().includes("/message")) return;
    posts.push(req.postDataJSON());
  });
  await page.getByTestId("orch-report-edit").click();
  expect(posts).toHaveLength(0);
  await page.getByTestId("orch-report-instruction").fill("убери скрипт");
  await page.getByTestId("orch-report-send").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({
    text: "убери скрипт",
    action: { name: "report.edit", report_id: "rpt-1", change_id: "chg-report-1" },
  });
  await expect(page.getByTestId("orch-report-send")).toBeDisabled();
});
