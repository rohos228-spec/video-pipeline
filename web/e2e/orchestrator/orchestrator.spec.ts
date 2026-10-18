import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import {
  acceptsOrchContractVersion,
  bearerTargetAllowed,
  envOrchApiBase,
  isSameOriginApiBase,
  noteOrchContractVersion,
  orchAuthHeaders,
  orchKillMessageBody,
  orchKillUsesMessageField,
  orchContractIsR34,
  parseOrchProjectId,
  parseOrchTaskId,
  isForeignTaskBindError,
  bindTaskId,
  projectBindWireBody,
  PROJECT_BIND_TASK_INVALID,
  PROJECT_BIND_CONFLICT,
  PROJECT_BIND_FOREIGN,
  PROJECT_BIND_INVALID,
  PROJECT_BIND_VALIDATION,
  orchTestModeEnabled,
  resolveOrchApiBase,
} from "../../src/lib/orchestratorApi";
import { ORCH_CONTRACT_VERSION, ORCH_EVENT_TYPES } from "../../src/lib/orchestratorTypes";
import {
  PAID_OUTCOME_BODY_FIELDS,
  paidOutcomeDecisionBody,
  paidOutcomeGrantBody,
  paidOutcomeRetryReady,
  paidOutcomeWireBody,
  type PaidOutcomeApproveBody,
} from "../../src/lib/orchestratorPaidOutcome";
import { initialOrchScreen, reduceOrchScreen } from "../../src/lib/orchestratorSession";

const SHOTS =
  process.env.ORCH_E2E_SHOTS_DIR ??
  path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../test-results/orch-shots");

async function saveShot(page: Page, name: string) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

test("поздний stop_key_set=false откатывает Остановлено в ошибку", () => {
  const stopped = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "kill_switch",
      task_id: 1,
      seq: 1,
      session_id: "s",
      payload: { stop_key_set: true },
    },
  });
  expect(stopped.killNotice?.phase).toBe("stopped");
  const late = reduceOrchScreen(stopped, {
    type: "event",
    event: {
      type: "kill_switch",
      task_id: 1,
      seq: 2,
      session_id: "s",
      payload: { stop_key_set: false, detail: "поздний отказ ключа" },
    },
  });
  expect(late.killNotice?.phase).toBe("error");
  expect(late.killNotice?.text).toBe("поздний отказ ключа");
  expect(late.killNotice?.lateStopWarning).toBe("");
});

test("чужая задача узнаётся и по detail, и по коду", () => {
  expect(isForeignTaskBindError("not the active task")).toBe(true);
  expect(isForeignTaskBindError("not_the_active_task")).toBe(true);
  expect(isForeignTaskBindError("задача уже не ждёт проект")).toBe(false);
});

test("not_the_active_task узнаётся по коду, а не по подстроке", () => {
  expect(isForeignTaskBindError("not_the_active_task")).toBe(true);
  expect(isForeignTaskBindError("conflict not_the_active_task")).toBe(false);
  expect(isForeignTaskBindError("see not_the_active_task in the log")).toBe(false);
});

test("неверный task_id при bind даёт явную ошибку", () => {
  expect(() => bindTaskId("0")).toThrow(PROJECT_BIND_TASK_INVALID);
  expect(() => bindTaskId("1e21")).toThrow(PROJECT_BIND_TASK_INVALID);
  expect(() => bindTaskId("abc")).toThrow(PROJECT_BIND_TASK_INVALID);
  expect(bindTaskId(null)).toBeNull();
  expect(bindTaskId("4")).toBe(4);
});

test("bind отвергает 0 и 1e21", () => {
  expect(() => projectBindWireBody(1e21, null)).toThrow(PROJECT_BIND_INVALID);
  expect(() => projectBindWireBody(0, null)).toThrow(PROJECT_BIND_INVALID);
  expect(() => projectBindWireBody(1, 1e21)).toThrow(PROJECT_BIND_TASK_INVALID);
  expect(() => projectBindWireBody(1, 0)).toThrow(PROJECT_BIND_TASK_INVALID);
  expect(projectBindWireBody(1, 2)).toEqual({ project_id: 1, task_id: 2 });
});

test("task_id вне безопасного целого не уходит в bind", () => {
  expect(parseOrchTaskId("12")).toBe(12);
  expect(parseOrchTaskId(null)).toBeNull();
  expect(parseOrchTaskId("1.5")).toBeNull();
  expect(parseOrchTaskId("9007199254740993")).toBeNull();
});

test("message без id после message_end(41) не затирает 41", () => {
  const started = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "message_start",
      task_id: 1,
      seq: 1,
      session_id: "s",
      payload: { message_id: 41, role: "assistant" },
    },
  });
  const streamed = reduceOrchScreen(started, {
    type: "event",
    event: {
      type: "token",
      task_id: 1,
      seq: 2,
      session_id: "s",
      payload: { message_id: 41, text: "реплика 41" },
    },
  });
  const closed = reduceOrchScreen(streamed, {
    type: "event",
    event: {
      type: "message_end",
      task_id: 1,
      seq: 3,
      session_id: "s",
      payload: { message_id: 41 },
    },
  });
  const extra = reduceOrchScreen(closed, {
    type: "event",
    event: {
      type: "message",
      task_id: 1,
      seq: 4,
      session_id: "s",
      payload: { role: "assistant", text: "другая реплика" },
    },
  });
  const bubble = extra.messages.find((message) => message.messageId === 41);
  expect(bubble?.text).toBe("реплика 41");
  expect(extra.messages.some((message) => message.text === "другая реплика" && message.messageId == null)).toBe(true);
});

test("message_start пользователя не получает id ассистента", () => {
  const opened = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "message_start",
      task_id: 1,
      seq: 1,
      session_id: "s",
      payload: { message_id: 3, role: "user" },
    },
  });
  expect(opened.messages[0]?.role).toBe("user");
  expect(opened.messages[0]?.id).toBe("user-mid-3");
});

