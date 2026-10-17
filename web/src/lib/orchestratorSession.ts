/**
 * Сведение потока OrchEvent в состояние экрана.
 * Дедуп по seq делает вызывающий код до dispatch.
 */

import {
  AGENT_ROLES,
  type AgentRole,
  type AgentStatusPayload,
  type ApprovalPayload,
  type CheckpointPayload,
  type ClassificationPayload,
  type MessageMode,
  type NodeDef,
  type OrchEvent,
  type StepPayload,
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
}

export interface ValidatorView {
  name: string;
  pass: boolean;
  details: string;
  blocking: boolean;
}

export interface ApprovalView {
  id: string;
  taskId: string | null;
  title: string;
  reason: string;
  risk: string;
  action: string;
  costEstimate: number | null;
  resolved: "approved" | "rejected" | null;
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

export interface OrchScreenState {
  messages: ChatMessage[];
  agents: AgentView[];
  tasks: TaskView[];
  approvals: ApprovalView[];
  ack: AckView | null;
  classification: ClassificationView | null;
  killNotice: string | null;
}

export type OrchScreenAction =
  | { type: "event"; event: OrchEvent }
  | { type: "user-message"; id: string; text: string; taskId: string | null }
  | { type: "kill-notice"; detail: string }
  | { type: "approval-resolved"; id: string; decision: "approved" | "rejected" }
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
function taskKey(value: number | string | null | undefined): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value) return value;
  return null;
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

function appendToken(state: OrchScreenState, taskId: string | null, text: string): void {
  if (!text) return;
  const last = [...state.messages].reverse().find((message) => message.role === "assistant" && message.taskId === taskId);
  if (last) {
    last.text += text;
    return;
  }
  state.messages.push({
    id: `asst-${taskId ?? "none"}-${state.messages.length}`,
    role: "assistant",
    text,
    taskId,
  });
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
    return { ...state, killNotice: action.detail };
  }
  if (action.type === "approval-resolved") {
    return {
      ...state,
      approvals: state.approvals.map((item) =>
        item.id === action.id ? { ...item, resolved: action.decision } : item,
      ),
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
    if (mode === "interrupt" || mode === "steer" || mode === "append") {
      const killSwitch = payload.kill_switch === true;
      next.classification = {
        mode,
        deterministic: payload.deterministic === true,
        detail: str(payload.detail),
        seq: event.seq,
        killSwitch,
      };
      if (killSwitch) {
        next.killNotice = "сработал из сообщения. Платный шаг остановится на границе";
      }
    }
    return next;
  }

  if (event.type === "token") {
    appendToken(next, taskId, str(payload.text));
    return next;
  }

  if (event.type === "agent_status" && taskId) {
    const agent = str(payload.agent) as AgentStatusPayload["agent"];
    const found = next.agents.find((item) => item.role === agent);
    if (found) {
      found.status = str(payload.status, found.status);
      found.detail = str(payload.detail);
    }
    const task = ensureTask(next.tasks, taskId, str(payload.title) || undefined);
    const taskStatus = str(payload.task_status);
    if (taskStatus) task.status = taskStatus as TaskStatus;
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
    const id = str(body.approval_id, `appr-${event.seq}`);
    if (!next.approvals.some((item) => item.id === id)) {
      next.approvals.push({
        id,
        taskId,
        title: str(body.title, "Нужно подтверждение"),
        reason: str(body.reason),
        risk: str(body.risk, "medium"),
        action: str(body.action),
        costEstimate: typeof body.cost_estimate === "number" ? body.cost_estimate : null,
        resolved: null,
      });
    }
    if (taskId) {
      const task = ensureTask(next.tasks, taskId);
      task.status = "waiting_approval";
    }
    return next;
  }

  if (event.type === "task_done" && taskId) {
    const task = ensureTask(next.tasks, taskId);
    task.status = "done";
    task.summary = str(payload.summary);
    return next;
  }

  if (event.type === "task_failed" && taskId) {
    const task = ensureTask(next.tasks, taskId);
    task.status = "failed";
    task.error = str(payload.error, str(payload.summary, "задача упала"));
    return next;
  }

  return next;
}
