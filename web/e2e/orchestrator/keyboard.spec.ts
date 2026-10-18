import { expect, test, type Locator, type Page } from "@playwright/test";

const TOKEN = "orch-dev-token";
const MOCK = process.env.ORCH_MOCK_URL ?? "http://127.0.0.1:8799";

function orchUrl(scenario: string, session: string): string {
  const q = new URLSearchParams({ scenario, session });
  return `/orchestrator?${q.toString()}`;
}

async function openWithToken(page: Page, scenario: string, session: string) {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  await page.goto(orchUrl(scenario, session));
  await expect(page.getByTestId("orch-composer")).toBeVisible();
}

function trackPosts(page: Page, needle: string) {
  const bodies: { text?: string }[] = [];
  page.on("request", (req) => {
    if (req.method() !== "POST" || !req.url().includes(needle)) return;
    try {
      bodies.push(req.postDataJSON() as { text?: string });
    } catch {
      bodies.push({});
    }
  });
  return bodies;
}

async function tabUntil(page: Page, target: Locator, steps = 20) {
  for (let i = 0; i < steps; i += 1) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
}

async function tabTo(page: Page, target: Locator) {
  await page.evaluate(() => {
    const active = document.activeElement;
    if (active instanceof HTMLElement) active.blur();
  });
  await tabUntil(page, target, 80);
}

async function expectFocusVisible(target: Locator) {
  await expect(target).toBeFocused();
  const shadow = await target.evaluate((el) => getComputedStyle(el).boxShadow);
  expect(shadow).not.toBe("none");
  expect(shadow.replace(/\s/g, "")).not.toBe("");
}

test("клавиатура: Enter во время стрима шлёт одно сообщение", async ({ page }) => {
  const posts = trackPosts(page, "/message");
  await openWithToken(page, "streamhold", "kbd-stream");
  const composer = page.getByTestId("orch-composer");
  await composer.focus();
  await page.keyboard.type("старт потока");
  await page.keyboard.press("Enter");
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]?.text).toBe("старт потока");
  await expect(composer).toHaveValue("");
  const assistant = page.getByTestId("orch-assistant");
  await expect(assistant).toContainText("ДЕРЖУ-ПОТОК");
  await expect(assistant).not.toContainText("КОНЕЦ-ПОТОКА");
  const mid = (await assistant.innerText()).length;

  await composer.focus();
  await page.keyboard.type("добавка посреди");
  await page.keyboard.press("Enter");
  await expect(composer).toHaveValue("");
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]?.text).toBe("добавка посреди");
  await expect(assistant).not.toContainText("КОНЕЦ-ПОТОКА");
  const released = await page.request.post(`${MOCK}/api/orchestrator/test/release-stream`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: { session_id: "kbd-stream" },
  });
  expect(released.ok()).toBeTruthy();
  await expect(assistant).toContainText("КОНЕЦ-ПОТОКА");
  expect((await assistant.innerText()).length).toBeGreaterThan(mid);
  expect(posts).toHaveLength(2);
});

test("клавиатура: Shift+Enter переносит строку, Enter отправляет", async ({ page }) => {
  const posts = trackPosts(page, "/message");
  await openWithToken(page, "idle", "kbd-newline");
  const composer = page.getByTestId("orch-composer");
  await composer.focus();
  await page.keyboard.type("первая");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("вторая");
  await expect(composer).toHaveValue("первая\nвторая");
  expect(posts).toHaveLength(0);
  await page.keyboard.press("Enter");
  await expect(composer).toHaveValue("");
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]?.text).toBe("первая\nвторая");
});

test("клавиатура: Enter во время IME не отправляет", async ({ page }) => {
  const posts = trackPosts(page, "/message");
  await openWithToken(page, "idle", "kbd-ime");
  const composer = page.getByTestId("orch-composer");
  await composer.focus();
  await page.keyboard.type("черновик");
  await composer.evaluate((el) => {
    el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    el.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        code: "Enter",
        bubbles: true,
        cancelable: true,
        isComposing: true,
      }),
    );
  });
  await expect(composer).toHaveValue("черновик");
  expect(posts).toHaveLength(0);
  await page.keyboard.press("Enter");
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]?.text).toBe("черновик");
  await expect(composer).toHaveValue("");
});

test("клавиатура: Kill switch Tab, фокус и один Enter-запрос", async ({ page }) => {
  const kills: number[] = [];
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/orchestrator/kill", async (route) => {
    kills.push(Date.now());
    await gate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, stop_key_set: true, detail: "" }),
    });
  });
  await openWithToken(page, "idle", "kbd-kill");
  const kill = page.getByTestId("orch-kill");
  await tabTo(page, kill);
  await expectFocusVisible(kill);
  await page.keyboard.press("Enter");
  const confirm = page.getByTestId("orch-kill-confirm");
  await expect(confirm).toBeVisible();
  await tabUntil(page, confirm);
  await expect(confirm).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  const banner = page.getByTestId("orch-kill-status");
  await expect(banner).toContainText("Останавливаю…");
  await expect.poll(() => kills.length).toBe(1);
  release();
  await expect(banner).toContainText("Остановлено");
  expect(kills).toHaveLength(1);
});

test("клавиатура: статус агентов читается через aria-live", async ({ page }) => {
  await openWithToken(page, "idle", "kbd-status");
  const status = page.getByRole("status", { name: "Статусы агентов" });
  await expect(status).toHaveAttribute("aria-live", "polite");
  await expect(status).toContainText("idle");
  const before = await status.ariaSnapshot();
  expect(before).toMatch(/status/i);
  const res = await page.request.post(`${MOCK}/api/orchestrator/test/emit`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: {
      session_id: "kbd-status",
      type: "agent_status",
      task_id: 1,
      payload: {
        agent: "supervisor",
        status: "planning",
        detail: "разбор клавиатуры",
      },
    },
  });
  expect(res.ok()).toBeTruthy();
  await expect(status).toContainText("planning");
  await expect(status).toContainText("разбор клавиатуры");
  const after = await status.ariaSnapshot();
  expect(after).not.toBe(before);
  expect(after).toContain("planning");
});

