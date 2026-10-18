/**
 * Клиент оркестратора: SSE через fetch (EventSource не умеет Authorization)
 * и REST message / approve / reject / kill.
 *
 * Формат сверен с WS3 (PR #346, `orchestrator_chat.py`):
 * POST `.../message` тело `{text}`, ответ `{ok, session, ack_seq}`;
 * GET `.../stream` — SSE `event`/`id`=seq/`data`=OrchEvent JSON, Bearer, 401,
 * replay по Last-Event-ID (seq строго больше).
 * Approve/kill в том роутере нет — мок держит их до WS2.
 * Куда уходит Bearer: тот же origin, относительный `/api/...` или
 * `NEXT_PUBLIC_ORCH_API`. Адрес из `?orchApi=` не используется
 * (исключение — same-origin путь `/api/...` при `NEXT_PUBLIC_ORCH_QUERY_API`).
 */

import type {
  OrchApprovalDecision,
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

/** Управляющие символы и обратный слэш меняют разбор URL. Такие строки не адрес API. */
function hasUnsafeUrlChars(value: string): boolean {
  return /[\u0000-\u001F\u007F\\]/.test(value);
}

/** Хвостовые CR/LF из Windows-env снимаются до проверки. Перевод строки в середине адреса по-прежнему отказ. */
function withoutTrailingBreaks(value: string): string {
  return value.replace(/[\r\n]+$/, "");
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  // URL.hostname для IPv6 уже со скобками (`[::1]`). Голый `::1` парсер не отдаёт.
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

/**
 * `NEXT_PUBLIC_ORCH_API`: https на любом хосте, http только на localhost / 127.0.0.1 / [::1].
 * Возвращает origin + pathname без query, hash и хвостового слэша.
 * Userinfo, протокол-относительный адрес и прочие схемы — null.
 */
export function normalizedOrchEnvApi(value: string): string | null {
  const prepared = withoutTrailingBreaks(value);
  if (hasUnsafeUrlChars(prepared)) return null;
  const raw = prepared.trim();
  if (!raw || raw.startsWith("//")) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.username || url.password) return null;
  const loopback = url.protocol === "http:" && isLoopbackHost(url.hostname);
  if (url.protocol !== "https:" && !loopback) return null;
  const path = url.pathname === "/" ? "" : url.pathname.replace(/\/$/, "");
  return `${url.origin}${path}`;
}

/** В лог — только origin. Userinfo, query и неразобранная строка не печатаются. */
function discardedOrchApiLabel(raw: string): string {
  try {
    const url = new URL(raw.trim());
    url.username = "";
    url.password = "";
    url.search = "";
    url.hash = "";
    return url.origin;
  } catch {
    return "некорректный адрес";
  }
}

/** Один warn на серию отброшенных чтений. Успешный env снова взводит флаг. */
let discardedOrchApiWarned = false;

function warnDiscardedOrchApi(raw: string): void {
  if (discardedOrchApiWarned) return;
  discardedOrchApiWarned = true;
  console.warn(`NEXT_PUBLIC_ORCH_API отброшен: ${discardedOrchApiLabel(raw)}`);
}

/** Абсолютный хост API из сборки. Пусто или отброшенный env — относительный `/api/...` на том же origin. */
export function envOrchApiBase(): string {
  const raw = withoutTrailingBreaks(process.env.NEXT_PUBLIC_ORCH_API || "");
  if (hasUnsafeUrlChars(raw)) {
    warnDiscardedOrchApi(raw);
    return "";
  }
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const normalized = normalizedOrchEnvApi(trimmed);
  if (!normalized) {
    warnDiscardedOrchApi(raw);
    return "";
  }
  discardedOrchApiWarned = false;
  return normalized;
}

function queryApiOverrideEnabled(): boolean {
  const raw = process.env.NEXT_PUBLIC_ORCH_QUERY_API;
  return raw === "1" || raw === "true";
}

function normalizeBase(value: string): string {
  return value.trim().replace(/\/$/, "");
}

/** Same-origin `/api` или `/api/...`. `//host`, userinfo и не-http(s) — чужой адрес. */
export function isSameOriginApiBase(value: string, pageOrigin: string): boolean {
  if (hasUnsafeUrlChars(value) || hasUnsafeUrlChars(pageOrigin)) return false;
  const raw = normalizeBase(value);
  if (!raw || !pageOrigin) return false;
  if (raw.startsWith("//")) return false;
  if (raw.startsWith("/")) {
    return raw === "/api" || raw.startsWith("/api/");
  }
  let url: URL;
  let page: URL;
  try {
    url = new URL(raw);
    page = new URL(pageOrigin);
  } catch {
    return false;
  }
  if (url.username || url.password) return false;
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;
  if (url.origin !== page.origin) return false;
  return url.pathname === "/api" || url.pathname.startsWith("/api/");
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

/** Пустая база, разрешённый env-хост или same-origin `/api/...`. Чужой origin — нет. */
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

/** Тот же конверт, что у postApproval: 204 — `{status:204, body:null}`, не `undefined`. */
export type OrchHttpResult<T> = {
  status: number;
  body: T | null;
};

async function readJsonObject<T>(res: Response): Promise<T | null> {
  const text = await res.text();
  if (!text.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new OrchApiError(res.status, text.slice(0, 300));
  }
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new OrchApiError(res.status, "ответ не объект");
  }
  return parsed as T;
}

async function postJson<T>(cfg: OrchClientConfig, path: string, body: unknown, signal?: AbortSignal): Promise<OrchHttpResult<T>> {
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
  if (res.status === 204) return { status: 204, body: null };
  return { status: res.status, body: await readJsonObject<T>(res) };
}

function requireJsonBody<T>(result: OrchHttpResult<T>): T {
  if (result.body == null) throw new OrchApiError(result.status, "пустой ответ");
  return result.body;
}

export async function postOrchMessage(
  cfg: OrchClientConfig,
  body: OrchOutboundMessage,
  signal?: AbortSignal,
): Promise<OrchMessageAckHttp> {
  return requireJsonBody(
    await postJson(cfg, `/api/orchestrator/chat/${encodeURIComponent(cfg.session)}/message`, body, signal),
  );
}

export type ApprovalHttpResult = {
  status: number;
  body: { ok?: boolean; matched?: boolean; approval_id?: string } | null;
};

/** Approve/reject. Пустое тело — успех только при status === 204. Остальные 2xx без тела — не успех. */
async function postApproval(
  cfg: OrchClientConfig,
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<ApprovalHttpResult> {
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
  if (res.status === 204) return { status: 204, body: null };
  const text = await res.text();
  if (!text.trim()) return { status: res.status, body: null };
  try {
    const parsed = JSON.parse(text) as ApprovalHttpResult["body"];
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { status: res.status, body: null };
    }
    return { status: res.status, body: parsed };
  } catch {
    throw new OrchApiError(res.status, text.slice(0, 300));
  }
}

export function postOrchApprove(
  cfg: OrchClientConfig,
  body: OrchApprovalDecision,
  signal?: AbortSignal,
): Promise<ApprovalHttpResult> {
  return postApproval(
    cfg,
    `/api/orchestrator/approvals/${encodeURIComponent(body.approval_id)}/approve`,
    body,
    signal,
  );
}

export function postOrchReject(
  cfg: OrchClientConfig,
  body: OrchApprovalDecision,
  signal?: AbortSignal,
): Promise<ApprovalHttpResult> {
  return postApproval(
    cfg,
    `/api/orchestrator/approvals/${encodeURIComponent(body.approval_id)}/reject`,
    body,
    signal,
  );
}

export type OrchKillHttpBody = OrchKillResult & { stop_key_set?: boolean };

export function postOrchKill(
  cfg: OrchClientConfig,
  body: OrchKillRequest,
  signal?: AbortSignal,
): Promise<OrchHttpResult<OrchKillHttpBody>> {
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

export function listOrchReports(cfg: OrchClientConfig, projectId: number, signal?: AbortSignal): Promise<{ reports: OrchReportsRow[] }> {
  return getJson(cfg, `/api/orchestrator/projects/${projectId}/reports`, signal);
}

export function getOrchReport(cfg: OrchClientConfig, reportId: number, signal?: AbortSignal): Promise<ReportDetail> {
  return getJson(cfg, `/api/orchestrator/reports/${reportId}`, signal);
}

export async function postCreateReport(
  cfg: OrchClientConfig,
  body: { title: string; project_id: number; sections: ReportSection[]; editable?: boolean },
  signal?: AbortSignal,
): Promise<ReportMutation> {
  return requireJsonBody(await postJson(cfg, "/api/orchestrator/actions/create_report", body, signal));
}

export async function postUpdateReport(
  cfg: OrchClientConfig,
  body: { report_id: number; base_version: number; sections_patch: ReportSection[]; reason?: string },
  signal?: AbortSignal,
): Promise<ReportMutation> {
  return requireJsonBody(await postJson(cfg, "/api/orchestrator/actions/update_report", body, signal));
}

export async function postRollbackReport(
  cfg: OrchClientConfig,
  reportId: number,
  version: number,
  signal?: AbortSignal,
): Promise<ReportMutation> {
  return requireJsonBody(await postJson(cfg, `/api/orchestrator/reports/${reportId}/rollback`, { version }, signal));
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
