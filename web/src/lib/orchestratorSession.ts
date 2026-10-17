/**
 * Сведение потока OrchEvent в состояние экрана.
 * Дедуп по seq делает вызывающий код до dispatch.
 */

import {
  readPaidOutcomeForecast,
  readPaidOutcomeRestartOffered,
  readPaidOutcomeRetryArmed,
} from "./orchestratorPaidOutcome";
import {
  AGENT_ROLES,
  BLOCK_OUTCOMES,
  type AgentRole,
  type AgentStatusPayload,
  type ApprovalPayload,
  type CheckpointPayload,
  type ClassificationPayload,
  type MessageMode,
  type NodeDef,
  type OrchEvent,
  type StepPayload,
  TASK_STATUSES,
  type TaskStatus,
  type ValidatorResultPayload,
} from "./orchestratorTypes";

const NODE_RESULT_ACTIONS = new Set([
  "get_node",
  "resolve_node",
  "edit_node",
  "edit_node_prompt",
  "edit_node_params",
]);

export function isNodeResultAction(action: string): boolean {
  return NODE_RESULT_ACTIONS.has(action);
}

function optNum(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function readNodeDef(value: unknown): NodeDef | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (raw.source !== "git" && raw.source !== "db") return null;
  const params = raw.params && typeof raw.params === "object" ? (raw.params as Record<string, unknown>) : {};
  return {
    node_key: typeof raw.node_key === "string" ? raw.node_key : "",
    prompt_template: typeof raw.prompt_template === "string" ? raw.prompt_template : "",
    params,
    source: raw.source,
    version: optNum(raw.version) ?? 0,
    db_version: optNum(raw.db_version),
    git_sha256: typeof raw.git_sha256 === "string" ? raw.git_sha256 : null,
    stale_base: raw.stale_base === true,
  };
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  taskId: string | null;
  /**
   * r3.4: id реплики (`message_start` / `token` / `message_end`).
   * Нет поля — склейка r3.3 по последнему пузырю ассистента той же задачи.
   */
  messageId?: number | null;
  /** `message_end` закрыл буфер. Итог ещё может прийти событием `message`. */
  closed?: boolean;
  /** Событие `message` уже подставило канонический текст. Повторно не дописываем. */
  finalized?: boolean;
}

export interface AgentView {
  role: AgentRole;
  status: string;
  detail: string;
}

export interface StepView {
  id: string;
  title: string;
  action: string;
  paid: boolean;
  status: "running" | "done" | "failed";
  note: string;
  node: NodeDef | null;
}

/** Шаг из CheckpointData.plan. Поля как у PlanStep. */
export interface PlanStepView {
  action: string;
  expected: string;
  paid: boolean;
  mutating: boolean;
  risk: string;
  parallelGroup: string | null;
  estimatedCost: number;
}

export interface PlanGroup {
  key: string;
  parallel: boolean;
  group: string;
  steps: PlanStepView[];
}

const BLOCKING_PLAN_RISK = new Set(["medium", "high"]);

export function readPlanSteps(value: unknown): PlanStepView[] {
  if (!Array.isArray(value)) return [];
  const steps: PlanStepView[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const raw = item as Record<string, unknown>;
    const action = typeof raw.action === "string" ? raw.action : "";
    if (!action) continue;
    const group = raw.parallel_group;
    const cost = raw.estimated_cost;
    steps.push({
      action,
      expected: typeof raw.expected === "string" ? raw.expected : "",
      paid: raw.paid === true,
      mutating: raw.mutating === true,
      risk: typeof raw.risk === "string" && raw.risk ? raw.risk : "none",
      parallelGroup: typeof group === "string" && group ? group : null,
      estimatedCost: typeof cost === "number" && Number.isFinite(cost) ? cost : 0,
    });
  }
  return steps;
}

/** Read-only шаг можно класть в параллельную группу: не mutating, не paid, риск не medium/high. */
export function isParallelReadonly(step: PlanStepView): boolean {
  return Boolean(step.parallelGroup) && !step.mutating && !step.paid && !BLOCKING_PLAN_RISK.has(step.risk);
}

