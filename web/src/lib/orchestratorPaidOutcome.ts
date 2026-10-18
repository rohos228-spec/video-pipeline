/**
 * Нетерминальная карточка `waiting_approval` / `paid_outcome_unknown`.
 * Провод ws3-r3.3 и ws3-r3.3.1 оба принимаются. Причина есть в BLOCK_OUTCOMES с ws3-r3.3.1.
 *
 * `decision` закреплён: approve | retry_step | deny.
 *
 * Имена сверены с #358 @ fbd8b6ee47472ad24a8f0036815055d66f60520e
 * (`origin/cursor/ws3-nonblocking-350`). Первое `approval_required` уже несёт
 * `change_id`, `forecast_usd` и `retry_armed: false`.
 * Сервер сам ставит `change_id` вида `orch-appr-<task_id>-<n>`
 * (`tests/agents/test_change_id.py`, `_paid_id`). Клиентский id отбрасывается.
 *
 * HTTP: `POST /api/orchestrator/chat/{session_id}/approve`.
 * Тело только `{change_id, decision, retry_grant}`. Лишнее поле — 422.
 * 404 — чужой или неизвестный id. 409 — уже решён или не текущий.
 * 401 — нет Bearer. Карточки change_control (#342) этот путь не используют.
 */

/** Нет в union ApprovalReason контракта ws3-r3.3. */
export const PAID_OUTCOME_UNKNOWN_REASON = "paid_outcome_unknown";

export const PAID_OUTCOME_DECISION = {
  approve: "approve",
  retryStep: "retry_step",
  deny: "deny",
} as const;

export type PaidOutcomeDecision = (typeof PAID_OUTCOME_DECISION)[keyof typeof PAID_OUTCOME_DECISION];

/** #358: прогноз на `approval_required` и в ответе `decision=retry_step`. */
export const PAID_OUTCOME_FORECAST_FIELD = "forecast_usd";

/** #358: список кнопок на `approval_required`. Нет `retry_step` — перезапуск не предлагается. */
export const PAID_OUTCOME_DECISIONS_FIELD = "decisions";

/** #358: повторное `approval_required` после `retry_step`. */
export const PAID_OUTCOME_RETRY_ARMED_FIELD = "retry_armed";

/** #358: `ApprovalDecisionBody.retry_grant` — одноразовый grant, не токен. */
export const PAID_OUTCOME_RETRY_GRANT_FIELD = "retry_grant";

/** #358: ответ `grant_approval`. */
export const PAID_OUTCOME_EXECUTED_FIELD = "executed";

/** #358: `retry_grant_spent` | `retry_grant_required` | `budget_exhausted`. */
export const PAID_OUTCOME_GRANT_FAILURES = ["retry_grant_spent", "retry_grant_required", "budget_exhausted"] as const;

/** Ровно эти ключи. Любое другое поле сервер отклоняет 422. */
export const PAID_OUTCOME_BODY_FIELDS = ["change_id", "decision", "retry_grant"] as const;

export function paidOutcomeApprovePath(sessionId: string): string {
  return `/api/orchestrator/chat/${encodeURIComponent(sessionId)}/approve`;
}

export interface PaidOutcomeApproveBody {
  change_id: string | null;
  decision: PaidOutcomeDecision | null;
  retry_grant: boolean;
}

export function paidOutcomeDecisionBody(changeId: string, decision: PaidOutcomeDecision): PaidOutcomeApproveBody {
  return { change_id: changeId, decision, retry_grant: false };
}

/** Второй шаг. `decision` пустой: grant не является новым решением. */
export function paidOutcomeGrantBody(changeId: string): PaidOutcomeApproveBody {
  return { change_id: changeId, decision: null, retry_grant: true };
}

/** На провод уходят только три поля. Лишний ключ сервер отклоняет 422 (`extra=forbid`). */
export function paidOutcomeWireBody(body: PaidOutcomeApproveBody): PaidOutcomeApproveBody {
  return {
    change_id: body.change_id ?? null,
    decision: body.decision ?? null,
    retry_grant: body.retry_grant === true,
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  return value as Record<string, unknown>;
}

/**
 * Только конечное число >= 0. NaN, ±Infinity, отрицательное, строка и отсутствие поля — null.
 * К 0 не приводится.
 */
export function readPaidOutcomeForecast(body: unknown): number | null {
  const record = asRecord(body);
  if (!record || !Object.prototype.hasOwnProperty.call(record, PAID_OUTCOME_FORECAST_FIELD)) return null;
  const value = record[PAID_OUTCOME_FORECAST_FIELD];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return value;
}

function jsonTokenBoundary(slice: string, length: number): boolean {
  const next = slice[length];
  return next == null || /[\s,\]}]/.test(next);
}

