/**
 * Сведение потока OrchEvent в состояние экрана.
 * Дедуп по seq делает вызывающий код до dispatch.
 */

import { readActionEnvelope, type ActionResultView } from "./orchestratorActionCards";
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
  /** CheckpointData.change_ids — id шагов, не orch-appr. */
  changeIds: string[];
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
  /** Серверный `change_id` карточки, формат `orch-appr-<задача>-<n>`. `step_change_id` сюда не попадает. */
  changeId: string | null;
  taskId: string | null;
  title: string;
  reason: string;
  risk: string;
  action: string;
  costEstimate: number | null;
  /** ApprovalEvent / payload `retry_armed`. Нет поля — null. Кнопки из этого флага не рисуются. */
  retryArmed: boolean | null;
  /** `approval_required.actions`. null — поля не было, остаются Approve/Reject. */
  actions: string[] | null;
  /** `approval_required.step_change_id`. null — поля не было, список берётся из чекпоинта. */
  stepChangeId: string | null;
  resolved: "approved" | "rejected" | "retried" | "closed" | null;
}

/** Подписи `APPROVAL_DECISION_LABELS`. В массиве `actions` их нет. */
export const PAID_DECISION_LABEL = {
  approve: "Шаг выполнен, продолжить",
  retry_step: "Перезапустить шаг",
  deny: "Остановить задачу",
} as const;

export type PaidDecision = keyof typeof PAID_DECISION_LABEL;

/** Кнопки paid_outcome_unknown только из массива `actions`. Иная причина и пустое поле — null. */
export function paidDecisionsOf(approval: ApprovalView): PaidDecision[] | null {
  if (approval.reason !== "paid_outcome_unknown" || approval.actions == null) return null;
  const known = approval.actions.filter(
    (item): item is PaidDecision => item === "approve" || item === "retry_step" || item === "deny",
  );
  return known.length ? known : null;
}

/**
 * partial_write: grant принимает approve и deny.
 * На проводе `actions` обычно только `["approve"]` — deny всё равно есть.
 */
export function partialDecisionsOf(approval: ApprovalView): PaidDecision[] | null {
  if (approval.reason !== "partial_write") return null;
  return ["approve", "deny"];
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
  /**
   * `agent_status.resume_allowed`. null — поля ещё не было, кнопка смотрит на статус и чекпоинт.
   * true/false перекрывают этот вывод.
   */
  resumeAllowed: boolean | null;
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

/** Событие `partial_write`. Список на экране берётся из GET, не из этого payload. */
export interface PartialWriteView {
  seq: number;
  taskId: string | null;
  level: string;
  position: number | null;
}

export interface KillNoticeView {
  text: string;
  /** Платный шаг останавливается на границе. UI не ищет эту фразу в тексте. */
  paidBoundary: boolean;
  /** stopping — запрос ушёл; stopped — stop_key_set; error — отказ; unconfirmed — задача отменена без ключа. */
  phase: KillPhase;
  /** Задача, которую останавливали. Чужой task_id баннер не двигает. */
  taskId: string | null;
  sessionId: string | null;
}

export interface OrchScreenState {
  messages: ChatMessage[];
  agents: AgentView[];
  tasks: TaskView[];
  approvals: ApprovalView[];
  actionResults: ActionResultView[];
  ack: AckView | null;
  classification: ClassificationView | null;
  killNotice: KillNoticeView | null;
  partialWrite: PartialWriteView | null;
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
  | { type: "approval-resolved"; id: string; decision: "approved" | "rejected" | "retried" | "closed" }
  | { type: "task-status"; taskId: string; status: TaskStatus; resumeAllowed?: boolean }
  | { type: "session-snapshot"; taskId: string; status: TaskStatus; resumeAllowed: boolean }
  | { type: "whitelist-denied"; approvalId: string; taskId: string | null }
  | { type: "snapshot"; taskId: string | null; status: string }
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
    actionResults: [],
    ack: null,
    classification: null,
    killNotice: null,
    partialWrite: null,
  };
}

/**
 * Статусы, из которых FSM пускает в `executing`.
 * Фолбэк, если в `agent_status` ещё не было `resume_allowed`.
 * `cancelled` терминальный (kill в planning → 404, чекпоинта нет; с чекпоинтом resume даёт 409).
 * `executing` не показываем: живой прогон — 409, а признака «прогон мёртв» в событиях нет.
 */
const RESUME_STATUSES = new Set<TaskStatus>(["paused", "waiting_approval", "critic_review"]);

export function canResumeTask(task: TaskView | null): boolean {
  if (!task) return false;
  if (!RESUME_STATUSES.has(task.status)) return false;
  return task.checkpoints.length > 0;
}

