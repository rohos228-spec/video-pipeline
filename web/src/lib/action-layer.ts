/**
 * Клиент слоя действий (бэкенд: app/services/action_layer, контракт: docs/ACTION_LAYER.md).
 * Только типы, вызовы REST и чистые функции форматирования. Подтверждение создаётся
 * только явным вызовом approvePlan из обработчика клика оператора.
 */
import { ApiError, http } from "./api";

const BASE = "/api/action-layer";

export type RiskTier = "read" | "local_safe" | "paid" | "destructive";
export type PlanStatus = "proposed" | "running" | "succeeded" | "failed" | "stopped";
export type StepStatus =
  | "pending" | "running" | "succeeded" | "failed" | "skipped" | "unknown" | "cancelled";

export interface ActionSpecJson {
  id: string;
  title_ru: string;
  description_ru: string;
  params_schema: {
    type?: string;
    required?: string[];
    properties?: Record<string, { type?: string; enum?: unknown[]; minimum?: number; maximum?: number; description?: string }>;
  } & Record<string, unknown>;
  risk: RiskTier;
  spends_money: boolean;
  destructive: boolean;
  reversible: boolean;
  idempotent: boolean;
  needs_approval: boolean;
  async_job: boolean;
  supports_stop: boolean;
  scope: string | null;
  tags: string[];
  preconditions: { id: string; description_ru: string }[];
}

export interface CatalogResponse { count: number; actions: ActionSpecJson[] }

/** low/high = null или known=false означает «неизвестно», а не 0. */
export interface CostEstimateJson { low: number; high: number; basis: string; known: boolean }

export interface PreviewStep {
  index: number;
  action_id: string;
  title_ru?: string;
  risk?: RiskTier;
  spends_money?: boolean;
  destructive?: boolean;
  reversible?: boolean;
  valid: boolean;
  errors: string[];
  precondition_failures: { id: string; reason: string }[];
  cost?: CostEstimateJson;
  params?: Record<string, unknown>;
}

export interface PlanPreview {
  ok: boolean;
  errors: string[];
  plan_hash: string;
  risk_max: RiskTier;
  needs_approval: boolean;
  steps: PreviewStep[];
  cost: { low: number; high: number; unknown_steps: number; known: boolean };
  destructive_effects: { index: number; action_id: string; text: string }[];
  budget?: { cap_credits: number; remaining_credits: number; fits: boolean };
}

/** Ответ preview / from-text / from-macro. Поля модели необязательны (зависят от маршрута). */
export interface PlanPreviewResponse {
  plan_id: string;
  preview: PlanPreview;
  explanation?: string | null;
  clarification?: string | null;
  steps?: { action_id: string; params: Record<string, unknown>; depends_on?: number[] }[];
}

export interface ApprovalResponse {
  approval_id: string; plan_id: string; plan_hash: string; operator: string; expires_at: string;
}

export interface StepView {
  index: number;
  action_id: string;
  params: Record<string, unknown>;
  depends_on: number[];
  status: StepStatus;
  attempts: number;
  progress: number | null;
  progress_note: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  cost: { low: number | null; high: number | null; actual: number | null };
  started_at: string | null;
  finished_at: string | null;
}

export interface PlanView {
  id: string;
  status: PlanStatus;
  project_id: number | null;
  operator: string;
  request_text: string;
  plan_hash: string;
  risk_max: RiskTier;
  needs_approval: boolean;
  cost_estimate: { low: number | null; high: number | null; unknown_steps: number };
  spent_credits: number;
  budget_cap_credits: number;
  stop_requested: boolean;
  stop_reason: string | null;
  error: string | null;
  live: boolean;
  interrupted: boolean;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  approvals: { id: string; operator: string; created_at: string | null; expires_at: string | null; used_at: string | null; active: boolean }[];
  steps: StepView[];
}

export interface HistoryItem {
  id: number;
  created_at: string | null;
  plan_id: string | null;
  step_index: number | null;
  event: string;
  actor: string;
  request_text: string | null;
  action_id: string | null;
  params: Record<string, unknown> | null;
  outcome: string | null;
  error: string | null;
  cost_estimate: { low: number | null; high: number | null };
  cost_recorded: number | null;
}
export interface HistoryResponse { total: number; items: HistoryItem[] }

