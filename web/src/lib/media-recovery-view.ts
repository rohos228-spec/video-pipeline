
export type RecoveryMode = "resume_poll" | "resume_local" | "retry_same" | "denied" | "nothing_to_do";

export interface RecoveryKey {
  key: string | null;
  mode: string;
  code: string;
  task_id?: string | null;
  provider?: string | null;
}

export interface RecoveryAssessment {
  project_id: number;
  node_type: string;
  verdict: { mode: string; code: string; reasons?: { key: string | null; code: string }[] };
  keys: RecoveryKey[];
  ledger?: string | null;
  retry_same_max?: number;
  supervisor_admission?: { admitted_node?: boolean; operation?: string | null; denied?: string | null };
  submits_new_paid_request?: boolean;
}

export interface UnknownOutcome {
  key: string;
  provider: string;
  intent_count?: number;
  first_at?: string;
  age_s: number;
  window_s?: number;
  blocked_until: string;
  may_be_billed?: boolean;
}

export interface UnknownOutcomes {
  project_id: number;
  window_s: number;
  window_enabled: boolean;
  blocked: UnknownOutcome[];
  count: number;
}

export const MEDIA_NODES: { node_type: string; title: string }[] = [
  { node_type: "videos", title: "Клипы" },
  { node_type: "items", title: "Предметы" },
  { node_type: "music", title: "Музыка" },
  { node_type: "hero", title: "Герой" },
  { node_type: "images", title: "Картинки кадров" },
  { node_type: "audio", title: "Озвучка" },
  { node_type: "sfx_gen", title: "Звуковые эффекты" },
  { node_type: "assemble", title: "Сборка видео" },
];

export const RESUME_NODES = ["videos", "items", "music"];

export const MODE_LABEL_RU: Record<RecoveryMode, string> = {
  resume_poll: "Можно докачать",
  resume_local: "Результат уже на диске",
  retry_same: "Один повтор допустим",
  denied: "Решает человек",
  nothing_to_do: "Делать нечего",
};

export const MODE_CLASS: Record<RecoveryMode, string> = {
  resume_poll: "border-emerald-400/50 text-emerald-200",
  resume_local: "border-sky-400/50 text-sky-200",
  retry_same: "border-amber-400/50 text-amber-200",
  denied: "border-red-400/50 text-red-300",
  nothing_to_do: "border-white/20 text-white/60",
};

export const MODE_EXPLAIN_RU: Record<RecoveryMode, string> = {
  resume_poll: "Задача у провайдера уже создана и оплачена. Можно спросить её состояние и скачать готовое: новой оплаты не будет.",
  resume_local: "Оплаченный файл уже лежит на диске, его можно использовать повторно без нового запроса.",
  retry_same: "Все прежние попытки провайдер точно отклонил. Один повторный платный запуск возможен, но его делает человек обычным запуском ноды, не эта кнопка.",
  denied: "Исход прошлой платной отправки неизвестен или повтор исчерпан. Продолжать можно только после проверки кабинета провайдера.",
  nothing_to_do: "Открытых задач нет: ничего не отправлялось или результат уже скачан.",
};

export const CODE_RU: Record<string, string> = {
  open_task: "есть открытая задача провайдера",
  downloaded: "результат уже скачан",
  nothing_submitted: "ничего не отправлялось",
  provably_failed: "прежняя задача точно провалилась",
  retry_cap_reached: "лимит повторов исчерпан",
  unknown_outcome_kie: "ответ Kie на отправку потерян: задача могла быть принята и оплачена",
  unknown_outcome_outsee: "ответ Outsee на отправку потерян: задача могла быть принята и оплачена",
  unknown_outcome_elevenlabs: "исход запроса к ElevenLabs неизвестен: списание могло пройти",
  finished_file_on_disk: "готовый файл на диске",
  no_paid_job_and_no_marker: "платной задачи и метки нет",
  fallback_after_provider_answer: "провайдер ответил отказом, списания не было",
  local_only_no_provider_spend: "локальная нода, платных запросов нет",
  ledger_disabled: "журнал задач провайдера выключен",
  ledger_unavailable: "журнал задач провайдера не прочитан",
};

export const LEDGER_RU: Record<string, string> = {
  ok: "журнал задач читается",
  disabled: "журнал задач выключен",
  unavailable: "журнал задач недоступен",
  "n/a": "журнал задач не нужен: нода без задач провайдера",
};

export const DENIED_RU: Record<string, string> = {
  node_not_admitted: "для этой ноды возобновление по задаче провайдера не допускается",
  verdict_denied: "исход прошлой платной отправки неизвестен или повтор исчерпан: решает человек",
  retry_same_not_admitted: "все прежние задачи упали, но платную повторную отправку делает человек обычным запуском ноды",
  nothing_to_do: "возобновлять нечего: открытых задач нет",
};

