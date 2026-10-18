import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import {
  bearerTargetAllowed,
  envOrchApiBase,
  isSameOriginApiBase,
  normalizedOrchEnvApi,
  orchAuthHeaders,
  orchTestModeEnabled,
  postOrchKill,
  readOrchBoot,
  postOrchMessage,
  resolveOrchApiBase,
} from "../../src/lib/orchestratorApi";
import { initialOrchScreen, reduceOrchScreen } from "../../src/lib/orchestratorSession";

test("postOrchKill принимает 200 и null", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response("null", { status: 200, headers: { "Content-Type": "application/json" } });
  try {
    const result = await postOrchKill(
      { apiBase: "", session: "studio", token: "tok", scenario: "kill" },
      { session_id: "studio", reason: "kill switch из чата" },
    );
    expect(result).toEqual({ status: 200, body: null });
  } finally {
    globalThis.fetch = original;
  }
});

test("терминальное событие чужой сессии не закрывает свою задачу", () => {
  const opened = reduceOrchScreen(initialOrchScreen("own"), {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 1,
      session_id: "own",
      payload: { agent: "supervisor", status: "running", task_status: "executing" },
    },
  });
  const foreign = reduceOrchScreen(opened, {
    type: "event",
    event: {
      type: "task_done",
      task_id: 1,
      seq: 2,
      session_id: "other",
      payload: { summary: "чужое" },
    },
  });
  expect(foreign.tasks[0]?.status).toBe("executing");
  expect(foreign.tasks[0]?.summary).toBe("");
  const failed = reduceOrchScreen(foreign, {
    type: "event",
    event: {
      type: "task_failed",
      task_id: 1,
      seq: 3,
      session_id: "other",
      payload: { error: "чужая ошибка" },
    },
  });
  expect(failed.tasks[0]?.status).toBe("executing");
  expect(failed.tasks[0]?.error).toBe("");
  const own = reduceOrchScreen(failed, {
    type: "event",
    event: {
      type: "task_done",
      task_id: 1,
      seq: 4,
      session_id: "own",
      payload: { summary: "своё" },
    },
  });
  expect(own.tasks[0]?.status).toBe("done");
  expect(own.tasks[0]?.summary).toBe("своё");
});

test("ownSession не замораживается первым рендером, boot.session не пустой", () => {
  expect(readOrchBoot().session.length).toBeGreaterThan(0);
  const first = initialOrchScreen(null);
  expect(first.ownSession).toBeNull();
  const next = reduceOrchScreen(first, { type: "own-session", session: "paid" });
  expect(next.ownSession).toBe("paid");
  const foreign = reduceOrchScreen(next, {
    type: "event",
    event: {
      type: "agent_status",
      task_id: 1,
      seq: 1,
      session_id: "other",
      payload: { agent: "supervisor", status: "running", task_status: "done" },
    },
  });
  expect(foreign.ownSession).toBe("paid");
  expect(foreign.tasks[0]?.status).not.toBe("done");
});

const SHOTS =
  process.env.ORCH_E2E_SHOTS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../test-results/orch-shots");

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

test("чужая сессия, пришедшая первой, не занимает статус своей", () => {
  let seq = 0;
  let state = initialOrchScreen("own");
  const push = (session: string, status: string) => {
    seq += 1;
    state = reduceOrchScreen(state, {
      type: "event",
      event: {
        type: "agent_status",
        task_id: 1,
        seq,
        session_id: session,
        payload: { agent: "supervisor", status: "running", task_status: status },
      },
    });
  };
  push("foreign", "executing");
  expect(state.tasks[0]?.status).toBe("queued");
  expect(state.tasks[0]?.statusSession).toBe("own");
  expect(state.tasks[0]?.statusBySession.foreign).toBe("executing");
  expect(state.tasks[0]?.statusBySession.own).toBeUndefined();
  push("own", "paused");
  expect(state.tasks[0]?.status).toBe("paused");
  expect(state.tasks[0]?.statusSession).toBe("own");
  expect(state.tasks[0]?.statusBySession.own).toBe("paused");
});

