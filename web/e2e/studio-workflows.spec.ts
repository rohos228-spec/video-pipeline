import { test, expect } from "@playwright/test";

test.describe("Workflows API", () => {
  test("default workflow has nodes", async ({ request }) => {
    const list = await request.get("/api/workflows");
    expect(list.ok()).toBeTruthy();
    const workflows = (await list.json()) as { id: number; is_default: boolean }[];
    const def = workflows.find((w) => w.is_default);
    expect(def).toBeTruthy();
    const detail = await request.get(`/api/workflows/${def!.id}`);
    expect(detail.ok()).toBeTruthy();
    const body = (await detail.json()) as { nodes: unknown[] };
    expect(Array.isArray(body.nodes)).toBeTruthy();
    expect(body.nodes.length).toBeGreaterThan(0);
  });

  test("steps catalog returns labels", async ({ request }) => {
    const res = await request.get("/api/projects/steps/catalog");
    expect(res.ok()).toBeTruthy();
    const rows = (await res.json()) as { code: string; label: string }[];
    const plan = rows.find((r) => r.code === "plan");
    expect(plan?.label?.length).toBeGreaterThan(0);
  });
});
