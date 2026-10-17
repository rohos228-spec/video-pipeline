import { mkdir } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

const SHOTS = "/opt/cursor/artifacts/screenshots";

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

const TOKEN = "orch-dev-token";
const MOCK = process.env.ORCH_MOCK_URL ?? "http://127.0.0.1:8799";

function orchUrl(scenario: string, session = "studio"): string {
  const q = new URLSearchParams({
    orchApi: MOCK,
    scenario,
    session,
  });
  return `/orchestrator?${q.toString()}`;
}

async function openWithToken(page: Page, scenario: string, session = "studio") {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  await page.goto(orchUrl(scenario, session));
  await expect(page.getByTestId("orch-composer")).toBeVisible();
}

test.describe("orchestrator chat", () => {
  test("основной вид", async ({ page }) => {
    await openWithToken(page, "idle", "idle-shot");
    await saveShot(page, "orch-main");
  });

  test("стриминг ответа по токенам и чекпоинт", async ({ page }) => {
    await openWithToken(page, "stream", "stream-1");
    await page.getByTestId("orch-composer").fill("собери раскадровку");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-transcript")).toContainText("Соберу");
    await expect(page.getByTestId("orch-ack")).toContainText("принял, разбираю");
    await expect(page.getByTestId("orch-transcript")).toContainText(
      "Соберу раскадровку сцен 3–5 и проверю длину закадра.",
    );
    await expect(page.getByTestId("orch-classification")).toContainText("append");
    await expect(page.getByTestId("orch-task-panel")).toContainText("код проверен");
    await expect(page.getByTestId("orch-transcript")).toContainText("из БД, v4");
    await expect(page.getByTestId("orch-transcript")).toContainText("git-шаблон изменился после версии в БД");
    await expect(page.getByTestId("orch-task-panel")).toContainText("из БД, v4");
    await expect(page.getByTestId("orch-task-panel")).toContainText("git-шаблон изменился после версии в БД");
    await saveShot(page, "orch-streaming");
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("done");
    await expect(page.getByTestId("orch-task-status")).toContainText("готово");
  });

  test("обрыв SSE и replay по Last-Event-ID", async ({ page }) => {
    await openWithToken(page, "reconnect", "reconnect-1");
    await page.getByTestId("orch-composer").fill("проверь обрыв");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-reconnect")).toContainText("восстановлено");
    await expect(page.getByTestId("orch-transcript")).toContainText("REPLAY_OK");
    await expect(page.getByTestId("orch-transcript")).toContainText("до обрыва");
  });

  test("сообщение посреди задачи: поле живо, ack ≤ 2 с, затем interrupt", async ({ page }) => {
    await openWithToken(page, "interrupt", "interrupt-1");
    await page.getByTestId("orch-composer").fill("генерируй кадры");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-transcript")).toContainText("Генерирую");
    await expect(page.getByTestId("orch-composer")).toBeEnabled();
    await page.getByTestId("orch-composer").fill("стой");
    const started = Date.now();
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-ack")).toContainText("стой", { timeout: 2_000 });
    expect(Date.now() - started).toBeLessThanOrEqual(2_000);
    await expect(page.getByTestId("orch-composer")).toBeEnabled();
    await expect(page.getByTestId("orch-classification")).toContainText("interrupt");
    await expect(page.getByTestId("orch-task-status")).toContainText("пауза");
  });

  test("approval: approve доводит задачу", async ({ page }) => {
    await openWithToken(page, "approval", "approval-1");
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-transcript")).toContainText("из БД, v4");
    await expect(page.getByTestId("orch-transcript")).toContainText("git-шаблон изменился после версии в БД");
    await saveShot(page, "orch-approval");
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-task-summary")).toContainText("Одобрено");
    await expect(page.getByTestId("orch-task-status")).toContainText("готово");
  });

  test("бюджет исчерпан: waiting_approval и подпись на карточке", async ({ page }) => {
    await openWithToken(page, "budget", "budget-1");
    await page.getByTestId("orch-composer").fill("ещё кадры");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-budget")).toContainText("Бюджет исчерпан");
    await expect(page.getByTestId("orch-approval")).toContainText("Лимит прогона выбран");
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
    await saveShot(page, "orch-budget");
  });

  test("kill switch честно говорит про границу платного шага", async ({ page }) => {
    await openWithToken(page, "kill", "kill-1");
    await page.getByTestId("orch-composer").fill("платная генерация");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-task-panel")).toContainText("run_node");
    await page.getByTestId("orch-kill").click();
    await expect(page.getByRole("heading", { name: "Остановить оркестратор?" })).toBeVisible();
    await page.getByTestId("orch-kill-confirm").click();
    await expect(page.getByTestId("orch-kill-status")).toContainText(/платный шаг остановится на границе/i);
    await saveShot(page, "orch-kill");
    await expect(page.getByTestId("orch-task-status")).toContainText("отменено");
  });

  test("план: риск, стоимость и параллельные read-only", async ({ page }) => {
    await openWithToken(page, "plan", "plan-1");
    await page.getByTestId("orch-composer").fill("составь план");
    await page.getByTestId("orch-send").click();
    const parallel = page.getByTestId("orch-plan-parallel");
    await expect(parallel).toHaveCount(1);
    await expect(parallel).toContainText("параллельно · reads");
    await expect(parallel).toContainText("read_pipeline_code");
    await expect(parallel).toContainText("get_node");
    await expect(parallel).not.toContainText("run_node");
    const paid = page.getByTestId("orch-plan-serial").filter({ hasText: "run_node" });
    await expect(paid).toContainText("риск high");
    await expect(paid).toContainText("~$12.5");
    await expect(parallel.getByTestId("orch-plan-risk").first()).toContainText("риск none");
  });

  test("критик: эскалация после 2 правок", async ({ page }) => {
    await openWithToken(page, "critic", "critic-1");
    await page.getByTestId("orch-composer").fill("перепиши план");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-critic")).toHaveText("Критик не одобрил план после 2 правок");
    await expect(page.getByTestId("orch-task-status").first()).toContainText("подтверждения");
  });

  test("kill switch из сообщения classification.kill_switch", async ({ page }) => {
    await openWithToken(page, "killmsg", "killmsg-1");
    await page.getByTestId("orch-composer").fill("останови всё");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-classification")).toContainText("kill switch из сообщения");
    await expect(page.getByTestId("orch-classification")).toContainText("interrupt");
    await expect(page.getByTestId("orch-kill-status")).toContainText(/из сообщения/i);
    await expect(page.getByTestId("orch-kill-status")).toContainText(/платный шаг остановится на границе/i);
  });

  test("401: пустой токен, отказ, затем верный токен", async ({ page }) => {
    await page.goto(orchUrl("stream", "auth-1"));
    await expect(page.getByTestId("orch-token-gate")).toBeVisible();
    await page.getByTestId("orch-token-input").fill("wrong-token");
    await page.getByTestId("orch-token-submit").click();
    await expect(page.getByTestId("orch-token-error")).toContainText("401");
    await saveShot(page, "orch-401");
    await page.getByTestId("orch-token-input").fill(TOKEN);
    await page.getByTestId("orch-token-submit").click();
    await expect(page.getByTestId("orch-composer")).toBeVisible();
    await expect(page.getByTestId("orch-token-gate")).toHaveCount(0);
  });
});