test("событие без сессии не оставляет экран на paused после своего executing", () => {
  let seq = 0;
  let state = initialOrchScreen("own");
  const push = (session: string, status: string) => {
    seq += 1;
    state = reduceOrchScreen(state, {
      type: "event",
      event: {
        type: "agent_status",
        task_id: 1,
        seq,
        session_id: session,
        payload: { agent: "supervisor", status: "running", task_status: status },
      },
    });
  };
  push("own", "executing");
  expect(state.tasks[0]?.status).toBe("executing");
  push("", "paused");
  expect(state.tasks[0]?.status).toBe("executing");
  expect(state.tasks[0]?.statusBySession.own).toBe("executing");
  push("own", "executing");
  expect(state.tasks[0]?.status).toBe("executing");
  expect(state.tasks[0]?.statusSession).toBe("own");
});

test("чужая сессия не съедает переход статуса своей задачи", () => {
  let seq = 0;
  let state = initialOrchScreen();
  const push = (type: string, session: string, payload: Record<string, unknown>) => {
    seq += 1;
    state = reduceOrchScreen(state, {
      type: "event",
      event: { type, task_id: 1, seq, session_id: session, payload },
    });
  };
  push("agent_status", "own", { agent: "supervisor", status: "running", task_status: "planning" });
  push("approval_required", "own", { reason: "budget_exhausted", approval_id: "ap-budget" });
  expect(state.approvals).toHaveLength(1);
  expect(state.approvals[0]?.resolved).toBeNull();
  expect(state.tasks[0]?.status).toBe("waiting_approval");

  push("agent_status", "foreign", {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
  });
  expect(state.approvals[0]?.resolved).toBeNull();
  expect(state.tasks[0]?.status).toBe("waiting_approval");
  expect(state.tasks[0]?.statusBySession.own).toBe("waiting_approval");
  expect(state.tasks[0]?.statusBySession.foreign).toBe("executing");

  push("agent_status", "own", { agent: "supervisor", status: "running", task_status: "executing" });
  expect(state.approvals[0]?.resolved).toBe("closed");
  expect(state.tasks[0]?.status).toBe("executing");
});

test("чужая смена статуса закрывает только карточку своей сессии", () => {
  let seq = 0;
  let state = initialOrchScreen("own");
  const push = (type: string, session: string, payload: Record<string, unknown>) => {
    seq += 1;
    state = reduceOrchScreen(state, {
      type: "event",
      event: { type, task_id: 1, seq, session_id: session, payload },
    });
  };
  push("approval_required", "own", { reason: "budget_exhausted", approval_id: "ap-own" });
  push("approval_required", "foreign", { reason: "budget_exhausted", approval_id: "ap-foreign" });
  expect(state.approvals.find((item) => item.id === "ap-own")?.resolved).toBeNull();
  expect(state.approvals.find((item) => item.id === "ap-foreign")?.resolved).toBeNull();
  expect(state.tasks[0]?.status).toBe("waiting_approval");

  push("agent_status", "foreign", { agent: "supervisor", status: "running", task_status: "executing" });
  expect(state.approvals.find((item) => item.id === "ap-own")?.resolved).toBeNull();
  expect(state.approvals.find((item) => item.id === "ap-foreign")?.resolved).toBe("closed");
  expect(state.tasks[0]?.status).toBe("waiting_approval");
  expect(state.tasks[0]?.statusBySession.foreign).toBe("executing");
});

