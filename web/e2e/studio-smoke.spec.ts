import { test, expect } from "@playwright/test";
import { selectProjectInSidebar } from "./helpers";

test.describe("Studio smoke", () => {
  test("health + home loads", async ({ page, request }) => {
    const health = await request.get("/api/health");
    expect(health.ok()).toBeTruthy();
    await page.goto("/");
    await expect(page.getByPlaceholder("Поиск проектов…")).toBeVisible();
    await expect(page.getByRole("button", { name: "Новый проект" })).toBeVisible();
  });

  test("select project shows canvas run controls", async ({ page, request }) => {
    await selectProjectInSidebar(page, request, "tyurmy-alkatras");
    await expect(
      page.getByRole("button", { name: /Создать Run|Перезапустить/ }),
    ).toBeVisible({ timeout: 20_000 });
  });

  test("V-menu enables toolbar run for video node", async ({ page, request }) => {
    await selectProjectInSidebar(page, request, "tyurmy-alkatras");
    await expect(page.getByRole("button", { name: /Создать Run|Перезапустить/ })).toBeVisible({
      timeout: 20_000,
    });
    const vTriggers = page.locator(".node-v-trigger");
    await expect(vTriggers.first()).toBeVisible({ timeout: 15_000 });
    const count = await vTriggers.count();
    await vTriggers.nth(Math.max(0, count - 3)).click({ force: true });
    await expect(page.getByRole("button", { name: "Запустить шаг" })).toBeVisible({
      timeout: 10_000,
    });
    const runTop = page.getByRole("button", { name: /Перезапустить|Создать Run/ });
    await expect(runTop).toBeEnabled({ timeout: 10_000 });
  });
});
