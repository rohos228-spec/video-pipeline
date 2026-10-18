/**
 * Детерминированный мок SSE/REST оркестратора до готовности WS3.
 *
 *   node web/e2e/orchestrator/mock-server.mjs
 *
 * Токен: orch-dev-token (или ORCH_MOCK_TOKEN).
 * Сценарий: заголовок X-Orch-Scenario или ?scenario=
 *   stream | streamhold | interrupt | approval | approvefail | approve404 | approve409 | budget | kill | reconnect | idle | plan | critic | killmsg | task | llmbudget
 * mode=task в теле POST ставит новую задачу в очередь за активной.
 *
 * Формат как у WS3 (PR #346): SSE id=seq, data=OrchEvent JSON
 * {type, task_id:number|null, payload, seq, session_id}.
 * POST message {text} → {ok, session, ack_seq}. 401 {detail:"unauthorized"}.
 * Replay: seq > Last-Event-ID.
 * Approve/kill в роутере WS3 нет — мок оставляет их для UI.
 * Любой другой токен и пустой Authorization — 401.
 *
 * События копятся в памяти сессии и отдаются повторно по Last-Event-ID.
 */

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.ORCH_MOCK_PORT || 8799);
const HOST = process.env.ORCH_MOCK_HOST || "127.0.0.1";
const VALID_TOKEN = process.env.ORCH_MOCK_TOKEN || "orch-dev-token";

const sessions = new Map();

function sessionOf(id) {
  if (!sessions.has(id)) {
    sessions.set(id, {
      id,
      seq: 0,
      events: [],
      clients: new Set(),
      messageCount: 0,
      currentTaskId: null,
      activeTaskId: null,
      activeDone: true,
      queue: [],
      killed: false,
      approvalWait: null,
      reconnectDropped: false,
    });
  }
  return sessions.get(id);
}

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Authorization, Content-Type, Last-Event-ID, X-Orch-Scenario, Accept",
  );
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Expose-Headers", "Content-Type, Content-Security-Policy, X-Content-Type-Options");
}

function sendJson(res, status, body) {
  cors(res);
  const raw = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(raw);
}

