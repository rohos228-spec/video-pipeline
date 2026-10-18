/**
 * Клиент оркестратора: SSE через fetch (EventSource не умеет Authorization)
 * и REST message / approve / reject / kill.
 *
 * Формат сверен с WS3 (PR #346, `orchestrator_chat.py`):
 * POST `.../message` тело `{text}`, ответ `{ok, session, ack_seq}`;
 * GET `.../stream` — SSE `event`/`id`=seq/`data`=OrchEvent JSON, Bearer, 401,
 * replay по Last-Event-ID (seq строго больше).
 * Решение: POST `.../chat/{session}/approve`, тело `{change_id, decision}`.
 * Успех — 200 и JSON. 204 пустым телом успехом не считается.
 * Куда уходит Bearer: тот же origin, относительный `/api/...` или
 * `NEXT_PUBLIC_ORCH_API`. Адрес из `?orchApi=` не используется
 * (исключение — same-origin путь `/api/...` при `NEXT_PUBLIC_ORCH_QUERY_API`).
 */

import type {
  OrchEvent,
  OrchKillRequest,
  OrchKillResult,
  OrchMessageAckHttp,
  OrchOutboundMessage,
  OrchReportsRow,
  OrchReportVersionsRow,
} from "./orchestratorTypes";

export const ORCH_TOKEN_STORAGE_KEY = "vp-orch-api-token";

export class OrchApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "OrchApiError";
    this.status = status;
  }
}

export interface OrchClientConfig {
  /** Пустой — тот же origin (`/api/...`). */
  apiBase: string;
  session: string;
  token: string;
  /**
   * Сценарий мока. В заголовок попадает только при NEXT_PUBLIC_ORCH_TEST.
   * Query и localStorage сами по себе заголовок не включают.
   */
  scenario: string;
}

export function readOrchToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(ORCH_TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function writeOrchToken(token: string): void {
  localStorage.setItem(ORCH_TOKEN_STORAGE_KEY, token);
}

/** Чекбокс «Новая задача». По умолчанию выключен: NEXT_PUBLIC_ORCH_TASK_MODE не задан. */
export function orchTaskModeEnabled(): boolean {
  const raw = process.env.NEXT_PUBLIC_ORCH_TASK_MODE;
  return raw === "1" || raw === "true";
}

/**
 * Тестовый режим сборки, аналог ORCH_TEST=1.
 * Без флага прод-клиент не шлёт X-Orch-Scenario.
 */
export function orchTestModeEnabled(): boolean {
  const raw = process.env.NEXT_PUBLIC_ORCH_TEST;
  return raw === "1" || raw === "true";
}

const STUDIO_PROJECT_STORAGE_KEY = "vp-studio-selected-project-id";

function positiveId(value: unknown): number | null {
  const id = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(id) && id > 0) return id;
  return null;
}

/** Проект из `?project=` или из выбора студии. Без запасного id. */
export function readOrchProjectId(): number | null {
  if (typeof window === "undefined") return null;
  const fromQuery = positiveId(new URLSearchParams(window.location.search).get("project"));
  if (fromQuery != null) return fromQuery;
  try {
    const raw = localStorage.getItem(STUDIO_PROJECT_STORAGE_KEY);
    if (!raw) return null;
    return positiveId(JSON.parse(raw) as unknown);
  } catch {
    return null;
  }
}

/** Абсолютный хост API из сборки. Пусто — относительный `/api/...` на том же origin. */
export function envOrchApiBase(): string {
  return (process.env.NEXT_PUBLIC_ORCH_API || "").trim().replace(/\/$/, "");
}

function queryApiOverrideEnabled(): boolean {
  const raw = process.env.NEXT_PUBLIC_ORCH_QUERY_API;
  return raw === "1" || raw === "true";
}

function normalizeBase(value: string): string {
  return value.trim().replace(/\/$/, "");
}

