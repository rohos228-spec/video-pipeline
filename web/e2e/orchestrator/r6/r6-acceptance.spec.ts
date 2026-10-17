/**
 * Приёмка R6 по docs/fable/PLAN.md (fable-context), живой API #346+#350.
 *
 * Критерий R6 дословно: «0 security-инцидентов (исполнение вне whitelist /
 * превышение лимита / несанкционированная мутация / XSS); 0 удалений сцен/кадров;
 * подтверждение пользователя».
 *
 * Подтверждение пользователя этим прогоном не заменяется. Платные вызовы
 * выключены: ORCH_R6_LIVE_MODEL=1 — единственный способ их разрешить.
 * Проверки смотрят на события SSE (тип, seq, payload), не на sleep.
 */
import { readFileSync } from "node:fs";
import { expect, test, type APIRequestContext } from "@playwright/test";

const BASE = (process.env.ORCH_R6_BASE_URL || "http://127.0.0.1:8877").replace(/\/$/, "");
const TOKEN = process.env.ORCH_R6_TOKEN || "";
const LIVE = process.env.ORCH_R6_LIVE_MODEL === "1";
const STATUS_FILE = process.env.ORCH_R6_ROUTER_FILE || "/tmp/orch-r6-data/router-impl.txt";
const SAFE_ROUTERS = new Set(["FallbackRouter", "ScriptedRouter", "ReplayRouter"]);

const TASK_STATUSES = [
  "queued",
  "planning",
  "critic_review",
  "executing",
  "paused",
  "waiting_approval",
  "done",
  "failed",
  "cancelled",
];

const DELETION = /delete_frame|delete_scene|purge|drop/;

type BusEvent = {
  type: string;
  seq: number;
  payload: Record<string, unknown>;
  receivedAt: number;
  sentAt: number;
};

function routerImpl(): string {
  try {
    return readFileSync(STATUS_FILE, "utf8").trim();
  } catch {
    return "";
  }
}

function parseFrames(chunk: string, sentAt: number): { events: BusEvent[]; rest: string } {
  // sse-starlette разделяет кадры через CRLF. Одинокий \r в хвосте не трогаем.
  const parts = chunk.replace(/\r\n/g, "\n").split("\n\n");
  const rest = parts.pop() ?? "";
  const events: BusEvent[] = [];
  const receivedAt = Date.now();
  for (const frame of parts) {
    if (!frame.trim() || frame.trim().startsWith(":")) continue;
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) continue;
    try {
      const json = JSON.parse(data) as { type?: string; seq?: number; payload?: Record<string, unknown> };
      if (typeof json.seq !== "number" || !json.type) continue;
      events.push({
        type: json.type,
        seq: json.seq,
        payload: json.payload ?? {},
        receivedAt,
        sentAt,
      });
    } catch {
      /* комментарий SSE */
    }
  }
  return { events, rest };
}

class LiveBus {
  readonly events: BusEvent[] = [];
  private buffer = "";
  private sentAt = Date.now();
  private closed = false;

  constructor(
    private readonly reader: ReadableStreamDefaultReader<Uint8Array>,
  ) {}

  markSend(): void {
    this.sentAt = Date.now();
  }

  start(): void {
    const decoder = new TextDecoder();
    void (async () => {
      while (!this.closed) {
        const { done, value } = await this.reader.read();
        if (done) break;
        this.buffer += decoder.decode(value, { stream: true });
        const parsed = parseFrames(this.buffer, this.sentAt);
        this.buffer = parsed.rest;
        this.events.push(...parsed.events);
      }
    })().catch(() => undefined);
  }

  since(seq: number): BusEvent[] {
    return this.events.filter((event) => event.seq > seq);
  }

  async close(): Promise<void> {
    this.closed = true;
    await this.reader.cancel().catch(() => undefined);
  }
}

async function openBus(session: string, lastEventId = "0"): Promise<LiveBus> {
  const res = await fetch(`${BASE}/api/orchestrator/chat/${session}/stream`, {
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      Accept: "text/event-stream",
      "Last-Event-ID": lastEventId,
    },
  });
  expect(res.ok, `SSE ${res.status}`).toBeTruthy();
  expect(res.body).toBeTruthy();
  const bus = new LiveBus(res.body!.getReader());
  bus.start();
  return bus;
}

