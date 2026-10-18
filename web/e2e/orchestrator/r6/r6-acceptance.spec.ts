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
const SAFE_ROUTERS = new Set(["R6Router", "FallbackRouter", "ScriptedRouter", "ReplayRouter"]);
const FIXTURE_TEXT = "фиксация принята и разобрана";

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

type StepRow = { step: string; expected: string; actual: string; pass: boolean };

function summarize(events: BusEvent[]): string {
  if (events.length === 0) return "(нет событий)";
  return events
    .map((event) => {
      const payload = event.payload;
      const bits = [`#${event.seq}`, event.type];
      if (payload.mode) bits.push(`mode=${payload.mode}`);
      if (payload.action) bits.push(`action=${payload.action}`);
      if (payload.status) bits.push(`status=${payload.status}`);
      if (payload.reason) bits.push(`reason=${payload.reason}`);
      if (payload.agent) bits.push(`agent=${payload.agent}`);
      if (payload.paid !== undefined && event.type.startsWith("step_")) bits.push(`paid=${payload.paid}`);
      if (payload.ok !== undefined && event.type === "step_finished") bits.push(`ok=${payload.ok}`);
      if (payload.kill_switch === true) bits.push("kill_switch");
      if (payload.deterministic !== undefined && event.type === "classification") bits.push(`det=${payload.deterministic}`);
      if (payload.stop_key_set !== undefined && event.type === "kill_switch") bits.push(`stop_key_set=${payload.stop_key_set}`);
      if (payload.only_node !== undefined && event.type === "kill_switch") bits.push(`only_node=${payload.only_node}`);
      if (payload.mid_flight !== undefined && event.type === "kill_switch") bits.push(`mid_flight=${payload.mid_flight}`);
      if (event.type === "token" && typeof payload.text === "string") bits.push(JSON.stringify(payload.text));
      if (event.type === "checkpoint" && Array.isArray(payload.plan)) {
        bits.push(`plan=${payload.plan.map((step) => String((step as { action?: string }).action || "?")).join(",")}`);
      }
      return bits.join(" ");
    })
    .join(" | ");
}

function planActions(event: BusEvent | undefined): string[] {
  const plan = event?.payload.plan;
  if (!Array.isArray(plan)) return [];
  return plan.map((step) => String((step as { action?: string }).action || ""));
}