/** Кнопка «Продолжить». Явный `resume_allowed` важнее статуса и чекпоинта. */
export function resumeButtonVisible(task: TaskView | null): boolean {
  if (!task) return false;
  if (task.resumeAllowed === true) return true;
  if (task.resumeAllowed === false) return false;
  return canResumeTask(task);
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

/** Текст отказа #370: имени нет в белом списке, карточка approval не открывается. */
export const NOT_IN_WHITELIST = "not_in_whitelist";
export const NOT_IN_WHITELIST_TEXT =
  "Действия нет в белом списке. Одобрить и выполнить его нельзя. not_in_whitelist";

export function isTerminalBlock(reason: string): boolean {
  const outcome = blockOutcome(reason);
  return outcome != null && TERMINAL_TASK.has(outcome);
}

function readStringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const items: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim()) items.push(item);
  }
  return items;
}

function readStepChangeId(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function approvalWire(body: Record<string, unknown>): { actions: string[] | null; stepChangeId: string | null } {
  return {
    actions: readStringList(body.actions),
    stepChangeId: readStepChangeId(body.step_change_id),
  };
}

function readChangeIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const ids: string[] = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim()) ids.push(item);
  }
  return ids;
}

export type ApprovalButton = "approve" | "deny" | "retry_step";

/** Кнопки карточки. `missing_project_id` снимается через POST .../project, не через /approve. */
export function approvalButtons(reason: string): ApprovalButton[] {
  if (reason === "missing_project_id") return [];
  if (reason === "paid_outcome_unknown" || reason === "paid_unknown_outcome") return ["retry_step", "deny"];
  if (isTerminalBlock(reason)) return [];
  return ["approve", "deny"];
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

/** Серверный id карточки approval. `step_change_id` — id шага, не карточки. */
const APPROVAL_CHANGE_ID = /^orch-appr-[0-9]+-[0-9]+$/;

export function approvalChangeId(value: unknown): string | null {
  const id = serverApprovalId(value);
  return id && APPROVAL_CHANGE_ID.test(id) ? id : null;
}

/**
 * Id карточки из шины: `approval_id`, иначе серверный `change_id`.
 * `step_change_id` карточку не открывает и в /approve не уходит.
 */
export function approvalServerKey(body: Record<string, unknown>): string | null {
  const direct = serverApprovalId(body.approval_id) ?? approvalChangeId(body.change_id);
  if (direct) return direct;
  const nested = body.payload;
  if (!nested || typeof nested !== "object") return null;
  const inner = nested as Record<string, unknown>;
  return serverApprovalId(inner.approval_id) ?? approvalChangeId(inner.change_id);
}

/**
 * `change_id` для `ApproveIn`: только `^orch-appr-[0-9]+-[0-9]+$` с карточки.
 * `step_change_id` не подставляется, даже если другого id нет.
 */
export function approvalWireChangeId(body: Record<string, unknown>): string | null {
  const direct = approvalChangeId(body.change_id);
  if (direct) return direct;
  const nested = body.payload;
  if (!nested || typeof nested !== "object") return null;
  return approvalChangeId((nested as Record<string, unknown>).change_id);
}

function approvalDecision(value: unknown): "approved" | "rejected" | null {
  if (value === "approved" || value === "approve") return "approved";
  if (value === "rejected" || value === "reject" || value === "deny") return "rejected";
  return null;
}

function closeServerApproval(state: OrchScreenState, id: string, decision: "approved" | "rejected"): void {
  const item = state.approvals.find((entry) => entry.id === id && entry.serverId);
  if (!item || item.resolved) return;
  item.resolved = decision;
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
  };
}

function absorbLlmSignal(state: OrchScreenState, taskId: string | null, payload: Record<string, unknown>, seq: number): void {
  const status = asTaskStatus(str(payload.task_status));
  if (taskId && status) {
    ensureTask(state.tasks, taskId).status = status;
  }
  const approval = payload.approval;
  if (!approval || typeof approval !== "object") return;
  const body = approval as Record<string, unknown>;
  const reason = str(body.reason);
  if (!reason) return;
  const fromServer = approvalServerKey(body);
  const id = fromServer ?? `local-llm-${seq}`;
  if (!state.approvals.some((item) => item.id === id)) {
    state.approvals.push({
      id,
      serverId: fromServer != null,
      changeId: approvalWireChangeId(body),
      taskId: taskKey(body.task_id) || taskId,
      title: str(body.title, "Нужно подтверждение"),
      reason,
      risk: str(body.risk),
      action: str(body.action),
      costEstimate: typeof body.cost_estimate === "number" ? body.cost_estimate : null,
      retryArmed: typeof body.retry_armed === "boolean" ? body.retry_armed : null,
      ...approvalWire(body),
      resolved: null,
    });
  }
  if (taskId && isTerminalBlock(reason)) {
    const task = ensureTask(state.tasks, taskId);
    task.status = blockOutcome(reason) ?? "failed";
    task.error = reason;
  } else if (taskId && (status === "waiting_approval" || blockOutcome(reason) === "waiting_approval" || reason === "budget_exhausted")) {
    ensureTask(state.tasks, taskId).status = "waiting_approval";
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
      resumeAllowed: null,
    };
    tasks.push(task);
  } else if (title && task.title === "Задача") {
    task.title = title;
  }
  return task;
}