test("дробный message_id не открывает пузырь", () => {
  const fractional = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "message_start",
      task_id: 1,
      seq: 1,
      session_id: "s",
      payload: { message_id: 1.5, role: "assistant" },
    },
  });
  expect(fractional.messages).toHaveLength(0);
  const whole = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "message_start",
      task_id: 1,
      seq: 1,
      session_id: "s",
      payload: { message_id: 2, role: "assistant" },
    },
  });
  expect(whole.messages[0]?.messageId).toBe(2);
});

test("legacy-токен не дописывается в пузырь выше реплики с message_id", () => {
  const older = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: { type: "token", task_id: 1, seq: 1, session_id: "s", payload: { text: "старое" } },
  });
  const bounded = reduceOrchScreen(older, {
    type: "event",
    event: {
      type: "message_start",
      task_id: 1,
      seq: 2,
      session_id: "s",
      payload: { message_id: 7, role: "assistant" },
    },
  });
  const legacy = reduceOrchScreen(bounded, {
    type: "event",
    event: { type: "token", task_id: 1, seq: 3, session_id: "s", payload: { text: "новое" } },
  });
  expect(legacy.messages[0]?.text).toBe("старое");
  expect(legacy.messages[1]?.messageId).toBe(7);
  expect(legacy.messages[1]?.text).toBe("");
  expect(legacy.messages[2]?.text).toBe("новое");
  expect(legacy.messages[2]?.messageId).toBeUndefined();
});

test("ws3-r3.4.1 не принимается как контракт клиента", () => {
  expect(acceptsOrchContractVersion("ws3-r3.4.1")).toBe(false);
});

test("ws3-r3.3 и ws3-r3.3.1 принимаются без предупреждения", () => {
  expect(acceptsOrchContractVersion("ws3-r3.3")).toBe(true);
  expect(acceptsOrchContractVersion("ws3-r3.3.1")).toBe(true);
  expect(acceptsOrchContractVersion(ORCH_CONTRACT_VERSION)).toBe(true);
  expect(ORCH_EVENT_TYPES).toContain("approval_decided");
  const warnings: string[] = [];
  const original = console.warn;
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  };
  try {
    noteOrchContractVersion("ws3-r3.3");
    noteOrchContractVersion("ws3-r3.3.1");
    expect(warnings).toEqual([]);
    noteOrchContractVersion("ws3-r9");
    noteOrchContractVersion("ws3-r9");
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("ws3-r9");
  } finally {
    console.warn = original;
  }
});

test("approval_decided закрывает карточку по своему change_id", () => {
  const opened = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 2,
      session_id: "decided",
      payload: {
        change_id: "orch-appr-1-1",
        reason: "paid_outcome_unknown",
        action: "run_node",
        forecast_usd: 2.4,
        retry_armed: false,
        decisions: [{ decision: "retry_step", label: "Перезапустить шаг" }],
      },
    },
  });
  const foreign = reduceOrchScreen(opened, {
    type: "event",
    event: {
      type: "approval_decided",
      task_id: 1,
      seq: 3,
      session_id: "decided",
      payload: { change_id: "orch-appr-9-1", decision: "deny", task_id: 1 },
    },
  });
  expect(foreign.approvals[0]?.resolved).toBeNull();

  const denied = reduceOrchScreen(foreign, {
    type: "event",
    event: {
      type: "approval_decided",
      task_id: 1,
      seq: 4,
      session_id: "decided",
      payload: { change_id: "orch-appr-1-1", decision: "deny", task_id: 1 },
    },
  });
  expect(denied.approvals[0]?.resolved).toBe("rejected");

  const retryOpened = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 2,
      session_id: "retry",
      payload: {
        change_id: "orch-appr-1-1",
        reason: "paid_outcome_unknown",
        retry_armed: false,
        decisions: [{ decision: "retry_step", label: "Перезапустить шаг" }],
      },
    },
  });
  const retryDecided = reduceOrchScreen(retryOpened, {
    type: "event",
    event: {
      type: "approval_decided",
      task_id: 1,
      seq: 3,
      session_id: "retry",
      payload: { change_id: "orch-appr-1-1", decision: "retry_step", task_id: 1 },
    },
  });
  expect(retryDecided.approvals[0]?.resolved).toBe("closed");
  const forecast = reduceOrchScreen(retryDecided, {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 4,
      session_id: "retry",
      payload: {
        change_id: "orch-appr-1-1",
        reason: "paid_outcome_unknown",
        forecast_usd: 2.4,
        retry_armed: true,
        decisions: [{ decision: "retry_step", label: "Перезапустить шаг" }],
      },
    },
  });
  expect(forecast.approvals).toHaveLength(1);
  expect(forecast.approvals[0]?.resolved).toBeNull();
  expect(forecast.approvals[0]?.retryArmed).toBe(true);
  expect(forecast.approvals[0]?.forecast).toBe(2.4);
});

test("первое approval_required несёт change_id, forecast_usd и retry_armed false", () => {
  const opened = reduceOrchScreen(initialOrchScreen(), {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 4,
      session_id: "paid-first",
      payload: {
        change_id: "orch-appr-1-1",
        reason: "paid_outcome_unknown",
        action: "run_node",
        forecast_usd: 2.4,
        retry_armed: false,
        decisions: [
          { decision: "approve", label: "Шаг выполнен, продолжить" },
          { decision: "retry_step", label: "Перезапустить шаг" },
          { decision: "deny", label: "Остановить задачу" },
        ],
      },
    },
  });
  expect(opened.approvals).toHaveLength(1);
  expect(opened.approvals[0]).toMatchObject({
    changeId: "orch-appr-1-1",
    forecast: 2.4,
    retryArmed: false,
    restartOffered: true,
  });
  const updated = reduceOrchScreen(opened, {
    type: "event",
    event: {
      type: "approval_required",
      task_id: 1,
      seq: 5,
      session_id: "paid-first",
      payload: {
        change_id: "orch-appr-1-1",
        reason: "paid_outcome_unknown",
        forecast_usd: 2.4,
        retry_armed: true,
        decisions: [{ decision: "retry_step", label: "Перезапустить шаг" }],
      },
    },
  });
  expect(updated.approvals).toHaveLength(1);
  expect(updated.approvals[0]?.retryArmed).toBe(true);
  expect(updated.approvals[0]?.forecast).toBe(2.4);
});