test("автозакрытие по статусу не трогает терминальную карточку", () => {
  let seq = 0;
  let state = initialOrchScreen();
  const push = (type: string, payload: Record<string, unknown>) => {
    seq += 1;
    state = reduceOrchScreen(state, {
      type: "event",
      event: { type, task_id: 1, seq, session_id: "own", payload },
    });
  };
  push("task_failed", { error: "deletion_blocked" });
  const terminal = state.approvals.find((item) => item.reason === "deletion_blocked");
  expect(terminal?.resolved).toBeNull();
  push("approval_required", { reason: "budget_exhausted", approval_id: "ap-budget" });
  expect(state.approvals.find((item) => item.reason === "deletion_blocked")?.resolved).toBeNull();
  expect(state.approvals.find((item) => item.reason === "budget_exhausted")?.resolved).toBeNull();
  push("agent_status", { agent: "supervisor", status: "running", task_status: "executing" });
  expect(state.approvals.find((item) => item.reason === "deletion_blocked")?.resolved).toBeNull();
  expect(state.approvals.find((item) => item.reason === "budget_exhausted")?.resolved).toBe("closed");
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

test("отброшенный NEXT_PUBLIC_ORCH_API предупреждает один раз на серию запросов", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  const warnings: string[] = [];
  const original = console.warn;
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map((item) => String(item)).join(" "));
  };
  const pageOrigin = "http://127.0.0.1:3100";
  try {
    process.env.NEXT_PUBLIC_ORCH_API = "http://evil.example/api";
    expect(envOrchApiBase()).toBe("");
    expect(resolveOrchApiBase(null, pageOrigin)).toBe("");
    expect(bearerTargetAllowed("http://evil.example/api", pageOrigin)).toBe(false);
    expect(envOrchApiBase()).toBe("");
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("NEXT_PUBLIC_ORCH_API отброшен");
  } finally {
    console.warn = original;
    process.env.NEXT_PUBLIC_ORCH_API = "https://api.example.com";
    envOrchApiBase();
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_API;
    else process.env.NEXT_PUBLIC_ORCH_API = previous;
  }
});

test("JSON не-объект в ответе message не называется пустым", async () => {
  const previous = globalThis.fetch;
  const cfg = { apiBase: "", session: "s", token: "t", scenario: "idle" };
  try {
    globalThis.fetch = async () => new Response("[]", { status: 200 });
    await expect(postOrchMessage(cfg, { text: "hi" })).rejects.toThrow("ответ не объект");
    globalThis.fetch = async () => new Response("  ", { status: 200 });
    await expect(postOrchMessage(cfg, { text: "hi" })).rejects.toThrow("пустой ответ");
  } finally {
    globalThis.fetch = previous;
  }
});