export function groupPlanSteps(steps: PlanStepView[]): PlanGroup[] {
  const groups: PlanGroup[] = [];
  for (const [index, step] of steps.entries()) {
    const last = groups[groups.length - 1];
    if (isParallelReadonly(step) && last?.parallel && last.group === step.parallelGroup) {
      last.steps.push(step);
      continue;
    }
    groups.push({
      key: `${index}-${step.action}`,
      parallel: isParallelReadonly(step),
      group: step.parallelGroup ?? "",
      steps: [step],
    });
  }
  return groups.map((group) => ({ ...group, parallel: group.parallel && group.steps.length > 1 }));
}

export interface CheckpointView {
  id: string;
  stepId: string;
  summary: string;
  spend: number | null;
}

export interface ValidatorView {
  name: string;
  pass: boolean;
  details: string;
  blocking: boolean;
}

export interface ApprovalView {
  id: string;
  /** id пришёл в payload сервера. Выдуманный ключ карточку не закрывает. */
  serverId: boolean;
  /** ApprovalEvent.change_id. Событие закрывает карточку только этим значением. */
  changeId: string | null;
  /** Сессия события, которое открыло карточку. Без change_id закрытие сверяет и её. */
  sessionId: string | null;
  taskId: string | null;
  title: string;
  reason: string;
  risk: string;
  action: string;
  costEstimate: number | null;
  /** Прогноз paid_outcome_unknown (`forecast_usd`). */
  forecast: number | null;
  /** В `decisions` есть `retry_step`. Иначе кнопка перезапуска неактивна. */
  restartOffered: boolean;
  /** Первое `approval_required` несёт `retry_armed: false`. Подтверждение только при true. */
  retryArmed: boolean;
  resolved: "approved" | "rejected" | "closed" | null;
}

export interface TaskView {
  id: string;
  title: string;
  status: TaskStatus;
  steps: StepView[];
  checkpoints: CheckpointView[];
  validators: ValidatorView[];
  summary: string;
  error: string;
  plan: PlanStepView[];
}

export interface AckView {
  echo: string;
  text: string;
  seq: number;
}

export interface ClassificationView {
  mode: MessageMode;
  deterministic: boolean;
  detail: string;
  seq: number;
  killSwitch: boolean;
}

export type KillPhase = "stopping" | "stopped" | "error" | "unconfirmed";

export interface KillNoticeView {
  text: string;
  /** Платный шаг останавливается на границе. UI не ищет эту фразу в тексте. */
  paidBoundary: boolean;
  /** stopping — запрос ушёл; stopped — stop_key_set; error — отказ; unconfirmed — задача отменена без ключа. */
  phase: KillPhase;
  /** Задача, которую останавливали. Чужой task_id баннер не двигает. */
  taskId: string | null;
  sessionId: string | null;
  /** Поздний stop_key_set=false. «Остановлено» не откатывается. */
  lateStopWarning: string;
}

export interface OrchScreenState {
  messages: ChatMessage[];
  agents: AgentView[];
  tasks: TaskView[];
  approvals: ApprovalView[];
  ack: AckView | null;
  classification: ClassificationView | null;
  killNotice: KillNoticeView | null;
}

export type OrchScreenAction =
  | { type: "event"; event: OrchEvent }
  | { type: "user-message"; id: string; text: string; taskId: string | null }
  | {
      type: "kill-notice";
      detail: string;
      paidBoundary: boolean;
      phase: KillPhase;
      taskId?: string | null;
      sessionId?: string | null;
    }
  | { type: "approval-resolved"; id: string; decision: "approved" | "rejected" | "closed" }
  | { type: "reset" };

const IDLE_AGENTS: AgentView[] = AGENT_ROLES.map((role) => ({
  role,
  status: "idle",
  detail: "",
}));

export function initialOrchScreen(): OrchScreenState {
  return {
    messages: [],
    agents: IDLE_AGENTS.map((agent) => ({ ...agent })),
    tasks: [],
    approvals: [],
    ack: null,
    classification: null,
    killNotice: null,
  };
}

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  queued: "в очереди",
  planning: "планирование",
  critic_review: "ревью критика",
  executing: "выполнение",
  paused: "пауза",
  waiting_approval: "ждёт подтверждения",
  done: "готово",
  failed: "ошибка",
  cancelled: "отменено",
};

