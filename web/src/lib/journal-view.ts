/**
 * Журнал проекта (шаг B06): чистая логика вкладки «Журнал». Без React и без сети:
 * формирование запроса к ленте, пагинация по курсору, подписи, итоги, имя файла экспорта.
 * Контракт ленты: GET /api/projects/{id}/timeline (см. docs/REGISTRAR_FULL_PLAN.md, W3-2).
 */

export type JournalSeverity = "info" | "warn" | "error";

export interface JournalItem {
  id: string;
  ts: string;
  kind: string;
  source: string;
  severity: JournalSeverity;
  title_ru: string;
  detail_ru: string;
  project_id: number | null;
  run_id: string | null;
  request_id: string | null;
  ref: string | null;
  amount_usd: number | null;
}

export interface JournalPage {
  items: JournalItem[];
  next_cursor: string | null;
  total_hint: number | null;
  sources_missing: string[];
}

export type JournalPeriod = "all" | "today" | "24h" | "7d" | "30d";

export interface JournalFilters {
  kinds: string[];
  severity: "" | JournalSeverity;
  period: JournalPeriod;
  q: string;
}

export const EMPTY_FILTERS: JournalFilters = { kinds: [], severity: "", period: "all", q: "" };
export const PAGE_LIMIT = 100;
export const MAX_LIMIT = 500;
export const EXPORT_MAX_ROWS = 50000;

/** Быстрые наборы фильтров (B06: деньги, ошибки, действия оператора, запуски). */
export const PRESETS: { id: string; label: string; filters: Partial<JournalFilters> }[] = [
  { id: "all", label: "Всё", filters: { kinds: [], severity: "" } },
  { id: "money", label: "Деньги", filters: { kinds: ["spend", "task"], severity: "" } },
  { id: "errors", label: "Ошибки", filters: { kinds: [], severity: "error" } },
  { id: "operator", label: "Действия оператора", filters: { kinds: ["operator", "action"], severity: "" } },
  { id: "runs", label: "Запуски", filters: { kinds: ["node_run"], severity: "" } },
];

export const KIND_LABELS_RU: Record<string, string> = {
  node_run: "Запуск ноды",
  incident: "Инцидент",
  decision: "Решение",
  action: "Действие системы",
  spend: "Трата",
  task: "Задача провайдера",
  operator: "Действие оператора",
  publication: "Публикация",
  notification: "Уведомление",
  job: "Фоновая задача",
  gap: "Пробел журнала",
  // Событие наблюдения у сервера имеет «сырой» вид (node_error, retry_scheduled...), поэтому фильтр и подпись
  // идут по источнику: сервер принимает имя источника в `kinds`.
  observation_events: "Событие наблюдения",
};

export const SEVERITY_LABELS_RU: Record<JournalSeverity, string> = {
  info: "Обычное",
  warn: "Внимание",
  error: "Ошибка",
};

export const PERIOD_LABELS_RU: Record<JournalPeriod, string> = {
  all: "За всё время",
  today: "Сегодня",
  "24h": "Последние 24 часа",
  "7d": "Последние 7 дней",
  "30d": "Последние 30 дней",
};

/**
 * Русская подпись вида записи. Сырой вид с сервера человеку не показывается никогда:
 * событие наблюдения подписывается по источнику, незнакомое получает «Прочее».
 * Понятный текст самой записи берётся с сервера (`title_ru`).
 */
export function kindLabel(kind: string, source?: string): string {
  if (source === "observation_events") return KIND_LABELS_RU.observation_events;
  return KIND_LABELS_RU[kind] ?? (source ? SOURCE_KIND_LABELS_RU[source] : undefined) ?? "Прочее";
}

