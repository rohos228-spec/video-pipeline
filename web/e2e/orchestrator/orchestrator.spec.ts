import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import {
  bearerTargetAllowed,
  envOrchApiBase,
  isSameOriginApiBase,
  orchAuthHeaders,
  orchTestModeEnabled,
  resolveOrchApiBase,
} from "../../src/lib/orchestratorApi";

const SHOTS =
  process.env.ORCH_E2E_SHOTS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../test-results/orch-shots");

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

test("X-Orch-Scenario только при NEXT_PUBLIC_ORCH_TEST", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_TEST;
  const cfg = {
    apiBase: "",
    session: "studio",
    token: "tok",
    scenario: "kill",
  };
  try {
    delete process.env.NEXT_PUBLIC_ORCH_TEST;
    expect(orchTestModeEnabled()).toBe(false);
    const fromQuery = orchAuthHeaders(cfg);
    expect(fromQuery.get("X-Orch-Scenario")).toBeNull();
    expect(fromQuery.get("Authorization")).toBe("Bearer tok");
    const fromStorage = orchAuthHeaders({ ...cfg, scenario: "budget" });
    expect(fromStorage.has("X-Orch-Scenario")).toBe(false);

    process.env.NEXT_PUBLIC_ORCH_TEST = "0";
    expect(orchTestModeEnabled()).toBe(false);
    expect(orchAuthHeaders(cfg).has("X-Orch-Scenario")).toBe(false);

    process.env.NEXT_PUBLIC_ORCH_TEST = "1";
    expect(orchTestModeEnabled()).toBe(true);
    expect(orchAuthHeaders(cfg).get("X-Orch-Scenario")).toBe("kill");

    process.env.NEXT_PUBLIC_ORCH_TEST = "true";
    expect(orchAuthHeaders({ ...cfg, scenario: "budget" }).get("X-Orch-Scenario")).toBe("budget");
  } finally {
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_TEST;
    else process.env.NEXT_PUBLIC_ORCH_TEST = previous;
  }
});

test("isSameOriginApiBase отвергает //evil, userinfo и javascript", () => {
  const pageOrigin = "https://host";
  expect(isSameOriginApiBase("//evil.com", "https://evil.com")).toBe(false);
  expect(isSameOriginApiBase("//evil.com/api", "https://evil.com")).toBe(false);
  expect(isSameOriginApiBase("https://user@host", pageOrigin)).toBe(false);
  expect(isSameOriginApiBase("https://user@host/api", pageOrigin)).toBe(false);
  expect(isSameOriginApiBase("http://localhost@evil.com", "http://evil.com")).toBe(false);
  expect(isSameOriginApiBase("http://localhost@evil.com/api", "http://evil.com")).toBe(false);
  expect(isSameOriginApiBase("javascript:", "http://localhost")).toBe(false);
  expect(isSameOriginApiBase("javascript:alert(1)", "http://localhost")).toBe(false);
  expect(isSameOriginApiBase("javascript:/api", "http://localhost")).toBe(false);
  expect(isSameOriginApiBase("/api", pageOrigin)).toBe(true);
  expect(isSameOriginApiBase("https://host/api", pageOrigin)).toBe(true);
});

test("отброшенный NEXT_PUBLIC_ORCH_API пишет warn без секрета", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  const warnings: string[] = [];
  const original = console.warn;
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map((item) => String(item)).join(" "));
  };
  const secret = "super-secret-token";
  try {
    process.env.NEXT_PUBLIC_ORCH_API = `http://user:${secret}@evil.com/api?token=${secret}`;
    expect(envOrchApiBase()).toBe("");
    const text = warnings.join("\n");
    expect(text).toContain("NEXT_PUBLIC_ORCH_API отброшен");
    expect(text).not.toContain(secret);
    expect(text).not.toContain("user:");

    warnings.length = 0;
    process.env.NEXT_PUBLIC_ORCH_API = "";
    expect(envOrchApiBase()).toBe("");
    expect(warnings).toEqual([]);

    process.env.NEXT_PUBLIC_ORCH_API = "https://api.example.com";
    expect(envOrchApiBase()).toBe("https://api.example.com");
    expect(warnings).toEqual([]);
  } finally {
    console.warn = original;
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_API;
    else process.env.NEXT_PUBLIC_ORCH_API = previous;
  }
});

