/**
 * Нетерминальная карточка `waiting_approval` / `paid_outcome_unknown`.
 * Версия провода остаётся ws3-r3.3: причина расширена локально, типы не перегенерированы.
 *
 * `decision` закреплён: approve | retry_step | deny.
 *
 * Имена сверены с #358 @ 08fb355f78946806350912a72fd4daf24326aa8f
 * (`origin/cursor/ws3-nonblocking-350`, 2026-10-17 20:36 МСК — HEAD уже новее cbd77ec9,
 * дедлайн 21:00 ещё не наступил). Источник: `ApprovalDecisionBody`,
 * `approval_required.forecast_usd` / `decisions` / `retry_armed`.
 *
 * TODO(ws3-358): HTTP-роутера `grant_approval` в #358 нет. Мок принимает то же
 * тело на POST `.../approvals/{id}/approve`. Если WS3 опубликует другой путь,
 * менять только `PAID_OUTCOME_GRANT_SEGMENT` и копию строк в
 * `web/e2e/orchestrator/mock-server.mjs`.
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

/**
 * TODO(ws3-358): хвост пути, пока роутера нет.
 * Тело — `ApprovalDecisionBody` (`decision` и/или `retry_grant`).
 */
export const PAID_OUTCOME_GRANT_SEGMENT = "approve";

export function paidOutcomeApprovalPath(approvalId: string): string {
  return `/api/orchestrator/approvals/${encodeURIComponent(approvalId)}/${PAID_OUTCOME_GRANT_SEGMENT}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  return value as Record<string, unknown>;
}

export function readPaidOutcomeForecast(body: unknown): number | null {
  const record = asRecord(body);
  if (!record) return null;
  const value = record[PAID_OUTCOME_FORECAST_FIELD];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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

/** Прогноз пришёл, grant ещё не исполнял шаг и не отказал. */
export function paidOutcomeRetryReady(body: unknown): boolean {
  return readPaidOutcomeFailureReason(body) == null && readPaidOutcomeForecast(body) != null && readPaidOutcomeExecuted(body) === false;
}

export const PAID_OUTCOME_COPY = {
  title: "Исход платного шага неизвестен",
  body: "Шаг мог выполниться, поэтому автоматически он не повторяется, проверьте результат.",
  done: "Шаг выполнен, продолжить",
  retry: "Перезапустить шаг",
  deny: "Остановить задачу",
  confirmRetry: "Подтвердить перезапуск",
  retryUnsupported: "Сервер не поддерживает перезапуск этого шага.",
  forecastMissing: "Сервер не прислал прогноз расхода.",
  grantMissing: "Сервер не выдал одноразовое подтверждение перезапуска.",
} as const;

export function formatPaidOutcomeForecast(value: number): string {
  return `Прогноз расхода: ~$${value}`;
}