test.describe("R6 приёмка на живом API", () => {
  test("сценарий чата по событиям SSE", async ({ request }) => {
    expect(TOKEN, "ORCH_R6_TOKEN").not.toBe("");
    const impl = routerImpl();
    const safe = SAFE_ROUTERS.has(impl);
    expect(safe || LIVE, `роутер «${impl || "неизвестен"}»`).toBeTruthy();
    if (!safe && !LIVE) return;

    const rows: StepRow[] = [];
    const session = `r6${Date.now().toString(36)}`;

    async function record(step: string, expected: string, run: () => Promise<string>): Promise<void> {
      await test.step(step, async () => {
        try {
          const actual = await run();
          rows.push({ step, expected, actual, pass: true });
        } catch (error) {
          const actual = error instanceof Error ? error.message.replace(/\s+/g, " ").slice(0, 500) : String(error);
          rows.push({ step, expected, actual, pass: false });
          expect.soft(false, actual).toBeTruthy();
        }
      });
    }

    await record("E8: без токена 401", "POST без Authorization → 401", async () => {
      const res = await request.post(`${BASE}/api/orchestrator/chat/${session}/message`, {
        data: { text: "добро пожаловать" },
      });
      expect(res.status()).toBe(401);
      return `status=${res.status()}`;
    });

    const bus = await openBus(session);
    try {
      await record(
        "стриминг: добро пожаловать",
        `token по словам = «${FIXTURE_TEXT}»; ack.seq < classification; mode=append; ack ≤ 2с`,
        async () => {
          const before = await postMessage(request, session, { text: "добро пожаловать" }, bus);
          await expect
            .poll(() => bus.since(before).some((event) => event.type === "token" || event.type === "message"))
            .toBe(true);
          const slice = bus.since(before);
          const tokens = slice.filter((event) => event.type === "token");
          const text = tokens.map((event) => String(event.payload.text || "")).join("");
          const ack = slice.find((event) => event.type === "ack");
          const classified = slice.find((event) => event.type === "classification");
          expect(tokens.length, summarize(slice)).toBeGreaterThan(1);
          expect(text, summarize(slice)).toBe(FIXTURE_TEXT);
          expect(ack, summarize(slice)).toBeTruthy();
          expect(classified, summarize(slice)).toBeTruthy();
          expect(ack!.seq).toBeLessThan(classified!.seq);
          expect(classified!.payload.mode).toBe("append");
          expect(ack!.receivedAt - ack!.sentAt).toBeLessThanOrEqual(2000);
          return summarize(slice);
        },
      );

      let taskBefore = 0;
      await record(
        "задача: три test.look",
        "mode=task; agent=supervisor; step_started test.look paid=false, пока шаг ещё идёт",
        async () => {
          taskBefore = await postMessage(request, session, { text: "посмотри вокруг", mode: "task" }, bus);
          await expect
            .poll(() =>
              bus.since(taskBefore).some(
                (event) => event.type === "step_started" && event.payload.action === "test.look",
              ),
            )
            .toBe(true);
          const slice = bus.since(taskBefore);
          const started = slice.find((event) => event.type === "step_started");
          const classified = slice.find((event) => event.type === "classification");
          const statuses = slice.filter((event) => event.type === "agent_status").map((event) => String(event.payload.status || ""));
          expect(classified?.payload.mode).toBe("task");
          expect(slice.some((event) => event.type === "agent_status" && event.payload.agent === "supervisor")).toBe(true);
          expect(statuses.some((status) => TASK_STATUSES.includes(status))).toBe(true);
          expect(started?.payload.action).toBe("test.look");
          expect(started?.payload.paid).toBe(false);
          return summarize(slice);
        },
      );

      await record(
        "append посреди задачи",
        "classification mode=append deterministic=true; kill_switch нет",
        async () => {
          const before = await postMessage(request, session, { text: "сколько будет два плюс два?" }, bus);
          await expect.poll(() => bus.since(before).some((event) => event.type === "classification")).toBe(true);
          const slice = bus.since(before);
          const classified = slice.find((event) => event.type === "classification");
          expect(classified?.payload.mode).toBe("append");
          expect(classified?.payload.deterministic).toBe(true);
          expect(slice.some((event) => event.type === "kill_switch")).toBe(false);
          return summarize(slice);
        },
      );

      await record(
        "steer: измени план",
        "mode=steer; checkpoint плана из трёх test.look; затем step_started test.look_again",
        async () => {
          const before = await postMessage(request, session, { text: "измени план" }, bus);
          await expect
            .poll(() => bus.since(before).some((event) => event.type === "checkpoint"), { timeout: 15_000 })
            .toBe(true);
          await expect
            .poll(
              () =>
                bus.since(before).some(
                  (event) => event.type === "step_started" && event.payload.action === "test.look_again",
                ),
              { timeout: 15_000 },
            )
            .toBe(true);
          const slice = bus.since(before);
          const classified = slice.find((event) => event.type === "classification");
          const checkpoint = slice.find((event) => event.type === "checkpoint");
          expect(classified?.payload.mode).toBe("steer");
          expect(classified?.payload.deterministic).toBe(true);
          expect(planActions(checkpoint)).toEqual(["test.look", "test.look", "test.look"]);
          expect(slice.some((event) => event.type === "step_started" && event.payload.action === "test.look_again")).toBe(true);
          return summarize(slice);
        },
      );

      await record(
        "interrupt: отмена",
        "mode=interrupt; kill_switch не true; ack ≤ 2с и раньше classification; agent_status=cancelled",
        async () => {
          const before = await postMessage(request, session, { text: "отмена" }, bus);
          await expect
            .poll(
              () =>
                bus.since(before).some(
                  (event) => event.type === "agent_status" && event.payload.status === "cancelled",
                ),
              { timeout: 15_000 },
            )
            .toBe(true);
          const slice = bus.since(before);
          const ack = slice.find((event) => event.type === "ack");
          const classified = slice.find((event) => event.type === "classification");
          expect(ack).toBeTruthy();
          expect(classified).toBeTruthy();
          expect(ack!.seq).toBeLessThan(classified!.seq);
          expect(classified?.payload.mode).toBe("interrupt");
          expect(classified?.payload.kill_switch).not.toBe(true);
          expect(ack!.receivedAt - ack!.sentAt).toBeLessThanOrEqual(2000);
          return summarize(slice);
        },
      );

      await record(
        "kill switch",
        "classification interrupt+kill_switch; событие kill_switch stop_key_set=true only_node=true; mid_flight не true",
        async () => {
          const before = await postMessage(request, session, { text: "kill switch" }, bus);
          await expect
            .poll(() => bus.since(before).some((event) => event.type === "kill_switch"), { timeout: 10_000 })
            .toBe(true);
          const slice = bus.since(before);
          const classified = slice.find((event) => event.type === "classification");
          const kill = slice.find((event) => event.type === "kill_switch");
          expect(classified?.payload.mode).toBe("interrupt");
          expect(classified?.payload.kill_switch).toBe(true);
          expect(kill?.payload.stop_key_set).toBe(true);
          expect(kill?.payload.only_node).toBe(true);
          expect(kill?.payload.mid_flight).not.toBe(true);
          return summarize(slice);
        },
      );

      await record(
        "approval: среднее касание без project_id",
        "approval_required reason=missing_project_id и agent_status=waiting_approval",
        async () => {
          const before = await postMessage(request, session, { text: "среднее касание", mode: "task" }, bus);
          await expect
            .poll(() => bus.since(before).some((event) => event.type === "approval_required"), { timeout: 15_000 })
            .toBe(true);
          const slice = bus.since(before);
          const approval = slice.find((event) => event.type === "approval_required");
          const waiting = slice.find(
            (event) => event.type === "agent_status" && event.payload.status === "waiting_approval",
          );
          expect(approval?.payload.reason).toBe("missing_project_id");
          expect(waiting).toBeTruthy();
          return summarize(slice);
        },
      );

      await record(
        "слово сцен делает задачу",
        "classification mode=task; событий token нет",
        async () => {
          const before = await postMessage(request, session, { text: "разбери сцен" }, bus);
          await expect
            .poll(() => {
              const slice = bus.since(before);
              const classified = slice.find((event) => event.type === "classification");
              if (!classified) return false;
              if (classified.payload.mode === "append") {
                return slice.some((event) => event.type === "token" || event.type === "message");
              }
              return true;
            })
            .toBe(true);
          const slice = bus.since(before);
          const classified = slice.find((event) => event.type === "classification");
          const tokens = slice.filter((event) => event.type === "token");
          expect(classified?.payload.mode, summarize(slice)).toBe("task");
          expect(tokens, summarize(slice)).toEqual([]);
          return summarize(slice);
        },
      );

      await record("0 удалений сцен и кадров", "нет успешного delete_frame/delete_scene/purge/drop", async () => {
        const deleted = bus.events.filter((event) => {
          const action = String(event.payload.action || event.payload.step || "");
          return DELETION.test(action) && event.payload.ok !== false;
        });
        expect(deleted.map((event) => summarize([event]))).toEqual([]);
        return "удалений нет";
      });

      await record(
        "reconnect Last-Event-ID",
        "повтор seq строго больше последнего; среди них ack или classification",
        async () => {
          const seen = bus.events.map((event) => event.seq);
          expect(seen.length).toBeGreaterThan(0);
          const last = Math.min(...seen);
          await bus.close();
          const replay = await openBus(session, String(last));
          try {
            await expect.poll(() => replay.events.length).toBeGreaterThan(0);
            expect(replay.events.every((event) => event.seq > last)).toBe(true);
            expect(replay.events.some((event) => event.type === "ack" || event.type === "classification")).toBe(true);
            return summarize(replay.events.slice(0, 8));
          } finally {
            await replay.close();
          }
        },
      );
    } finally {
      await bus.close();
      console.log(`R6_STEP_TABLE ${JSON.stringify(rows)}`);
    }
    expect(rows.filter((row) => !row.pass).map((row) => row.step)).toEqual([]);
  });
});
