import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import {
  bearerTargetAllowed,
  envOrchApiBase,
  normalizedOrchEnvApi,
  postOrchApprove,
  postOrchMessage,
  isSameOriginApiBase,
  orchAuthHeaders,
  orchTestModeEnabled,
  resolveOrchApiBase,
} from "../../src/lib/orchestratorApi";
import { initialOrchScreen, reduceOrchScreen } from "../../src/lib/orchestratorSession";

const SHOTS =
  process.env.ORCH_E2E_SHOTS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../test-results/orch-shots");

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

test("approve и message делят отказ 401", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: "no" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  const cfg = { apiBase: "", session: "s", token: "t", scenario: "" };
  try {
    await expect(postOrchApprove(cfg, { approval_id: "appr-1", task_id: null })).rejects.toMatchObject({ status: 401 });
    await expect(postOrchMessage(cfg, { text: "привет" })).rejects.toMatchObject({ status: 401 });
  } finally {
    globalThis.fetch = original;
  }
});

test("хвостовые слэши в NEXT_PUBLIC_ORCH_API снимаются все", () => {
  expect(normalizedOrchEnvApi("https://api.example.com/orch//")).toBe("https://api.example.com/orch");
  expect(normalizedOrchEnvApi("https://api.example.com/orch/")).toBe("https://api.example.com/orch");
  expect(normalizedOrchEnvApi("http://127.0.0.1:8799/")).toBe("http://127.0.0.1:8799");
});

test("отброшенный NEXT_PUBLIC_ORCH_API предупреждает один раз", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  const warnings: string[] = [];
  const original = console.warn;
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  };
  try {
    process.env.NEXT_PUBLIC_ORCH_API = "http://evil.example";
    expect(envOrchApiBase()).toBe("");
    expect(envOrchApiBase()).toBe("");
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("http://evil.example");
  } finally {
    console.warn = original;
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_API;
    else process.env.NEXT_PUBLIC_ORCH_API = previous;
  }
});

test("терминальная block-карточка закрывается следующей сменой статуса своей сессии", () => {
  const blocked = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "task_failed",
      task_id: 4,
      seq: 3,
      session_id: "own",
      payload: { error: "deletion_blocked" },
    },
  });
  const card = blocked.approvals.find((item) => item.id === "block-3");
  expect(card?.changeId).toBeNull();
  expect(card?.resolved).toBeNull();
  const done = reduceOrchScreen(blocked, {
    type: "event",
    event: {
      type: "task_done",
      task_id: 4,
      seq: 4,
      session_id: "own",
      payload: { summary: "закрыто" },
    },
  });
  expect(done.approvals.find((item) => item.id === "block-3")?.resolved).toBe("closed");
});

test("serverId ставится, когда id пришёл с сервера", () => {
  const both = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 1,
      session_id: "own",
      payload: { approval_id: "appr-other", change_id: "chg-own", reason: "risk", title: "Риск" },
    },
  });
  expect(both.approvals[0]?.serverId).toBe(true);
  expect(both.approvals[0]?.id).toBe("appr-other");
  expect(both.approvals[0]?.changeId).toBe("chg-own");
  const local = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 2,
      seq: 2,
      session_id: "own",
      payload: { reason: "risk", title: "Без id" },
    },
  });
  expect(local.approvals[0]?.serverId).toBe(false);
  expect(local.approvals[0]?.id).toBe("local-2");
});

test("карточка без change_id и без session_id не закрывается сменой статуса", () => {
  const opened = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 1,
      payload: { approval_id: "appr-nosession", reason: "budget_exhausted", title: "Бюджет" },
    },
  });
  expect(opened.approvals[0]?.sessionId).toBeNull();
  expect(opened.approvals[0]?.changeId).toBeNull();
  const moved = reduceOrchScreen(opened, {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 2,
      session_id: "own",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  expect(moved.tasks[0]?.status).toBe("executing");
  expect(moved.approvals[0]?.resolved).toBeNull();
});

test("поздний stop_key_set=false говорит, что остановка не случилась", () => {
  const stopped = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "kill_switch",
      task_id: 1,
      seq: 1,
      session_id: "own",
      payload: { stop_key_set: true, detail: "ключ встал" },
    },
  });
  expect(stopped.killNotice?.phase).toBe("stopped");
  const late = reduceOrchScreen(stopped, {
    type: "event",
    event: {
      type: "kill_switch",
      task_id: 1,
      seq: 2,
      session_id: "own",
      payload: { stop_key_set: false },
    },
  });
  expect(late.killNotice?.phase).toBe("stopped");
  expect(late.killNotice?.lateStopWarning).toBe(
    "Не остановлено: стоп-ключ не установлен, платный шаг может продолжаться",
  );
});