function appendToken(state: OrchScreenState, taskId: string | null, text: string): void {
  if (!text) return;
  const messages = state.messages;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message && message.role === "assistant" && message.taskId === taskId) {
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

function rememberActionResult(state: OrchScreenState, payload: Record<string, unknown>, seq: number): void {
  const view = readActionEnvelope(payload, seq);
  if (!view) return;
  if (state.actionResults.some((item) => item.id === view.id)) return;
  state.actionResults.push(view);
}

export function reduceOrchScreen(state: OrchScreenState, action: OrchScreenAction): OrchScreenState {
  if (action.type === "reset") return initialOrchScreen();
  if (action.type === "task-status") {
    const status = asTaskStatus(action.status);
    if (!status) return state;
    const tasks = state.tasks.map((task) => ({
      ...task,
      steps: task.steps.map((step) => ({ ...step, node: step.node ? { ...step.node } : null })),
      checkpoints: task.checkpoints.map((checkpoint) => ({ ...checkpoint })),
      validators: [...task.validators],
      plan: task.plan.map((step) => ({ ...step })),
    }));
    const task = tasks.find((item) => item.id === action.taskId) ?? ensureTask(tasks, action.taskId);
    task.status = status;
    if (typeof action.resumeAllowed === "boolean") task.resumeAllowed = action.resumeAllowed;
    return { ...state, tasks };
  }
  if (action.type === "session-snapshot") {
    const tasks = state.tasks.map((task) => ({
      ...task,
      steps: task.steps.map((step) => ({ ...step, node: step.node ? { ...step.node } : null })),
      checkpoints: task.checkpoints.map((checkpoint) => ({ ...checkpoint })),
      validators: [...task.validators],
      plan: task.plan.map((step) => ({ ...step })),
    }));
    const existing = tasks.find((item) => item.id === action.taskId);
    const task = existing ?? ensureTask(tasks, action.taskId);
    if (!existing) task.status = action.status;
    if (task.resumeAllowed == null) task.resumeAllowed = action.resumeAllowed;
    return { ...state, tasks };
  }
  if (action.type === "whitelist-denied") {
    const tasks = state.tasks.map((task) => ({
      ...task,
      steps: task.steps.map((step) => ({ ...step, node: step.node ? { ...step.node } : null })),
      checkpoints: task.checkpoints.map((checkpoint) => ({ ...checkpoint })),
      validators: [...task.validators],
      plan: task.plan.map((step) => ({ ...step })),
    }));
    if (action.taskId) {
      const task = tasks.find((item) => item.id === action.taskId) ?? ensureTask(tasks, action.taskId);
      task.status = "failed";
      task.resumeAllowed = false;
    }
    const approvals = state.approvals.map((item) =>
      item.id === action.approvalId ? { ...item, resolved: "closed" as const } : { ...item },
    );
    if (!approvals.some((item) => item.reason === NOT_IN_WHITELIST && item.resolved == null)) {
      approvals.push({
        id: `block-whitelist-${action.approvalId}`,
        serverId: false,
        changeId: null,
        taskId: action.taskId,
        title: "",
        reason: NOT_IN_WHITELIST,
        risk: "",
        action: "",
        costEstimate: null,
        retryArmed: null,
        actions: null,
        stepChangeId: null,
        resolved: null,
      });
    }
    return { ...state, tasks, approvals };
  }
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
      },
    };
  }
  if (action.type === "snapshot") {
    const status = asTaskStatus(action.status);
    if (!status) return state;
    const wanted = action.taskId;
    const known = wanted != null && state.tasks.some((task) => task.id === wanted);
    const target = known ? wanted : state.tasks.at(-1)?.id;
    if (!target) return state;
    return {
      ...state,
      tasks: state.tasks.map((task) => (task.id === target ? { ...task, status } : task)),
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
    actionResults: state.actionResults.map((item) => ({ ...item })),
  };
  const payload = asRecord(event.payload);
  const taskId = taskKey(event.task_id);
  if (event.type !== "approval_required") {
    const decided = approvalDecision(payload.decision ?? payload.approval_decision);
    const decidedId = serverApprovalId(payload.approval_id);
    if (decided && decidedId) closeServerApproval(next, decidedId, decided);
  }

  if (event.type === "approval_decided") {
    const decided = approvalDecision(payload.decision);
    const id = serverApprovalId(payload.change_id) ?? serverApprovalId(payload.approval_id);
    if (decided && id) closeServerApproval(next, id, decided);
    if (decided === "rejected") {
      const idTask = taskId ?? taskKey(payload.task_id);
      if (idTask) {
        const task = ensureTask(next.tasks, idTask);
        task.status = "failed";
        task.resumeAllowed = false;
      }
    }
    return next;
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
    absorbLlmSignal(next, taskId, payload, event.seq);
    return next;
  }

  if (event.type === "message") {
    const text = str(payload.text);
    if (text && str(payload.role) !== "user") appendToken(next, taskId, text);
    absorbLlmSignal(next, taskId, payload, event.seq);
    rememberActionResult(next, payload, event.seq);
    return next;
  }

  if (event.type === "token") {
    appendToken(next, taskId, str(payload.text));
    return next;
  }

  if (event.type === "kill_switch") {
    noteKillRequested(next, payload, { taskId, sessionId: event.session_id || null });
    return next;
  }

  if (event.type === "partial_write") {
    const position = payload.position;
    next.partialWrite = {
      seq: event.seq,
      taskId: taskId ?? taskKey(payload.task_id),
      level: str(payload.level) || "WARN",
      position: typeof position === "number" && Number.isFinite(position) ? position : null,
    };
    return next;
  }

  if (event.type === "agent_status") {
    const agent = str(payload.agent) as AgentStatusPayload["agent"];
    const found = next.agents.find((item) => item.role === agent);
    if (found) {
      found.status = str(payload.status, found.status);
      found.detail = str(payload.detail);
    }
    // Мок пишет task_status. Супервизор WS3 шлёт только {status, agent:"supervisor"}.
    const taskStatusRaw =
      str(payload.task_status) || (agent === "supervisor" ? str(payload.status) : "");
    if (isTerminalKillStatus(taskStatusRaw)) noteTaskCancelled(next, event);
    if (!taskId) return next;
    const task = ensureTask(next.tasks, taskId, str(payload.title) || undefined);
    const taskStatus = asTaskStatus(str(payload.task_status)) ?? (agent === "supervisor" ? asTaskStatus(str(payload.status)) : null);
    if (taskStatus) task.status = taskStatus;
    if (typeof payload.resume_allowed === "boolean") task.resumeAllowed = payload.resume_allowed;
    return next;
  }

  if ((event.type === "step_started" || event.type === "step_finished") && taskId) {
    const task = ensureTask(next.tasks, taskId);
    if (event.type === "step_started" && task.status === "queued") task.status = "executing";
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
    if (event.type === "step_finished") {
      const body = payload as StepPayload & Record<string, unknown>;
      step.status = body.ok === false ? "failed" : "done";
      rememberActionResult(next, payload, event.seq);
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
        changeIds: readChangeIds(body.change_ids),
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
    const fromServer = approvalServerKey(body);
    const id = fromServer ?? `local-${event.seq}`;
    if (!next.approvals.some((item) => item.id === id)) {
      next.approvals.push({
        id,
        serverId: fromServer != null,
        changeId: approvalWireChangeId(body),
        taskId,
        title: str(body.title, "Нужно подтверждение"),
        reason: str(body.reason),
        risk: str(body.risk, "medium"),
        action: str(body.action),
        costEstimate: typeof body.cost_estimate === "number" ? body.cost_estimate : null,
        retryArmed: typeof body.retry_armed === "boolean" ? body.retry_armed : null,
        ...approvalWire(body),
        resolved: null,
      });
    }
    if (taskId) {
      const task = ensureTask(next.tasks, taskId);
      const reason = str(body.reason);
      if (isTerminalBlock(reason)) {
        task.status = blockOutcome(reason) ?? "failed";
        task.error = reason;
      } else {
        task.status = "waiting_approval";
      }
    }
    return next;
  }

  if (event.type === "task_done" && taskId) {
    const task = ensureTask(next.tasks, taskId);
    task.status = "done";
    task.summary = str(payload.summary);
    task.resumeAllowed = false;
    return next;
  }

  if (event.type === "task_failed" && taskId) {
    const task = ensureTask(next.tasks, taskId);
    task.status = "failed";
    task.resumeAllowed = false;
    const error = str(payload.error, str(payload.summary, "задача упала"));
    task.error = error;
    if (isTerminalBlock(error) && !next.approvals.some((item) => item.id === `block-${event.seq}`)) {
      next.approvals.push({
        id: `block-${event.seq}`,
        serverId: false,
        changeId: null,
        taskId,
        title: "",
        reason: error,
        risk: "",
        action: "",
        costEstimate: null,
        retryArmed: null,
        actions: null,
        stepChangeId: null,
        resolved: null,
      });
    }
    return next;
  }

  return next;
}