test("валидатор NEXT_PUBLIC_ORCH_API возвращает нормализованный URL", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  const pageOrigin = "http://127.0.0.1:3100";
  try {
    process.env.NEXT_PUBLIC_ORCH_API = "http://127.0.0.1:8799?x=1#frag";
    expect(envOrchApiBase()).toBe("http://127.0.0.1:8799");
    expect(resolveOrchApiBase(null, pageOrigin)).toBe("http://127.0.0.1:8799");
    expect(bearerTargetAllowed(envOrchApiBase(), pageOrigin)).toBe(true);

    process.env.NEXT_PUBLIC_ORCH_API = "HTTP://LOCALHOST:8799/";
    expect(envOrchApiBase()).toBe("http://localhost:8799");
    expect(resolveOrchApiBase(null, pageOrigin)).toBe("http://localhost:8799");

    process.env.NEXT_PUBLIC_ORCH_API = "https://api.example.com/orch/?q=1";
    expect(envOrchApiBase()).toBe("https://api.example.com/orch");
    expect(resolveOrchApiBase(null, pageOrigin)).toBe("https://api.example.com/orch");
    expect(bearerTargetAllowed("https://api.example.com/orch", pageOrigin)).toBe(true);
  } finally {
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_API;
    else process.env.NEXT_PUBLIC_ORCH_API = previous;
  }
});

test("NEXT_PUBLIC_ORCH_API только https или http на loopback", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  const pageOrigin = "http://127.0.0.1:3100";
  try {
    process.env.NEXT_PUBLIC_ORCH_API = "https://api.example.com";
    expect(envOrchApiBase()).toBe("https://api.example.com");
    expect(bearerTargetAllowed("https://api.example.com", pageOrigin)).toBe(true);

    process.env.NEXT_PUBLIC_ORCH_API = "http://127.0.0.1:8799";
    expect(envOrchApiBase()).toBe("http://127.0.0.1:8799");
    expect(bearerTargetAllowed("http://127.0.0.1:8799", pageOrigin)).toBe(true);

    process.env.NEXT_PUBLIC_ORCH_API = "http://localhost:8799/";
    expect(envOrchApiBase()).toBe("http://localhost:8799");

    process.env.NEXT_PUBLIC_ORCH_API = "http://[::1]:8799";
    expect(envOrchApiBase()).toBe("http://[::1]:8799");

    const rejected = [
      "http://evil.com",
      "http://evil.com/api",
      "https://user@evil.com",
      "http://127.0.0.1:8799@evil.com",
      "//evil.com",
      "javascript:alert(1)",
      "http://127.0.0.1.evil.com",
    ];
    for (const value of rejected) {
      process.env.NEXT_PUBLIC_ORCH_API = value;
      expect(envOrchApiBase(), value).toBe("");
      expect(resolveOrchApiBase(null, pageOrigin), value).toBe("");
      expect(bearerTargetAllowed(value, pageOrigin), value).toBe(false);
    }
  } finally {
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_API;
    else process.env.NEXT_PUBLIC_ORCH_API = previous;
  }
});

const TOKEN = "orch-dev-token";
const MOCK = process.env.ORCH_MOCK_URL ?? "http://127.0.0.1:8799";

function orchUrl(scenario: string, session = "studio", project?: number): string {
  const q = new URLSearchParams({
    scenario,
    session,
  });
  if (project != null) q.set("project", String(project));
  return `/orchestrator?${q.toString()}`;
}

async function openWithToken(page: Page, scenario: string, session = "studio", project?: number) {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  await page.goto(orchUrl(scenario, session, project));
  await expect(page.getByTestId("orch-composer")).toBeVisible();
}