export const AGENT_LABEL: Record<AgentRole, string> = {
  supervisor: "supervisor",
  planner: "planner",
  critic: "critic",
  executor: "executor",
  classifier: "classifier",
  summarizer: "summarizer",
};

function asRecord(payload: unknown): Record<string, unknown> {
  if (payload && typeof payload === "object") return payload as Record<string, unknown>;
  return {};
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** WS3: task_id — integer. В стейте карточки ключ строковый. */
function asTaskStatus(value: string): TaskStatus | null {
  return (TASK_STATUSES as readonly string[]).includes(value) ? (value as TaskStatus) : null;
}

const TERMINAL_TASK = new Set<TaskStatus>(["done", "failed", "cancelled"]);

/** Исход из BLOCK_OUTCOMES. Причины нет в таблице — null, UI не падает и не ставит failed сам. */
export function blockOutcome(reason: string): TaskStatus | null {
  if (Object.prototype.hasOwnProperty.call(BLOCK_OUTCOMES, reason)) {
    return BLOCK_OUTCOMES[reason as keyof typeof BLOCK_OUTCOMES];
  }
  return null;
}

export function isTerminalBlock(reason: string): boolean {
  const outcome = blockOutcome(reason);
  return outcome != null && TERMINAL_TASK.has(outcome);
}

function taskKey(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value) return value;
  return null;
}

