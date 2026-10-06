/**
 * Клиент слоя действий (бэкенд: app/services/action_layer, контракт: docs/ACTION_LAYER.md).
 * Только типы, вызовы REST и чистые функции форматирования. Подтверждение создаётся
 * только явным вызовом approvePlan из обработчика клика оператора.
 */
import { useEffect, useState } from "react";
import { ApiError, http } from "./api";
import type { AttachmentRules } from "./attachment-view";

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
  /** null, если планировщик просит уточнение или план не прошёл проверку. */
  plan_id: string | null;
  preview: PlanPreview | null;
  explanation_ru?: string | null;
  needs_clarification?: boolean | string | null;
  /** Короткие варианты ответа на уточнение (для кнопок); пусто, если вариантов нет. */
  clarification_options?: string[];
  /** Планировщик ждёт файл: какое действие он применит после загрузки (кнопкой загрузки). */
  attachment_request?: { action_id: string; params: Record<string, unknown>; formats?: string } | null;
  /** id прикреплённых файлов, которые сервер подставил в шаги плана (после создания плана они заняты). */
  attachments_used?: string[];
  context_version?: string | number | null;
  macro_id?: string | null;
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
  /** Резерв по журналу расходов (верхняя граница), а не факт. */
  reserved_credits?: number;
  /** Сумма credits_spent, которую сообщили сами действия; null, если факт не сообщён. */
  actual_credits?: number | null;
  resume_window_s?: number;
  resume_expired?: boolean;
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
export interface MacroParamSchema {
  type?: string;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  default?: unknown;
  description?: string;
  title?: string;
}
export interface MacroJson {
  id: string;
  title_ru?: string;
  description_ru?: string;
  params_schema?: { required?: string[]; properties?: Record<string, MacroParamSchema> };
  builtin?: boolean;
  available?: boolean;
  missing_action_ids?: string[];
}

export interface PlanStepInput { action_id: string; params: Record<string, unknown>; depends_on?: number[] }

// ---- вызовы ----

export const getCatalog = () => http<CatalogResponse>(`${BASE}/catalog`);

export const previewPlan = (steps: PlanStepInput[], requestText = "") =>
  http<PlanPreviewResponse>(`${BASE}/plans/preview`, {
    method: "POST",
    body: JSON.stringify({ steps: steps.map((s) => ({ depends_on: [], ...s })), request_text: requestText }),
  });

export interface SceneContext { project_id: number; selected_scene?: number | null; selected_frame?: number | null }

const sel = (ctx: SceneContext) => ({
  ...(ctx.selected_scene ? { selected_scene: ctx.selected_scene } : {}),
  ...(ctx.selected_frame ? { selected_frame: ctx.selected_frame } : {}),
});

/** attachmentIds: файлы, уже загруженные через uploadAttachment (путь подставляет сервер). */
export const planFromText = (text: string, ctx: SceneContext, attachmentIds: string[] = []) =>
  http<PlanPreviewResponse>(`${BASE}/plans/from-text`, {
    method: "POST",
    body: JSON.stringify({
      text, project_id: ctx.project_id, ...sel(ctx),
      ...(attachmentIds.length > 0 ? { attachments: attachmentIds } : {}),
    }),
  }, 90_000);

// ---- файлы командной строки ----

export interface AttachmentInfo { attachment_id: string; name: string; size: number; kind: string; sha256: string }

/** Правила загрузки с сервера; null, если ручки нет (тогда берутся запасные правила). */
export async function getAttachmentRules(): Promise<AttachmentRules | null> {
  try {
    const r = await http<AttachmentRules>(`${BASE}/attachments/rules`);
    return r && r.kinds ? r : null;
  } catch {
    return null;
  }
}