test("?orchApi чужого origin не получает Bearer", async ({ page }) => {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  const allowed = new URL(MOCK).origin;
  const foreigners = ["https://evil.example", "http://127.0.0.1:9"];
  for (const [index, foreign] of foreigners.entries()) {
    const foreignOrigin = new URL(foreign).origin;
    const leaked: string[] = [];
    const authed: string[] = [];
    const onRequest = (req: { url: () => string; headers: () => Record<string, string> }) => {
      const auth = req.headers().authorization || "";
      if (!auth.toLowerCase().startsWith("bearer ")) return;
      const url = req.url();
      authed.push(url);
      if (new URL(url).origin === foreignOrigin) leaked.push(url);
    };
    page.on("request", onRequest);
    try {
      const q = new URLSearchParams({
        orchApi: foreign,
        scenario: "idle",
        session: `leak-${index}`,
      });
      await page.goto(`/orchestrator?${q.toString()}`);
      await expect(page.getByTestId("orch-composer")).toBeVisible();
      await expect.poll(() => authed.length).toBeGreaterThan(0);
      expect(leaked).toEqual([]);
      for (const url of authed) {
        expect(new URL(url).origin).toBe(allowed);
      }
    } finally {
      page.off("request", onRequest);
    }
  }
});

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
    await page.getByTestId("orch-send").click();
    await expect
      .poll(async () => {
        const ack = page.getByTestId("orch-ack");
        const echo = await ack.getAttribute("data-ack-echo");
        const raw = await ack.getAttribute("data-ack-ms");
        const ms = raw == null ? Number.NaN : Number(raw);
        if (echo !== "стой" || !Number.isFinite(ms)) return Number.POSITIVE_INFINITY;
        return ms;
      }, { timeout: 2_000 })
      .toBeLessThanOrEqual(2_000);
    await expect(page.getByTestId("orch-ack")).toContainText("стой");
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

  test("approve 404, 409 и 422 оставляют карточку", async ({ page }) => {
    const cases = [
      ["approve404", "approval not found"],
      ["approve409", "approval conflict"],
      ["approve422", "approval unprocessable"],
    ] as const;
    for (const [scenario, detail] of cases) {
      await openWithToken(page, scenario, scenario);
      await page.getByTestId("orch-composer").fill("запусти ноду");
      await page.getByTestId("orch-send").click();
      await expect(page.getByTestId("orch-approval")).toBeVisible();
      await page.getByTestId("orch-approve").click();
      await expect(page.getByTestId("orch-approval-error")).toContainText(detail);
      await expect(page.getByTestId("orch-approval")).toBeVisible();
      await expect(page.getByTestId("orch-approve")).toBeEnabled();
      await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
    }
  });

  test("approve 500: карточка остаётся и ошибку можно повторить", async ({ page }) => {
    await openWithToken(page, "approvefail", "approvefail-1");
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approve")).toBeDisabled();
    await expect(page.getByTestId("orch-approval-error")).toContainText("approve failed");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-approve")).toBeEnabled();
    await expect(page.getByTestId("orch-task-status")).not.toContainText("готово");
  });

  test("бюджет: approve по change_id выдаёт суб-бюджет", async ({ page }) => {
    await openWithToken(page, "budget", "budget-grant");
    await page.getByTestId("orch-composer").fill("ещё кадры");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-budget")).toContainText("Бюджет исчерпан");
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    const posted = page.waitForRequest(
      (req) => req.method() === "POST" && req.url().includes("/approvals/chg-budget-d4/approve"),
    );
    await page.getByTestId("orch-approve").click();
    const body = (await posted).postDataJSON() as { approval_id?: string };
    expect(body.approval_id).toBe("chg-budget-d4");
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    await expect(page.getByTestId("orch-transcript")).toContainText("Суб-бюджет добавлен");
    await expect(page.getByTestId("orch-task-status")).toContainText("выполнение");
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

  test("kill switch: оговорка видна при произвольном detail", async ({ page }) => {
    await openWithToken(page, "idle", "kill-custom");
    await page.route("**/api/orchestrator/kill", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          status: "cancelled",
          stop_key_set: true,
          detail: "произвольный текст сервера",
        }),
      });
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toContainText(
      "Неплатные шаги отменены, новые действия запрещены. Платный шаг остановится на границе.",
    );
    await expect(banner).toContainText("произвольный текст сервера");
    const text = await banner.innerText();
    expect(text.indexOf("Платный шаг остановится на границе.")).toBeLessThan(
      text.indexOf("произвольный текст сервера"),
    );
  });

  test("kill: Останавливаю, ошибка при stop_key_set=false и повтор", async ({ page }) => {
    await openWithToken(page, "idle", "kill-pending");
    let release = () => {};
    let gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let body: { ok: boolean; stop_key_set: boolean; detail: string } = {
      ok: true,
      stop_key_set: false,
      detail: "стоп-ключ не встал",
    };
    await page.route("**/api/orchestrator/kill", async (route) => {
      await gate;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopping");
    await expect(banner).toContainText("Останавливаю…");
    await expect(banner).not.toContainText("Остановлено");
    release();
    await expect(banner).toHaveAttribute("data-kill-phase", "error");
    await expect(banner).toContainText("стоп-ключ не встал");
    await expect(banner).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();
    gate = Promise.resolve();
    body = { ok: true, stop_key_set: true, detail: "ключ установлен" };
    await page.getByTestId("orch-kill-retry").click();
    await expect(banner).toHaveAttribute("data-kill-phase", "stopped");
    await expect(banner).toContainText("Остановлено");
    await expect(banner).toContainText("Платный шаг остановится на границе");
    await expect(banner).toContainText("ключ установлен");
    await expect(page.getByTestId("orch-kill-retry")).toHaveCount(0);
  });

  test("kill: таймаут запроса показывает ошибку и повтор", async ({ page }) => {
    await openWithToken(page, "idle", "kill-timeout");
    let release = () => {};
    const hang = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/orchestrator/kill", async (route) => {
      await hang;
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" }).catch(() => undefined);
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopping");
    await expect(banner).toHaveAttribute("data-kill-phase", "error", { timeout: 20_000 });
    await expect(banner).toContainText("Нет подтверждения остановки");
    await expect(banner).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();
    release();
  });

  test("kill: 401 показывает ошибку и повтор", async ({ page }) => {
    await openWithToken(page, "idle", "kill-401");
    await page.route("**/api/orchestrator/kill", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unauthorized" }),
      });
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "error");
    await expect(banner).toContainText("401");
    await expect(banner).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();
  });

  async function emitBus(
    page: Page,
    session: string,
    type: string,
    taskId: number | null,
    payload: Record<string, unknown>,
    eventSession?: string,
  ) {
    const res = await page.request.post(`${MOCK}/api/orchestrator/test/emit`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      data: {
        session_id: session,
        type,
        task_id: taskId,
        payload,
        ...(eventSession ? { event_session_id: eventSession } : {}),
      },
    });
    expect(res.ok()).toBeTruthy();
  }

  test("kill: cancelled чужой задачи или шага не даёт Остановлено", async ({ page }) => {
    await openWithToken(page, "kill", "kill-scope");
    await page.getByTestId("orch-composer").fill("платная генерация");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-task-panel")).toContainText("run_node");
    let release = () => {};
    const hang = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/orchestrator/kill", async (route) => {
      await hang;
      await route.fulfill({ status: 204, body: "" }).catch(() => undefined);
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopping");
    await emitBus(page, "kill-scope", "agent_status", 2, {
      agent: "supervisor",
      status: "cancelled",
      task_status: "cancelled",
    });
    await expect(page.getByTestId("orch-task-panel")).toContainText("отменено");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopping");
    await expect(banner).not.toContainText("Остановлено");
    await emitBus(page, "kill-scope", "step_finished", 1, {
      step_id: "s-paid",
      title: "run_node images",
      ok: false,
      note: "шаг отменён",
    });
    await expect(page.getByTestId("orch-task-panel")).toContainText("шаг отменён");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopping");
    await emitBus(page, "kill-scope", "agent_status", 1, {
      agent: "executor",
      status: "cancelled",
      detail: "агент встал",
    });
    await expect(page.getByTestId("orch-agent-executor")).toContainText("cancelled");
    await expect(banner).not.toContainText("Остановлено");
    await emitBus(
      page,
      "kill-scope",
      "agent_status",
      1,
      { agent: "supervisor", status: "cancelled", task_status: "cancelled" },
      "other-session",
    );
    await expect(banner).toHaveAttribute("data-kill-phase", "stopping");
    await emitBus(page, "kill-scope", "agent_status", 1, {
      agent: "supervisor",
      status: "idle",
      task_status: "cancelled",
    });
    await expect(banner).toHaveAttribute("data-kill-phase", "unconfirmed");
    await expect(banner).toContainText("Задача отменена, стоп-ключ не подтверждён");
    await expect(banner).not.toContainText("Остановлено");
    release();
  });

  test("kill: после stop_key_set=false cancelled не затирает ошибку", async ({ page }) => {
    await openWithToken(page, "kill", "kill-keep-error");
    await page.getByTestId("orch-composer").fill("платная генерация");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-task-panel")).toContainText("run_node");
    await page.route("**/api/orchestrator/kill", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, stop_key_set: false, detail: "стоп-ключ не встал" }),
      });
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "error");
    await expect(banner).toContainText("стоп-ключ не встал");
    await emitBus(page, "kill-keep-error", "agent_status", 1, {
      agent: "supervisor",
      status: "cancelled",
      task_status: "cancelled",
    });
    await expect(page.getByTestId("orch-task-status")).toContainText("отменено");
    await expect(banner).toHaveAttribute("data-kill-phase", "error");
    await expect(banner).toContainText("стоп-ключ не встал");
    await expect(banner).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();
  });

  test("после Остановлено поздний stop_key_set=false показывает предупреждение", async ({ page }) => {
    await openWithToken(page, "idle", "kill-late-false");
    await page.route("**/api/orchestrator/kill", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, stop_key_set: true, detail: "ключ установлен" }),
      });
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopped");
    await expect(banner).toContainText("Остановлено");
    await emitBus(page, "kill-late-false", "kill_switch", null, {
      stop_key_set: false,
      detail: "поздний отказ ключа",
    });
    await expect(page.getByTestId("orch-kill-late-warning")).toContainText("поздний отказ ключа");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopped");
    await expect(banner).toContainText("Остановлено");
    await expect(banner).not.toContainText("Стоп-ключ не установлен");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();
  });

  test("kill: ответ без stop_key_set показывает ошибку и повтор", async ({ page }) => {
    await openWithToken(page, "idle", "kill-nokey");
    await page.route("**/api/orchestrator/kill", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, status: "cancelled" }),
      });
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "error");
    await expect(banner).toContainText("В ответе нет stop_key_set");
    await expect(banner).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();
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
    await expect(page.getByTestId("orch-classification")).toContainText("остановка");
    await expect(page.getByTestId("orch-classification")).toContainText("kill switch из сообщения");
    await expect(page.getByTestId("orch-classification")).not.toContainText("interrupt");
    await expect(page.getByTestId("orch-kill-status")).toHaveAttribute("data-kill-phase", "stopping");
    await expect(page.getByTestId("orch-kill-status")).toContainText("Останавливаю…");
    await expect(page.getByTestId("orch-kill-status")).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-status")).not.toContainText(/платный шаг остановится на границе/i);
  });

  test("task: новая задача встаёт в очередь и стартует после текущей", async ({ page }) => {
    await openWithToken(page, "task", "task-1");
    await expect(page.getByTestId("orch-mode-task-hint")).toContainText("очередь за текущей");
    await page.getByTestId("orch-composer").fill("текущая работа");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-task-panel")).toContainText("Текущая");
    await expect(page.getByTestId("orch-task-panel")).toContainText("выполнение");
    await page.getByTestId("orch-mode-task").check();
    await page.getByTestId("orch-composer").fill("следующая задача");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-classification")).toContainText("task");
    await expect(page.getByTestId("orch-task-panel")).toContainText("в очереди");
    await expect(page.getByTestId("orch-task-panel")).toContainText("Текущая");
    await expect(page.getByTestId("orch-task-panel")).toContainText("следующая задача");
    await expect(page.getByTestId("orch-task-panel")).toContainText("стартовала после текущей");
  });

  test("budget_exhausted приходит из LLMResponse и показывает карточку", async ({ page }) => {
    await openWithToken(page, "llmbudget", "llm-budget-1");
    await page.getByTestId("orch-composer").fill("ещё один ход");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-transcript")).toContainText("внутренний бюджет выбран");
    await expect(page.getByTestId("orch-budget")).toContainText("Бюджет исчерпан");
    await expect(page.getByTestId("orch-task-status").first()).toContainText("подтверждения");
  });

  test("401: пустой токен, отказ, затем верный токен", async ({ page }) => {
    await page.goto(orchUrl("stream", "auth-1"));
    await expect(page.getByTestId("orch-token-gate")).toBeVisible();
    await page.getByTestId("orch-token-input").fill("wrong-token");
    await page.getByTestId("orch-token-submit").click();
    await expect(page.getByTestId("orch-token-error")).toContainText("401");
    await expect.poll(async () => page.evaluate(() => localStorage.getItem("vp-orch-api-token"))).toBeNull();
    await saveShot(page, "orch-401");
    await page.getByTestId("orch-token-input").fill(TOKEN);
    await page.getByTestId("orch-token-submit").click();
    await expect(page.getByTestId("orch-composer")).toBeVisible();
    await expect(page.getByTestId("orch-token-gate")).toHaveCount(0);
  });

  test("project_id уходит в MessageIn, если проект выбран", async ({ page }) => {
    await openWithToken(page, "idle", "project-id", 13);
    const posted = page.waitForRequest(
      (req) => req.method() === "POST" && req.url().includes("/message"),
    );
    await page.getByTestId("orch-composer").fill("кадр в проекте");
    await page.getByTestId("orch-send").click();
    const body = (await posted).postDataJSON() as { text?: string; project_id?: number };
    expect(body.text).toBe("кадр в проекте");
    expect(body.project_id).toBe(13);
  });

  test("без проекта поле project_id не уходит", async ({ page }) => {
    await openWithToken(page, "idle", "no-project");
    const posted = page.waitForRequest(
      (req) => req.method() === "POST" && req.url().includes("/message"),
    );
    await page.getByTestId("orch-composer").fill("кадр без проекта");
    await page.getByTestId("orch-send").click();
    const body = (await posted).postDataJSON() as Record<string, unknown>;
    expect(body).not.toHaveProperty("project_id");
  });

  test("missing_project_id: карточка и кнопки approval", async ({ page }) => {
    await openWithToken(page, "missingproject", "missing-project");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-missing-project")).toContainText(
      "Не выбран проект: план содержит платный или изменяющий шаг",
    );
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await expect(page.getByTestId("orch-reject")).toBeVisible();
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
  });

  test("deletion_blocked: нет кнопки одобрить и grant не уходит", async ({ page }) => {
    const grants: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && /\/approvals\/.+\/(approve|reject)/.test(req.url())) {
        grants.push(req.url());
      }
    });
    await openWithToken(page, "deletion", "deletion-1");
    await page.getByTestId("orch-composer").fill("удали кадр");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-deletion")).toContainText("Удаление запрещено");
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
    await page.getByTestId("orch-block-close").click();
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    expect(grants).toEqual([]);
  });

  test("empty_plan: задача failed и понятный текст", async ({ page }) => {
    await openWithToken(page, "emptyplan", "empty-plan");
    await page.getByTestId("orch-composer").fill("продолжи");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-empty-plan")).toContainText("План пустой");
    await expect(page.getByTestId("orch-approval")).toContainText("В чекпоинте нет шагов");
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
  });

  test("risk: карточка approval с кнопками", async ({ page }) => {
    await openWithToken(page, "risk", "risk-1", 13);
    const posted = page.waitForRequest(
      (req) => req.method() === "POST" && req.url().includes("/message"),
    );
    await page.getByTestId("orch-composer").fill("среднее касание");
    await page.getByTestId("orch-send").click();
    const body = (await posted).postDataJSON() as { project_id?: number };
    expect(body.project_id).toBe(13);
    const card = page.getByTestId("orch-approval");
    await expect(card).toHaveAttribute("data-terminal", "false");
    await expect(page.getByTestId("orch-risk")).toHaveText("Шаг с риском: нужно подтверждение");
    await expect(card).toContainText("test.medium_touch");
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await expect(page.getByTestId("orch-reject")).toBeVisible();
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
  });

  test("200 и прочие 2xx без тела не закрывают карточку", async ({ page }) => {
    for (const status of [200, 202]) {
      await openWithToken(page, "approval", `empty-${status}`);
      await page.route("**/api/orchestrator/approvals/**/approve", async (route) => {
        await route.fulfill({ status, body: "" });
      });
      await page.getByTestId("orch-composer").fill("запусти ноду");
      await page.getByTestId("orch-send").click();
      await expect(page.getByTestId("orch-approval")).toBeVisible();
      await page.getByTestId("orch-approve").click();
      await expect(page.getByTestId("orch-approval")).toBeVisible();
      await expect(page.getByTestId("orch-approval-error")).toContainText("сервер не подтвердил");
      await expect(page.getByTestId("orch-approve")).toBeEnabled();
      await page.unroute("**/api/orchestrator/approvals/**/approve");
    }
  });

  test("204 на approve, deny и повтор закрывает карточку без ошибки", async ({ page }) => {
    const fulfill204 = async (route: { fulfill: (opts: { status: number; body: string }) => Promise<void> }) => {
      await route.fulfill({ status: 204, body: "" });
    };

    await openWithToken(page, "approval", "approve-204");
    await page.route("**/api/orchestrator/approvals/**/approve", fulfill204);
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);
    await page.unroute("**/api/orchestrator/approvals/**/approve");

    await openWithToken(page, "approval", "deny-204");
    await page.route("**/api/orchestrator/approvals/**/reject", fulfill204);
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-reject").click();
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);
    await page.unroute("**/api/orchestrator/approvals/**/reject");

    await openWithToken(page, "approvefail", "retry-204");
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("approve failed");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.route("**/api/orchestrator/approvals/**/approve", fulfill204);
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);
  });

  test("чужой change_id не закрывает карточку, свой закрывает", async ({ page }) => {
    await openWithToken(page, "idle", "chg-close");
    await emitBus(page, "chg-close", "approval_required", 1, {
      change_id: "chg-own",
      approval_id: "appr-other",
      title: "Своя карточка",
      reason: "нужно решение",
      risk: "low",
      action: "run_node",
    });
    const card = page.getByTestId("orch-approval");
    await expect(card).toContainText("Своя карточка");

    await emitBus(page, "chg-close", "agent_status", 1, {
      agent: "supervisor",
      status: "foreign-seen",
      decision: "approved",
      change_id: "chg-foreign",
      approval_id: "appr-other",
    });
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("foreign-seen");
    await expect(card).toBeVisible();

    await emitBus(page, "chg-close", "agent_status", 1, {
      agent: "supervisor",
      status: "missing-id",
      decision: "approved",
      approval_id: "appr-other",
    });
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("missing-id");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);

    await emitBus(page, "chg-close", "agent_status", 1, {
      agent: "supervisor",
      status: "own-seen",
      decision: "approved",
      change_id: "chg-own",
      approval_id: "appr-other",
    });
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("own-seen");
    await expect(card).toHaveCount(0);
  });

  test("неизвестная причина: нейтральная карточка с кнопками", async ({ page }) => {
    await openWithToken(page, "unknownreason", "unknown-1");
    await page.getByTestId("orch-composer").fill("проверь");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toContainText("Проверьте план");
    await expect(page.getByTestId("orch-approval")).toContainText("plan_sanity");
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
  });

  test("paid_outcome_unknown: карточка и три кнопки", async ({ page }) => {
    await openWithToken(page, "paidoutcome", "paid-card");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    const card = page.getByTestId("orch-approval");
    await expect(card).toHaveAttribute("data-terminal", "false");
    await expect(page.getByTestId("orch-paid-outcome")).toHaveText("Исход платного шага неизвестен");
    await expect(card).toContainText("Шаг мог выполниться, поэтому автоматически он не повторяется, проверьте результат.");
    await expect(page.getByTestId("orch-paid-done")).toHaveText("Шаг выполнен, продолжить");
    await expect(page.getByTestId("orch-paid-retry")).toHaveText("Перезапустить шаг");
    await expect(page.getByTestId("orch-paid-deny")).toHaveText("Остановить задачу");
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
    await expect(page.getByTestId("orch-step").filter({ hasText: "Платная генерация" })).toHaveAttribute("data-status", "running");
  });

  test("paid_outcome_unknown: шаг выполнен без повторного execute", async ({ page }) => {
    const posts: { url: string; body: unknown }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST") return;
      posts.push({ url: req.url(), body: req.postDataJSON() });
    });
    await openWithToken(page, "paidoutcome", "paid-done");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-done")).toBeVisible();
    await page.getByTestId("orch-paid-done").click();
    const paid = page.getByTestId("orch-step").filter({ hasText: "Платная генерация" });
    await expect(paid).toHaveAttribute("data-status", "done");
    await expect(page.getByTestId("orch-step").filter({ hasText: "Следующий шаг" })).toHaveAttribute("data-status", "running");
    await expect(page.getByTestId("orch-task-status")).toContainText("выполнение");
    const execute = posts.filter((item) => item.url.includes("/execute"));
    expect(execute).toEqual([]);
    const approvals = posts.filter((item) => item.url.includes("/approvals/"));
    expect(approvals).toHaveLength(1);
    expect(approvals[0]?.url).toContain("/approvals/orch-appr-1-1/approve");
    expect(approvals[0]?.body).toMatchObject({ decision: "approve", approval_id: "orch-appr-1-1" });
    expect(approvals[0]?.body).not.toHaveProperty("retry_grant");
  });

  test("paid_outcome_unknown: перезапуск показывает прогноз и шлёт одноразовый grant", async ({ page }) => {
    const posts: { url: string; body: unknown }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST") return;
      posts.push({ url: req.url(), body: req.postDataJSON() });
    });
    await openWithToken(page, "paidoutcome", "paid-retry");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-forecast")).toContainText("Прогноз расхода: ~$2.4");
    await page.getByTestId("orch-paid-retry-confirm").click();
    await expect(page.getByTestId("orch-paid-forecast")).toHaveCount(0);
    await expect(page.getByTestId("orch-step").filter({ hasText: "повтор после grant" })).toHaveAttribute("data-status", "running");
    const retry = posts.filter((item) => item.url.includes("/approve"));
    expect(retry).toHaveLength(2);
    expect(retry[0]?.url).toContain("/approvals/orch-appr-1-1/approve");
    expect(retry[0]?.body).toMatchObject({ decision: "retry_step", approval_id: "orch-appr-1-1" });
    expect(retry[0]?.body).not.toHaveProperty("retry_grant");
    expect(retry[1]?.url).toContain("/approvals/orch-appr-1-1/approve");
    expect(retry[1]?.body).toMatchObject({ retry_grant: true, approval_id: "orch-appr-1-1" });
    expect(retry[1]?.body).not.toHaveProperty("decision");
    expect(posts.filter((item) => item.url.includes("/execute"))).toEqual([]);
  });

  test("paid_outcome_unknown: остановить задачу — deny и failed", async ({ page }) => {
    const posts: { url: string; body: unknown }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST") return;
      posts.push({ url: req.url(), body: req.postDataJSON() });
    });
    await openWithToken(page, "paidoutcome", "paid-deny");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-deny").click();
    await expect(page.getByTestId("orch-step").filter({ hasText: "Платная генерация" })).toHaveAttribute("data-status", "failed");
    await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
    const denials = posts.filter((item) => item.url.includes("/approve"));
    expect(denials).toHaveLength(1);
    expect(denials[0]?.url).toContain("/approvals/orch-appr-1-1/approve");
    expect(denials[0]?.body).toMatchObject({ decision: "deny", approval_id: "orch-appr-1-1" });
    expect(denials[0]?.body).not.toHaveProperty("retry_grant");
    expect(posts.filter((item) => item.url.includes("/execute"))).toEqual([]);
  });

  test("paid_outcome_unknown: 404, 409 и 422 оставляют карточку", async ({ page }) => {
    const cases = [
      ["paid404", "approval not found"],
      ["paid409", "approval conflict"],
      ["paid422", "approval unprocessable"],
    ] as const;
    for (const [scenario, detail] of cases) {
      await openWithToken(page, scenario, scenario);
      await page.getByTestId("orch-composer").fill("платный шаг оборвался");
      await page.getByTestId("orch-send").click();
      await expect(page.getByTestId("orch-paid-done")).toBeVisible();
      for (const button of ["orch-paid-done", "orch-paid-deny", "orch-paid-retry"] as const) {
        await page.getByTestId(button).click();
        await expect(page.getByTestId("orch-approval-error")).toContainText(detail);
        await expect(page.getByTestId("orch-approval")).toBeVisible();
        await expect(page.getByTestId("orch-paid-forecast")).toHaveCount(0);
        await expect(page.getByTestId(button)).toBeEnabled();
        await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
      }
    }
  });

  test("paid_outcome_unknown: 409 на retry_grant оставляет прогноз", async ({ page }) => {
    await openWithToken(page, "paidgrant409", "paid-grant-409");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-forecast")).toContainText("Прогноз расхода: ~$2.4");
    await page.getByTestId("orch-paid-retry-confirm").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("approval conflict");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-paid-forecast")).toBeVisible();
    await expect(page.getByTestId("orch-paid-retry-confirm")).toBeEnabled();
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
  });

  test("paid_outcome_unknown: forecast_usd только конечное число >= 0", async ({ page }) => {
    const rejected = ["paidbad-nan", "paidbad-inf", "paidbad-ninf", "paidbad-neg", "paidbad-str", "paidbad-miss"] as const;
    for (const scenario of rejected) {
      await openWithToken(page, scenario, scenario);
      await page.getByTestId("orch-composer").fill("платный шаг оборвался");
      await page.getByTestId("orch-send").click();
      await page.getByTestId("orch-paid-retry").click();
      const panel = page.getByTestId("orch-paid-forecast");
      await expect(panel).toContainText("Прогноз расхода недоступен");
      await expect(panel).not.toContainText("~$0");
      await expect(panel).not.toContainText("~$2.4");
      await expect(page.getByTestId("orch-paid-retry-confirm")).toBeDisabled();
      await expect(page.getByTestId("orch-approval")).toBeVisible();
    }
    await openWithToken(page, "paidzero", "paid-zero");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-forecast")).toContainText("Прогноз расхода: ~$0");
    await expect(page.getByTestId("orch-paid-retry-confirm")).toBeEnabled();
  });

  test("paid_outcome_unknown: без поддержки перезапуска кнопка неактивна", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().includes("/approvals/")) posts.push(req.url());
    });
    await openWithToken(page, "paidoutcomeoff", "paid-off");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-retry")).toBeDisabled();
    await expect(page.getByTestId("orch-paid-retry-hint")).toContainText("Сервер не поддерживает перезапуск");
    await page.getByTestId("orch-paid-retry").click({ force: true });
    expect(posts).toEqual([]);
  });

  test("бюджет без change_id: решение не уходит, карточка закрывается статусом своей задачи", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && /\/approvals\/.+\/(approve|reject)/.test(req.url())) {
        posts.push(req.url());
      }
    });
    await openWithToken(page, "budgetnoid", "budget-noid");
    await page.getByTestId("orch-composer").fill("ещё бюджет");
    await page.getByTestId("orch-send").click();
    const card = page.getByTestId("orch-approval");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("orch-budget")).toContainText("Бюджет исчерпан");
    await expect(page.getByTestId("orch-approve")).toBeDisabled();
    await expect(page.getByTestId("orch-reject")).toBeDisabled();
    await expect(page.getByTestId("orch-approval-noid")).toHaveText("Решение недоступно: сервер не передал id");
    await expect(page.getByTestId("orch-kill")).toBeEnabled();

    await emitBus(page, "budget-noid", "agent_status", 2, {
      agent: "supervisor",
      status: "foreign-task",
      task_status: "executing",
    });
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("foreign-task");
    await expect(card).toBeVisible();

    await emitBus(
      page,
      "budget-noid",
      "agent_status",
      1,
      { agent: "supervisor", status: "foreign-session", task_status: "paused" },
      "other-session",
    );
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("foreign-session");
    await expect(card).toBeVisible();

    await emitBus(page, "budget-noid", "agent_status", 1, {
      agent: "supervisor",
      status: "own-move",
      task_status: "executing",
    });
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("own-move");
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    expect(posts).toEqual([]);

    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    await expect(page.getByTestId("orch-kill-status")).toBeVisible();
    expect(posts).toEqual([]);
  });

  test("paid_outcome_unknown без change_id: решение не уходит и карточка закрывается статусом задачи", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && /\/approvals\/.+\/approve/.test(req.url())) posts.push(req.url());
    });
    await openWithToken(page, "idle", "paid-noid");
    await emitBus(page, "paid-noid", "approval_required", 1, {
      reason: "paid_outcome_unknown",
      action: "run_node",
      forecast_usd: 2.4,
      decisions: [
        { decision: "approve", label: "Шаг выполнен, продолжить" },
        { decision: "retry_step", label: "Перезапустить шаг" },
        { decision: "deny", label: "Остановить задачу" },
      ],
    });
    await expect(page.getByTestId("orch-paid-outcome")).toBeVisible();
    await expect(page.getByTestId("orch-paid-done")).toBeDisabled();
    await expect(page.getByTestId("orch-paid-retry")).toBeDisabled();
    await expect(page.getByTestId("orch-paid-deny")).toBeDisabled();
    await expect(page.getByTestId("orch-approval-noid")).toHaveText("Решение недоступно: сервер не передал id");
    await expect(page.getByTestId("orch-kill")).toBeEnabled();
    expect(posts).toEqual([]);

    await emitBus(page, "paid-noid", "agent_status", 2, {
      agent: "supervisor",
      status: "foreign-task",
      task_status: "executing",
    });
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("foreign-task");
    await expect(page.getByTestId("orch-approval")).toBeVisible();

    await emitBus(page, "paid-noid", "agent_status", 1, {
      agent: "supervisor",
      status: "own-move",
      task_status: "executing",
    });
    await expect(page.getByTestId("orch-agent-supervisor")).toContainText("own-move");
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    expect(posts).toEqual([]);
  });
});