test("javascript: в NEXT_PUBLIC_ORCH_API пишется как некорректный адрес", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  const warnings: string[] = [];
  const original = console.warn;
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  };
  try {
    process.env.NEXT_PUBLIC_ORCH_API = "javascript:alert(1)";
    expect(envOrchApiBase()).toBe("");
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("некорректный адрес");
    expect(warnings[0]).not.toMatch(/отброшен: null$/);
  } finally {
    console.warn = original;
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_API;
    else process.env.NEXT_PUBLIC_ORCH_API = previous;
  }
});

test("чужое executing не закрепляет задачу: своё done закрывает карточку", () => {
  const foreign = reduceOrchScreen(initialOrchScreen("own"), {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 1,
      session_id: "other-session",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  expect(foreign.tasks).toHaveLength(0);

  const opened = reduceOrchScreen(foreign, {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 2,
      session_id: "own",
      payload: { approval_id: "appr-own", reason: "budget_exhausted", title: "Бюджет" },
    },
  });
  const executing = reduceOrchScreen(opened, {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 3,
      session_id: "own",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  const done = reduceOrchScreen(executing, {
    type: "event",
    event: {
      type: "task_done",
      task_id: 1,
      seq: 4,
      session_id: "own",
      payload: { summary: "готово" },
    },
  });
  expect(done.tasks[0]?.status).toBe("done");
  expect(done.approvals[0]?.resolved).toBe("closed");
});

test("событие без session_id не перезаписывает статус и не мешает закрыть карточку", () => {
  const opened = reduceOrchScreen(initialOrchScreen("own"), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 1,
      session_id: "own",
      payload: { approval_id: "appr-own", reason: "budget_exhausted", title: "Бюджет" },
    },
  });
  const bare = reduceOrchScreen(opened, {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 2,
      session_id: "",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  expect(bare.tasks[0]?.status).toBe("waiting_approval");
  expect(bare.approvals[0]?.resolved).toBeNull();
  expect(bare.tasks).toHaveLength(1);

  const executing = reduceOrchScreen(bare, {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 3,
      session_id: "own",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  expect(executing.tasks[0]?.status).toBe("executing");
  expect(executing.approvals[0]?.resolved).toBe("closed");
});

test("чужая сессия с тем же executing не прячет своё закрытие карточки", () => {
  const opened = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 1,
      session_id: "own",
      payload: {
        approval_id: "appr-own",
        reason: "budget_exhausted",
        title: "Бюджет",
      },
    },
  });
  expect(opened.tasks[0]?.status).toBe("waiting_approval");
  expect(opened.approvals[0]?.resolved).toBeNull();

  const foreign = reduceOrchScreen(opened, {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 2,
      session_id: "other-session",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  expect(foreign.tasks[0]?.status).toBe("waiting_approval");
  expect(foreign.approvals[0]?.resolved).toBeNull();

  const own = reduceOrchScreen(foreign, {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 3,
      session_id: "own",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  expect(own.tasks[0]?.status).toBe("executing");
  expect(own.approvals[0]?.resolved).toBe("closed");
});

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

  test("approval только с approval_id: кнопки активны и событие решения закрывает карточку", async ({ page }) => {
    const approveUrls: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().includes("/approve")) approveUrls.push(req.url());
    });
    await openWithToken(page, "approvallegacy", "approval-legacy");
    await page.getByTestId("orch-composer").fill("старый approval");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approve")).toBeEnabled();
    await expect(page.getByTestId("orch-reject")).toBeEnabled();
    await expect(page.getByTestId("orch-approval-noid")).toHaveCount(0);
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-task-summary")).toContainText("Одобрено");
    expect(approveUrls.some((url) => url.includes("/approvals/appr-legacy/approve"))).toBe(true);

    await emitBus(page, "approval-legacy", "approval_required", 2, {
      approval_id: "appr-old",
      title: "Старый формат",
      reason: "risk",
      action: "note",
      risk: "low",
    });
    await expect(page.getByTestId("orch-approve")).toBeEnabled();
    await expect(page.getByTestId("orch-approval-noid")).toHaveCount(0);
    await emitBus(page, "approval-legacy", "approval_decided", 2, {
      approval_id: "appr-old",
      decision: "approve",
    });
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
  });

  test("approve 404 и 409 оставляют карточку", async ({ page }) => {
    const cases = [
      ["approve404", "approval not found"],
      ["approve409", "approval conflict"],
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

  test("approve с change_id и approval_id уходит на change_id", async ({ page }) => {
    const urls: string[] = [];
    await page.route("**/api/orchestrator/approvals/**/approve", async (route) => {
      urls.push(route.request().url());
      await route.fulfill({ status: 204, body: "" });
    });
    await openWithToken(page, "idle", "chg-path");
    await emitBus(page, "chg-path", "approval_required", 1, {
      change_id: "chg-own",
      approval_id: "appr-other",
      title: "Два id",
      reason: "нужно решение",
      risk: "low",
      action: "run_node",
    });
    await expect(page.getByTestId("orch-approve")).toBeEnabled();
    await page.getByTestId("orch-approve").click();
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain("/approvals/chg-own/approve");
    expect(urls[0]).not.toContain("appr-other");
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
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
});