test("клиент approve шлёт только change_id, decision и retry_grant", () => {
  const fields = [...PAID_OUTCOME_BODY_FIELDS];
  const decision = paidOutcomeDecisionBody("orch-appr-1-1", "retry_step");
  const grant = paidOutcomeGrantBody("orch-appr-1-1");
  expect(Object.keys(decision)).toEqual(fields);
  expect(Object.keys(grant)).toEqual(fields);
  const dirty = {
    ...decision,
    note: "лишнее",
    approval_id: "appr-1",
    task_id: "1",
  } as PaidOutcomeApproveBody;
  const wire = paidOutcomeWireBody(dirty);
  expect(Object.keys(wire)).toEqual(fields);
  expect(JSON.parse(JSON.stringify(wire))).toEqual({
    change_id: "orch-appr-1-1",
    decision: "retry_step",
    retry_grant: false,
  });
  expect(JSON.parse(JSON.stringify(paidOutcomeWireBody(grant)))).toEqual({
    change_id: "orch-appr-1-1",
    decision: null,
    retry_grant: true,
  });
});

test("подтверждение перезапуска требует retry_armed и конечный forecast_usd", () => {
  expect(paidOutcomeRetryReady({ retry_armed: true, forecast_usd: 2.4, executed: false })).toBe(true);
  expect(paidOutcomeRetryReady({ retry_armed: true, forecast_usd: 0 })).toBe(true);
  expect(paidOutcomeRetryReady({ retry_armed: false, forecast_usd: 2.4 })).toBe(false);
  expect(paidOutcomeRetryReady({ forecast_usd: 2.4 })).toBe(false);
  expect(paidOutcomeRetryReady({ retry_armed: true, forecast_usd: Number.NaN })).toBe(false);
  expect(paidOutcomeRetryReady({ retry_armed: true, forecast_usd: -1 })).toBe(false);
  expect(paidOutcomeRetryReady({ retry_armed: true })).toBe(false);
});

function screenFrom(events: { type: string; seq: number; payload: Record<string, unknown> }[]) {
  return events.reduce(
    (state, event) =>
      reduceOrchScreen(state, {
        type: "event",
        event: { ...event, task_id: 1, session_id: "boundary" },
      }),
    initialOrchScreen(),
  );
}

test("message без id чужой задачи не затирает открытый пузырь 41", () => {
  const events = [
    {
      type: "message_start" as const,
      task_id: 7,
      seq: 1,
      session_id: "boundary",
      payload: { message_id: 41, role: "assistant" },
    },
    {
      type: "token" as const,
      task_id: 7,
      seq: 2,
      session_id: "boundary",
      payload: { text: "Альфа ", message_id: 41 },
    },
    {
      type: "message" as const,
      task_id: 9,
      seq: 3,
      session_id: "boundary",
      payload: { role: "assistant", text: "параллельный append" },
    },
    {
      type: "message_end" as const,
      task_id: 7,
      seq: 4,
      session_id: "boundary",
      payload: { message_id: 41, role: "assistant" },
    },
    {
      type: "message" as const,
      task_id: 7,
      seq: 5,
      session_id: "boundary",
      payload: { message_id: 41, role: "assistant", text: "Альфа один" },
    },
  ];
  const state = events.reduce(
    (screen, event) => reduceOrchScreen(screen, { type: "event", event }),
    initialOrchScreen(),
  );
  const bubble = state.messages.find((item) => item.messageId === 41);
  expect(bubble?.text).toBe("Альфа один");
  expect(bubble?.finalized).toBe(true);
  expect(bubble?.taskId).toBe("7");
  const foreign = state.messages.find((item) => item.messageId == null);
  expect(foreign?.text).toBe("параллельный append");
  expect(foreign?.taskId).toBe("9");
});

test("токены с message_id не склеивают две реплики, без id клеятся по-старому", () => {
  const bounded = screenFrom([
    { type: "message_start", seq: 1, payload: { message_id: 41, role: "assistant" } },
    { type: "token", seq: 2, payload: { text: "Альфа ", message_id: 41 } },
    { type: "token", seq: 3, payload: { text: "один", message_id: 41 } },
    { type: "message_end", seq: 4, payload: { message_id: 41, role: "assistant" } },
    { type: "message", seq: 5, payload: { role: "assistant", text: "Альфа один" } },
    { type: "message_start", seq: 6, payload: { message_id: 42, role: "assistant" } },
    { type: "token", seq: 7, payload: { text: "Бета ", message_id: 42 } },
    { type: "token", seq: 8, payload: { text: "два", message_id: 42 } },
    { type: "message_end", seq: 9, payload: { message_id: 42, role: "assistant" } },
    { type: "message", seq: 10, payload: { role: "assistant", text: "Бета два" } },
  ]);
  expect(bounded.messages.map((item) => item.text)).toEqual(["Альфа один", "Бета два"]);
  expect(bounded.messages.map((item) => item.messageId)).toEqual([41, 42]);
  expect(bounded.messages.every((item) => item.finalized)).toBe(true);

  const legacy = screenFrom([
    { type: "token", seq: 1, payload: { text: "до обрыва " } },
    { type: "token", seq: 2, payload: { text: "REPLAY_OK" } },
  ]);
  expect(legacy.messages).toHaveLength(1);
  expect(legacy.messages[0]?.text).toBe("до обрыва REPLAY_OK");
  expect(legacy.messages[0]?.messageId ?? null).toBeNull();
});