/** Загрузка файла с прогрессом (fetch не умеет прогресс отправки, поэтому XMLHttpRequest). */
export function uploadAttachment(
  file: File, projectId: number, onProgress?: (percent: number) => void, signal?: AbortSignal,
): Promise<AttachmentInfo> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/attachments`);
    xhr.timeout = 10 * 60_000;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && e.total > 0) onProgress?.(Math.round((e.loaded * 100) / e.total)); };
    xhr.onload = () => {
      let body: string | object = xhr.responseText;
      try { body = JSON.parse(xhr.responseText) as object; } catch { /* оставляем текст */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as AttachmentInfo);
      else reject(new ApiError(xhr.status, body));
    };
    xhr.onerror = () => reject(new ApiError(0, "Сервер не ответил: проверьте, что бэкенд запущен."));
    xhr.ontimeout = () => reject(new ApiError(0, "Файл не успел загрузиться за 10 минут."));
    xhr.onabort = () => reject(new ApiError(0, "Отменено"));
    if (signal) {
      if (signal.aborted) { xhr.abort(); return; }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    const form = new FormData();
    form.append("project_id", String(projectId));
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

/** Убирает ещё не использованный файл на сервере (кнопка «убрать»). */
export const deleteAttachment = (attachmentId: string, projectId: number) =>
  http<{ deleted: string }>(`${BASE}/attachments/${encodeURIComponent(attachmentId)}?project_id=${projectId}`, { method: "DELETE" });

export const planFromMacro = (macroId: string, params: Record<string, unknown>, ctx: SceneContext, requestText = "") =>
  http<PlanPreviewResponse>(`${BASE}/plans/from-macro`, {
    method: "POST",
    body: JSON.stringify({ macro_id: macroId, params, request_text: requestText, ...sel(ctx) }),
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

export interface PlanListItem {
  id: string; status: PlanStatus; project_id: number | null; request_text: string;
  live: boolean; interrupted: boolean; created_at: string | null; updated_at: string | null;
}

/** Планы по статусу (для восстановления экрана выполнения после перезагрузки страницы). */
export const listPlans = (status: string, limit = 10) =>
  http<{ items: PlanListItem[] }>(`${BASE}/plans?status=${encodeURIComponent(status)}&limit=${limit}`);

export const getPlan = (planId: string) =>
  http<PlanView>(`${BASE}/plans/${encodeURIComponent(planId)}`);

export const stopPlan = (planId: string) =>
  http<{ status: string; stop_requested: boolean }>(`${BASE}/plans/${encodeURIComponent(planId)}/stop`, { method: "POST", body: "{}" });

export const getHistory = (limit = 20) =>
  http<HistoryResponse>(`${BASE}/history?limit=${limit}`);

// ---- ошибки ----

export const ERROR_TEXT_RU: Record<string, string> = {
  plan_invalid: "План не прошёл проверку: исправьте шаги и составьте план заново.",
  bad_plan: "План составлен неверно.",
  plan_changed: "План изменился после предпросмотра. Составьте план заново.",
  stale_context: "Данные проекта изменились после предпросмотра. Составьте план заново.",
  over_budget: "План не помещается в потолок расходов.",
  plan_not_approvable: "Этот план нельзя подтвердить в текущем состоянии.",
  approval_not_needed: "Этому плану подтверждение не требуется.",
  operator_required: "Подтвердить может только оператор-человек.",
  approval_required: "Нужно подтверждение оператора перед запуском.",
  already_running: "План уже выполняется.",
  plan_finished: "План уже завершён.",
  kill_switch_active: "Включён аварийный стоп оркестратора: планы не составляются и не выполняются.",
  project_busy: "В этом проекте уже идёт другой план.",
  macro_unavailable: "Умная кнопка недоступна: не хватает действий на сервере.",
  macro_params_invalid: "Параметры умной кнопки заданы неверно.",
  planner_rejected: "Планировщик отклонил запрос.",
  project_not_found: "Проект не найден.",
  type_not_allowed: "Такой тип файла прикрепить нельзя.",
  mime_not_allowed: "Тип содержимого не подходит к расширению файла.",
  content_mismatch: "Содержимое файла не похоже на его расширение.",
  too_large: "Файл слишком большой.",
  empty_file: "Файл пустой.",
  bad_name: "Недопустимое имя файла.",
  length_required: "Сервер не принял загрузку без размера.",
  attachment_not_found: "Прикреплённый файл не найден.",
  attachment_wrong_project: "Файл прикреплён к другому проекту.",
  attachment_consumed: "Файл уже использован в другом плане.",
  attachment_missing: "Файл удалён с диска.",
  attachments_need_project: "Файлы прикрепляются к открытому проекту.",
  too_many_attachments: "Прикреплено слишком много файлов.",
};

function stringifyItem(x: unknown): string {
  if (typeof x === "string") return x;
  if (x && typeof x === "object") {
    const o = x as { msg?: unknown; loc?: unknown; message?: unknown };
    const where = Array.isArray(o.loc) ? o.loc.filter((p) => p !== "body").join(".") : "";
    const text = typeof o.msg === "string" ? o.msg : typeof o.message === "string" ? o.message : JSON.stringify(x);
    return where ? `${where}: ${text}` : text;
  }
  return String(x);
}

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

/** Читаемое русское сообщение: строка, {detail:{code,message}}, массив ошибок валидации или текст. */
export function describeError(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : "Неизвестная ошибка.";
  const root = e.detail as unknown;
  const inner: unknown = root && typeof root === "object" && "detail" in (root as object) ? (root as { detail: unknown }).detail : root;
  const code = errorCode(e);
  const head = code ? (ERROR_TEXT_RU[code] ?? "Сервер отклонил запрос.") : null;
  let more = "";
  if (Array.isArray(inner)) {
    more = inner.map(stringifyItem).join("; ");
  } else if (inner && typeof inner === "object") {
    const o = inner as { message?: unknown; reason?: unknown; errors?: unknown; missing_action_ids?: unknown };
    const parts: string[] = [];
    if (typeof o.message === "string") parts.push(o.message);
    if (typeof o.reason === "string") parts.push(`причина: ${o.reason}`);
    if (Array.isArray(o.errors)) parts.push(o.errors.map(stringifyItem).join("; "));
    if (Array.isArray(o.missing_action_ids) && o.missing_action_ids.length) parts.push(`нет действий: ${o.missing_action_ids.join(", ")}`);
    more = parts.join(" · ");
  } else if (typeof inner === "string" && inner.trim()) {
    more = inner.trim();
  }
  if (e.status === 404 && !code) return `Не найдено или недоступно на сервере (404)${more ? `: ${more}` : ""}.`;
  if (e.status === 401 || e.status === 403) {
    if (!code) return `Нет доступа (${e.status})${more ? `: ${more}` : ""}.`;
  }
  if (head) return `${head}${more && more !== head ? ` ${more}` : ""} (код: ${code})`;
  if (more) return `Сервер отклонил запрос (${e.status}): ${more}`;
  return `Ошибка сервера (${e.status}): ${e.message}`;
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
  planner_call: "запрос к планировщику",
  planner_plan_linked: "план составлен по тексту",
  resume_refused: "возобновление отклонено",
  invalid_format: "ответ модели в неверном формате",
  transport_error: "сбой связи с моделью",
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

export interface ParamInfo { label: string; hint?: string }

/** Русские подписи параметров действий и умных кнопок. Неизвестные имена показываются как есть. */
export const PARAM_RU: Record<string, ParamInfo> = {
  project_id: { label: "Проект" },
  frame_id: { label: "Номер кадра", hint: "номер кадра на доске монтажа" },
  frame_number: { label: "Номер кадра", hint: "порядковый номер кадра в сцене" },
  frame_ids: { label: "Кадры", hint: "список номеров кадров" },
  frame_numbers: { label: "Номера кадров", hint: "список номеров; пусто означает все кадры" },
  frame_uuid: { label: "Идентификатор кадра" },
  after_frame_id: { label: "После кадра", hint: "номер кадра, после которого вставить" },
  left_frame_id: { label: "Левый кадр" },
  right_frame_id: { label: "Правый кадр" },
  a_frame: { label: "Первый кадр" },
  b_frame: { label: "Второй кадр" },
  from_frame: { label: "Из кадра" },
  to_frame: { label: "В кадр" },
  a_shot: { label: "План первого кадра", hint: "1 или 2" },
  b_shot: { label: "План второго кадра", hint: "1 или 2" },
  from_shot: { label: "Из плана", hint: "1 или 2" },
  to_shot: { label: "В план", hint: "1 или 2" },
  from_slot: { label: "Из ячейки" },
  to_slot: { label: "В ячейку" },
  shot: { label: "План", hint: "1 или 2" },
  shot_id: { label: "План" },
  slot: { label: "Ячейка" },
  slot_id: { label: "Ячейка" },
  scene_id: { label: "Сцена" },
  scene_number: { label: "Номер сцены" },
  scene_prompt: { label: "Описание сцены" },
  parent_number: { label: "Номер родительского кадра" },
  parent_uuid: { label: "Идентификатор родительского кадра" },
  before_index: { label: "Вставить перед позицией" },
  text: { label: "Текст", hint: "новый текст закадра" },
  prompt: { label: "Описание сцены", hint: "что должно быть в сцене" },
  instruction: { label: "Что изменить", hint: "правка простыми словами" },
  voiceover: { label: "Закадровый текст" },
  value: { label: "Значение" },
  field: { label: "Поле" },
  kind: { label: "Тип" },
  type: { label: "Тип операции" },
  role: { label: "Роль" },
  variant: { label: "Вариант" },
  desc: { label: "Описание" },
  title: { label: "Название" },
  description: { label: "Описание" },
  name: { label: "Название" },
  file: { label: "Файл" },
  category: { label: "Категория" },
  status: { label: "Статус" },
  coverage: { label: "Покрытие" },
  count: { label: "Количество вариантов" },
  max_image_ops: { label: "Макс. операций с картинками", hint: "лимит платных операций с картинками" },
  max_video_ops: { label: "Макс. операций с видео", hint: "лимит платных операций с видео" },
  image_generator: { label: "Генератор картинок", hint: "идентификатор модели" },
  image_resolution: { label: "Разрешение картинок" },
  video_generator: { label: "Генератор видео", hint: "идентификатор модели" },
  video_resolution: { label: "Разрешение видео" },
  node_key: { label: "Ключ ноды", hint: "идентификатор ноды группы" },
  node_ids: { label: "Ноды" },
  group_id: { label: "Группа" },
  ref_id: { label: "Референс" },
  step_code: { label: "Код шага" },
  ops: { label: "Операции" },
  action: { label: "Действие" },
  after: { label: "После" },
  anchors: { label: "Опорные точки" },
  anchor: { label: "Опорная точка" },
  change: { label: "Изменение" },
  main: { label: "Основная" },
  correction: { label: "Поправка" },
  replace_ns: { label: "Заменить номера" },
  clear: { label: "Сбросить" },
  clear_all: { label: "Очистить всё" },
  start: { label: "Начало" },
  end: { label: "Конец" },
  full: { label: "Весь отрезок" },
  method: { label: "Метод" },
  force_asr: { label: "Принудительно распознать речь" },
  bgm_level: { label: "Громкость фоновой музыки" },
  skip_intro_enabled: { label: "Пропускать заставку" },
  skip_intro_seconds: { label: "Длина заставки, сек." },
  subtitles_enabled: { label: "Субтитры" },
  post_voiceover_tail_seconds: { label: "Пауза после закадра, сек." },
  disabled: { label: "Отключено" },
};

export const paramLabel = (name: string): string => PARAM_RU[name]?.label ?? name;
export const paramHint = (name: string): string | undefined => PARAM_RU[name]?.hint;

/** Параметры шага простым языком: «Проект: 12; Номер кадра: 3». */
export function describeParams(params: Record<string, unknown> | undefined | null): string {
  const entries = Object.entries(params ?? {});
  if (entries.length === 0) return "без параметров";
  return entries
    .map(([k, v]) => {
      const val = typeof v === "string" ? v : typeof v === "boolean" ? (v ? "да" : "нет") : JSON.stringify(v);
      const short = val.length > 80 ? `${val.slice(0, 80)}…` : val;
      return `${paramLabel(k)}: ${short}`;
    })
    .join("; ");
}

/** Итоги и причины, которые сервер пишет в журнал и в план. */
export const OUTCOME_RU: Record<string, string> = {
  proposed: "предложен",
  approved: "подтверждён",
  consumed: "использовано",
  start: "запущен",
  resume: "продолжен после перезапуска",
  requested: "запрошено",
  running: "выполняется",
  succeeded: "готово",
  failed: "ошибка",
  stopped: "остановлен",
  blocked: "заблокировано",
  unknown: "неизвестно",
  skipped: "пропущен",
  cancelled: "отменён",
  interrupted: "прервано перезапуском",
  pending: "ожидает",
};
export const outcomeRu = (o: string | null | undefined): string => (o ? (OUTCOME_RU[o] ?? o) : "");

export const STOP_REASON_RU: Record<string, string> = {
  operator_stop: "остановлено оператором",
  kill_switch_active: "включён аварийный стоп оркестратора",
  resume_window_expired: "прошло слишком много времени после прерывания: нужно новое подтверждение",
};
export const stopReasonRu = (r: string): string => STOP_REASON_RU[r] ?? r;

/** Переводит известные английские тексты ошибок сервера; неизвестные оставляет как есть. */
export function serverTextRu(t: string | null | undefined): string {
  if (!t) return "";
  let m: RegExpMatchArray | null;
  if ((m = t.match(/^step (\d+): ([\s\S]*)$/))) return `шаг ${Number(m[1]) + 1}: ${serverTextRu(m[2])}`;
  if (/^outcome unknown after interruption/.test(t)) return "результат неизвестен после прерывания, нужен ручной разбор";
  if (/^timeout \(outcome unknown\)/.test(t)) return "время вышло, результат неизвестен";
  if ((m = t.match(/^check failed: ActionCallError: HTTP (\d+): ([\s\S]*)$/))) return `проверка не пройдена (HTTP ${m[1]}): ${m[2]}`;
  if ((m = t.match(/^check failed: ([\s\S]*)$/))) return `проверка не пройдена: ${m[1]}`;
  if (t === "blocked") return "заблокировано";
  if (t === "invalid params") return "неверные параметры";
  if (t === "step was claimed elsewhere") return "шаг уже взят в работу в другом месте";
  if ((m = t.match(/^dependency (\d+) not satisfied/))) return `не выполнен шаг ${Number(m[1]) + 1}, от которого зависит этот`;
  if ((m = t.match(/^precondition (\S+) failed/))) return `не выполнено условие (${m[1]})`;
  if ((m = t.match(/^budget: (\S+)/))) return `ограничение бюджета (${m[1]})`;
  if (t === "kill_switch_active") return "включён аварийный стоп оркестратора";
  if (t === "operator_stop") return "остановлено оператором";
  if (t === "stopped by operator") return "остановлено оператором";
  if (t === "stopped by kill switch") return "остановлено аварийным стопом";
  return t;
}

let titlesPromise: Promise<Record<string, string>> | null = null;
/** Словарь «id действия -> русское название» из каталога (кэшируется на время сессии страницы). */
export function loadActionTitles(): Promise<Record<string, string>> {
  if (!titlesPromise) {
    titlesPromise = getCatalog()
      .then((c) => Object.fromEntries(c.actions.map((a) => [a.id, a.title_ru])))
      .catch(() => { titlesPromise = null; return {} as Record<string, string>; });
  }
  return titlesPromise;
}

export function useActionTitles(): Record<string, string> {
  const [t, setT] = useState<Record<string, string>>({});
  useEffect(() => {
    let off = false;
    void loadActionTitles().then((r) => { if (!off) setT(r); });
    return () => { off = true; };
  }, []);
  return t;
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
  return m.title_ru ?? String(m.id);
}

/** Начальные значения формы макроса: default, первый enum, пусто; project_id подставляется отдельно. */
export function macroInitialValues(m: MacroJson, projectId: number): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (const [k, sch] of Object.entries(m.params_schema?.properties ?? {})) {
    if (k === "project_id") out[k] = String(projectId);
    else if (sch.default !== undefined) out[k] = sch.type === "boolean" ? Boolean(sch.default) : String(sch.default);
    else if (sch.type === "boolean") out[k] = false;
    else if (sch.enum && sch.enum.length) out[k] = String(sch.enum[0]);
    else out[k] = "";
  }
  return out;
}

/** Собирает типизированные params из значений формы. Пустые необязательные поля опускаются. */
export function buildMacroParams(m: MacroJson, values: Record<string, string | boolean>): { params: Record<string, unknown>; errors: string[] } {
  const params: Record<string, unknown> = {};
  const errors: string[] = [];
  const req = new Set(m.params_schema?.required ?? []);
  for (const [k, sch] of Object.entries(m.params_schema?.properties ?? {})) {
    const raw = values[k];
    const label = sch.title ?? paramLabel(k);
    if (sch.type === "boolean") { params[k] = Boolean(raw); continue; }
    const str = typeof raw === "string" ? raw.trim() : "";
    if (str === "") { if (req.has(k)) errors.push(`Заполните поле: ${label}`); continue; }
    if (sch.type === "integer" || sch.type === "number") {
      const n = Number(str);
      if (!Number.isFinite(n) || (sch.type === "integer" && !Number.isInteger(n))) { errors.push(`${label}: нужно число`); continue; }
      if (sch.minimum != null && n < sch.minimum) errors.push(`${label}: не меньше ${sch.minimum}`);
      if (sch.maximum != null && n > sch.maximum) errors.push(`${label}: не больше ${sch.maximum}`);
      params[k] = n;
    } else {
      params[k] = str;
    }
  }
  return { params, errors };
}