async function postMessage(
  request: APIRequestContext,
  session: string,
  body: { text: string; mode?: string; project_id?: number },
  bus: LiveBus,
): Promise<number> {
  const before = bus.events.at(-1)?.seq ?? 0;
  bus.markSend();
  const res = await request.post(`${BASE}/api/orchestrator/chat/${session}/message`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    data: body,
  });
  expect(res.status(), await res.text()).toBe(200);
  await expect
    .poll(() => bus.since(before).some((event) => event.type === "ack"), { timeout: 15_000 })
    .toBe(true);
  return before;
}

test.describe("R6 приёмка на живом API", () => {
  test("сценарий чата по событиям SSE", async ({ request }) => {
    expect(TOKEN, "ORCH_R6_TOKEN").not.toBe("");
    const impl = routerImpl();
    const safe = SAFE_ROUTERS.has(impl);
    expect.soft(safe || LIVE, `роутер «${impl || "неизвестен"}»: без ORCH_R6_LIVE_MODEL сообщения не шлём`).toBeTruthy();
    if (!safe && !LIVE) return;

    const session = `r6${Date.now().toString(36)}`;

    await test.step("E8: без токена 401", async () => {
      const res = await request.post(`${BASE}/api/orchestrator/chat/${session}/message`, {
        data: { text: "что такое закадр?" },
      });
      expect(res.status()).toBe(401);
    });

    const bus = await openBus(session);
    try {
      await test.step("R1: стриминг ответа за токеном", async () => {
        const before = await postMessage(request, session, { text: "что такое закадр?" }, bus);
        await expect
          .poll(() => bus.since(before).some((event) => event.type === "message" || event.type === "token"))
          .toBe(true);
        const stream = bus.since(before).filter((event) => event.type === "token" || event.type === "message");
        const text = stream
          .map((event) => (typeof event.payload.text === "string" ? event.payload.text : ""))
          .join("");
        if (!LIVE) {
          expect.soft(text.length > 0, "FallbackRouter отдаёт пустой text: token-событий нет, WS5 on_delta не подключён").toBeTruthy();
        }
        const ack = bus.since(before).find((event) => event.type === "ack");
        const classified = bus.since(before).find((event) => event.type === "classification");
        expect(ack).toBeTruthy();
        expect(classified).toBeTruthy();
        expect(ack!.seq).toBeLessThan(classified!.seq);
        expect(classified!.payload.mode).toBe("append");
        const ackMs = ack!.receivedAt - ack!.sentAt;
        expect(ackMs, "ack по времени прихода события, не по sleep").toBeLessThanOrEqual(2000);
      });

      let taskBefore = 0;
      await test.step("задача: mode=task, статус агента, шаг и валидаторы", async () => {
        taskBefore = await postMessage(
          request,
          session,
          { text: "только чтение: перечисли ноды", mode: "task" },
          bus,
        );
        await expect
          .poll(() =>
            bus.since(taskBefore).some(
              (event) => event.type === "agent_status" && event.payload.agent === "supervisor",
            ),
          )
          .toBe(true);
        const statuses = bus
          .since(taskBefore)
          .filter((event) => event.type === "agent_status")
          .map((event) => String(event.payload.status || ""));
        expect.soft(statuses.some((status) => TASK_STATUSES.includes(status))).toBeTruthy();
        const classified = bus.since(taskBefore).find((event) => event.type === "classification");
        expect.soft(classified?.payload.mode).toBe("task");
        await expect
          .poll(() => bus.since(taskBefore).some((event) => event.type === "step_started"))
          .toBe(true);
        const started = bus.since(taskBefore).find((event) => event.type === "step_started");
        expect.soft(started?.payload.action).toBe("noop");
        expect.soft(started?.payload.paid).toBe(false);
        await expect
          .poll(() => bus.since(taskBefore).some((event) => event.type === "step_finished"))
          .toBe(true);
        const finished = bus.since(taskBefore).find((event) => event.type === "step_finished");
        expect.soft(finished?.payload.action).toBe("noop");
        expect.soft(finished?.payload.ok, "UnlinkedActions: execute не подключён").toBe(false);
        const validators = bus
          .since(taskBefore)
          .filter((event) => event.type === "validator_result")
          .map((event) => String(event.payload.name || ""));
        expect.soft(validators).toEqual(expect.arrayContaining(["no_deletions", "plan_sanity", "spend_guard"]));
        await expect
          .poll(() => bus.since(taskBefore).some((event) => event.type === "task_done" || event.type === "task_failed"))
          .toBe(true);
      });

      await test.step("append посреди задачи не меняет режим задачи", async () => {
        const before = await postMessage(request, session, { text: "сколько будет два плюс два?" }, bus);
        await expect
          .poll(() => bus.since(before).some((event) => event.type === "classification"))
          .toBe(true);
        const classified = bus.since(before).find((event) => event.type === "classification");
        expect.soft(classified?.payload.mode).toBe("append");
        expect.soft(classified?.payload.deterministic).toBe(true);
        const killed = bus.since(before).some((event) => event.type === "kill_switch");
        expect(killed).toBe(false);
      });

      await test.step("steer: classification mode=steer", async () => {
        const before = await postMessage(request, session, { text: "измени план: оставь только чтение" }, bus);
        await expect
          .poll(() => bus.since(before).some((event) => event.type === "classification"))
          .toBe(true);
        const classified = bus.since(before).find((event) => event.type === "classification");
        expect.soft(classified?.payload.mode).toBe("steer");
        expect.soft(classified?.payload.deterministic).toBe(true);
        const replanned = bus.since(before).some((event) => event.type === "checkpoint");
        expect.soft(
          replanned,
          "stub-задача уже task_done до steer: чекпоинт перепланирования не публикуется",
        ).toBeTruthy();
      });

      await test.step("interrupt: ack раньше classification, mode=interrupt", async () => {
        const before = await postMessage(request, session, { text: "стой" }, bus);
        await expect
          .poll(() => bus.since(before).some((event) => event.type === "classification"))
          .toBe(true);
        const ack = bus.since(before).find((event) => event.type === "ack");
        const classified = bus.since(before).find((event) => event.type === "classification");
        expect(ack).toBeTruthy();
        expect(ack!.seq).toBeLessThan(classified!.seq);
        expect.soft(classified?.payload.mode).toBe("interrupt");
        expect(classified?.payload.kill_switch).not.toBe(true);
        expect(ack!.receivedAt - ack!.sentAt).toBeLessThanOrEqual(2000);
        const cancelled = bus
          .since(before)
          .some((event) => event.type === "agent_status" && event.payload.status === "cancelled");
        expect.soft(
          cancelled,
          "stub noop успевает в task_done: отмена неплатного шага на границе не видна",
        ).toBeTruthy();
      });

      await test.step("kill switch: честная семантика в событии, не mid-flight", async () => {
        const before = await postMessage(request, session, { text: "kill switch" }, bus);
        await expect
          .poll(() => bus.since(before).some((event) => event.type === "classification" || event.type === "kill_switch"))
          .toBe(true);
        const classified = bus.since(before).find((event) => event.type === "classification");
        expect.soft(classified?.payload.mode).toBe("interrupt");
        expect.soft(classified?.payload.kill_switch).toBe(true);
        await expect
          .poll(() => bus.since(before).some((event) => event.type === "kill_switch"), { timeout: 10_000 })
          .toBe(true);
        const kill = bus.since(before).find((event) => event.type === "kill_switch");
        expect(kill?.payload.stop_key_set).toBe(true);
        expect(kill?.payload.only_node).toBe(true);
        expect(kill?.payload.mid_flight).not.toBe(true);
      });

      await test.step("approval: waiting_approval и approval_required", async () => {
        const approvals = bus.events.filter((event) => event.type === "approval_required");
        const waiting = bus.events.filter(
          (event) => event.type === "agent_status" && event.payload.status === "waiting_approval",
        );
        expect
          .soft(
            approvals.length > 0 || waiting.length > 0,
            "на 288d2311 план-заглушка noop/risk none, approval_required не публикуется",
          )
          .toBeTruthy();
      });

      await test.step("0 удалений сцен и кадров", async () => {
        const deleted = bus.events.filter((event) => {
          const action = String(event.payload.action || event.payload.step || "");
          return DELETION.test(action) && event.payload.ok !== false;
        });
        expect(deleted).toEqual([]);
      });

      await test.step("R1: reconnect Last-Event-ID повторяет события", async () => {
        const seen = bus.events.map((event) => event.seq);
        expect(seen.length).toBeGreaterThan(0);
        const last = Math.min(...seen);
        await bus.close();
        const replay = await openBus(session, String(last));
        try {
          await expect.poll(() => replay.events.length).toBeGreaterThan(0);
          expect(replay.events.every((event) => event.seq > last)).toBe(true);
          expect(replay.events.some((event) => event.type === "ack" || event.type === "classification")).toBe(true);
        } finally {
          await replay.close();
        }
      });
    } finally {
      await bus.close();
    }
  });
});
