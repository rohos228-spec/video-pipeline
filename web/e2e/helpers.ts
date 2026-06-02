import { expect, type APIRequestContext, type Page } from "@playwright/test";

export async function pickProjectId(
  request: APIRequestContext,
  preferSlugIncludes?: string,
): Promise<number> {
  const res = await request.get("/api/projects");
  if (!res.ok()) throw new Error(`projects API ${res.status()}`);
  const projects = (await res.json()) as { id: number; slug: string }[];
  if (!projects.length) throw new Error("no projects in DB");
  if (preferSlugIncludes) {
    const hit = projects.find((p) => p.slug.includes(preferSlugIncludes));
    if (hit) return hit.id;
  }
  return projects[0].id;
}

/** Клик по строке проекта в сайдбаре (устойчив к перерисовке списка). */
export async function selectProjectInSidebar(
  page: Page,
  request: APIRequestContext,
  preferSlugIncludes?: string,
): Promise<number> {
  const id = await pickProjectId(request, preferSlugIncludes);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByText("Application error")).not.toBeVisible({ timeout: 5_000 });
  const search = page.getByPlaceholder("Поиск проектов…");
  if (preferSlugIncludes) {
    await search.fill(preferSlugIncludes);
    await page.waitForTimeout(400);
  }
  const row = page.locator(`[data-project-id="${id}"]`);
  await row.click({ timeout: 15_000, force: true });
  await page.waitForTimeout(800);
  if (errors.length) {
    throw new Error(`pageerror: ${errors.join(" | ")}`);
  }
  return id;
}