function authorized(req) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  return token === VALID_TOKEN;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch {
        resolve({});
      }
    });
    req.on("error", reject);
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function writeEvent(res, event) {
  res.write(`id: ${event.seq}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
}

function emit(session, type, taskId, payload, sessionId = session.id) {
  const event = {
    type,
    task_id: taskId,
    payload,
    seq: ++session.seq,
    session_id: sessionId,
  };
  session.events.push(event);
  for (const client of session.clients) {
    try {
      writeEvent(client, event);
    } catch {
      session.clients.delete(client);
    }
  }
  return event;
}

function dropClients(session) {
  for (const client of [...session.clients]) {
    session.clients.delete(client);
    try {
      client.end();
    } catch {
      /* уже закрыт */
    }
  }
}

const NODE_DB = {
  node_key: "n_scenes_to_frames",
  prompt_template: "",
  params: {},
  source: "db",
  version: 4,
  db_version: 4,
  git_sha256: "a8b5a03f",
  stale_base: false,
};

function ackPayload(session) {
  return { text: session.lastEcho || "", accepted: true };
}

function classPayload(mode, detail) {
  return { mode, tier: "fast", deterministic: true, confidence: 1, detail };
}

const NODE_STALE = {
  ...NODE_DB,
  stale_base: true,
};

function scenarioOf(req, url) {
  const header = req.headers["x-orch-scenario"];
  const fromHeader = Array.isArray(header) ? header[0] : header;
  return String(fromHeader || url.searchParams.get("scenario") || "stream").trim() || "stream";
}

async function runStream(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(40);
  if (session.killed) return;
  emit(session, "classification", taskId, {
    mode: "append",
    deterministic: true,
    detail: "новый запрос, задача не трогается",
  });
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "running",
    task_status: "planning",
    title: "Раскадровка",
    detail: "разбор",
  });
  emit(session, "agent_status", taskId, { agent: "planner", status: "running", detail: "план" });
  const text = "Соберу раскадровку сцен 3–5 и проверю длину закадра.";
  for (const ch of text) {
    if (session.killed) return;
    emit(session, "token", taskId, { text: ch });
    await sleep(35);
  }
  emit(session, "agent_status", taskId, { agent: "planner", status: "done", detail: "план готов" });
  emit(session, "agent_status", taskId, {
    agent: "critic",
    status: "running",
    task_status: "critic_review",
    detail: "ревью",
  });
  emit(session, "validator_result", taskId, {
    name: "plan_sanity",
    pass: true,
    details: "все действия в whitelist",
    blocking: false,
  });
  emit(session, "agent_status", taskId, { agent: "critic", status: "done", detail: "approve" });
  emit(session, "agent_status", taskId, {
    agent: "executor",
    status: "running",
    task_status: "executing",
    detail: "verify",
  });
  emit(session, "step_started", taskId, {
    step_id: "s1",
    title: "verify_code_state",
    action: "read_pipeline_code",
    paid: false,
  });
  await sleep(30);
  emit(session, "step_finished", taskId, {
    step_id: "s1",
    title: "verify_code_state",
    action: "read_pipeline_code",
    paid: false,
    ok: true,
  });
  emit(session, "checkpoint", taskId, {
    checkpoint_id: "cp1",
    step_id: "s1",
    summary: "код проверен, можно идти дальше",
    spend: 0,
  });
  emit(session, "step_started", taskId, {
    step_id: "s-node",
    title: "get_node",
    action: "get_node",
    paid: false,
    node: NODE_DB,
  });
  emit(session, "step_finished", taskId, {
    step_id: "s-node",
    title: "get_node",
    action: "get_node",
    paid: false,
    ok: true,
    node: NODE_DB,
  });
  emit(session, "step_started", taskId, {
    step_id: "s-edit",
    title: "edit_node",
    action: "edit_node",
    paid: false,
    node: NODE_STALE,
  });
  emit(session, "step_finished", taskId, {
    step_id: "s-edit",
    title: "edit_node",
    action: "edit_node",
    paid: false,
    ok: true,
    node: NODE_STALE,
  });
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "done",
    task_status: "done",
    detail: "готово",
  });
  emit(session, "agent_status", taskId, { agent: "executor", status: "idle", detail: "" });
  emit(session, "task_done", taskId, { summary: "Готово: раскадровка собрана." });
}

async function runStreamHold(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  if (session.killed || session.interrupted) return;
  emit(session, "classification", taskId, {
    mode: "append",
    deterministic: true,
    detail: "поток держится",
  });
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
    title: "Долгий поток",
    detail: "стрим",
  });
  for (let i = 0; i < 24; i += 1) {
    if (session.killed || session.interrupted) return;
    emit(session, "token", taskId, { text: "ДЕРЖУ-ПОТОК " });
    await sleep(50);
  }
  if (session.killed || session.interrupted) return;
  await new Promise((resolve) => {
    session.releaseStream = resolve;
  });
  session.releaseStream = null;
  if (session.killed || session.interrupted) return;
  emit(session, "token", taskId, { text: "КОНЕЦ-ПОТОКА" });
  emit(session, "task_done", taskId, { summary: "поток закончен" });
}

function runStreamHoldFollowup(session, taskId, text) {
  session.lastEcho = text;
  emit(session, "ack", taskId, ackPayload(session));
  emit(session, "classification", taskId, {
    mode: "append",
    deterministic: true,
    detail: "добавка, поток не трогается",
  });
}

async function runInterruptStart(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(30);
  emit(session, "classification", taskId, {
    mode: "append",
    deterministic: true,
    detail: "старт задачи",
  });
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
    title: "Генерация кадров",
    detail: "платный шаг",
  });
  emit(session, "agent_status", taskId, { agent: "executor", status: "running", detail: "run_node" });
  emit(session, "step_started", taskId, {
    step_id: "s-paid",
    title: "run_node images",
    action: "run_node",
    paid: true,
  });
  const text = "Генерирую кадры. ".repeat(12);
  for (const ch of text) {
    if (session.killed || session.interrupted) break;
    emit(session, "token", taskId, { text: ch });
    await sleep(30);
  }
}

function runInterruptFollowup(session, taskId, echo) {
  emit(session, "ack", taskId, { text: echo, accepted: true });
  setTimeout(() => {
    session.interrupted = true;
    emit(session, "classification", taskId, {
      mode: "interrupt",
      deterministic: true,
      detail: "стоп-слово, пауза на границе шага",
    });
    emit(session, "agent_status", taskId, {
      agent: "supervisor",
      status: "paused",
      task_status: "paused",
      detail: "платный шаг остановится на границе",
    });
    emit(session, "step_finished", taskId, {
      step_id: "s-paid",
      title: "run_node images",
      action: "run_node",
      paid: true,
      ok: false,
      note: "платный шаг остановится на границе",
    });
  }, 80);
}

async function runApproval(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(30);
  emit(session, "classification", taskId, {
    mode: "append",
    deterministic: true,
    detail: "запрос с риском",
  });
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "running",
    task_status: "waiting_approval",
    title: "Запуск ноды",
    detail: "approval",
  });
  emit(session, "agent_status", taskId, { agent: "critic", status: "waiting", detail: "эскалация" });
  const text = "План трогает платную ноду. Нужно подтверждение.";
  for (const ch of text) {
    emit(session, "token", taskId, { text: ch });
    await sleep(8);
  }
  emit(session, "step_started", taskId, {
    step_id: "s-node",
    title: "get_node",
    action: "get_node",
    paid: false,
    node: NODE_STALE,
  });
  emit(session, "step_finished", taskId, {
    step_id: "s-node",
    title: "get_node",
    action: "get_node",
    paid: false,
    ok: true,
    node: NODE_STALE,
  });
  emit(session, "approval_required", taskId, {
    approval_id: "appr-1",
    title: "Запустить run_node",
    reason: "Платный шаг, риск high. Лимит прогона $50.",
    risk: "high",
    action: "run_node",
    cost_estimate: 4,
  });
  const decision = await new Promise((resolve) => {
    session.approvalWait = { id: "appr-1", resolve };
  });
  session.approvalWait = null;
  if (decision === "approve") {
    emit(session, "agent_status", taskId, {
      agent: "executor",
      status: "running",
      task_status: "executing",
      detail: "после approve",
    });
    emit(session, "step_started", taskId, {
      step_id: "s-run",
      title: "run_node",
      action: "run_node",
      paid: true,
    });
    emit(session, "step_finished", taskId, {
      step_id: "s-run",
      title: "run_node",
      action: "run_node",
      paid: true,
      ok: true,
    });
    emit(session, "checkpoint", taskId, {
      checkpoint_id: "cp-appr",
      step_id: "s-run",
      summary: "шаг подтверждён и записан",
      spend: 4,
    });
    emit(session, "task_done", taskId, { summary: "Одобрено, шаг выполнен." });
    emit(session, "agent_status", taskId, {
      agent: "supervisor",
      status: "done",
      task_status: "done",
      detail: "готово",
    });
  } else {
    emit(session, "task_failed", taskId, { error: "Отклонено пользователем." });
    emit(session, "agent_status", taskId, {
      agent: "supervisor",
      status: "idle",
      task_status: "failed",
      detail: "reject",
    });
  }
}

async function runKillScenario(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, {
    mode: "append",
    deterministic: true,
    detail: "старт платного шага",
  });
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
    title: "Платная генерация",
    detail: "run_node",
  });
  emit(session, "agent_status", taskId, { agent: "executor", status: "running", detail: "платный шаг" });
  emit(session, "step_started", taskId, {
    step_id: "s-paid",
    title: "run_node images",
    action: "run_node",
    paid: true,
  });
  const text = "Идёт платная генерация. ".repeat(8);
  for (const ch of text) {
    if (session.killed) return;
    emit(session, "token", taskId, { text: ch });
    await sleep(25);
  }
}

async function runReconnect(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(40);
  emit(session, "classification", taskId, {
    mode: "append",
    deterministic: true,
    detail: "проверка обрыва",
  });
  emit(session, "token", taskId, { text: "до обрыва " });
  await sleep(80);
  if (!session.reconnectDropped) {
    session.reconnectDropped = true;
    dropClients(session);
  }
  await sleep(40);
  emit(session, "token", taskId, { text: "REPLAY_OK" });
  emit(session, "task_done", taskId, { summary: "" });
}

async function runBudget(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("append", "лимит бюджета"));
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "waiting_approval",
    task_status: "waiting_approval",
    title: "Бюджет",
    detail: "budget_exhausted",
  });
  const approvalId = "chg-budget-d4";
  emit(session, "approval_required", taskId, {
    change_id: approvalId,
    reason: "budget_exhausted",
    action: "run_node",
    risk: "high",
    title: "Добавить суб-бюджет",
  });
  const decision = await new Promise((resolve) => {
    session.approvalWait = { id: approvalId, resolve };
  });
  session.approvalWait = null;
  if (decision !== "approve") return;
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
    title: "Бюджет",
    detail: "суб-бюджет выдан",
  });
  emit(session, "message", taskId, { role: "assistant", text: "Суб-бюджет добавлен." });
}

async function runPlan(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("append", "план"));
  emit(session, "agent_status", taskId, {
    agent: "planner",
    status: "done",
    task_status: "planning",
    title: "План сцен",
    detail: "план готов",
  });
  emit(session, "checkpoint", taskId, {
    checkpoint_id: "cp-plan",
    summary: "план до критика",
    plan: [
      {
        action: "read_pipeline_code",
        expected: "код",
        paid: false,
        mutating: false,
        risk: "none",
        parallel_group: "reads",
        estimated_cost: 0,
      },
      {
        action: "get_node",
        expected: "нода",
        paid: false,
        mutating: false,
        risk: "low",
        parallel_group: "reads",
        estimated_cost: 0,
      },
      {
        action: "run_node",
        expected: "кадры",
        paid: true,
        mutating: true,
        risk: "high",
        parallel_group: null,
        estimated_cost: 12.5,
      },
    ],
  });
}

async function runCritic(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("append", "критик"));
  emit(session, "agent_status", taskId, {
    agent: "critic",
    status: "done",
    task_status: "waiting_approval",
    title: "Критик",
    detail: "две правки",
  });
  emit(session, "approval_required", taskId, {
    approval_id: "ap-critic",
    title: "Нужно решение",
    reason: "critic_escalation",
    action: "revise_plan",
    risk: "medium",
  });
}

async function runKillMessage(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, {
    mode: "interrupt",
    tier: "fast",
    deterministic: true,
    confidence: 1,
    kill_switch: true,
    detail: "стоп",
  });
}

async function drainQueue(session) {
  const next = session.queue.shift();
  if (!next || session.killed) return;
  session.activeTaskId = next.id;
  session.currentTaskId = next.id;
  session.activeDone = false;
  emit(session, "agent_status", next.id, {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
    title: next.text,
    detail: "стартовала после текущей",
  });
  emit(session, "step_started", next.id, {
    step_id: `q-${next.id}`,
    title: "стартовала после текущей",
    action: "read_pipeline_code",
    paid: false,
  });
  emit(session, "step_finished", next.id, { step_id: `q-${next.id}`, title: "стартовала после текущей", ok: true });
  emit(session, "task_done", next.id, { summary: "стартовала после текущей" });
  session.activeDone = true;
}

async function runHeldTask(session, taskId) {
  session.activeTaskId = taskId;
  session.activeDone = false;
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("append", "текущая задача"));
  emit(session, "agent_status", taskId, {
    agent: "executor",
    status: "running",
    task_status: "executing",
    title: "Текущая",
    detail: "идёт",
  });
  emit(session, "step_started", taskId, {
    step_id: "hold",
    title: "идёт текущая",
    action: "read_pipeline_code",
    paid: false,
  });
  await sleep(2500);
  if (session.killed) return;
  emit(session, "step_finished", taskId, { step_id: "hold", title: "идёт текущая", ok: true });
  emit(session, "agent_status", taskId, {
    agent: "executor",
    status: "done",
    task_status: "done",
    title: "Текущая",
    detail: "готова",
  });
  emit(session, "task_done", taskId, { summary: "текущая готова" });
  session.activeDone = true;
  await drainQueue(session);
}

async function acceptTaskMode(session, text) {
  if (session.activeTaskId && !session.activeDone) {
    const id = (session.taskSeq = (session.taskSeq || 0) + 1);
    session.queue.push({ id, text });
    emit(session, "ack", id, ackPayload(session));
    await sleep(20);
    emit(session, "classification", id, {
      mode: "task",
      tier: "fast",
      deterministic: true,
      confidence: 1,
      detail: "встаёт в очередь за текущей",
    });
    emit(session, "agent_status", id, {
      agent: "supervisor",
      status: "queued",
      task_status: "queued",
      title: text,
      detail: "в очереди за текущей",
    });
    return;
  }
  const id = (session.taskSeq = (session.taskSeq || 0) + 1);
  session.currentTaskId = id;
  session.activeTaskId = id;
  session.activeDone = false;
  emit(session, "ack", id, ackPayload(session));
  await sleep(20);
  emit(session, "classification", id, classPayload("task", "нет текущей, стартует сразу"));
  emit(session, "agent_status", id, {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
    title: text,
    detail: "стартовала после текущей",
  });
  emit(session, "task_done", id, { summary: "стартовала после текущей" });
  session.activeDone = true;
}

async function runLlmBudget(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("append", "ход модели"));
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "running",
    task_status: "executing",
    title: "Ход LLM",
    detail: "router.call",
  });
  emit(session, "message", taskId, {
    role: "assistant",
    text: "Ход сохранён, внутренний бюджет выбран.",
    task_status: "waiting_approval",
    approval: {
      approval_id: "ap-llm-budget",
      reason: "budget_exhausted",
      action: "router.call",
      risk: "high",
    },
  });
}

function startScenario(session, scenario, text, mode) {
  session.messageCount += 1;
  session.lastEcho = text;
  if (mode === "task") {
    void acceptTaskMode(session, text);
    return;
  }
  if (scenario === "interrupt" && session.messageCount > 1) {
    runInterruptFollowup(session, session.currentTaskId, text);
    return;
  }
  if (scenario === "streamhold" && session.messageCount > 1) {
    runStreamHoldFollowup(session, session.currentTaskId, text);
    return;
  }
  session.taskSeq = (session.taskSeq || 0) + 1;
  session.currentTaskId = session.taskSeq;
  const id = session.currentTaskId;
  session.killed = false;
  session.interrupted = false;
  if (scenario === "interrupt") {
    void runInterruptStart(session, id);
    return;
  }
  if (scenario === "streamhold") {
    void runStreamHold(session, id);
    return;
  }
  if (scenario === "approval" || scenario === "approvefail" || scenario === "approve404" || scenario === "approve409") {
    void runApproval(session, id);
    return;
  }
  if (scenario === "budget") {
    void runBudget(session, id);
    return;
  }
  if (scenario === "kill") {
    void runKillScenario(session, id);
    return;
  }
  if (scenario === "reconnect") {
    void runReconnect(session, id);
    return;
  }
  if (scenario === "plan") {
    void runPlan(session, id);
    return;
  }
  if (scenario === "critic") {
    void runCritic(session, id);
    return;
  }
  if (scenario === "killmsg") {
    void runKillMessage(session, id);
    return;
  }
  if (scenario === "task") {
    void runHeldTask(session, id);
    return;
  }
  if (scenario === "llmbudget") {
    void runLlmBudget(session, id);
    return;
  }
  if (scenario === "idle") {
    emit(session, "ack", id, ackPayload(session));
    return;
  }
  if (scenario === "missingproject") {
    void runMissingProject(session, id);
    return;
  }
  if (scenario === "deletion") {
    void runTerminalBlock(session, id, "deletion_blocked");
    return;
  }
  if (scenario === "emptyplan") {
    void runTerminalBlock(session, id, "empty_plan");
    return;
  }
  if (scenario === "unknownreason") {
    void runUnknownReason(session, id);
    return;
  }
  if (scenario === "risk") {
    void runRiskApproval(session, id);
    return;
  }
  void runStream(session, id);
}

async function runMissingProject(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("append", "нет проекта"));
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "waiting_approval",
    task_status: "waiting_approval",
    title: "Платный шаг",
    detail: "missing_project_id",
  });
  emit(session, "approval_required", taskId, {
    approval_id: "ap-missing-project",
    title: "Запустить run_node",
    reason: "missing_project_id",
    risk: "high",
    action: "run_node",
  });
}

async function runTerminalBlock(session, taskId, reason) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("append", reason));
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "failed",
    task_status: "failed",
    title: reason,
    detail: reason,
  });
  emit(session, "task_failed", taskId, { error: reason });
}

async function runRiskApproval(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "classification", taskId, classPayload("task", "риск"));
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "waiting_approval",
    task_status: "waiting_approval",
    title: "Среднее касание",
    detail: "risk",
  });
  emit(session, "approval_required", taskId, {
    approval_id: "ap-risk",
    title: "Запустить test.medium_touch",
    reason: "risk",
    risk: "medium",
    action: "test.medium_touch",
  });
}

async function runUnknownReason(session, taskId) {
  emit(session, "ack", taskId, ackPayload(session));
  await sleep(20);
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "waiting_approval",
    task_status: "waiting_approval",
    title: "Чужая причина",
    detail: "plan_sanity",
  });
  emit(session, "approval_required", taskId, {
    approval_id: "ap-unknown",
    title: "Проверьте план",
    reason: "plan_sanity",
    risk: "low",
    action: "note",
  });
}

function handleKill(session) {
  session.killed = true;
  const taskId = session.currentTaskId;
  if (taskId) {
    emit(session, "agent_status", taskId, {
      agent: "executor",
      status: "cancelled",
      detail: "неплатные шаги отменены",
    });
    emit(session, "agent_status", taskId, {
      agent: "supervisor",
      status: "cancelled",
      task_status: "cancelled",
      detail: "новые действия запрещены",
    });
    const paidOpen = session.events.some((event) => {
      if (event.type !== "step_started" || !event.payload || event.payload.step_id !== "s-paid") return false;
      return !session.events.some(
        (done) => done.type === "step_finished" && done.payload && done.payload.step_id === "s-paid",
      );
    });
    if (paidOpen) {
      emit(session, "step_finished", taskId, {
        step_id: "s-paid",
        title: "run_node images",
        action: "run_node",
        paid: true,
        ok: false,
        note: "платный шаг остановится на границе",
      });
    }
  }
  return {
    ok: true,
    status: "cancelled",
    stop_key_set: true,
    detail: "Неплатные шаги отменены, новые действия запрещены. Платный шаг остановится на границе.",
  };
}

function handleStream(req, res, sessionId) {
  const session = sessionOf(sessionId);
  const lastRaw = req.headers["last-event-id"];
  const last = Number(Array.isArray(lastRaw) ? lastRaw[0] : lastRaw || 0) || 0;
  cors(res);
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
  });
  if (typeof res.flushHeaders === "function") res.flushHeaders();
  res.write(":\n\n");
  for (const event of session.events) {
    if (event.seq > last) writeEvent(res, event);
  }
  session.clients.add(res);
  const detach = () => {
    session.clients.delete(res);
  };
  req.on("close", detach);
  res.on("close", detach);
}

const ALLOWLIST = JSON.parse(
  fs.readFileSync(
    path.join(path.dirname(fileURLToPath(import.meta.url)), "../../src/lib/orchestratorSanitizeAllowlist.json"),
    "utf8",
  ),
);
const REPORT_TAGS = new Set(ALLOWLIST.allowed_tags);
const REPORT_PROTOCOLS = new Set(ALLOWLIST.allowed_protocols.map((item) => String(item).toLowerCase()));
const REPORT_IMG_SRC_PREFIX = "/api/orchestrator/reports/";
const xssHits = [];
const reports = new Map();
let reportSeq = 1;
let versionSeq = 1;

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    if (char === "&") return "&amp;";
    if (char === "<") return "&lt;";
    if (char === ">") return "&gt;";
    if (char === '"') return "&quot;";
    return "&#39;";
  });
}

/**
 * Только мок, не референс санитайзера.
 * Референс для WS2 — allowlist JSON: web/src/lib/orchestratorSanitizeAllowlist.json.
 */
function sanitizeHtml(raw) {
  let html = String(raw ?? "");
  html = html.replace(/<!--[\s\S]*?-->/g, "");
  html = html.replace(/<(script|style|iframe|svg|math|object|embed|link|meta|base|form)\b[^>]*>[\s\S]*?<\/\1>/gi, "");
  html = html.replace(/<(script|style|iframe|svg|math|object|embed|link|meta|base|form)\b[^>]*\/?>/gi, "");
  html = html.replace(/\s+on[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  html = html.replace(/\s(?:href|src)\s*=\s*("|\')\s*javascript:[\s\S]*?\1/gi, "");
  html = html.replace(/\s(?:href|src)\s*=\s*javascript:[^\s>]+/gi, "");
  return html.replace(/<\/?([a-zA-Z0-9]+)([^>]*)>/g, (full, name, attrs) => {
    const tag = name.toLowerCase();
    if (!REPORT_TAGS.has(tag)) return "";
    if (full.startsWith("</")) return `</${tag}>`;
    let clean = "";
    const allowedAttr = new Set([...(ALLOWLIST.allowed_attributes[tag] || []), ...(ALLOWLIST.allowed_attributes["*"] || [])]);
    if (tag === "a" && allowedAttr.has("href")) {
      const href = /\shref\s*=\s*("|\')([^"']+)\1/i.exec(` ${attrs}`);
      const scheme = href ? /^([a-z][a-z0-9+.-]*):/i.exec(href[2].trim()) : null;
      if (href && scheme && REPORT_PROTOCOLS.has(scheme[1].toLowerCase())) clean += ` href="${href[2]}"`;
    }
    if (tag === "img" && allowedAttr.has("src")) {
      const src = /\ssrc\s*=\s*("|\')([^"']+)\1/i.exec(` ${attrs}`);
      if (src && src[2].startsWith(REPORT_IMG_SRC_PREFIX)) clean += ` src="${src[2]}"`;
    }
    return `<${tag}${clean}>`;
  });
}

function headingTooLong(list) {
  const source = Array.isArray(list) ? list : [];
  return source.some((item) => String(item?.heading || "").length > 200);
}

function cleanSections(list) {
  const source = Array.isArray(list) ? list : [];
  return source.map((item) => ({
    heading: String(item?.heading || ""),
    html: sanitizeHtml(item?.html || ""),
    data_table: item?.data_table && typeof item.data_table === "object" ? item.data_table : null,
  }));
}

function rejectStaleVersion(res, report, body) {
  const base = body?.base_version;
  if (typeof base !== "number" || base !== report.version) {
    sendJson(res, 409, { detail: "conflict", current_version: report.version });
    return true;
  }
  return false;
}

function reportRow(report) {
  return {
    id: report.id,
    project_id: report.project_id,
    slug: report.slug,
    title: report.title,
    file_path: report.file_path,
    editable: report.editable,
    version: report.version,
    kind: report.kind,
    created_change_id: report.created_change_id,
    updated_change_id: report.updated_change_id,
    created_at: report.created_at,
    updated_at: report.updated_at,
  };
}

function pushVersion(report, sections, changeId) {
  report.versions.push({
    id: versionSeq++,
    report_id: report.id,
    version: report.version,
    sections_json: JSON.stringify(sections),
    html_sha256: `v${report.version}`,
    change_id: changeId,
    created_at: report.updated_at,
  });
}

function applySections(report, sections, changeId) {
  const clean = cleanSections(sections);
  report.version += 1;
  report.sections = clean;
  report.updated_at = "2026-10-15T09:00:00.000Z";
  report.updated_change_id = changeId;
  pushVersion(report, clean, changeId);
  return { ok: true, report_id: report.id, version: report.version, change_id: changeId };
}

function createReport(body) {
  const title = String(body.title || "").trim();
  if (!title) return null;
  const id = reportSeq++;
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "report";
  const report = {
    id,
    project_id: Number(body.project_id) || 0,
    slug,
    title,
    file_path: `data/reports/${Number(body.project_id) || 0}/${slug}.html`,
    editable: body.editable === false ? 0 : 1,
    version: 0,
    kind: "report",
    created_change_id: `chg-${id}-1`,
    updated_change_id: null,
    created_at: "2026-10-15T09:00:00.000Z",
    updated_at: "2026-10-15T09:00:00.000Z",
    sections: [],
    versions: [],
  };
  reports.set(id, report);
  return applySections(report, body.sections, `chg-${id}-1`);
}

function renderReportHtml(report) {
  const body = report.sections
    .map((section) => `<h2>${escapeHtml(section.heading)}</h2>${sanitizeHtml(section.html)}`)
    .join("");
  return `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>${body}</body></html>`;
}

function sendHtml(res, html) {
  cors(res);
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
  });
  res.end(html);
}

function seedReports() {
  const sections = [{ heading: "Сцена 3", html: "<p>Он смотрит в окно и молчит.</p>", data_table: null }];
  const report = {
    id: reportSeq++,
    project_id: 13,
    slug: "scenes",
    title: "Разбор сцен",
    file_path: "data/reports/13/scenes.html",
    editable: 1,
    version: 1,
    kind: "report",
    created_change_id: "chg-seed",
    updated_change_id: "chg-seed",
    created_at: "2026-10-15T09:00:00.000Z",
    updated_at: "2026-10-15T09:00:00.000Z",
    sections,
    versions: [],
  };
  pushVersion(report, sections, "chg-seed");
  reports.set(report.id, report);
}

seedReports();

const server = http.createServer(async (req, res) => {
  if (!req.url || !req.method) {
    sendJson(res, 400, { detail: "bad request" });
    return;
  }
  if (req.method === "OPTIONS") {
    cors(res);
    res.writeHead(204);
    res.end();
    return;
  }
  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const parts = url.pathname.split("/").filter(Boolean);

  if (url.pathname === "/xss-beacon") {
    xssHits.push(url.search);
    cors(res);
    res.writeHead(204);
    res.end();
    return;
  }

  if (!authorized(req)) {
    sendJson(res, 401, { detail: "unauthorized" });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/orchestrator/test/release-stream") {
    const body = await readBody(req);
    const session = sessionOf(String(body.session_id || "studio"));
    const release = session.releaseStream;
    session.releaseStream = null;
    if (typeof release === "function") release();
    sendJson(res, 200, { ok: true });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/orchestrator/test/emit") {
    const body = await readBody(req);
    const session = sessionOf(String(body.session_id || "studio"));
    const taskRaw = body.task_id;
    const taskId = taskRaw == null || taskRaw === "" ? null : Number(taskRaw);
    const eventSession = body.event_session_id ? String(body.event_session_id) : session.id;
    emit(
      session,
      String(body.type || "agent_status"),
      Number.isFinite(taskId) ? taskId : null,
      body.payload && typeof body.payload === "object" ? body.payload : {},
      eventSession,
    );
    sendJson(res, 200, { ok: true });
    return;
  }

  if (req.method === "GET" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "chat" && parts[4] === "stream") {
    handleStream(req, res, decodeURIComponent(parts[3]));
    return;
  }

  if (req.method === "POST" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "chat" && parts[4] === "message") {
    const body = await readBody(req);
    const text = String(body.text || "").trim();
    if (!text) {
      sendJson(res, 400, { detail: "empty message" });
      return;
    }
    const session = sessionOf(decodeURIComponent(parts[3]));
    const scenario = scenarioOf(req, url);
    startScenario(session, scenario, text, body.mode);
    sendJson(res, 200, { ok: true, session: session.id, ack_seq: session.seq });
    return;
  }

  if (
    req.method === "POST" &&
    parts[0] === "api" &&
    parts[1] === "orchestrator" &&
    parts[2] === "approvals" &&
    (parts[4] === "approve" || parts[4] === "reject")
  ) {
    const approvalId = decodeURIComponent(parts[3]);
    const decision = parts[4] === "approve" ? "approve" : "reject";
    const scenario = scenarioOf(req, url);
    if (decision === "approve" && scenario === "approvefail") {
      await sleep(800);
      sendJson(res, 500, { detail: "approve failed" });
      return;
    }
    if (decision === "approve" && scenario === "approve404") {
      sendJson(res, 404, { detail: "approval not found" });
      return;
    }
    if (decision === "approve" && scenario === "approve409") {
      sendJson(res, 409, { detail: "approval conflict" });
      return;
    }
    let matched = false;
    for (const session of sessions.values()) {
      const wait = session.approvalWait;
      if (wait && wait.id === approvalId) {
        wait.resolve(decision);
        session.approvalWait = null;
        matched = true;
        break;
      }
    }
    if (!matched) {
      sendJson(res, 404, { detail: "approval not found" });
      return;
    }
    sendJson(res, 200, { ok: true, approval_id: approvalId, decision, matched: true });
    return;
  }

  if (req.method === "GET" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "projects" && parts[4] === "reports") {
    const projectId = Number(parts[3]);
    const list = [...reports.values()].filter((report) => report.project_id === projectId).map(reportRow);
    sendJson(res, 200, { reports: list });
    return;
  }

  if (req.method === "GET" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "reports" && parts.length === 4) {
    const report = reports.get(Number(parts[3]));
    if (!report) {
      sendJson(res, 404, { detail: "not found" });
      return;
    }
    sendJson(res, 200, { report: reportRow(report), sections: report.sections, versions: report.versions });
    return;
  }

  if (req.method === "GET" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "reports" && parts[4] === "html") {
    const report = reports.get(Number(parts[3]));
    if (!report) {
      sendJson(res, 404, { detail: "not found" });
      return;
    }
    sendHtml(res, renderReportHtml(report));
    return;
  }

  if (req.method === "POST" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "actions" && parts[3] === "create_report") {
    const body = await readBody(req);
    if (headingTooLong(body.sections)) {
      sendJson(res, 422, { detail: "heading longer than 200" });
      return;
    }
    const created = createReport(body);
    if (!created) {
      sendJson(res, 400, { detail: "title required" });
      return;
    }
    sendJson(res, 200, created);
    return;
  }

  if (req.method === "POST" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "actions" && parts[3] === "update_report") {
    const body = await readBody(req);
    const report = reports.get(Number(body.report_id));
    if (!report) {
      sendJson(res, 404, { detail: "not found" });
      return;
    }
    if (headingTooLong(body.sections_patch)) {
      sendJson(res, 422, { detail: "heading longer than 200" });
      return;
    }
    if (rejectStaleVersion(res, report, body)) return;
    const changeId = `chg-${report.id}-${report.version + 1}`;
    sendJson(res, 200, applySections(report, body.sections_patch, changeId));
    return;
  }

  if (req.method === "PUT" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "reports" && parts.length === 4) {
    const body = await readBody(req);
    const report = reports.get(Number(parts[3]));
    if (!report) {
      sendJson(res, 404, { detail: "not found" });
      return;
    }
    if (headingTooLong(body.sections_patch)) {
      sendJson(res, 422, { detail: "heading longer than 200" });
      return;
    }
    if (rejectStaleVersion(res, report, body)) return;
    const changeId = `chg-${report.id}-${report.version + 1}`;
    sendJson(res, 200, applySections(report, body.sections_patch, changeId));
    return;
  }

  if (req.method === "POST" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "reports" && parts[4] === "rollback") {
    const body = await readBody(req);
    const report = reports.get(Number(parts[3]));
    const version = report?.versions.find((item) => item.version === Number(body.version));
    if (!report || !version) {
      sendJson(res, 404, { detail: "not found" });
      return;
    }
    const sections = JSON.parse(version.sections_json || "[]");
    const changeId = `chg-${report.id}-rollback-${version.version}`;
    sendJson(res, 200, applySections(report, sections, changeId));
    return;
  }

  if (req.method === "POST" && parts[0] === "api" && parts[1] === "orchestrator" && parts[2] === "kill") {
    const body = await readBody(req);
    const sessionId = String(body.session_id || "studio");
    const session = sessionOf(sessionId);
    sendJson(res, 200, handleKill(session));
    return;
  }

  sendJson(res, 404, { detail: "not found" });
});

server.listen(PORT, HOST, () => {
  process.stdout.write(`orch-mock listening on http://${HOST}:${PORT}\n`);
});