/** Same-origin `/api` или `/api/...`. `//host` — чужой origin, не путь. */
export function isSameOriginApiBase(value: string, pageOrigin: string): boolean {
  const raw = normalizeBase(value);
  if (!raw || !pageOrigin) return false;
  if (raw.startsWith("/") && !raw.startsWith("//")) {
    return raw === "/api" || raw.startsWith("/api/");
  }
  try {
    const url = new URL(raw);
    if (url.origin !== new URL(pageOrigin).origin) return false;
    return url.pathname === "/api" || url.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

/**
 * База запросов с Bearer.
 * Query не выбирает хост. При тестовом флаге принимается только same-origin `/api/...`.
 * Иначе остаётся адрес из `NEXT_PUBLIC_ORCH_API` (или пустая строка — тот же origin).
 */
export function resolveOrchApiBase(queryApi: string | null, pageOrigin: string): string {
  const query = queryApi == null ? "" : normalizeBase(queryApi);
  if (query && queryApiOverrideEnabled() && isSameOriginApiBase(query, pageOrigin)) {
    return query;
  }
  return envOrchApiBase();
}

/** Пустая база, env-хост или same-origin `/api/...`. Чужой origin — нет. */
export function bearerTargetAllowed(apiBase: string, pageOrigin = ""): boolean {
  const base = normalizeBase(apiBase);
  if (!base) return true;
  const env = envOrchApiBase();
  if (env && base === env) return true;
  return isSameOriginApiBase(base, pageOrigin);
}

function assertBearerTarget(apiBase: string): void {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  if (!bearerTargetAllowed(apiBase, origin)) {
    throw new OrchApiError(0, "отказ: токен не уходит на чужой origin");
  }
}

export function readOrchBoot(): { apiBase: string; session: string; scenario: string } {
  if (typeof window === "undefined") {
    return { apiBase: envOrchApiBase(), session: "studio", scenario: "stream" };
  }
  const q = new URLSearchParams(window.location.search);
  const apiBase = resolveOrchApiBase(q.get("orchApi"), window.location.origin);
  const session = (q.get("session") || "studio").trim() || "studio";
  const scenario = (q.get("scenario") || "stream").trim() || "stream";
  return { apiBase, session, scenario };
}

function apiUrl(apiBase: string, path: string): string {
  if (!apiBase) return path;
  return `${apiBase}${path}`;
}

/** Bearer всегда. X-Orch-Scenario — только в тестовой сборке, значение сценария не важно. */
export function orchAuthHeaders(cfg: OrchClientConfig, extra?: Record<string, string>): Headers {
  const headers = new Headers(extra);
  headers.set("Authorization", `Bearer ${cfg.token}`);
  if (orchTestModeEnabled()) headers.set("X-Orch-Scenario", cfg.scenario);
  return headers;
}

async function readError(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  if (!text) return `HTTP ${res.status}`;
  try {
    const json = JSON.parse(text) as { detail?: unknown; message?: unknown };
    if (typeof json.detail === "string") return json.detail;
    if (typeof json.message === "string") return json.message;
  } catch {
    /* текст как есть */
  }
  return text.slice(0, 300);
}

async function postJson<T>(cfg: OrchClientConfig, path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  assertBearerTarget(cfg.apiBase);
  const headers = orchAuthHeaders(cfg);
  headers.set("Content-Type", "application/json");
  const res = await fetch(apiUrl(cfg.apiBase, path), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401) {
    throw new OrchApiError(401, "401 — токен не принят");
  }
  if (!res.ok) {
    throw new OrchApiError(res.status, await readError(res));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function postOrchMessage(
  cfg: OrchClientConfig,
  body: OrchOutboundMessage,
  signal?: AbortSignal,
): Promise<OrchMessageAckHttp> {
  return postJson(cfg, `/api/orchestrator/chat/${encodeURIComponent(cfg.session)}/message`, body, signal);
}

/** Правка отчёта (`update_report`) или восстановление пометки. Тело в контракте #378 не задано. */
export function postOrchActionMessage(
  cfg: OrchClientConfig,
  body: { text: string; action: { name: string; report_id?: number; change_id?: string; snapshot_id?: string; archive_ref?: string; base_version?: number } },
  signal?: AbortSignal,
): Promise<OrchMessageAckHttp> {
  return postJson(cfg, `/api/orchestrator/chat/${encodeURIComponent(cfg.session)}/message`, body, signal);
}

/** Тело `ApproveIn` (#370). Лишние поля (`approval_id`, `task_id`) не отправляются. */
export interface OrchApproveIn {
  change_id: string;
  decision: "approve" | "retry_step" | "deny";
}

/** Успешный `POST .../approve`: 200 и JSON, не 204. */
export interface OrchApproveResult {
  executed?: boolean;
  reason?: string | null;
  forecast_usd?: number | null;
  retry_armed?: boolean;
  change_id?: string | null;
  decision?: string | null;
  ok?: boolean;
  matched?: boolean;
  approval_id?: string;
}

const APPROVE_NOT_JSON = "ответ approve — не JSON 200";

/** Все решения: `POST /api/orchestrator/chat/{session}/approve`. 204 — отказ. */
export async function postOrchDecision(
  cfg: OrchClientConfig,
  body: OrchApproveIn,
  signal?: AbortSignal,
): Promise<OrchApproveResult> {
  assertBearerTarget(cfg.apiBase);
  const headers = orchAuthHeaders(cfg);
  headers.set("Content-Type", "application/json");
  const res = await fetch(apiUrl(cfg.apiBase, `/api/orchestrator/chat/${encodeURIComponent(cfg.session)}/approve`), {
    method: "POST",
    headers,
    body: JSON.stringify({ change_id: body.change_id, decision: body.decision }),
    signal,
  });
  if (res.status === 204) throw new OrchApiError(204, APPROVE_NOT_JSON);
  if (res.status === 401) throw new OrchApiError(401, "401 — токен не принят");
  if (!res.ok) throw new OrchApiError(res.status, await readError(res));
  if (res.status !== 200) throw new OrchApiError(res.status, APPROVE_NOT_JSON);
  let json: unknown;
  try {
    json = await res.json();
  } catch {
    throw new OrchApiError(res.status, APPROVE_NOT_JSON);
  }
  if (!json || typeof json !== "object") throw new OrchApiError(200, APPROVE_NOT_JSON);
  return json as OrchApproveResult;
}

export function postOrchKill(cfg: OrchClientConfig, body: OrchKillRequest, signal?: AbortSignal): Promise<OrchKillResult> {
  return postJson(cfg, "/api/orchestrator/kill", body, signal);
}

/** Проверка Bearer до записи в localStorage. Тело стрима закрывается сразу после статуса. */
export async function probeOrchAuth(cfg: OrchClientConfig): Promise<void> {
  assertBearerTarget(cfg.apiBase);
  const ac = new AbortController();
  const res = await fetch(apiUrl(cfg.apiBase, `/api/orchestrator/chat/${encodeURIComponent(cfg.session)}/stream`), {
    method: "GET",
    headers: orchAuthHeaders(cfg, { Accept: "text/event-stream" }),
    signal: ac.signal,
  });
  const status = res.status;
  let message = "";
  if (status === 401) message = "401 — токен не принят";
  else if (!res.ok) message = await readError(res);
  ac.abort();
  void res.body?.cancel().catch(() => undefined);
  if (status === 401 || !res.ok) throw new OrchApiError(status, message || `HTTP ${status}`);
}

/**
 * Секция отчёта. Форма как в create_report (план §6.3). В таблице это sections_json.
 * Пути ниже — предложение для WS2, мок их уже отвечает.
 */
export interface ReportSection {
  heading: string;
  html: string;
  data_table?: Record<string, unknown> | null;
}

export interface ReportDetail {
  report: OrchReportsRow;
  sections: ReportSection[];
  versions: OrchReportVersionsRow[];
}

export interface ReportMutation {
  ok: boolean;
  report_id: number;
  version: number;
  change_id: string;
}

async function getJson<T>(cfg: OrchClientConfig, path: string, signal?: AbortSignal): Promise<T> {
  assertBearerTarget(cfg.apiBase);
  const res = await fetch(apiUrl(cfg.apiBase, path), {
    method: "GET",
    headers: orchAuthHeaders(cfg, { Accept: "application/json" }),
    signal,
  });
  if (res.status === 401) throw new OrchApiError(401, "401 — токен не принят");
  if (!res.ok) throw new OrchApiError(res.status, await readError(res));
  return (await res.json()) as T;
}

function liveVersion(body: { version?: unknown; base_version?: unknown } | null): number | null {
  const value = body?.version ?? body?.base_version;
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : null;
}

/** Текущая версия после 409: снимок, если он есть, иначе status. */
export async function readOrchLiveVersion(
  cfg: OrchClientConfig,
  snapshotUrl: string | null,
  signal?: AbortSignal,
): Promise<number | null> {
  const paths: string[] = [];
  if (snapshotUrl && snapshotUrl.startsWith("/api/orchestrator/") && !snapshotUrl.split("/").includes("..")) {
    paths.push(snapshotUrl);
  }
  paths.push(`/api/orchestrator/chat/${encodeURIComponent(cfg.session)}/status`);
  for (const path of paths) {
    try {
      const body = await getJson<{ version?: unknown; base_version?: unknown }>(cfg, path, signal);
      const version = liveVersion(body);
      if (version != null) return version;
    } catch (error) {
      if (signal?.aborted) return null;
      if (error instanceof OrchApiError && error.status === 401) throw error;
    }
  }
  return null;
}

export function listOrchReports(cfg: OrchClientConfig, projectId: number, signal?: AbortSignal): Promise<{ reports: OrchReportsRow[] }> {
  return getJson(cfg, `/api/orchestrator/projects/${projectId}/reports`, signal);
}

export function getOrchReport(cfg: OrchClientConfig, reportId: number, signal?: AbortSignal): Promise<ReportDetail> {
  return getJson(cfg, `/api/orchestrator/reports/${reportId}`, signal);
}

export function postCreateReport(
  cfg: OrchClientConfig,
  body: { title: string; project_id: number; sections: ReportSection[]; editable?: boolean },
  signal?: AbortSignal,
): Promise<ReportMutation> {
  return postJson(cfg, "/api/orchestrator/actions/create_report", body, signal);
}

export function postUpdateReport(
  cfg: OrchClientConfig,
  body: { report_id: number; base_version: number; sections_patch: ReportSection[]; reason?: string },
  signal?: AbortSignal,
): Promise<ReportMutation> {
  return postJson(cfg, "/api/orchestrator/actions/update_report", body, signal);
}

export function postRollbackReport(
  cfg: OrchClientConfig,
  reportId: number,
  version: number,
  signal?: AbortSignal,
): Promise<ReportMutation> {
  return postJson(cfg, `/api/orchestrator/reports/${reportId}/rollback`, { version }, signal);
}

export interface ParsedSse {
  id: string;
  event: string;
  data: string;
}

/** Разбор буфера SSE. Неполное событие остаётся в rest. */
export function takeSseEvents(buffer: string): { events: ParsedSse[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const chunks = normalized.split("\n\n");
  const rest = chunks.pop() ?? "";
  const events: ParsedSse[] = [];
  for (const chunk of chunks) {
    const lines = chunk.split("\n");
    let id = "";
    let event = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (dataLines.length === 0) continue;
    events.push({ id, event, data: dataLines.join("\n") });
  }
  return { events, rest };
}

function readTaskId(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

/** data — JSON OrchEvent: type, task_id (int|null), payload, seq, session_id. id кадра = seq. */
export function eventFromSse(parsed: ParsedSse): OrchEvent | null {
  try {
    const json = JSON.parse(parsed.data) as Partial<OrchEvent> & { seq?: number };
    const seqFromId = parsed.id ? Number(parsed.id) : NaN;
    const seq = typeof json.seq === "number" ? json.seq : seqFromId;
    if (!Number.isFinite(seq)) return null;
    const type = String(json.type ?? parsed.event);
    const payload =
      json.payload && typeof json.payload === "object" ? (json.payload as Record<string, unknown>) : {};
    return {
      type,
      task_id: readTaskId(json.task_id),
      payload,
      seq,
      session_id: typeof json.session_id === "string" ? json.session_id : "",
    };
  } catch {
    return null;
  }
}

export interface StreamHandlers {
  onEvent: (event: OrchEvent) => void;
  onOpen?: () => void;
  signal: AbortSignal;
  lastEventId: number;
}

/**
 * Одна попытка чтения SSE. Возврат — штатный конец потока (сервер закрыл).
 * 401 и прочие HTTP — OrchApiError. Abort — DOMException AbortError.
 */
export async function consumeOrchStream(cfg: OrchClientConfig, handlers: StreamHandlers): Promise<void> {
  assertBearerTarget(cfg.apiBase);
  const headers = orchAuthHeaders(cfg, { Accept: "text/event-stream" });
  if (handlers.lastEventId > 0) {
    headers.set("Last-Event-ID", String(handlers.lastEventId));
  }
  const res = await fetch(
    apiUrl(cfg.apiBase, `/api/orchestrator/chat/${encodeURIComponent(cfg.session)}/stream`),
    { method: "GET", headers, signal: handlers.signal },
  );
  if (res.status === 401) {
    throw new OrchApiError(401, "401 — токен не принят");
  }
  if (!res.ok) {
    throw new OrchApiError(res.status, await readError(res));
  }
  if (!res.body) {
    throw new OrchApiError(res.status, "пустой поток SSE");
  }
  handlers.onOpen?.();
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!handlers.signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const taken = takeSseEvents(buffer);
      buffer = taken.rest;
      for (const parsed of taken.events) {
        const event = eventFromSse(parsed);
        if (event) handlers.onEvent(event);
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* уже закрыт */
    }
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function sleepWithSignal(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort);
  });
}
