/**
 * Клиент оркестратора: SSE через fetch (EventSource не умеет Authorization)
 * и REST message / approve / reject / kill.
 *
 * Формат сверен с WS3 (PR #346, `orchestrator_chat.py`):
 * POST `.../message` тело `{text}`, ответ `{ok, session, ack_seq}`;
 * GET `.../stream` — SSE `event`/`id`=seq/`data`=OrchEvent JSON, Bearer, 401,
 * replay по Last-Event-ID (seq строго больше).
 * Approve/kill в том роутере нет — мок держит их до WS2.
 * Базовый URL можно подменить мок-сервером (`?orchApi=http://127.0.0.1:8799`).
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
  /** Уходит заголовком X-Orch-Scenario, мок-сервер выбирает по нему сценарий. */
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

export function readOrchBoot(): { apiBase: string; session: string; scenario: string } {
  if (typeof window === "undefined") {
    return { apiBase: "", session: "studio", scenario: "stream" };
  }
  const q = new URLSearchParams(window.location.search);
  const apiBase = (q.get("orchApi") || "").replace(/\/$/, "");
  const session = (q.get("session") || "studio").trim() || "studio";
  const scenario = (q.get("scenario") || "stream").trim() || "stream";
  return { apiBase, session, scenario };
}

function apiUrl(apiBase: string, path: string): string {
  if (!apiBase) return path;
  return `${apiBase}${path}`;
}

function authHeaders(cfg: OrchClientConfig, extra?: Record<string, string>): Headers {
  const headers = new Headers(extra);
  headers.set("Authorization", `Bearer ${cfg.token}`);
  headers.set("X-Orch-Scenario", cfg.scenario);
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
  const headers = authHeaders(cfg);
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

export function postOrchApprove(cfg: OrchClientConfig, body: OrchApprovalDecision, signal?: AbortSignal): Promise<{ ok: boolean }> {
  return postJson(
    cfg,
    `/api/orchestrator/approvals/${encodeURIComponent(body.approval_id)}/approve`,
    body,
    signal,
  );
}

export function postOrchReject(cfg: OrchClientConfig, body: OrchApprovalDecision, signal?: AbortSignal): Promise<{ ok: boolean }> {
  return postJson(
    cfg,
    `/api/orchestrator/approvals/${encodeURIComponent(body.approval_id)}/reject`,
    body,
    signal,
  );
}

export function postOrchKill(cfg: OrchClientConfig, body: OrchKillRequest, signal?: AbortSignal): Promise<OrchKillResult> {
  return postJson(cfg, "/api/orchestrator/kill", body, signal);
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
  const res = await fetch(apiUrl(cfg.apiBase, path), {
    method: "GET",
    headers: authHeaders(cfg, { Accept: "application/json" }),
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

export async function fetchOrchReportHtml(
  cfg: OrchClientConfig,
  reportId: number,
  signal?: AbortSignal,
): Promise<{ html: string; csp: string }> {
  const res = await fetch(apiUrl(cfg.apiBase, `/api/orchestrator/reports/${reportId}/html`), {
    method: "GET",
    headers: authHeaders(cfg, { Accept: "text/html" }),
    signal,
  });
  if (res.status === 401) throw new OrchApiError(401, "401 — токен не принят");
  if (!res.ok) throw new OrchApiError(res.status, await readError(res));
  return {
    html: await res.text(),
    csp: res.headers.get("content-security-policy") ?? "",
  };
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
  body: { report_id: number; sections_patch: ReportSection[]; reason?: string },
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
  const headers = authHeaders(cfg, { Accept: "text/event-stream" });
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
