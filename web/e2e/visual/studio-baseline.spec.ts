import { expect, test, type Page } from "@playwright/test";
import { installFrozenClock, installStudioMocks, seedStudio, settleVisual } from "./studio-mock";

const TOKEN = "orch-dev-token";

async function shot(page: Page, name: string) {
  await settleVisual(page);
  await expect(page).toHaveScreenshot(`${name}.png`, { maxDiffPixelRatio: 0.01 });
}

test.beforeEach(async ({ page }) => {
  await installFrozenClock(page);
  await installStudioMocks(page);
});

test("01 канвас без сайдбара", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  await page.getByRole("button", { name: "Скрыть панель" }).click();
  await expect(page.getByRole("button", { name: "Показать проекты" })).toBeVisible();
  await shot(page, "01-canvas-collapsed");
});

test("02 канвас проекта", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await expect(page.getByText("Ницше", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  await shot(page, "02-canvas");
});

test("03 инспектор ноды", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  const node = page.locator(".react-flow__node").filter({ hasText: "Тема" });
  await expect(node).toBeVisible();
  await node.click();
  await expect(page.getByText("Тема ролика")).toBeVisible();
  await shot(page, "03-inspector");
});

test("04 мастер нового проекта", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await page.getByRole("button", { name: "Новый проект" }).click();
  await expect(page.getByRole("heading", { name: "Новый проект" })).toBeVisible();
  await shot(page, "04-new-project");
});

test("05 чат", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await page.getByRole("button", { name: "Чат" }).click();
  await expect(page.getByText("ИИ Чат")).toBeVisible();
  await expect(page.getByText("Кабинет, улица, финал.")).toBeVisible();
  await shot(page, "05-gpt");
});

test("06 база", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await page.getByRole("button", { name: "База" }).click();
  await expect(page.getByText("Он смотрит в окно и молчит.").first()).toBeVisible();
  await shot(page, "06-baza");
});

test("07 сеть", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await page.getByRole("button", { name: "Сеть" }).click();
  await expect(page.getByText("Сеть · Tailscale").first()).toBeVisible();
  await expect(page.getByText("studio").first()).toBeVisible();
  await shot(page, "07-fleet");
});

test("08 генерация", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await page.getByRole("button", { name: "Генерация" }).click();
  await expect(page.getByText("Генерация", { exact: true }).first()).toBeVisible();
  await shot(page, "08-generation");
});

test("09 логи", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await page.getByRole("button", { name: "Логи" }).click();
  await expect(page.getByText("Логи пайплайна")).toBeVisible();
  await expect(page.getByText("Подписка на канал global…")).toBeVisible();
  await shot(page, "09-logs");
});

test("10 кадры", async ({ page }) => {
  await seedStudio(page, 1);
  await page.goto("/");
  await expect(page.getByText("Он смотрит в окно и молчит.").first()).toBeVisible();
  await page.getByRole("button", { name: "Открыть", exact: true }).click();
  await expect(page.getByText("Кадры проекта")).toBeVisible();
  await shot(page, "10-frames");
});

test("11 страница оркестратора", async ({ page }) => {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  const q = new URLSearchParams({ scenario: "idle", session: "visual" });
  await page.goto(`/orchestrator?${q.toString()}`);
  await expect(page.getByTestId("orch-composer")).toBeVisible();
  await expect(page.getByTestId("orch-agent-supervisor")).toBeVisible();
  await shot(page, "11-orchestrator");
});

test("13 исход платного шага неизвестен", async ({ page }) => {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  const q = new URLSearchParams({ scenario: "paidoutcome", session: "visual-paid" });
  await page.goto(`/orchestrator?${q.toString()}`);
  await expect(page.getByTestId("orch-composer")).toBeVisible();
  await page.getByTestId("orch-composer").fill("платный шаг оборвался");
  await page.getByTestId("orch-send").click();
  await expect(page.getByTestId("orch-paid-outcome")).toHaveText("Исход платного шага неизвестен");
  await expect(page.getByTestId("orch-approve")).toHaveCount(0);
  await expect(page.getByTestId("orch-retry")).toBeEnabled();
  await expect(page.getByTestId("orch-reject")).toBeEnabled();
  await shot(page, "13-paid-outcome");
});

test("12 редактор отчётов", async ({ page }) => {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  const q = new URLSearchParams({ scenario: "idle", session: "visual", project: "13" });
  await page.goto(`/orchestrator/reports?${q.toString()}`);
  await expect(page.getByTestId("orch-report-title")).toHaveText("Разбор сцен");
  await expect(page.getByTestId("orch-report-preview")).toHaveAttribute("srcdoc", /молчит/);
  await shot(page, "12-report-editor");
});