/** Умная кнопка (макрос). Форма ответа допускает несколько вариантов имён полей. */
export interface MacroJson {
  id: string | number;
  title_ru?: string;
  title?: string;
  name?: string;
  description_ru?: string;
  description?: string;
}

export interface PlanStepInput { action_id: string; params: Record<string, unknown>; depends_on?: number[] }

// ---- вызовы ----

export const getCatalog = () => http<CatalogResponse>(`${BASE}/catalog`);

export const previewPlan = (steps: PlanStepInput[], requestText = "") =>
  http<PlanPreviewResponse>(`${BASE}/plans/preview`, {
    method: "POST",
    body: JSON.stringify({ steps: steps.map((s) => ({ depends_on: [], ...s })), request_text: requestText }),
  });

export interface SceneContext { project_id: number; scene_id?: number | null; frame_id?: number | null }

export const planFromText = (text: string, ctx: SceneContext) =>
  http<PlanPreviewResponse>(`${BASE}/plans/from-text`, {
    method: "POST",
    body: JSON.stringify({ text, ...ctx }),
  }, 90_000);

export const planFromMacro = (macroId: string | number, ctx: SceneContext) =>
  http<PlanPreviewResponse>(`${BASE}/plans/from-macro`, {
    method: "POST",
    body: JSON.stringify({ macro_id: macroId, ...ctx }),
  });

/** Возвращает null, если маршрут отсутствует (404/405): функция недоступна, не ошибка. */
export async function listMacros(): Promise<MacroJson[] | null> {
  try {
    const r = await http<{ macros?: MacroJson[] } | MacroJson[]>(`${BASE}/macros`);
    return Array.isArray(r) ? r : (r.macros ?? []);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return null;
    throw e;
  }
}

/** Только по клику оператора. */
export const approvePlan = (planId: string) =>
  http<ApprovalResponse>(`${BASE}/plans/${encodeURIComponent(planId)}/approve`, { method: "POST", body: "{}" });

export const runPlan = (planId: string) =>
  http<{ plan_id: string; status: string }>(`${BASE}/plans/${encodeURIComponent(planId)}/run`, { method: "POST", body: "{}" });

export const getPlan = (planId: string) =>
  http<PlanView>(`${BASE}/plans/${encodeURIComponent(planId)}`);

export const stopPlan = (planId: string) =>
  http<{ status: string; stop_requested: boolean }>(`${BASE}/plans/${encodeURIComponent(planId)}/stop`, { method: "POST", body: "{}" });

export const getHistory = (limit = 20) =>
  http<HistoryResponse>(`${BASE}/history?limit=${limit}`);

// ---- ошибки ----

export const ERROR_TEXT_RU: Record<string, string> = {
  plan_invalid: "План не прошёл проверку: исправьте шаги и составьте план заново.",
  plan_changed: "План изменился после предпросмотра. Составьте план заново.",
  over_budget: "План не помещается в потолок расходов.",
  plan_not_approvable: "Этот план нельзя подтвердить в текущем состоянии.",
  approval_not_needed: "Этому плану подтверждение не требуется.",
  operator_required: "Подтвердить может только оператор-человек.",
  approval_required: "Нужно подтверждение оператора перед запуском.",
  already_running: "План уже выполняется.",
  plan_finished: "План уже завершён.",
  kill_switch_active: "Включён аварийный стоп оркестратора: шаги не выполняются.",
  project_busy: "В этом проекте уже идёт другой план.",
};

/** Достаёт стабильный код ошибки из ответа API (detail.code или detail строкой). */
export function errorCode(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  const d = e.detail as unknown;
  if (d && typeof d === "object") {
    const o = d as { code?: unknown; detail?: unknown };
    if (typeof o.code === "string") return o.code;
    if (o.detail && typeof o.detail === "object" && typeof (o.detail as { code?: unknown }).code === "string") {
      return (o.detail as { code: string }).code;
    }
  }
  if (typeof d === "string" && d in ERROR_TEXT_RU) return d;
  return null;
}

export function describeError(e: unknown): string {
  const code = errorCode(e);
  if (code) return `${ERROR_TEXT_RU[code] ?? "Сервер отклонил запрос."} (код: ${code})`;
  if (e instanceof ApiError && e.status === 404) return "Функция недоступна на сервере (404).";
  return e instanceof Error ? e.message : "Неизвестная ошибка.";
}