test("replay без токенов собирает пузырь из message и не дублирует текст", () => {
  const replayed = screenFrom([
    { type: "message_start", seq: 1, payload: { message_id: 41, role: "assistant" } },
    { type: "message_end", seq: 4, payload: { message_id: 41, role: "assistant" } },
    { type: "message", seq: 5, payload: { role: "assistant", text: "Итог replay" } },
    { type: "message_start", seq: 6, payload: { message_id: 42, role: "assistant" } },
    { type: "message_end", seq: 7, payload: { message_id: 42, role: "assistant" } },
    { type: "message", seq: 8, payload: { role: "assistant", text: "Второй replay" } },
  ]);
  expect(replayed.messages.map((item) => item.text)).toEqual(["Итог replay", "Второй replay"]);

  const live = screenFrom([
    { type: "message_start", seq: 1, payload: { message_id: 41, role: "assistant" } },
    { type: "token", seq: 2, payload: { text: "Альфа ", message_id: 41 } },
    { type: "token", seq: 3, payload: { text: "один", message_id: 41 } },
    { type: "message_end", seq: 4, payload: { message_id: 41, role: "assistant" } },
    { type: "message", seq: 5, payload: { role: "assistant", text: "Альфа один" } },
  ]);
  expect(live.messages).toHaveLength(1);
  expect(live.messages[0]?.text).toBe("Альфа один");
});

test("kill_switch уходит полем только на контракте ws3-r3.4", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_CONTRACT;
  const previousTest = process.env.NEXT_PUBLIC_ORCH_TEST;
  try {
    delete process.env.NEXT_PUBLIC_ORCH_CONTRACT;
    process.env.NEXT_PUBLIC_ORCH_TEST = "1";
    expect(orchKillUsesMessageField("")).toBe(false);
    expect(orchKillUsesMessageField("?scenario=idle")).toBe(false);
    expect(orchKillUsesMessageField("?orchContract=ws3-r3.4&scenario=killfield")).toBe(true);
  } finally {
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_CONTRACT;
    else process.env.NEXT_PUBLIC_ORCH_CONTRACT = previous;
    if (previousTest == null) delete process.env.NEXT_PUBLIC_ORCH_TEST;
    else process.env.NEXT_PUBLIC_ORCH_TEST = previousTest;
  }
  expect(orchKillMessageBody()).toEqual({ text: "останови генерацию", kill_switch: true });
  expect(Object.keys(orchKillMessageBody())).toEqual(["text", "kill_switch"]);
});

test("?orchContract=ws3-r3.4 не переключает kill вне тест-режима", () => {
  const previousContract = process.env.NEXT_PUBLIC_ORCH_CONTRACT;
  const previousTest = process.env.NEXT_PUBLIC_ORCH_TEST;
  try {
    delete process.env.NEXT_PUBLIC_ORCH_CONTRACT;
    delete process.env.NEXT_PUBLIC_ORCH_TEST;
    expect(orchTestModeEnabled()).toBe(false);
    expect(orchKillUsesMessageField("?orchContract=ws3-r3.4")).toBe(false);
    expect(orchContractIsR34("?orchContract=ws3-r3.4&scenario=killfield")).toBe(false);
    process.env.NEXT_PUBLIC_ORCH_CONTRACT = "ws3-r3.4";
    expect(orchKillUsesMessageField("")).toBe(true);
    delete process.env.NEXT_PUBLIC_ORCH_CONTRACT;
    process.env.NEXT_PUBLIC_ORCH_TEST = "1";
    expect(orchKillUsesMessageField("?orchContract=ws3-r3.4")).toBe(true);
  } finally {
    if (previousContract == null) delete process.env.NEXT_PUBLIC_ORCH_CONTRACT;
    else process.env.NEXT_PUBLIC_ORCH_CONTRACT = previousContract;
    if (previousTest == null) delete process.env.NEXT_PUBLIC_ORCH_TEST;
    else process.env.NEXT_PUBLIC_ORCH_TEST = previousTest;
  }
});

test("флаг r3.4 принимает ws3-r3.4 и не предупреждает", () => {
  const previous = process.env.NEXT_PUBLIC_ORCH_CONTRACT;
  const previousTest = process.env.NEXT_PUBLIC_ORCH_TEST;
  const warnings: string[] = [];
  const original = console.warn;
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  };
  try {
    delete process.env.NEXT_PUBLIC_ORCH_CONTRACT;
    process.env.NEXT_PUBLIC_ORCH_TEST = "1";
    expect(orchContractIsR34("?orchContract=ws3-r3.4")).toBe(true);
    expect(orchContractIsR34("?orchContract=ws3-r3.4.1")).toBe(false);
    expect(orchContractIsR34("?orchContract=ws3-r3.3.1")).toBe(false);
    process.env.NEXT_PUBLIC_ORCH_CONTRACT = "ws3-r3.4";
    expect(orchContractIsR34("")).toBe(true);
    expect(acceptsOrchContractVersion("ws3-r3.4")).toBe(true);
    noteOrchContractVersion("ws3-r3.4");
    expect(warnings).toEqual([]);
  } finally {
    console.warn = original;
    if (previous == null) delete process.env.NEXT_PUBLIC_ORCH_CONTRACT;
    else process.env.NEXT_PUBLIC_ORCH_CONTRACT = previous;
    if (previousTest == null) delete process.env.NEXT_PUBLIC_ORCH_TEST;
    else process.env.NEXT_PUBLIC_ORCH_TEST = previousTest;
  }
});

test("project_id клиента — целое от 1", () => {
  expect(parseOrchProjectId("13")).toBe(13);
  expect(parseOrchProjectId("1")).toBe(1);
  expect(parseOrchProjectId("0")).toBeNull();
  expect(parseOrchProjectId("-1")).toBeNull();
  expect(parseOrchProjectId("1.5")).toBeNull();
  expect(parseOrchProjectId("")).toBeNull();
  expect(parseOrchProjectId("01")).toBeNull();
  expect(parseOrchProjectId("13.0")).toBeNull();
  expect(parseOrchProjectId("true")).toBeNull();
});