/** NaN и Infinity вне строк. Внутри кавычек текст не трогаем. */
function maskJsonNonFinite(text: string): string {
  let out = "";
  let inString = false;
  let escaped = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i] ?? "";
    if (inString) {
      out += ch;
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      out += ch;
      continue;
    }
    const prev = i === 0 ? "" : (text[i - 1] ?? "");
    if (prev === "" || /[\s:,\[]/.test(prev)) {
      const slice = text.slice(i);
      let token = "";
      if (slice.startsWith("-Infinity") && jsonTokenBoundary(slice, "-Infinity".length)) token = "-Infinity";
      else if (slice.startsWith("Infinity") && jsonTokenBoundary(slice, "Infinity".length)) token = "Infinity";
      else if (slice.startsWith("NaN") && jsonTokenBoundary(slice, "NaN".length)) token = "NaN";
      if (token) {
        const marker = token === "NaN" ? "__NaN__" : token === "-Infinity" ? "__-Infinity__" : "__Infinity__";
        out += `"${marker}"`;
        i += token.length - 1;
        continue;
      }
    }
    out += ch;
  }
  return out;
}

/** Python `json.dumps` пишет NaN и Infinity как есть. Это не 0. */
export function parsePaidOutcomeJson(text: string): unknown {
  const marked = maskJsonNonFinite(text);
  return JSON.parse(marked, (_key, value) => {
    if (value === "__NaN__") return Number.NaN;
    if (value === "__-Infinity__") return Number.NEGATIVE_INFINITY;
    if (value === "__Infinity__") return Number.POSITIVE_INFINITY;
    return value;
  });
}

/** Кнопка «Перезапустить шаг» активна, только если сервер прислал это decision. */
export function readPaidOutcomeRestartOffered(body: unknown): boolean {
  const record = asRecord(body);
  if (!record) return false;
  const decisions = record[PAID_OUTCOME_DECISIONS_FIELD];
  if (!Array.isArray(decisions)) return false;
  return decisions.some((item) => asRecord(item)?.decision === PAID_OUTCOME_DECISION.retryStep);
}

export function readPaidOutcomeExecuted(body: unknown): boolean {
  const record = asRecord(body);
  return record?.[PAID_OUTCOME_EXECUTED_FIELD] === true;
}

export function readPaidOutcomeFailureReason(body: unknown): string | null {
  const record = asRecord(body);
  if (!record) return null;
  const reason = record.reason;
  if (typeof reason !== "string") return null;
  return (PAID_OUTCOME_GRANT_FAILURES as readonly string[]).includes(reason) ? reason : null;
}

export function readPaidOutcomeRetryArmed(body: unknown): boolean {
  const record = asRecord(body);
  return record?.[PAID_OUTCOME_RETRY_ARMED_FIELD] === true;
}

/** «Подтвердить перезапуск» только при retry_armed и конечном прогнозе >= 0. */
export function paidOutcomeRetryReady(body: unknown): boolean {
  return readPaidOutcomeRetryArmed(body) && readPaidOutcomeForecast(body) != null;
}

export const PAID_OUTCOME_COPY = {
  title: "Исход платного шага неизвестен",
  body: "Шаг мог выполниться, поэтому автоматически он не повторяется, проверьте результат.",
  done: "Шаг выполнен, продолжить",
  retry: "Перезапустить шаг",
  deny: "Остановить задачу",
  confirmRetry: "Подтвердить перезапуск",
  retryUnsupported: "Сервер не поддерживает перезапуск этого шага.",
  forecastMissing: "Прогноз расхода недоступен",
  grantMissing: "Сервер не выдал одноразовое подтверждение перезапуска.",
  grantSpent: "Одноразовый перезапуск уже использован. Шаг второй раз не выполняется.",
  grantRequired: "Сервер ещё не подтвердил перезапуск. Нажмите «Перезапустить шаг».",
  grantBudget: "Бюджет исчерпан, перезапуск не выполняется.",
} as const;

/** 200 с reason — не HTTP-ошибка. Текст для карточки, сырой код не показываем. */
export function paidOutcomeGrantNotice(reason: string | null): string | null {
  if (reason === "retry_grant_spent") return PAID_OUTCOME_COPY.grantSpent;
  if (reason === "retry_grant_required") return PAID_OUTCOME_COPY.grantRequired;
  if (reason === "budget_exhausted") return PAID_OUTCOME_COPY.grantBudget;
  return null;
}

export function formatPaidOutcomeForecast(value: number): string {
  return `Прогноз расхода: ~$${value}`;
}
