import { expect, test, type Page } from "@playwright/test";

const TOKEN = "orch-dev-token";
const MOCK = process.env.ORCH_MOCK_URL ?? "http://127.0.0.1:8799";

const PAYLOADS = [
  { id: "script", html: `<script>fetch("${MOCK}/xss-beacon?p=script")</script><p>маркер</p>` },
  { id: "img", html: `<p>маркер</p><img src="x" onerror="fetch('${MOCK}/xss-beacon?p=img')">` },
  { id: "svg", html: `<svg onload="fetch('${MOCK}/xss-beacon?p=svg')"></svg><p>маркер</p>` },
  { id: "js", html: `<a href="javascript:fetch('${MOCK}/xss-beacon?p=js')">ссылка</a><p>маркер</p>` },
  { id: "srcdoc", html: `<iframe srcdoc="<script>fetch('${MOCK}/xss-beacon?p=srcdoc')</script>"></iframe><p>маркер</p>` },
];

function reportsUrl(project = "13"): string {
  const q = new URLSearchParams({ orchApi: MOCK, scenario: "idle", project });
  return `/orchestrator/reports?${q.toString()}`;
}

async function openReports(page: Page) {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  await page.goto(reportsUrl());
  await expect(page.getByTestId("orch-report-title")).toHaveText("Разбор сцен");
}

function watchXss(page: Page): string[] {
  const hits: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("xss-beacon")) hits.push(req.url());
  });
  page.on("dialog", (dialog) => {
    void dialog.dismiss();
    throw new Error(`xss dialog: ${dialog.message()}`);
  });
  return hits;
}

async function expectClean(page: Page, hits: string[]) {
  const preview = page.getByTestId("orch-report-preview");
  await expect(preview).not.toHaveAttribute("srcdoc", /<script/i);
  await expect(preview).not.toHaveAttribute("srcdoc", /onerror/i);
  await expect(preview).not.toHaveAttribute("srcdoc", /onload/i);
  await expect(preview).not.toHaveAttribute("srcdoc", /javascript:/i);
  await expect(preview).not.toHaveAttribute("srcdoc", /srcdoc=/i);
  expect(hits).toEqual([]);
  const id = await page.getByTestId("orch-report-title").getAttribute("data-report-id");
  const res = await page.request.get(`${MOCK}/api/orchestrator/reports/${id}/html`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  expect(res.status()).toBe(200);
  const headers = res.headers();
  expect(headers["content-security-policy"]).toContain("default-src 'none'");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  const body = (await res.text()).toLowerCase();
  expect(body).not.toContain("<script");
  expect(body).not.toContain("onerror");
  expect(body).not.toContain("onload");
  expect(body).not.toContain("javascript:");
  expect(body).not.toContain("srcdoc");
}

test.describe("report editor", () => {
  test("правка секции, версия и откат", async ({ page }) => {
    await openReports(page);
    await page.getByTestId("orch-section-html").fill("<p>Новая редакция.</p>");
    await page.getByTestId("orch-report-save").click();
    await expect(page.getByTestId("orch-report-preview")).toHaveAttribute("srcdoc", /Новая редакция/);
    await expect(page.getByTestId("orch-report-version").filter({ hasText: "v2 · текущая" })).toBeVisible();
    await page.getByTestId("orch-report-version").filter({ hasText: /^v1 / }).getByTestId("orch-report-rollback").click();
    await expect(page.getByTestId("orch-report-preview")).toHaveAttribute("srcdoc", /молчит/);
    await expect(page.getByTestId("orch-section-html")).toHaveValue(/молчит/);
    await expect(page.getByTestId("orch-report-version").filter({ hasText: "v3 · текущая" })).toBeVisible();
  });

  test("XSS на создании и правке не исполняется", async ({ page }) => {
    const hits = watchXss(page);
    await openReports(page);
    for (const payload of PAYLOADS) {
      await page.getByTestId("orch-create-title").fill(`xss-${payload.id}`);
      await page.getByTestId("orch-create-heading").fill("Секция");
      await page.getByTestId("orch-create-html").fill(payload.html);
      await page.getByTestId("orch-report-create").click();
      await expect(page.getByTestId("orch-report-title")).toHaveText(`xss-${payload.id}`);
      await expect(page.getByTestId("orch-report-preview")).toHaveAttribute("srcdoc", /маркер/);
      await expectClean(page, hits);

      await page.getByTestId("orch-section-html").fill(payload.html);
      await expect(page.getByTestId("orch-report-preview")).toHaveAttribute("srcdoc", /маркер/);
      await expectClean(page, hits);
      await page.getByTestId("orch-report-save").click();
      await expect(page.getByTestId("orch-report-version").filter({ hasText: "v2 · текущая" })).toBeVisible();
      await expectClean(page, hits);
    }
  });
});