const SOURCE_KIND_LABELS_RU: Record<string, string> = {
  node_runs: "Запуск ноды",
  observation_incidents: "Инцидент",
  observation_degradations: "Пробел журнала",
  observation_commit_uncertainty: "Пробел журнала",
  decision_records: "Решение",
  action_v1_records: "Действие системы",
  action_layer_history: "Действие системы",
  spend_ledger: "Трата",
  provider_task_ledger: "Задача провайдера",
  operator_audit: "Действие оператора",
  publications: "Публикация",
  notification_log: "Уведомление",
};

export function severityLabel(s: string): string {
  return SEVERITY_LABELS_RU[s as JournalSeverity] ?? "Обычное";
}

/** Классы Tailwind для важности. Цвет не единственный признак: рядом всегда текст. */
export function severityClass(s: string): { dot: string; text: string; row: string } {
  if (s === "error") return { dot: "bg-red-400", text: "text-red-300", row: "border-red-400/30" };
  if (s === "warn") return { dot: "bg-amber-300", text: "text-amber-200", row: "border-amber-300/25" };
  return { dot: "bg-sky-300/70", text: "text-white/60", row: "border-white/[0.08]" };
}

/** Начало периода в ISO UTC (для `since`). `all` даёт null. Для «Сегодня» берётся локальная полночь. */
export function periodSince(period: JournalPeriod, now: Date = new Date()): string | null {
  const DAY = 86_400_000;
  switch (period) {
    case "all": return null;
    case "today": return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
    case "24h": return new Date(now.getTime() - DAY).toISOString();
    case "7d": return new Date(now.getTime() - 7 * DAY).toISOString();
    case "30d": return new Date(now.getTime() - 30 * DAY).toISOString();
  }
}

export function clampLimit(n: number): number {
  if (!Number.isFinite(n)) return PAGE_LIMIT;
  return Math.min(MAX_LIMIT, Math.max(1, Math.trunc(n)));
}

function filterParams(f: JournalFilters, now: Date): URLSearchParams {
  const p = new URLSearchParams();
  const since = periodSince(f.period, now);
  if (since) p.set("since", since);
  const kinds = [...new Set(f.kinds.map((k) => k.trim()).filter(Boolean))].sort();
  if (kinds.length) p.set("kinds", kinds.join(","));
  const q = f.q.trim();
  if (q) p.set("q", q);
  if (f.severity) p.set("severity", f.severity);
  return p;
}

/** Адрес страницы ленты. `cursor` — непрозрачная строка из прошлого ответа. */
export function buildTimelineUrl(
  projectId: number,
  f: JournalFilters,
  opts: { cursor?: string | null; limit?: number; now?: Date } = {},
): string {
  const p = filterParams(f, opts.now ?? new Date());
  p.set("limit", String(clampLimit(opts.limit ?? PAGE_LIMIT)));
  if (opts.cursor) p.set("cursor", opts.cursor);
  return `/api/projects/${projectId}/timeline?${p.toString()}`;
}

/** Адрес экспорта: те же фильтры, без limit/cursor (сервер отдаёт до 50000 строк). */
export function buildExportUrl(
  projectId: number,
  f: JournalFilters,
  format: "csv" | "json",
  now: Date = new Date(),
): string {
  const p = filterParams(f, now);
  p.set("format", format);
  return `/api/projects/${projectId}/timeline/export?${p.toString()}`;
}

export function exportFileName(projectId: number, format: "csv" | "json", now: Date = new Date()): string {
  const d = now.toISOString().slice(0, 19).replace(/[:T]/g, "-");
  return `zhurnal-proekt-${projectId}-${d}.${format}`;
}

/** Склейка страниц без дублей по id; порядок как у сервера (ts по убыванию, затем id по убыванию). */
export function mergeItems(old: JournalItem[], incoming: JournalItem[]): JournalItem[] {
  const seen = new Map<string, JournalItem>();
  for (const it of old) seen.set(it.id, it);
  for (const it of incoming) if (!seen.has(it.id)) seen.set(it.id, it);
  return [...seen.values()].sort((a, b) =>
    a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : a.id < b.id ? 1 : a.id > b.id ? -1 : 0);
}