/** Непустая строка. Число и пустота — не id. */
export function serverApprovalId(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/**
 * Id решения из шины ws3-r3.3: `ApprovalEvent.change_id`.
 * Явный `approval_id` оставлен для моков, у которых это поле уже есть.
 * Вложенный `payload` — тот же объект, если событие завернуло ApprovalEvent.
 */
export function approvalServerKey(body: Record<string, unknown>): string | null {
  const direct = serverApprovalId(body.approval_id) ?? serverApprovalId(body.change_id);
  if (direct) return direct;
  const nested = body.payload;
  if (!nested || typeof nested !== "object") return null;
  const inner = nested as Record<string, unknown>;
  return serverApprovalId(inner.approval_id) ?? serverApprovalId(inner.change_id);
}

function approvalDecision(value: unknown): "approved" | "rejected" | null {
  if (value === "approved" || value === "approve") return "approved";
  if (value === "rejected" || value === "reject") return "rejected";
  return null;
}

/** change_id решения. Чужой или пустой id карточку не закрывает. */
function approvalChangeId(body: Record<string, unknown>): string | null {
  const direct = serverApprovalId(body.change_id);
  if (direct) return direct;
  const nested = body.payload;
  if (!nested || typeof nested !== "object") return null;
  return serverApprovalId((nested as Record<string, unknown>).change_id);
}

function closeServerApproval(state: OrchScreenState, changeId: string, decision: "approved" | "rejected"): void {
  const item = state.approvals.find((entry) => entry.serverId && entry.changeId === changeId);
  if (!item || item.resolved) return;
  item.resolved = decision;
}

/** Карточка без change_id закрывается только сменой статуса своей задачи и сессии. */
function closeApprovalsWithoutChange(state: OrchScreenState, taskId: string, sessionId: string | null): void {
  if (!taskId || !sessionId) return;
  for (const item of state.approvals) {
    if (item.resolved || item.changeId) continue;
    if (item.taskId !== taskId || item.sessionId !== sessionId) continue;
    item.resolved = "closed";
  }
}

function applyTaskStatus(
  state: OrchScreenState,
  taskId: string,
  status: TaskStatus,
  sessionId: string | null,
): TaskView {
  const task = ensureTask(state.tasks, taskId);
  if (task.status !== status) {
    closeApprovalsWithoutChange(state, taskId, sessionId);
    task.status = status;
  }
  return task;
}

function isTerminalKillStatus(value: string): boolean {
  return value === "cancelled" || value === "killed";
}

function readStopKey(payload: Record<string, unknown>): boolean | null {
  if (payload.stop_key_set === true) return true;
  if (payload.stop_key_set === false) return false;
  return null;
}

function markKill(
  next: OrchScreenState,
  phase: KillPhase,
  text: string,
  paidBoundary: boolean,
  target?: { taskId: string | null; sessionId: string | null },
): void {
  const current = next.killNotice;
  if (current?.phase === "stopped" && phase !== "stopped") return;
  if (current?.phase === "error" && phase !== "error" && phase !== "stopped") return;
  next.killNotice = {
    phase,
    text,
    paidBoundary,
    taskId: target?.taskId ?? current?.taskId ?? null,
    sessionId: target?.sessionId ?? current?.sessionId ?? null,
    lateStopWarning: "",
  };
}

/** «Остановлено» только при stop_key_set=true. Иначе запрос или ошибка. */
function noteKillRequested(
  next: OrchScreenState,
  payload: Record<string, unknown>,
  target: { taskId: string | null; sessionId: string | null },
): void {
  const stopKey = readStopKey(payload);
  const detail = str(payload.detail);
  if (stopKey === true) {
    markKill(next, "stopped", detail, true, target);
    return;
  }
  if (stopKey === false) {
    if (next.killNotice?.phase === "stopped") {
      next.killNotice = {
        ...next.killNotice,
        lateStopWarning: detail || "Поздний ответ: стоп-ключ не установлен",
      };
      return;
    }
    markKill(next, "error", detail || "Стоп-ключ не установлен", false, target);
    return;
  }
  if (next.killNotice?.phase === "stopped" || next.killNotice?.phase === "error") return;
  markKill(next, "stopping", "", true, target);
}

/** cancelled/killed той же задачи и сессии. Шаг и чужая задача сюда не попадают. Ошибку ключа не затирает. */
function noteTaskCancelled(next: OrchScreenState, event: OrchEvent): void {
  const notice = next.killNotice;
  if (!notice || notice.phase !== "stopping") return;
  if (!notice.taskId || !notice.sessionId) return;
  if (taskKey(event.task_id) !== notice.taskId) return;
  if (event.session_id !== notice.sessionId) return;
  next.killNotice = {
    phase: "unconfirmed",
    text: "Задача отменена, стоп-ключ не подтверждён.",
    paidBoundary: false,
    taskId: notice.taskId,
    sessionId: notice.sessionId,
    lateStopWarning: "",
  };
}

function absorbLlmSignal(
  state: OrchScreenState,
  taskId: string | null,
  payload: Record<string, unknown>,
  seq: number,
  sessionId: string | null,
): void {
  const reported = asTaskStatus(str(payload.task_status));
  const approval = payload.approval;
  const body = approval && typeof approval === "object" ? (approval as Record<string, unknown>) : null;
  const reason = body ? str(body.reason) : "";
  let nextStatus = reported;
  if (reason && isTerminalBlock(reason)) {
    nextStatus = blockOutcome(reason) ?? "failed";
  } else if (
    reason &&
    (reported === "waiting_approval" || blockOutcome(reason) === "waiting_approval" || reason === "budget_exhausted")
  ) {
    nextStatus = "waiting_approval";
  }
  if (taskId && nextStatus) {
    const task = applyTaskStatus(state, taskId, nextStatus, sessionId);
    if (reason && isTerminalBlock(reason)) task.error = reason;
  }
  if (!reason || !body) return;
  const fromServer = approvalServerKey(body);
  const id = fromServer ?? `local-llm-${seq}`;
  if (!state.approvals.some((item) => item.id === id)) {
    state.approvals.push({
      id,
      serverId: fromServer != null,
      changeId: approvalChangeId(body),
      sessionId,
      taskId: taskKey(body.task_id) || taskId,
      title: str(body.title, "Нужно подтверждение"),
      reason,
      risk: str(body.risk),
      action: str(body.action),
      costEstimate: typeof body.cost_estimate === "number" ? body.cost_estimate : null,
      forecast: readPaidOutcomeForecast(body),
      restartOffered: readPaidOutcomeRestartOffered(body),
      retryArmed: readPaidOutcomeRetryArmed(body),
      resolved: null,
    });
  }
}

function ensureTask(tasks: TaskView[], id: string, title?: string): TaskView {
  let task = tasks.find((item) => item.id === id);
  if (!task) {
    task = {
      id,
      title: title || "Задача",
      status: "queued",
      steps: [],
      checkpoints: [],
      validators: [],
      summary: "",
      error: "",
      plan: [],
    };
    tasks.push(task);
  } else if (title && task.title === "Задача") {
    task.title = title;
  }
  return task;
}

function readMessageId(payload: Record<string, unknown>): number | null {
  const raw = payload.message_id;
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return raw;
}

function messageRole(payload: Record<string, unknown>): "user" | "assistant" {
  return str(payload.role) === "user" ? "user" : "assistant";
}

/** Пузырь r3.3: без message_id, последняя реплика ассистента той же задачи. */
function appendToken(state: OrchScreenState, taskId: string | null, text: string): void {
  if (!text) return;
  const messages = state.messages;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (
      message &&
      message.role === "assistant" &&
      message.taskId === taskId &&
      (message.messageId == null)
    ) {
      message.text += text;
      return;
    }
  }
  messages.push({
    id: `asst-${taskId ?? "none"}-${messages.length}`,
    role: "assistant",
    text,
    taskId,
  });
}

