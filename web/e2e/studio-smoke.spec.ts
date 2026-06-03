import { test, expect } from "@playwright/test";
import { openNodeVMenu, selectProjectInSidebar } from "./helpers";

test.describe("Studio smoke", () => {
  test("health + home loads", async ({ page, request }) => {
    const health = await request.get("/api/health");
    expect(health.ok()).toBeTruthy();
    await page.goto("/");
    await expect(page.getByPlaceholder("Поиск проектов…")).toBeVisible();
    await expect(page.getByRole("button", { name: "Новый проект" })).toBeVisible();
  });

  test("select project shows canvas run controls", async ({ page, request }) => {
    await selectProjectInSidebar(page, request);
  });

  test("V-menu enables toolbar run for video node", async ({ page, request }) => {
    await selectProjectInSidebar(page, request);
    await openNodeVMenu(page, /видео/i);
    const runTop = page.getByRole("button", { name: /Перезапустить|Создать Run/ });
    await expect(runTop).toBeEnabled({ timeout: 10_000 });
  });
});