export function sameFilters(a: JournalFilters, b: JournalFilters): boolean {
  return a.severity === b.severity && a.period === b.period && a.q.trim() === b.q.trim()
    && [...a.kinds].sort().join(",") === [...b.kinds].sort().join(",");
}

export function activePresetId(f: JournalFilters): string | null {
  for (const pr of PRESETS) {
    const merged: JournalFilters = { ...EMPTY_FILTERS, ...pr.filters, period: f.period, q: f.q };
    if (sameFilters(f, merged)) return pr.id;
  }
  return null;
}

export function applyPreset(f: JournalFilters, presetId: string): JournalFilters {
  const pr = PRESETS.find((x) => x.id === presetId);
  return pr ? { ...f, kinds: [], severity: "", ...pr.filters } : f;
}

export function toggleKind(f: JournalFilters, kind: string): JournalFilters {
  const has = f.kinds.includes(kind);
  return { ...f, kinds: has ? f.kinds.filter((k) => k !== kind) : [...f.kinds, kind] };
}

export interface JournalTotals {
  count: number;
  errors: number;
  warnings: number;
  spentUsd: number;
  spendRows: number;
}

/** Итоги по ЗАГРУЖЕННЫМ строкам (сервер итогов не отдаёт). Деньги считаются только по kind=spend. */
export function summarize(items: JournalItem[]): JournalTotals {
  let errors = 0, warnings = 0, spentUsd = 0, spendRows = 0;
  for (const it of items) {
    if (it.severity === "error") errors += 1;
    else if (it.severity === "warn") warnings += 1;
    if (it.kind === "spend" && typeof it.amount_usd === "number" && Number.isFinite(it.amount_usd)) {
      spentUsd += it.amount_usd;
      spendRows += 1;
    }
  }
  return { count: items.length, errors, warnings, spentUsd: Math.round(spentUsd * 1e6) / 1e6, spendRows };
}

/** Итоги по всему проекту с сервера: GET /api/projects/{id}/timeline/totals. */
export interface CountCell { count: number; more: boolean }
export interface ServerTotals {
  spend: { daily_spent: number | null; project_spent: number | null; run_spent: number | null; run_id: string | null;
    caps: { daily: number; project: number; run: number } | null };
  counts: { errors: CountCell; warnings: CountCell; gaps: CountCell };
  disk: { level: string; free_gb: number | null; total_gb: number | null; reason: string };
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const cell = (v: unknown): CountCell => {
  const r = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
  return { count: num(r.count) ?? 0, more: r.more === true };
};

/** Безопасная форма ответа: пропущенное остаётся «неизвестно» (null), а не превращается в ноль. */
export function normalizeTotals(raw: unknown): ServerTotals {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, any>;
  const sp = (r.spend && typeof r.spend === "object" ? r.spend : {}) as Record<string, any>;
  const caps = sp.caps && typeof sp.caps === "object"
    ? { daily: num(sp.caps.daily) ?? 0, project: num(sp.caps.project) ?? 0, run: num(sp.caps.run) ?? 0 } : null;
  const c = (r.counts && typeof r.counts === "object" ? r.counts : {}) as Record<string, unknown>;
  const d = (r.disk && typeof r.disk === "object" ? r.disk : {}) as Record<string, unknown>;
  return {
    spend: { daily_spent: num(sp.daily_spent), project_spent: num(sp.project_spent), run_spent: num(sp.run_spent),
      run_id: typeof sp.run_id === "string" ? sp.run_id : null, caps },
    counts: { errors: cell(c.errors), warnings: cell(c.warnings), gaps: cell(c.gaps) },
    disk: { level: typeof d.level === "string" ? d.level : "unknown", free_gb: num(d.free_gb), total_gb: num(d.total_gb),
      reason: typeof d.reason === "string" ? d.reason : "" },
  };
}

/** «12» или «500+» (если сервер насчитал до потолка). */
export function countText(c: CountCell): string {
  return c.more ? `${c.count}+` : String(c.count);
}

/** «$0.31 из $2.00» или «$0.31» без лимита; неизвестное - «нет данных». */
export function spendVsCap(spent: number | null, cap: number | null | undefined): string {
  if (spent === null) return "нет данных";
  return cap && cap > 0 ? `${formatUsd(spent)} из ${formatUsd(cap)}` : formatUsd(spent);
}

export const DISK_LEVEL_RU: Record<string, string> = {
  ok: "достаточно", low: "мало", critical: "критично мало", unknown: "не удалось измерить",
};

export function diskText(d: ServerTotals["disk"]): string {
  const lvl = DISK_LEVEL_RU[d.level] ?? "не удалось измерить";
  if (d.free_gb === null) return `место на диске: ${lvl}`;
  return `свободно ${d.free_gb.toFixed(1).replace(".", ",")} ГБ (${lvl})`;
}

export function formatUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  return `$${v.toFixed(abs > 0 && abs < 0.01 ? 4 : 2)}`;
}