function bubbleByMessageId(state: OrchScreenState, messageId: number): ChatMessage | undefined {
  return state.messages.find((message) => message.messageId === messageId);
}

/** Открывает пустой пузырь. Повторный start с тем же id второй пузырь не создаёт. */
function openMessageBubble(
  state: OrchScreenState,
  taskId: string | null,
  messageId: number,
  role: "user" | "assistant",
): ChatMessage {
  const existing = bubbleByMessageId(state, messageId);
  if (existing) return existing;
  const created: ChatMessage = {
    id: `asst-mid-${messageId}`,
    role,
    text: "",
    taskId,
    messageId,
    closed: false,
    finalized: false,
  };
  state.messages.push(created);
  return created;
}

function appendTokenForMessage(
  state: OrchScreenState,
  taskId: string | null,
  messageId: number,
  text: string,
): void {
  if (!text) return;
  const bubble = openMessageBubble(state, taskId, messageId, "assistant");
  if (bubble.finalized) return;
  bubble.text += text;
}

/** Самый ранний незакрытый итогом буфер. Два id подряд не делят один текст. */
function pendingBoundedMessage(state: OrchScreenState): ChatMessage | undefined {
  return state.messages.find(
    (message) => message.role === "assistant" && message.messageId != null && message.finalized !== true,
  );
}

function applyFinalMessage(state: OrchScreenState, taskId: string | null, payload: Record<string, unknown>): void {
  const text = str(payload.text);
  if (!text || str(payload.role) === "user") return;
  const explicit = readMessageId(payload);
  const pending = explicit != null ? bubbleByMessageId(state, explicit) : pendingBoundedMessage(state);
  if (pending && pending.finalized !== true) {
    pending.text = text;
    pending.closed = true;
    pending.finalized = true;
    return;
  }
  if (pending?.finalized === true) return;
  appendToken(state, taskId, text);
}