export const PROVIDER_RU: Record<string, string> = { kie: "Kie", outsee: "Outsee", elevenlabs: "ElevenLabs" };
export const providerLabel = (p: string | null | undefined): string => (p ? PROVIDER_RU[p] ?? p : "");

const isMode = (m: string): m is RecoveryMode => m in MODE_LABEL_RU;

export function modeLabel(mode: string): string {
  return isMode(mode) ? MODE_LABEL_RU[mode] : "Неизвестно";
}
export function modeClass(mode: string): string {
  return isMode(mode) ? MODE_CLASS[mode] : "border-white/20 text-white/60";
}
export const RETRY_EXPLAIN_RU: Record<string, string> = {
  no_paid_job_and_no_marker: "Платной озвучки ещё не было и меток неизвестного исхода нет: обычный запуск ноды ничего не удвоит.",
  local_only_no_provider_spend: "Локальная сборка: платных запросов к провайдерам нет, её можно просто запустить заново.",
  fallback_after_provider_answer: "Провайдер ответил отказом, списания не было. Один повтор допустим, его делает человек обычным запуском ноды.",
};

export function modeExplain(mode: string, code?: string | null): string {
  if (mode === "retry_same" && code && RETRY_EXPLAIN_RU[code]) return RETRY_EXPLAIN_RU[code];
  return isMode(mode) ? MODE_EXPLAIN_RU[mode] : "Состояние не распознано: ничего не делайте, пока не проверите кабинет провайдера.";
}
export function codeText(code: string | null | undefined): string {
  return code ? CODE_RU[code] ?? code : "";
}
export function ledgerText(ledger: string | null | undefined): string {
  return ledger ? LEDGER_RU[ledger] ?? ledger : "журнал задач: нет данных";
}

export function ledgerCounts(keys: RecoveryKey[] | null | undefined): string {
  const list = keys ?? [];
  if (list.length === 0) return "задач в журнале нет";
  const c: Record<string, number> = {};
  for (const k of list) c[k.mode] = (c[k.mode] ?? 0) + 1;
  const part = (mode: RecoveryMode, word: string) => (c[mode] ? `${word} ${c[mode]}` : "");
  return [
    part("resume_poll", "открытых"), part("resume_local", "готовых на диске"), part("retry_same", "для повтора"),
    part("denied", "ждут человека"), part("nothing_to_do", "завершённых"),
  ].filter(Boolean).join(", ") || `ключей ${list.length}`;
}

export const hasTaskId = (a: RecoveryAssessment): boolean => a.keys.some((k) => Boolean(k.task_id));
export const taskIds = (a: RecoveryAssessment): string[] =>
  Array.from(new Set(a.keys.map((k) => k.task_id).filter((t): t is string => Boolean(t))));

export function canResume(a: RecoveryAssessment | null | undefined): boolean {
  if (!a) return false;
  return RESUME_NODES.includes(a.node_type) && a.supervisor_admission?.operation === "resume_poll";
}

export function resumeBlockedReason(a: RecoveryAssessment | null | undefined): string | null {
  if (!a) return "Состояние не прочитано.";
  if (canResume(a)) return null;
  if (!RESUME_NODES.includes(a.node_type)) return "Для этой ноды нет задачи провайдера, которую можно докачать.";
  const adm = a.supervisor_admission ?? {};
  const code = adm.denied ?? (adm.admitted_node === false ? "node_not_admitted" : "nothing_to_do");
  return DENIED_RU[code] ?? `возобновление недоступно (${code})`;
}

export function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "неизвестно";
  const s = Math.floor(seconds);
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h} ч ${rest} мин` : `${h} ч`;
}

export function formatBlockedUntil(iso: string | null | undefined): string {
  if (!iso) return "неизвестно";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  return m ? `до ${m[3]}.${m[2]} ${m[4]}:${m[5]} (UTC)` : iso;
}

export const MIN_REASON_CHARS = 10;
export const MAX_REASON_CHARS = 500;

export const cleanReason = (text: string): string => text.split(/\s+/).filter(Boolean).join(" ");
export const reasonOk = (text: string): boolean => {
  const n = cleanReason(text).length;
  return n >= MIN_REASON_CHARS && n <= MAX_REASON_CHARS;
};

export const resumeParams = (projectId: number, nodeType: string) => ({ project_id: projectId, node_type: nodeType });
export const releaseParams = (projectId: number, row: Pick<UnknownOutcome, "key" | "provider">, reason: string) => ({
  project_id: projectId, key: row.key, provider: row.provider, reason: cleanReason(reason),
});

export const RESUME_ACTION = "media.resume_poll";
export const RELEASE_ACTION = "media.release_unknown";

export const SECOND_CHARGE_WARNING =
  "Снятие блокировки снова открывает платную отправку: за тот же результат возможно ВТОРОЕ СПИСАНИЕ. "
  + "Сначала проверьте в кабинете провайдера историю задач и баланс.";
