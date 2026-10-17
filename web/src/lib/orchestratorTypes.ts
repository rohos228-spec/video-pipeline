/**
 * Контракт событий оркестратора для веба.
 *
 * до freeze contracts Д+2 — ручные типы.
 * Когда появится `app/orchestrator/contracts.py`, источник правды переезжает
 * туда: `python web/scripts/gen_orch_types.py` перезапишет этот файл.
 * До этого момента скрипт файл не трогает.
 *
 * Событие шины (WS3): `OrchEvent{type, task_id, payload, seq}`.
 * Токен-события не персистятся; reconnect-replay — по `seq` / Last-Event-ID.
 */

export const ORCH_EVENT_TYPES = [
  "ack",
  "token",
  "agent_status",
  "step_started",
  "step_finished",
  "approval_required",
  "checkpoint",
  "validator_result",
  "task_done",
  "task_failed",
  "classification",
] as const;

export type OrchEventType = (typeof ORCH_EVENT_TYPES)[number];

/** FSM супервизора — единственная точка записи статусов задачи (план 3.4). */
export const TASK_STATUSES = [
  "queued",
  "planning",
  "critic_review",
  "executing",
  "paused",
  "waiting_approval",
  "done",
  "failed",
  "cancelled",
] as const;

export type TaskStatus = (typeof TASK_STATUSES)[number];

/** Режимы входящего сообщения посреди задачи (план 3.3). */
export const MESSAGE_MODES = ["interrupt", "steer", "append"] as const;

export type MessageMode = (typeof MESSAGE_MODES)[number];

export const AGENT_ROLES = ["supervisor", "planner", "executor", "critic"] as const;

export type AgentRole = (typeof AGENT_ROLES)[number];

export interface OrchEvent<TPayload = Record<string, unknown>> {
  type: OrchEventType;
  task_id: string | null;
  payload: TPayload;
  seq: number;
}

export interface AckPayload {
  /** «принял, разбираю» — до классификации. */
  text?: string;
  /** Эхо текста пользователя, чтобы UI связал ack с конкретным сообщением. */
  echo?: string;
  message_id?: string;
}

export interface TokenPayload {
  text: string;
}

export interface AgentStatusPayload {
  agent: AgentRole;
  status: string;
  detail?: string;
  /** Если событие двигает FSM задачи. */
  task_status?: TaskStatus;
  title?: string;
}

export interface StepPayload {
  step_id: string;
  title: string;
  action?: string;
  /** Платный шаг (run_node и пр.) не рвётся mid-flight. */
  paid?: boolean;
  ok?: boolean;
  note?: string;
}

export interface ApprovalPayload {
  approval_id: string;
  title: string;
  reason: string;
  risk?: "none" | "low" | "medium" | "high" | string;
  action: string;
  cost_estimate?: number;
}

export interface CheckpointPayload {
  checkpoint_id: string;
  step_id?: string;
  summary: string;
  spend?: number;
}

export interface ValidatorResultPayload {
  name: string;
  pass: boolean;
  details: string;
  blocking?: boolean;
}

export interface TaskDonePayload {
  summary?: string;
}

export interface TaskFailedPayload {
  error?: string;
  summary?: string;
}

export interface ClassificationPayload {
  mode: MessageMode;
  deterministic?: boolean;
  detail?: string;
}

export interface OrchOutboundMessage {
  text: string;
  task_id?: string | null;
}

export interface OrchApprovalDecision {
  task_id?: string | null;
  approval_id: string;
  reason?: string;
}

export interface OrchKillRequest {
  session_id: string;
  reason?: string;
}

export interface OrchKillResult {
  ok: boolean;
  status?: TaskStatus | string;
  /** Честная семантика: платный шаг останавливается на границе, не mid-flight. */
  detail: string;
}

export interface OrchMessageAckHttp {
  ok: boolean;
  message_id?: string;
}
