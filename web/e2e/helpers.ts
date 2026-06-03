import { expect, type APIRequestContext, type Page } from "@playwright/test";

export async function pickProjectId(
  request: APIRequestContext,
  preferSlugIncludes?: string,
): Promise<number> {
  const res = await request.get("/api/projects");
  if (!res.ok()) throw new Error(`projects API ${res.status()}`);
  const projects = (await res.json()) as { id: number; slug: string; topic?: string }[];
  if (!projects.length) throw new Error("no projects in DB");
  if (preferSlugIncludes) {
    const needle = preferSlugIncludes.toLowerCase();
    const hit = projects.find(
      (p) =>
        p.slug.toLowerCase().includes(needle) ||
        (p.topic ?? "").toLowerCase().includes(needle),
    );
    if (hit) return hit.id;
  }
  return projects[0].id;
}

/** Выбор проекта в сайдбаре по id из API (без хрупкого текста «Алькатрас»). */
export async function selectProjectInSidebar(
  page: Page,
  request: APIRequestContext,
  preferSlugIncludes?: string,
): Promise<number> {
  const id = await pickProjectId(request, preferSlugIncludes);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByPlaceholder("Поиск проектов…")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Application error")).not.toBeVisible({ timeout: 3_000 });

  const row = page.locator(`[data-project-id="${id}"]`);
  await expect(row).toBeVisible({ timeout: 20_000 });
  await row.scrollIntoViewIfNeeded();
  await row.click({ timeout: 10_000 });

  await expect(
    page.getByRole("button", { name: /Создать Run|Перезапустить/ }),
  ).toBeVisible({ timeout: 25_000 });

  if (errors.length) {
    throw new Error(`pageerror: ${errors.join(" | ")}`);
  }
  return id;
}

/** Клик V-меню на ноде канваса (устойчиво к перерисовке React Flow). */
export async function openNodeVMenu(page: Page, nodeTitle: string | RegExp): Promise<void> {
  const node = page.locator(".react-flow__node").filter({ hasText: nodeTitle });
  await expect(node.first()).toBeVisible({ timeout: 20_000 });

  let lastErr: unknown;
  for (let attempt = 0; attempt < 6; attempt++) {
    try {
      const trigger = node.first().locator(".node-v-trigger");
      await trigger.scrollIntoViewIfNeeded();
      await trigger.click({ timeout: 4000, force: true });
      await expect(page.getByRole("button", { name: "Запустить шаг" })).toBeVisible({
        timeout: 8000,
      });
      return;
    } catch (e) {
      lastErr = e;
      await page.waitForTimeout(350);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("openNodeVMenu failed");
}