export function reduceOrchScreen(state: OrchScreenState, action: OrchScreenAction): OrchScreenState {
  if (action.type === "reset") return initialOrchScreen();
  if (action.type === "user-message") {
    return {
      ...state,
      messages: [
        ...state.messages,
        { id: action.id, role: "user", text: action.text, taskId: action.taskId },
      ],
    };
  }
  if (action.type === "kill-notice") {
    if (state.killNotice?.phase === "stopped" && action.phase !== "stopped" && action.phase !== "stopping") return state;
    if (state.killNotice?.phase === "error" && action.phase === "unconfirmed") return state;
    return {
      ...state,
      killNotice: {
        text: action.detail,
        paidBoundary: action.paidBoundary,
        phase: action.phase,
        taskId: action.taskId ?? state.killNotice?.taskId ?? null,
        sessionId: action.sessionId ?? state.killNotice?.sessionId ?? null,
        lateStopWarning: action.phase === "stopped" ? state.killNotice?.lateStopWarning ?? "" : "",
      },
    };
  }
  if (action.type === "approval-resolved") {
    return {
      ...state,
      approvals: state.approvals.map((item) => {
        if (item.id !== action.id) return item;
        if (action.decision !== "closed" && !item.serverId) return item;
        return { ...item, resolved: action.decision };
      }),
    };
  }

  const event = action.event;
  const next: OrchScreenState = {
    ...state,
    messages: state.messages.map((message) => ({ ...message })),
    agents: state.agents.map((agent) => ({ ...agent })),
    tasks: state.tasks.map((task) => ({
      ...task,
      steps: task.steps.map((step) => ({
        ...step,
        node: step.node ? { ...step.node } : null,
      })),
      checkpoints: task.checkpoints.map((checkpoint) => ({ ...checkpoint })),
      validators: [...task.validators],
      plan: task.plan.map((step) => ({ ...step })),
    })),
    approvals: state.approvals.map((item) => ({ ...item })),
  };
  const payload = asRecord(event.payload);
  const taskId = taskKey(event.task_id);
  if (event.type !== "approval_required") {
    const decided = approvalDecision(payload.decision ?? payload.approval_decision);
    const changeId = approvalChangeId(payload);
    if (decided && changeId) closeServerApproval(next, changeId, decided);
  }

  if (event.type === "ack") {
    const accepted = payload.accepted === true;
    const echoed = str(payload.echo) || (accepted ? str(payload.text) : "");
    next.ack = {
      echo: echoed,
      text: accepted || !str(payload.text) ? "принял, разбираю" : str(payload.text),
      seq: event.seq,
    };
    return next;
  }

  if (event.type === "classification") {
    const mode = str(payload.mode) as ClassificationPayload["mode"];
    if (mode === "interrupt" || mode === "steer" || mode === "append" || mode === "task") {
      const killSwitch = payload.kill_switch === true;
      next.classification = {
        mode,
        deterministic: payload.deterministic === true,
        detail: str(payload.detail),
        seq: event.seq,
        killSwitch,
      };
      if (killSwitch) {
        noteKillRequested(next, payload, { taskId, sessionId: event.session_id || null });
      }
    }
    absorbLlmSignal(next, taskId, payload, event.seq, event.session_id || null);
    return next;
  }

  if (event.type === "message_start") {
    const messageId = readMessageId(payload);
    if (messageId != null) openMessageBubble(next, taskId, messageId, messageRole(payload));
    return next;
  }

  if (event.type === "message_end") {
    const messageId = readMessageId(payload);
    if (messageId != null) {
      const bubble = openMessageBubble(next, taskId, messageId, messageRole(payload));
      bubble.closed = true;
    }
    return next;
  }

  if (event.type === "message") {
    applyFinalMessage(next, taskId, payload);
    absorbLlmSignal(next, taskId, payload, event.seq, event.session_id || null);
    return next;
  }

  if (event.type === "token") {
    const messageId = readMessageId(payload);
    const text = str(payload.text);
    if (messageId != null) appendTokenForMessage(next, taskId, messageId, text);
    else appendToken(next, taskId, text);
    return next;
  }

  if (event.type === "kill_switch") {
    noteKillRequested(next, payload, { taskId, sessionId: event.session_id || null });
    return next;
  }

  if (event.type === "agent_status") {
    const agent = str(payload.agent) as AgentStatusPayload["agent"];
    const found = next.agents.find((item) => item.role === agent);
    if (found) {
      found.status = str(payload.status, found.status);
      found.detail = str(payload.detail);
    }
    const taskStatusRaw = str(payload.task_status);
    if (isTerminalKillStatus(taskStatusRaw)) noteTaskCancelled(next, event);
    if (!taskId) return next;
    ensureTask(next.tasks, taskId, str(payload.title) || undefined);
    const taskStatus = asTaskStatus(taskStatusRaw);
    if (taskStatus) applyTaskStatus(next, taskId, taskStatus, event.session_id || null);
    return next;
  }

  if ((event.type === "step_started" || event.type === "step_finished") && taskId) {
    const task = ensureTask(next.tasks, taskId);
    if (event.type === "step_started" && task.status === "queued") {
      applyTaskStatus(next, taskId, "executing", event.session_id || null);
    }
    const stepId = str(payload.step_id, `step-${event.seq}`);
    let step = task.steps.find((item) => item.id === stepId);
    if (!step) {
      step = {
        id: stepId,
        title: str(payload.title, stepId),
        action: str(payload.action),
        paid: payload.paid === true,
        status: "running",
        note: "",
        node: null,
      };
      task.steps.push(step);
    }
    step.title = str(payload.title, step.title);
    step.action = str(payload.action, step.action);
    if (payload.paid === true) step.paid = true;
    step.note = str(payload.note, step.note);
    const node = readNodeDef(payload.node);
    if (node) step.node = node;
    if (event.type === "step_started") step.status = "running";
    if (event.type === "step_finished") {
      const body = payload as StepPayload & Record<string, unknown>;
      step.status = body.ok === false ? "failed" : "done";
    }
    return next;
  }

  if (event.type === "checkpoint" && taskId) {
    const task = ensureTask(next.tasks, taskId);
    const id = str(payload.checkpoint_id, `cp-${event.seq}`);
    const plan = readPlanSteps(payload.plan);
    if (plan.length) task.plan = plan;
    if (!task.checkpoints.some((item) => item.id === id)) {
      const body = payload as CheckpointPayload & Record<string, unknown>;
      task.checkpoints.push({
        id,
        stepId: str(body.step_id),
        summary: str(body.summary, plan.length ? "план" : "чекпоинт"),
        spend: typeof body.spend === "number" ? body.spend : null,
      });
    }
    return next;
  }

  if (event.type === "validator_result" && taskId) {
    const task = ensureTask(next.tasks, taskId);
    const body = payload as ValidatorResultPayload & Record<string, unknown>;
    task.validators.push({
      name: str(body.name, "validator"),
      pass: body.pass !== false,
      details: str(body.details),
      blocking: body.blocking === true,
    });
    return next;
  }

  if (event.type === "approval_required") {
    const body = payload as ApprovalPayload & Record<string, unknown>;
    const reason = str(body.reason);
    const sessionId = event.session_id || null;
    if (taskId) {
      if (isTerminalBlock(reason)) {
        const task = applyTaskStatus(next, taskId, blockOutcome(reason) ?? "failed", sessionId);
        task.error = reason;
      } else {
        applyTaskStatus(next, taskId, "waiting_approval", sessionId);
      }
    }
    const fromServer = approvalServerKey(body);
    const id = fromServer ?? `local-${event.seq}`;
    const incoming = {
      id,
      serverId: fromServer != null,
      changeId: approvalChangeId(body),
      sessionId,
      taskId,
      title: str(body.title, "Нужно подтверждение"),
      reason,
      risk: str(body.risk, "medium"),
      action: str(body.action),
      costEstimate: typeof body.cost_estimate === "number" ? body.cost_estimate : null,
      forecast: readPaidOutcomeForecast(body),
      restartOffered: readPaidOutcomeRestartOffered(body),
      retryArmed: readPaidOutcomeRetryArmed(body),
      resolved: null as ApprovalView["resolved"],
    };
    const existing = next.approvals.find((item) => item.id === id);
    if (!existing) {
      next.approvals.push(incoming);
    } else {
      existing.changeId = incoming.changeId ?? existing.changeId;
      existing.forecast = incoming.forecast;
      existing.restartOffered = incoming.restartOffered;
      existing.retryArmed = incoming.retryArmed;
      existing.serverId = existing.serverId || incoming.serverId;
    }
    return next;
  }

  if (event.type === "task_done" && taskId) {
    const task = applyTaskStatus(next, taskId, "done", event.session_id || null);
    task.summary = str(payload.summary);
    return next;
  }

  if (event.type === "task_failed" && taskId) {
    const task = applyTaskStatus(next, taskId, "failed", event.session_id || null);
    const error = str(payload.error, str(payload.summary, "задача упала"));
    task.error = error;
    if (isTerminalBlock(error) && !next.approvals.some((item) => item.id === `block-${event.seq}`)) {
      next.approvals.push({
        id: `block-${event.seq}`,
        serverId: false,
        changeId: null,
        sessionId: event.session_id || null,
        taskId,
        title: "",
        reason: error,
        risk: "",
        action: "",
        costEstimate: null,
        forecast: null,
        restartOffered: false,
        retryArmed: false,
        resolved: null,
      });
    }
    return next;
  }

  return next;
}
