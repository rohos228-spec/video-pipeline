/**
 * Детерминированный мок SSE/REST оркестратора до готовности WS3.
 *
 *   node web/e2e/orchestrator/mock-server.mjs
 *
 * Токен: orch-dev-token (или ORCH_MOCK_TOKEN).
 * Сценарий: заголовок X-Orch-Scenario или ?scenario=
 *   stream | interrupt | approval | kill | reconnect | idle
 * Любой другой токен и пустой Authorization — 401.
 *
 * События копятся в памяти сессии и отдаются повторно по Last-Event-ID.
 */

import http from "node:http";

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
  res.setHeader("Access-Control-Expose-Headers", "Content-Type");
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

function emit(session, type, taskId, payload) {
  const event = {
    type,
    task_id: taskId,
    payload,
    seq: ++session.seq,
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

function scenarioOf(req, url) {
  const header = req.headers["x-orch-scenario"];
  const fromHeader = Array.isArray(header) ? header[0] : header;
  return String(fromHeader || url.searchParams.get("scenario") || "stream").trim() || "stream";
}

async function runStream(session, taskId) {
  emit(session, "ack", taskId, { text: "принял, разбираю", echo: session.lastEcho || "" });
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
  emit(session, "agent_status", taskId, {
    agent: "supervisor",
    status: "done",
    task_status: "done",
    detail: "готово",
  });
  emit(session, "agent_status", taskId, { agent: "executor", status: "idle", detail: "" });
  emit(session, "task_done", taskId, { summary: "Готово: раскадровка собрана." });
}

async function runInterruptStart(session, taskId) {
  emit(session, "ack", taskId, { text: "принял, разбираю", echo: session.lastEcho || "" });
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
  emit(session, "ack", taskId, { text: "принял, разбираю", echo });
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
  emit(session, "ack", taskId, { text: "принял, разбираю", echo: session.lastEcho || "" });
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
  emit(session, "approval_required", taskId, {
    approval_id: "appr-1",
    title: "Запустить run_node",
    reason: "Платный шаг, риск high. Лимит прогона $50.",
    risk: "high",
    action: "run_node",
    cost_estimate: 4,
  });
  const decision = await new Promise((resolve) => {
    session.approvalWait = resolve;
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
  emit(session, "ack", taskId, { text: "принял, разбираю", echo: session.lastEcho || "" });
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
  emit(session, "ack", taskId, { text: "принял, разбираю", echo: session.lastEcho || "" });
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

function startScenario(session, scenario, text) {
  session.messageCount += 1;
  session.lastEcho = text;
  const taskId = session.currentTaskId || `task-${session.messageCount}`;
  if (scenario === "interrupt" && session.messageCount > 1) {
    runInterruptFollowup(session, session.currentTaskId || taskId, text);
    return;
  }
  session.currentTaskId = `task-${session.messageCount}`;
  const id = session.currentTaskId;
  session.killed = false;
  session.interrupted = false;
  if (scenario === "interrupt") {
    void runInterruptStart(session, id);
    return;
  }
  if (scenario === "approval") {
    void runApproval(session, id);
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
  if (scenario === "idle") {
    emit(session, "ack", id, { text: "принял, разбираю", echo: text });
    return;
  }
  void runStream(session, id);
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
    emit(session, "step_finished", taskId, {
      step_id: "s-paid",
      title: "run_node images",
      action: "run_node",
      paid: true,
      ok: false,
      note: "платный шаг остановится на границе",
    });
  }
  return {
    ok: true,
    status: "cancelled",
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

  if (!authorized(req)) {
    sendJson(res, 401, { detail: "unauthorized" });
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
    startScenario(session, scenario, text);
    sendJson(res, 200, { ok: true, message_id: `m-${session.messageCount}` });
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
    let matched = false;
    for (const session of sessions.values()) {
      if (session.approvalWait) {
        session.approvalWait(decision);
        matched = true;
        break;
      }
    }
    sendJson(res, 200, { ok: true, approval_id: approvalId, decision, matched });
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