test("project bind шлёт строго целые project_id и task_id", () => {
  const body = projectBindWireBody(13, 1);
  expect(body).toEqual({ project_id: 13, task_id: 1 });
  expect(typeof body.project_id).toBe("number");
  expect(typeof body.task_id).toBe("number");
  const json = JSON.stringify(body);
  expect(json).toBe('{"project_id":13,"task_id":1}');
  expect(json).not.toContain('"13"');
  expect(json).not.toContain('"1"');
  expect(projectBindWireBody(13, null)).toEqual({ project_id: 13 });
  expect(() => projectBindWireBody(13.5, 1)).toThrow();
  expect(() => projectBindWireBody(13, 1.5)).toThrow();
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

async function openWithContract(page: Page, scenario: string, session: string) {
  await page.addInitScript(
    ([token]) => {
      localStorage.setItem("vp-orch-api-token", token);
    },
    [TOKEN],
  );
  await page.goto(`${orchUrl(scenario, session)}&orchContract=ws3-r3.4`);
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

  test("два ответа подряд не склеиваются по message_id", async ({ page }) => {
    await openWithToken(page, "boundary", "boundary-1");
    await page.getByTestId("orch-composer").fill("две реплики");
    await page.getByTestId("orch-send").click();
    const bubbles = page.getByTestId("orch-assistant");
    await expect(bubbles).toHaveCount(2);
    await expect(bubbles.nth(0)).toHaveText("Альфа один");
    await expect(bubbles.nth(1)).toHaveText("Бета два");
    await expect(bubbles.nth(0)).toHaveAttribute("data-message-id", "41");
    await expect(bubbles.nth(1)).toHaveAttribute("data-message-id", "42");
    await expect(page.getByTestId("orch-transcript")).not.toContainText("Альфа одинБета");
  });

  test("replay Last-Event-ID собирает пузырь из message без токенов", async ({ page }) => {
    await openWithToken(page, "boundaryreplay", "boundary-replay-1");
    await page.getByTestId("orch-composer").fill("replay границ");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-reconnect")).toContainText("восстановлено");
    const bubbles = page.getByTestId("orch-assistant");
    await expect(bubbles).toHaveCount(2);
    await expect(bubbles.nth(0)).toHaveText("Итог replay");
    await expect(bubbles.nth(1)).toHaveText("Второй replay");
    await expect(page.getByTestId("orch-transcript")).not.toContainText("СКРЫТЫЙ");
    await expect(page.getByTestId("orch-transcript")).not.toContainText("Итог replayИтог");
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
    const approveUrls: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().includes("/approve")) approveUrls.push(req.url());
    });
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
    expect(approveUrls.some((url) => new URL(url).pathname === "/api/orchestrator/chat/approval-1/approve")).toBe(true);
    expect(approveUrls.some((url) => url.includes("/approvals/"))).toBe(false);
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
    expect(posts[0]?.body).toEqual({ project_id: 13, task_id: 1 });
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
    await expect(banner).toHaveAttribute("data-kill-phase", "error");
    await expect(banner).toContainText("поздний отказ ключа");
    await expect(banner).not.toContainText("Остановлено");
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

  test("kill_switch: кнопка шлёт поле, Остановлено только при stop_key_set", async ({ page }) => {
    const posts: { url: string; body: Record<string, unknown> | null }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST" || !req.url().includes("/api/orchestrator/")) return;
      posts.push({ url: req.url(), body: req.postDataJSON() });
    });
    await page.route("**/api/orchestrator/chat/**/message", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 150));
      await route.continue();
    });
    await openWithContract(page, "killfield", "kill-field-1");
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopping");
    await expect(banner).toContainText("Останавливаю…");
    await expect(banner).toHaveAttribute("data-kill-phase", "stopped");
    await expect(banner).toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toHaveCount(0);
    const message = posts.find((item) => item.url.includes("/chat/") && item.url.includes("/message"));
    expect(message?.body).toEqual({ text: "останови генерацию", kill_switch: true });
    expect(posts.some((item) => item.url.includes("/api/orchestrator/kill"))).toBe(false);
  });

  test("kill_switch: false, поздний false, таймаут и 401 оставляют Повторить", async ({ page }) => {
    await openWithContract(page, "killfieldfalse", "kill-field-false");
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const banner = page.getByTestId("orch-kill-status");
    await expect(banner).toHaveAttribute("data-kill-phase", "error");
    await expect(banner).toContainText("стоп-ключ не встал");
    await expect(banner).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();

    await openWithContract(page, "killfieldlate", "kill-field-late");
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const late = page.getByTestId("orch-kill-status");
    await expect(late).toHaveAttribute("data-kill-phase", "error");
    await expect(late).toContainText("поздний отказ ключа");
    await expect(late).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();

    await openWithContract(page, "killfield401", "kill-field-401");
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const denied = page.getByTestId("orch-kill-status");
    await expect(denied).toHaveAttribute("data-kill-phase", "error");
    await expect(denied).toContainText("401");
    await expect(denied).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();

    await openWithContract(page, "killfieldhang", "kill-field-hang");
    await page.getByTestId("orch-kill").click();
    await page.getByTestId("orch-kill-confirm").click();
    const hung = page.getByTestId("orch-kill-status");
    await expect(hung).toHaveAttribute("data-kill-phase", "stopping");
    await expect(hung).toContainText("Останавливаю…");
    await expect(hung).toHaveAttribute("data-kill-phase", "error", { timeout: 20_000 });
    await expect(hung).toContainText("Нет подтверждения остановки");
    await expect(hung).not.toContainText("Остановлено");
    await expect(page.getByTestId("orch-kill-retry")).toBeVisible();
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

  test("missing_project_id: project_id ≥ 1 закрывает карточку, 0 не уходит", async ({ page }) => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && /\/project(?:\?|$)/.test(req.url())) {
        posts.push({ url: req.url(), body: req.postDataJSON() });
      }
    });
    await openWithContract(page, "missingproject", "project-bind-1");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-project-bind")).toBeVisible();
    await expect(page.getByTestId("orch-approve")).toHaveCount(0);
    const field = page.getByTestId("orch-project-id");
    await field.fill("0");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText(PROJECT_BIND_INVALID);
    await field.fill("-3");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText(PROJECT_BIND_INVALID);
    expect(posts).toEqual([]);
    await expect(page.getByTestId("orch-missing-project")).toBeVisible();
    await field.fill("13");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-missing-project")).toHaveCount(0);
    expect(posts).toHaveLength(1);
    expect(posts[0]?.url).toContain("/api/orchestrator/chat/project-bind-1/project");
    expect(posts[0]?.body).toEqual({ project_id: 13, task_id: 1 });
  });

  test("после project 200 новый approval_required показывает новую карточку", async ({ page }) => {
    await openWithContract(page, "projectnext", "project-next");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-missing-project")).toBeVisible();
    await page.getByTestId("orch-project-id").fill("13");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-missing-project")).toHaveCount(0);
    await expect(page.getByTestId("orch-risk")).toBeVisible();
    await expect(page.getByTestId("orch-approval")).toContainText("Шаг с риском");
    await expect(page.getByTestId("orch-approve")).toBeEnabled();
  });

  test("409 чужой задачи пишет сообщение на карточке проекта", async ({ page }) => {
    await openWithContract(page, "project409task", "project-409-task");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-project-id").fill("13");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-approval-error")).toHaveText(PROJECT_BIND_FOREIGN);
    await expect(page.getByTestId("orch-missing-project")).toBeVisible();
  });

  test("missing_project_id: 422 и 409 остаются на карточке, 401 — авторизация", async ({ page }) => {
    await openWithContract(page, "project422", "project-422");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-project-id").fill("13");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText(PROJECT_BIND_VALIDATION);
    await expect(page.getByTestId("orch-missing-project")).toBeVisible();

    await openWithContract(page, "project409", "project-409");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-project-id").fill("13");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-approval-error")).toHaveText(PROJECT_BIND_CONFLICT);
    await expect(page.getByTestId("orch-missing-project")).toBeVisible();

    await openWithContract(page, "project401", "project-401");
    await page.getByTestId("orch-composer").fill("платный шаг");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-project-id").fill("13");
    await page.getByTestId("orch-bind-project").click();
    await expect(page.getByTestId("orch-token-gate")).toBeVisible();
    await expect(page.getByTestId("orch-token-error")).toContainText("401");
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);
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

    await openWithToken(page, "approval", "approve-204-legacy");
    await page.route("**/api/orchestrator/chat/**/approve", fulfill204);
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-approve").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("JSON 200");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.unroute("**/api/orchestrator/chat/**/approve");

    await openWithToken(page, "approval", "deny-204");
    await page.route("**/api/orchestrator/chat/**/approve", fulfill204);
    await page.getByTestId("orch-composer").fill("запусти ноду");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await page.getByTestId("orch-reject").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("JSON 200");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
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
    await expect(page.getByTestId("orch-approval-error")).toContainText("JSON 200");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
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

  test("paid_outcome_unknown: первое событие включает кнопки при change_id", async ({ page }) => {
    await openWithToken(page, "paidoutcome", "paid-first-event");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    const card = page.getByTestId("orch-approval");
    await expect(card).toHaveAttribute("data-retry-armed", "false");
    await expect(card).toHaveAttribute("data-forecast", "2.4");
    await expect(page.getByTestId("orch-paid-done")).toBeEnabled();
    await expect(page.getByTestId("orch-paid-retry")).toBeEnabled();
    await expect(page.getByTestId("orch-paid-deny")).toBeEnabled();
    await expect(page.getByTestId("orch-approval-noid")).toHaveCount(0);
    await expect(page.getByTestId("orch-paid-retry-confirm")).toHaveCount(0);
  });

  test("paid_outcome_unknown: тело approve без лишних ключей", async ({ page }) => {
    const bodies: Record<string, unknown>[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST" || !req.url().includes("/approve")) return;
      bodies.push(req.postDataJSON() as Record<string, unknown>);
    });
    await openWithToken(page, "paidoutcome", "paid-wire");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-retry-confirm")).toBeEnabled();
    await page.getByTestId("orch-paid-retry-confirm").click();
    await expect(page.getByTestId("orch-step").filter({ hasText: "повтор после grant" })).toHaveCount(1);
    expect(bodies.map((body) => Object.keys(body))).toEqual([
      ["change_id", "decision", "retry_grant"],
      ["change_id", "decision", "retry_grant"],
    ]);
    expect(bodies[0]).toEqual({ change_id: "orch-appr-1-1", decision: "retry_step", retry_grant: false });
    expect(bodies[1]).toEqual({ change_id: "orch-appr-1-1", decision: null, retry_grant: true });
    for (const body of bodies) {
      expect(body).not.toHaveProperty("approval_id");
      expect(body).not.toHaveProperty("task_id");
      expect(body).not.toHaveProperty("note");
    }
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
    const posts: { url: string; body: unknown; authorization?: string }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST") return;
      posts.push({ url: req.url(), body: req.postDataJSON(), authorization: req.headers().authorization });
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
    const approvals = posts.filter((item) => item.url.includes("/approve"));
    expect(approvals).toHaveLength(1);
    expect(approvals[0]?.url).toContain("/chat/paid-done/approve");
    expect(approvals[0]?.url).not.toContain("/approvals/");
    expect(approvals[0]?.body).toEqual({
      change_id: "orch-appr-1-1",
      decision: "approve",
      retry_grant: false,
    });
    expect(approvals[0]?.authorization).toBe(`Bearer ${TOKEN}`);
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
    expect(retry[0]?.url).toContain("/chat/paid-retry/approve");
    expect(retry[0]?.url).not.toContain("/approvals/");
    expect(retry[0]?.body).toEqual({
      change_id: "orch-appr-1-1",
      decision: "retry_step",
      retry_grant: false,
    });
    expect(retry[1]?.url).toContain("/chat/paid-retry/approve");
    expect(retry[1]?.body).toEqual({
      change_id: "orch-appr-1-1",
      decision: null,
      retry_grant: true,
    });
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
    expect(denials[0]?.url).toContain("/chat/paid-deny/approve");
    expect(denials[0]?.url).not.toContain("/approvals/");
    expect(denials[0]?.body).toEqual({
      change_id: "orch-appr-1-1",
      decision: "deny",
      retry_grant: false,
    });
    expect(posts.filter((item) => item.url.includes("/execute"))).toEqual([]);
  });

  test("paid_outcome_unknown: 404, 409 и 422 оставляют карточку", async ({ page }) => {
    const cases = [
      ["paid404", "unknown change_id"],
      ["paid404foreign", "foreign change_id"],
      ["paid409", "change_id already decided"],
      ["paid409stale", "change_id is not current"],
      ["paid409transition", "invalid transition"],
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
    await expect(page.getByTestId("orch-approval-error")).toContainText("change_id is not current");
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

  test("paid_outcome_unknown: 422 и retry_grant_spent на карточке, 401 — авторизация", async ({ page }) => {
    await openWithToken(page, "paid422", "paid-err-422");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-done").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("approval unprocessable");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-token-gate")).toHaveCount(0);
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");

    await openWithToken(page, "paidspent", "paid-spent");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-forecast")).toContainText("Прогноз расхода: ~$2.4");
    await page.getByTestId("orch-paid-retry-confirm").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("Одноразовый перезапуск уже использован");
    await expect(page.getByTestId("orch-approval-error")).not.toContainText("retry_grant_spent");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-paid-forecast")).toBeVisible();
    await expect(page.getByTestId("orch-paid-retry-confirm")).toHaveCount(0);
    await expect(page.getByTestId("orch-paid-retry")).toBeDisabled();
    await expect(page.getByTestId("orch-step").filter({ hasText: "повтор после grant" })).toHaveCount(0);
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");

    await openWithToken(page, "paidoutcome", "paid-auth");
    await page.route("**/api/orchestrator/chat/*/approve", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unauthorized" }),
      });
    });
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-done")).toBeVisible();
    await page.getByTestId("orch-paid-done").click();
    await expect(page.getByTestId("orch-token-gate")).toBeVisible();
    await expect(page.getByTestId("orch-token-error")).toContainText("401");
    await expect(page.getByTestId("orch-approval-error")).toHaveCount(0);
  });

  test("paid_outcome_unknown: retry_grant_required оставляет карточку и возвращает перезапуск", async ({ page }) => {
    const posts: { body: unknown }[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST" || !req.url().includes("/approve")) return;
      posts.push({ body: req.postDataJSON() });
    });
    await openWithToken(page, "paidrequired", "paid-required");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-retry-confirm")).toBeEnabled();
    await page.getByTestId("orch-paid-retry-confirm").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("Сервер ещё не подтвердил перезапуск");
    await expect(page.getByTestId("orch-approval-error")).not.toContainText("retry_grant_required");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-paid-retry-confirm")).toHaveCount(0);
    await expect(page.getByTestId("orch-paid-retry")).toBeEnabled();
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-retry-confirm")).toBeEnabled();
    const decisions = posts.map((item) => (item.body as { decision?: string }).decision);
    expect(decisions.filter((item) => item === "retry_step")).toHaveLength(2);
  });

  test("paid_outcome_unknown: коды approve как у #358", async ({ page }) => {
    const changeId = "orch-appr-1-1";
    const auth = {
      Authorization: `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
    };

    async function postApprove(
      session: string,
      body: Record<string, unknown>,
      headers: Record<string, string> = auth,
    ) {
      return page.request.post(`${MOCK}/api/orchestrator/chat/${session}/approve`, {
        headers,
        data: body,
      });
    }

    async function readApprove(session: string, click: () => Promise<void>) {
      const pending = page.waitForResponse(
        (res) => res.request().method() === "POST" && res.url().includes(`/chat/${session}/approve`),
      );
      await click();
      const res = await pending;
      return { status: res.status(), json: (await res.json()) as Record<string, unknown> };
    }

    const missingToken = await postApprove(
      "wire-ghost",
      { change_id: changeId, decision: "approve", retry_grant: false },
      { "Content-Type": "application/json" },
    );
    expect(missingToken.status()).toBe(401);
    expect(await missingToken.json()).toEqual({ detail: "unauthorized" });
    const wrongToken = await postApprove(
      "wire-ghost",
      { change_id: changeId, decision: "approve", retry_grant: false },
      { Authorization: "Bearer not-the-token", "Content-Type": "application/json" },
    );
    expect(wrongToken.status()).toBe(401);
    expect(await wrongToken.json()).toEqual({ detail: "unauthorized" });

    const noTask = await postApprove("wire-ghost", {
      change_id: "missing-change",
      decision: "approve",
      retry_grant: false,
    });
    expect(noTask.status()).toBe(404);
    expect(await noTask.json()).toEqual({ detail: "not found" });

    await openWithToken(page, "paidoutcome", "wire-retry");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-retry")).toBeVisible();
    const armed = await readApprove("wire-retry", () => page.getByTestId("orch-paid-retry").click());
    expect(armed.status).toBe(200);
    expect(armed.json).toMatchObject({
      executed: false,
      retry_armed: true,
      forecast_usd: 2.4,
      decision: "retry_step",
      change_id: changeId,
    });
    await expect(page.getByTestId("orch-step").filter({ hasText: "повтор после grant" })).toHaveCount(0);
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");

    const granted = await readApprove("wire-retry", () => page.getByTestId("orch-paid-retry-confirm").click());
    expect(granted.status).toBe(200);
    expect(granted.json).toMatchObject({
      executed: true,
      reason: null,
      retry_armed: false,
      decision: "retry_step",
      change_id: changeId,
    });
    await expect(page.getByTestId("orch-step").filter({ hasText: "повтор после grant" })).toHaveCount(1);
    const spent = await postApprove("wire-retry", { change_id: changeId, decision: null, retry_grant: true });
    expect(spent.status()).toBe(200);
    expect(await spent.json()).toMatchObject({
      executed: false,
      reason: "retry_grant_spent",
      retry_armed: false,
      forecast_usd: null,
    });
    await expect(page.getByTestId("orch-step").filter({ hasText: "повтор после grant" })).toHaveCount(1);

    await openWithToken(page, "paidoutcome", "wire-approve");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-done")).toBeVisible();
    const approved = await readApprove("wire-approve", () => page.getByTestId("orch-paid-done").click());
    expect(approved.json.retry_armed).toBe(false);
    expect(approved.json.executed).toBe(false);
    expect(approved.json.decision).toBe("approve");
    const again = await postApprove("wire-approve", {
      change_id: changeId,
      decision: "approve",
      retry_grant: false,
    });
    expect(again.status()).toBe(409);
    expect(await again.json()).toEqual({ detail: "change_id already decided" });

    await openWithToken(page, "paidoutcome", "wire-deny");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-deny")).toBeVisible();
    const denied = await readApprove("wire-deny", () => page.getByTestId("orch-paid-deny").click());
    expect(denied.json).toMatchObject({ executed: false, retry_armed: false, decision: "deny" });
    await expect(page.getByTestId("orch-task-status")).toContainText("ошибка");

    await openWithToken(page, "paidoutcome", "wire-empty");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-done")).toBeVisible();
    const empty = await postApprove("wire-empty", { change_id: changeId, decision: null, retry_grant: false });
    expect(empty.status()).toBe(200);
    expect(await empty.json()).toMatchObject({ executed: false, retry_armed: false, decision: null });
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-step").filter({ hasText: "Платная генерация" })).toHaveAttribute(
      "data-status",
      "running",
    );
    const extra = await postApprove("wire-empty", {
      change_id: changeId,
      decision: "approve",
      retry_grant: false,
      note: "лишнее",
    });
    expect(extra.status()).toBe(422);
    expect(await extra.json()).toEqual({ detail: "approval unprocessable" });
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
    await expect(page.getByTestId("orch-paid-done")).toBeEnabled();

    const unknown = await postApprove("wire-empty", {
      change_id: "missing-change",
      decision: "approve",
      retry_grant: false,
    });
    expect(unknown.status()).toBe(404);
    expect(await unknown.json()).toEqual({ detail: "unknown change_id" });

    await openWithToken(page, "paidoutcome", "wire-owner");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await expect(page.getByTestId("orch-paid-done")).toBeVisible();
    const started = await page.request.post(`${MOCK}/api/orchestrator/chat/wire-stranger/message`, {
      headers: { ...auth, "X-Orch-Scenario": "stream" },
      data: { text: "чужая сессия" },
    });
    expect(started.ok()).toBeTruthy();
    const foreign = await postApprove("wire-stranger", {
      change_id: changeId,
      decision: "approve",
      retry_grant: false,
    });
    expect(foreign.status()).toBe(404);
    expect(await foreign.json()).toEqual({ detail: "foreign change_id" });
    await expect(page.getByTestId("orch-approval")).toBeVisible();

    await emitBus(page, "wire-owner", "approval_required", 1, {
      change_id: "orch-appr-stale",
      reason: "note",
      title: "устаревший id",
    });
    const stale = await postApprove("wire-owner", {
      change_id: "orch-appr-stale",
      decision: "deny",
      retry_grant: false,
    });
    expect(stale.status()).toBe(409);
    expect(await stale.json()).toEqual({ detail: "change_id is not current" });
    await expect(page.getByTestId("orch-paid-done")).toBeEnabled();

    await openWithToken(page, "paid404nostep", "paid-no-step");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-done").click();
    await expect(page.getByTestId("orch-approval-error")).toContainText("not found");
    await expect(page.getByTestId("orch-approval")).toBeVisible();
    await expect(page.getByTestId("orch-task-status")).toContainText("подтверждения");
    await expect(page.getByTestId("orch-paid-done")).toBeEnabled();
  });

  test("paid_outcome_unknown: без retry_armed подтверждение перезапуска неактивно", async ({ page }) => {
    const grants: unknown[] = [];
    page.on("request", (req) => {
      if (req.method() !== "POST" || !req.url().includes("/approve")) return;
      const body = req.postDataJSON() as { retry_grant?: boolean };
      if (body?.retry_grant === true) grants.push(body);
    });
    await openWithToken(page, "paidnoarmed", "paid-no-armed");
    await page.getByTestId("orch-composer").fill("платный шаг оборвался");
    await page.getByTestId("orch-send").click();
    await page.getByTestId("orch-paid-retry").click();
    await expect(page.getByTestId("orch-paid-forecast")).toContainText("Прогноз расхода: ~$2.4");
    await expect(page.getByTestId("orch-paid-retry-confirm")).toBeDisabled();
    await expect(page.getByTestId("orch-paid-grant-hint")).toContainText("Сервер не выдал одноразовое подтверждение");
    await page.getByTestId("orch-paid-retry-confirm").click({ force: true });
    expect(grants).toEqual([]);
    await expect(page.getByTestId("orch-approval")).toBeVisible();
  });

  test("paid_outcome_unknown: без поддержки перезапуска кнопка неактивна", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().includes("/approve")) posts.push(req.url());
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
      if (req.method() === "POST" && req.url().includes("/approve")) posts.push(req.url());
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