/** «2026-10-07 14:03:09» по местному времени; нечитаемое значение возвращается как есть. */
export function formatTs(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const z = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`;
}

const SOURCE_LABELS_RU: Record<string, string> = {
  node_runs: "запуски нод",
  observation_events: "события наблюдения",
  observation_incidents: "инциденты",
  observation_degradations: "пробелы журнала наблюдения",
  observation_commit_uncertainty: "неопределённость записи данных",
  decision_records: "решения",
  action_v1_records: "действия системы",
  action_layer_history: "история слоя действий",
  spend_ledger: "учёт расходов",
  provider_task_ledger: "задачи провайдеров",
  operator_audit: "журнал оператора",
  publications: "публикации",
  notification_log: "уведомления",
};

/** Русское название источника записи (вместо имени таблицы). Незнакомый источник не показывается как есть. */
export function sourceLabel(source: string): string {
  return SOURCE_LABELS_RU[source] ?? "другой источник";
}

export function missingSourcesText(missing: string[]): string {
  if (!missing.length) return "";
  const names = missing.map(sourceLabel);
  return `В этой базе нет источников: ${names.join(", ")}. Их записей в ленте нет.`;
}

/** Приводит ответ сервера к безопасной форме: пропущенное заполняем, неизвестную важность считаем обычной. */
export function normalizePage(raw: unknown): JournalPage {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const items = Array.isArray(r.items)
    ? (r.items as JournalItem[]).filter((x) => x && typeof x.id === "string")
    : [];
  return {
    items: items.map((x) => ({
      ...x,
      severity: x.severity === "error" || x.severity === "warn" ? x.severity : "info",
      title_ru: x.title_ru ?? "",
      detail_ru: x.detail_ru ?? "",
      project_id: x.project_id ?? null,
      run_id: x.run_id ?? null,
      request_id: x.request_id ?? null,
      ref: x.ref ?? null,
      amount_usd: typeof x.amount_usd === "number" ? x.amount_usd : null,
    })),
    next_cursor: typeof r.next_cursor === "string" && r.next_cursor ? r.next_cursor : null,
    total_hint: typeof r.total_hint === "number" ? r.total_hint : null,
    sources_missing: Array.isArray(r.sources_missing) ? (r.sources_missing as string[]) : [],
  };
}

/** Текст ошибки загрузки по-русски. */
export function journalErrorText(status: number | null): string {
  if (status === 404) return "Проект или журнал не найден. Обновите страницу.";
  if (status === 422) return "Сервер не принял фильтры. Сбросьте фильтры и повторите.";
  if (status === 0) return "Сервер не ответил. Проверьте, что бэкенд запущен.";
  if (status !== null && status >= 500) return "Сервер не смог собрать журнал. Повторите чуть позже.";
  return "Не удалось загрузить журнал.";
}