test("postOrchKill на 204 возвращает status и body null", async () => {
  const previous = globalThis.fetch;
  const cfg = { apiBase: "", session: "s", token: "t", scenario: "idle" };
  try {
    globalThis.fetch = async () => new Response(null, { status: 204 });
    await expect(postOrchKill(cfg, { session_id: "s" })).resolves.toEqual({ status: 204, body: null });
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ ok: true, stop_key_set: true, detail: "ключ установлен" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    await expect(postOrchKill(cfg, { session_id: "s" })).resolves.toEqual({
      status: 200,
      body: { ok: true, stop_key_set: true, detail: "ключ установлен" },
    });
  } finally {
    globalThis.fetch = previous;
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

test("хвостовые CR и LF в NEXT_PUBLIC_ORCH_API не отбрасывают адрес", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  try {
    expect(normalizedOrchEnvApi("http://127.0.0.1:8799\r\n")).toBe("http://127.0.0.1:8799");
    expect(normalizedOrchEnvApi("http://127.0.0.1:8799\n")).toBe("http://127.0.0.1:8799");
    expect(normalizedOrchEnvApi("http://127.0.0.1:87\n99")).toBeNull();
    process.env.NEXT_PUBLIC_ORCH_API = "http://127.0.0.1:8799\r\n";
    expect(envOrchApiBase()).toBe("http://127.0.0.1:8799");
    process.env.NEXT_PUBLIC_ORCH_API = "https://api.example.com/orch\n";
    expect(envOrchApiBase()).toBe("https://api.example.com/orch");
    process.env.NEXT_PUBLIC_ORCH_API = "http://127.0.0.1:8799\t";
    expect(envOrchApiBase()).toBe("");
  } finally {
    process.env.NEXT_PUBLIC_ORCH_API = "https://api.example.com";
    envOrchApiBase();
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_API;
    else process.env.NEXT_PUBLIC_ORCH_API = previous;
  }
});

test("data, localhost., 127.0.0.2, управляющие и обратный слэш не становятся базой API", () => {
  expect(new URL("http://[::1]:8799/api").hostname).toBe("[::1]");
  expect(normalizedOrchEnvApi("http://[::1]:8799")).toBe("http://[::1]:8799");
  expect(isSameOriginApiBase("http://[::1]:8799/api", "http://[::1]:8799")).toBe(true);

  const pageOrigin = "https://host";
  const sameOriginRejected = [
    "data:",
    "data:text/html,/api",
    "http://localhost./api",
    "http://127.0.0.2/api",
    "https://host/api\n",
    "https://host/api\u0001",
    "https://host\\@evil.com/api",
    "http://127.0.0.1\\@evil.com/api",
    "https://host/api\\@evil",
  ];
  for (const value of sameOriginRejected) {
    expect(isSameOriginApiBase(value, pageOrigin), value).toBe(false);
    expect(isSameOriginApiBase(value, "http://127.0.0.1:8799"), value).toBe(false);
  }

  const previous = process.env.NEXT_PUBLIC_ORCH_API;
  const envRejected = [
    "data:",
    "http://localhost.:8799",
    "http://127.0.0.2:8799",
    "http://127.0.0.1:87\n99",
    "https://api.example.com/api\u0001",
    "http://127.0.0.1\\@evil.com/api",
    "https://user\\@host/api",
  ];
  try {
    for (const value of envRejected) {
      process.env.NEXT_PUBLIC_ORCH_API = value;
      expect(normalizedOrchEnvApi(value), value).toBeNull();
      expect(envOrchApiBase(), value).toBe("");
    }
  } finally {
    process.env.NEXT_PUBLIC_ORCH_API = "https://api.example.com";
    envOrchApiBase();
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

  test("approve 404 и 409 оставляют карточку", async ({ page }) => {
    const cases = [
      ["approve404", "неизвестный change_id"],
      ["approve409", "change_id не текущий"],
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
      (req) => req.method() === "POST" && new URL(req.url()).pathname === "/api/orchestrator/chat/budget-grant/approve",
    );
    await page.getByTestId("orch-approve").click();
    const body = (await posted).postDataJSON() as { change_id?: string; decision?: string };
    expect(body).toEqual({ change_id: "orch-appr-4-1", decision: "approve" });
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    await expect(page.getByTestId("orch-transcript")).toContainText("Суб-бюджет добавлен");
    await expect(page.getByTestId("orch-task-status")).toContainText("выполнение");
  });

  test("решения approval зависят от причины", async ({ page }) => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST") return;
      const pathname = new URL(req.url()).pathname;
      if (!pathname.endsWith("/approve") && !pathname.endsWith("/project")) return;
      posts.push({ url: req.url(), body: (req.postDataJSON() ?? {}) as Record<string, unknown> });
    });

    await openWithToken(page, "risk", "reason-risk");
    await page.getByTestId("orch-composer").fill("риск");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await expect(page.getByTestId("orch-reject")).toBeVisible();
    await expect(page.getByTestId("orch-retry")).toHaveCount(0);
    await page.getByTestId("orch-approve").click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]?.body).toEqual({ change_id: "orch-appr-5-1", decision: "approve" });

    posts.length = 0;
    await openWithToken(page, "destructive", "reason-destructive");
    await page.getByTestId("orch-composer").fill("разрушение");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await expect(page.getByTestId("orch-retry")).toHaveCount(0);
    await page.getByTestId("orch-reject").click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]?.body).toEqual({ change_id: "orch-appr-2-1", decision: "deny" });

    posts.length = 0;
    await openWithToken(page, "partialwrite", "reason-partial");
    await page.getByTestId("orch-composer").fill("частичная запись");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await expect(page.getByTestId("orch-reject")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]?.body).toEqual({ change_id: "orch-appr-8-1", decision: "approve" });

    posts.length = 0;
    await openWithToken(page, "paidunknown", "reason-paid");
    await page.getByTestId("orch-composer").fill("платный исход");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    await expect(page.getByTestId("orch-retry")).toBeVisible();
    await page.getByTestId("orch-retry").click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]?.body).toEqual({
      change_id: "orch-appr-3-1",
      decision: "retry_step",
      retry_grant: true,
    });
    expect(JSON.stringify(posts[0]?.body)).not.toContain("cc-step-88");

    posts.length = 0;
    await openWithToken(page, "paidunknown", "reason-paid-deny");
    await page.getByTestId("orch-composer").fill("платный исход");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-reject").click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]?.body).toEqual({ change_id: "orch-appr-3-1", decision: "deny" });

    posts.length = 0;
    await openWithToken(page, "missingproject", "reason-missing");
    await page.getByTestId("orch-composer").fill("нет проекта");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-missing-project")).toBeVisible();
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    await expect(page.getByTestId("orch-reject")).toHaveCount(0);
    await expect(page.getByTestId("orch-retry")).toHaveCount(0);
    await page.getByTestId("orch-project-id").fill("13");
    await page.getByTestId("orch-bind-project").click();
    await expect.poll(() => posts.length).toBe(1);
    expect(new URL(posts[0]!.url).pathname).toBe("/api/orchestrator/chat/reason-missing/project");
    expect(posts[0]?.body).toEqual({ project_id: 13 });
  });

  test("step_change_id не подставляется в /approve", async ({ page }) => {
    const posts: Record<string, unknown>[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST") return;
      if (!new URL(req.url()).pathname.endsWith("/approve")) return;
      posts.push((req.postDataJSON() ?? {}) as Record<string, unknown>);
    });

    await openWithToken(page, "stepchange", "step-only");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-approval-noid")).toContainText("orch-appr");
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    expect(posts).toEqual([]);

    posts.length = 0;
    await openWithToken(page, "stepchangeboth", "step-both");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    await expect(page.getByTestId("orch-reject")).toBeVisible();
    await page.getByTestId("orch-reject").click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]?.change_id).toBe("orch-appr-9-3");
    expect(posts[0]?.decision).toBe("deny");
    expect(JSON.stringify(posts[0])).not.toContain("cc-step-88");
  });

  test("коды approve: отказ без автоповтора, 409 читает snapshot", async ({ page }) => {
    const cases = [
      [401, "нет токена или токен неверный"],
      [400, "плохой session_id"],
      [422, "тело не по схеме"],
      [404, "неизвестный change_id"],
      [403, "not_in_whitelist"],
      [409, "change_id не текущий"],
    ] as const;
    for (const [status, text] of cases) {
      let posts = 0;
      let snapshots = 0;
      await page.route("**/api/orchestrator/chat/*/approve", async (route) => {
        if (route.request().method() !== "POST") {
          await route.continue();
          return;
        }
        posts += 1;
        await route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify({ detail: "raw" }),
        });
      });
      await page.route("**/api/orchestrator/chat/*/status", async (route) => {
        if (route.request().method() !== "GET") {
          await route.continue();
          return;
        }
        snapshots += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            session: "snap",
            task_id: 1,
            status: "failed",
            resume_allowed: false,
          }),
        });
      });
      await openWithToken(page, "budget", `code-${status}`);
      await page.getByTestId("orch-composer").fill("ещё кадры");
      await page.getByTestId("orch-send").click();
      await expect(page.getByTestId("orch-approve")).toBeVisible();
      await page.getByTestId("orch-approve").click();
      await expect(page.getByText(text).first()).toBeVisible();
      await expect.poll(() => posts).toBe(1);
      if (status === 409) {
        await expect.poll(() => snapshots).toBeGreaterThan(0);
        await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");
      } else {
        expect(snapshots).toBe(0);
      }
      await page.unroute("**/api/orchestrator/chat/*/approve");
      await page.unroute("**/api/orchestrator/chat/*/status");
    }
  });

  test("approve 204 не успех: нужен JSON 200", async ({ page }) => {
    let posts = 0;
    await page.route("**/api/orchestrator/chat/*/approve", async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      posts += 1;
      await route.fulfill({ status: 204, body: "" });
    });
    await openWithToken(page, "budget", "approve-204");
    await page.getByTestId("orch-composer").fill("ещё кадры");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("JSON 200");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-approve")).toBeEnabled();
    expect(posts).toBe(1);
  });

  test("approve и deny уходят на chat/approve телом ApproveIn", async ({ page }) => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST") return;
      const pathname = new URL(req.url()).pathname;
      if (!pathname.endsWith("/approve") && !pathname.endsWith("/reject")) return;
      posts.push({ url: req.url(), body: (req.postDataJSON() ?? {}) as Record<string, unknown> });
    });

    await openWithToken(page, "budget", "wire-ok");
    await page.getByTestId("orch-composer").fill("ещё кадры");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approve")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect.poll(() => posts.length).toBe(1);
    const approve = posts[0];
    expect(approve).toBeTruthy();
    expect(new URL(approve!.url).pathname).toBe("/api/orchestrator/chat/wire-ok/approve");
    expect(approve!.body).toEqual({ change_id: "orch-appr-4-1", decision: "approve" });
    expect(approve!.body).not.toHaveProperty("approval_id");
    expect(approve!.body).not.toHaveProperty("task_id");

    posts.length = 0;
    await openWithToken(page, "budget", "wire-no");
    await page.getByTestId("orch-composer").fill("ещё кадры");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-reject")).toBeVisible();
    await page.getByTestId("orch-reject").click();
    await expect.poll(() => posts.length).toBe(1);
    const deny = posts[0];
    expect(deny).toBeTruthy();
    expect(new URL(deny!.url).pathname).toBe("/api/orchestrator/chat/wire-no/approve");
    expect(deny!.body).toEqual({ change_id: "orch-appr-4-1", decision: "deny" });
    expect(Object.keys(deny!.body).sort()).toEqual(["change_id", "decision"]);
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
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    await expect(page.getByTestId("orch-reject")).toHaveCount(0);
    await expect(page.getByTestId("orch-bind-project")).toBeVisible();
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
      await page.route("**/api/orchestrator/chat/**/approve", async (route) => {
        await route.fulfill({ status, body: "" });
      });
      await page.getByTestId("orch-composer").fill("запусти ноду");
      await page.getByTestId("orch-send").click();
      await expect(page.getByTestId("orch-approval")).toBeVisible();
      await page.getByTestId("orch-approve").click();
      await expect(page.getByTestId("orch-approval")).toBeVisible();
      await expect(page.getByTestId("orch-approval-error")).toContainText("сервер не подтвердил");
      await expect(page.getByTestId("orch-approve")).toBeEnabled();
      await page.unroute("**/api/orchestrator/chat/**/approve");
    }
  });

  test("204 на approve, deny и повтор закрывает карточку без ошибки", async ({ page }) => {
    const fulfill204 = async (route: { fulfill: (opts: { status: number; body: string }) => Promise<void> }) => {
      await route.fulfill({ status: 204, body: "" });
    };

    await openWithToken(page, "approval", "approve-204");
    await page.route("**/api/orchestrator/chat/**/approve", fulfill204);
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);
    await page.unroute("**/api/orchestrator/chat/**/approve");

    await openWithToken(page, "approval", "deny-204");
    await page.route("**/api/orchestrator/chat/**/approve", fulfill204);
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-reject").click();
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);
    await page.unroute("**/api/orchestrator/chat/**/approve");

    await openWithToken(page, "approvefail", "retry-204");
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("approve failed");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.route("**/api/orchestrator/chat/**/approve", fulfill204);
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
    await expect(page.getByTestId("orch-approval-hide")).toBeVisible();
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

  test("карточка без change_id: Скрыть закрывает её и не шлёт решение", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && /\/approvals\/.+\/(approve|reject)/.test(req.url())) {
        posts.push(req.url());
      }
    });
    await openWithToken(page, "budgetnoid", "budget-hide");
    await page.getByTestId("orch-composer").fill("ещё бюджет");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-approve")).toBeDisabled();
    await page.getByTestId("orch-approval-hide").click();
    await expect(page.getByTestId("orch-approval")).toHaveCount(0);
    expect(posts).toEqual([]);
  });

  test("kill: двойной клик пока запрос в полёте — один POST", async ({ page }) => {
    let posts = 0;
    await openWithToken(page, "idle", "kill-dbl");
    await page.route("**/api/orchestrator/kill", async (route) => {
      posts += 1;
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, stop_key_set: true, detail: "ключ установлен" }),
      });
    });
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").waitFor();
    await page.evaluate(() => {
      const button = document.querySelector("[data-testid='orch-kill-confirm']");
      if (!(button instanceof HTMLButtonElement)) throw new Error("нет кнопки подтверждения");
      button.click();
      button.click();
    });
    await expect(page.getByTestId("orch-kill-status")).toHaveAttribute("data-kill-phase", "stopped");
    expect(posts).toBe(1);
  });
});
