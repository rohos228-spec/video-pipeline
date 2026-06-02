import { test, expect } from "@playwright/test";

test("projects API matches sidebar", async ({ page, request }) => {
  const api = await request.get("/api/projects");
  expect(api.ok()).toBeTruthy();
  const projects = await api.json();
  expect(projects.length).toBeGreaterThan(0);

  await page.goto("/");
  const firstTopic = projects[0].topic as string;
  if (firstTopic && firstTopic.length > 4) {
    const snippet = firstTopic.slice(0, Math.min(12, firstTopic.length));
    await expect(page.getByRole("button", { name: new RegExp(snippet, "i") }).first()).toBeVisible({
      timeout: 10_000,
    });
  }
});