/** Недопустимый план при preview: 422 {detail:{code:"plan_invalid", preview}}. */
export function previewFromError(e: unknown): PlanPreview | null {
  if (e instanceof ApiError && e.status === 422 && e.detail && typeof e.detail === "object") {
    const d = (e.detail as { detail?: { preview?: PlanPreview } }).detail ?? (e.detail as { preview?: PlanPreview });
    return d?.preview ?? null;
  }
  return null;
}

// ---- форматирование (чистые функции) ----

export const RISK_LABEL_RU: Record<RiskTier, string> = {
  read: "чтение",
  local_safe: "локально",
  paid: "платное",
  destructive: "стирает данные",
};

export const STEP_STATUS_RU: Record<StepStatus, string> = {
  pending: "ожидает",
  running: "выполняется",
  succeeded: "готово",
  failed: "ошибка",
  skipped: "пропущен",
  unknown: "неизвестно (нужен ручной разбор)",
  cancelled: "отменён",
};

export const PLAN_STATUS_RU: Record<PlanStatus, string> = {
  proposed: "предложен",
  running: "выполняется",
  succeeded: "выполнен",
  failed: "ошибка",
  stopped: "остановлен",
};

export const EVENT_RU: Record<string, string> = {
  plan_created: "план создан",
  approval_created: "подтверждение выдано",
  approval_consumed: "подтверждение использовано",
  run_requested: "запуск запрошен",
  plan_started: "план запущен",
  step_started: "шаг начат",
  step_succeeded: "шаг выполнен",
  step_failed: "шаг не удался",
  step_skipped: "шаг пропущен",
  step_unknown: "шаг в неизвестном состоянии",
  step_cancelled: "шаг отменён",
  budget_block: "заблокировано бюджетом",
  kill_switch_block: "заблокировано аварийным стопом",
  stop_requested: "запрошена остановка",
  plan_finished: "план завершён",
};

const fmtNum = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, ""));

/** "2–6 кр." / "3 кр." / "неизвестно". Неизвестное никогда не превращается в 0. */
export function formatCredits(low: number | null | undefined, high: number | null | undefined, known = true): string {
  if (!known || low == null || high == null) return "неизвестно";
  return low === high ? `${fmtNum(low)} кр.` : `${fmtNum(low)}–${fmtNum(high)} кр.`;
}

export function formatStepCost(step: PreviewStep): string {
  if (!step.spends_money) return "0 кр. (бесплатно)";
  return step.cost ? formatCredits(step.cost.low, step.cost.high, step.cost.known) : "неизвестно";
}

export function formatTotalCost(preview: PlanPreview): string {
  const base = formatCredits(preview.cost.low, preview.cost.high, true);
  if (preview.cost.unknown_steps > 0) {
    const n = preview.cost.unknown_steps;
    return preview.cost.low === 0 && preview.cost.high === 0
      ? `неизвестно (шагов без оценки: ${n})`
      : `${base} и ещё неизвестно (шагов без оценки: ${n})`;
  }
  return base;
}

const PARAM_LABEL_RU: Record<string, string> = {
  project_id: "проект", frame_id: "кадр", scene_id: "сцена", shot_id: "план",
  voiceover: "озвучка", coverage: "покрытие", ops: "операции", status: "статус",
};

/** Параметры шага простым языком: «проект: 12; кадр: 3». */
export function describeParams(params: Record<string, unknown> | undefined | null): string {
  const entries = Object.entries(params ?? {});
  if (entries.length === 0) return "без параметров";
  return entries
    .map(([k, v]) => {
      const val = typeof v === "string" ? v : typeof v === "boolean" ? (v ? "да" : "нет") : JSON.stringify(v);
      const short = val.length > 80 ? `${val.slice(0, 80)}…` : val;
      return `${PARAM_LABEL_RU[k] ?? k}: ${short}`;
    })
    .join("; ");
}

/** «Выполнить» без подтверждения допустимо только если сервер не требует approval и план валиден. */
export function canRunWithoutApproval(p: PlanPreview): boolean {
  return p.ok && !p.needs_approval;
}

export function canApprove(p: PlanPreview): boolean {
  return p.ok && p.needs_approval && (p.budget ? p.budget.fits : true);
}

export const isTerminal = (s: PlanStatus) => s === "succeeded" || s === "failed" || s === "stopped";

export function macroTitle(m: MacroJson): string {
  return m.title_ru ?? m.title ?? m.name ?? String(m.id);
}
