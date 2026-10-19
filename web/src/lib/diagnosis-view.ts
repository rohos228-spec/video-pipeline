import type {Diagnosis, DiagnosticIncident} from './observations';


export interface IncidentExtras {
  rule?: string | null;
  rule_diary?: {calls?: number; counts?: Record<string, number>; rules?: Record<string, number>; stop_rule?: string | null} | null;
  hold_explanation?: {
    summary?: string; criteria?: string[]; failures_total?: number; hold_release?: string;
    failing_frames?: {criterion?: string | null; frame_uuid?: string | null; number?: number | null;
      primary_len?: number | null; second_len?: number | null; required_min?: number | null}[];
  } | null;
  frames_source?: string | null;
  stall?: {kind?: string; request_id?: string; age_s?: number; threshold_s?: number; last_event_at?: string; running_nodes?: string[]} | null;
  human_review_summary?: {
    model_steps_used?: number; max_model_steps?: number; evidence_event_count?: number; recommended_next_step?: string | null;
    rule?: string | null; hold_reason?: string | null; frames_source?: string | null;
    provider_error_title?: string | null; hold_failing_frames?: unknown[];
  } | null;
}
export type IncidentView = DiagnosticIncident & IncidentExtras;

const REPAIRED_KINDS = ['format_recovery', 'supervisor_recovery_verified'];

function rank(i: Partial<DiagnosticIncident>): number {
  if (i.status === 'resolved_confirmed') return 3;
  if (i.currently_relevant && i.blocking) return 0;
  if (i.currently_relevant) return 1;
  return 2;
}

function byAttention<T extends Partial<DiagnosticIncident>>(items: T[]): T[] {
  return items.map((item, index) => ({item, index}))
    .sort((a, b) => rank(a.item) - rank(b.item) || a.index - b.index).map(x => x.item);
}

export function groupIncidents(incidents: DiagnosticIncident[]) {
  return {
    attention: byAttention(incidents.filter(i=>i.status!=='resolved_confirmed')),
    repaired: incidents.filter(i=>i.status==='resolved_confirmed' && REPAIRED_KINDS.includes(i.resolution?.kind ?? '')),
    other: incidents.filter(i=>i.status==='resolved_confirmed' && !REPAIRED_KINDS.includes(i.resolution?.kind ?? '')),
  };
}
export function diagnosisSummary(diagnosis: Diagnosis | null): string {
  if(!diagnosis) return 'Диагностика ещё не подтверждена.';
  if(!diagnosis.available) return 'Диагностика недоступна; показанные выводы могут быть устаревшими.';
  if(diagnosis.lagging) return 'Диагностика отстаёт от журнала; текущая картина не подтверждена.';
  if(!diagnosis.evidence_complete) return `Доказательства неполны. Выявлено блокирующих проблем: ${diagnosis.blocking_count}.`;
  return diagnosis.blocking_count ? `Требуют внимания блокирующие проблемы: ${diagnosis.blocking_count}.` : 'По доступным правилам блокирующих проблем не выявлено; качество результата не оценивалось.';
}

export const INCIDENT_TYPE_RU: Record<string, string> = {
  media_failure: 'Результат медиа-задачи не подтверждён',
  media_policy: 'Отправка остановлена защитным ограничением',
  invalid_json: 'Ответ не разобран как обязательный JSON',
  missing_payload: 'Нет обязательных данных в ответе',
  contract_rejected: 'Ответ отклонён существующей проверкой',
  api_error: 'Зафиксирована ошибка API или транспорта',
  uncertain_request: 'Завершение внешнего запроса не подтверждено',
  runtime_wait: 'Ожидание очереди, блокировки или подтверждения',
  user_stop: 'Остановка пользователем',
  db_disagreement: 'Состояния master и project расходятся',
  journal_gap: 'Доказательства неполны: пробел журнала',
  stalled: 'Запрос или выполнение зависли: нет завершения в заданное время',
  split_degraded: 'Разбивка получена локальным запасным путём: ответ модели непригоден',
  verification_hold: 'Нативная проверка не подтвердила результат; запуск удержан',
};

export function incidentTitle(i: Pick<DiagnosticIncident, 'type' | 'title'>): string {
  const t = (i.title ?? '').trim();
  return t && t !== i.type ? t : (INCIDENT_TYPE_RU[i.type] ?? i.type);
}

export function nodeTitles(incidents: DiagnosticIncident[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const i of incidents) if (i.node_key && i.node_title && i.node_title !== i.node_key && !out[i.node_key]) out[i.node_key] = i.node_title;
  return out;
}

const TOKEN_RU: [RegExp, string][] = [
  [/verdict fail\/reject/g, 'вердикт «не пройдено» или «отклонено»'],
  [/master\/project snapshot/g, 'снимков основной и проектной БД'],
  [/\bbody_not_available\b/g, 'тело записи журнала недоступно'],
  [/\battempts_without_recorded_end\b/g, 'попытки без записанного конца'],
  [/\brequests_without_recorded_end\b/g, 'запросы без записанного конца'],
  [/\bproject_database_not_available\b/g, 'проектная БД недоступна'],
  [/\bproject_snapshot_unavailable\b/g, 'снимок проектной БД недоступен'],
  [/\bobserver_error\b/g, 'ошибка наблюдателя'],
  [/\bbusiness_commit_unconfirmed\b/g, 'запись бизнес-данных не подтверждена'],
];
export function textRu(s: string | null | undefined): string {
  return TOKEN_RU.reduce((acc, [re, to]) => acc.replace(re, to), s ?? '');
}

export const CRITERION_RU: Record<string, string> = {
  missing_or_short_parent_prompt: 'промпт пустой или короткий',
  voiceover_changed: 'закадр изменён',
  frame_number_changed: 'номер кадра изменён',
  input_or_output_unavailable: 'вход или выход недоступен',
};
export const criterionRu = (c: string | null | undefined): string => (c ? (CRITERION_RU[c] ?? c) : 'критерий не указан');

const DIARY_KIND_RU: Record<string, string> = {info: 'сведения', warn: 'предупреждения', warning: 'предупреждения', repair: 'исправления',
  fix: 'исправления', stop: 'остановки', fired: 'сработало', repaired: 'исправлено', stopped: 'остановлено'};

export const FRAMES_SOURCE_RU: Record<string, string> = {
  local_voiceover_fallback: 'локальная разбивка закадра по предложениям (запасной путь, ответ модели не использован)',
};
export const framesSourceRu = (s: string): string => FRAMES_SOURCE_RU[s] ?? s;

export function ruleDiaryLines(d: NonNullable<IncidentExtras['rule_diary']>): string[] {
  const lines: string[] = [];
  const rules = Object.entries(d.rules ?? {});
  if (rules.length) lines.push('Правила, сработавшие в этой попытке: ' + rules.map(([k, v]) => `${k} ×${v}`).join(', '));
  const counts = Object.entries(d.counts ?? {});
  if (counts.length) lines.push('Записи дневника: ' + counts.map(([k, v]) => `${DIARY_KIND_RU[k] ?? k} ${v}`).join(', '));
  if (typeof d.calls === 'number') lines.push(`Вызовов с дневником правил: ${d.calls}`);
  if (d.stop_rule) lines.push(`Последнее правило остановки: ${d.stop_rule}`);
  return lines;
}

export function stallLine(s: NonNullable<IncidentExtras['stall']>): string | null {
  const age = typeof s.age_s === 'number' ? `${s.age_s} с` : 'время неизвестно';
  const limit = typeof s.threshold_s === 'number' ? ` (порог ${s.threshold_s} с)` : '';
  if (s.kind === 'model_request') return `Запрос к модели без ответа: ${age}${limit}.`;
  if (s.kind === 'execution_idle') return `Тишина в журнале при работающей ноде: ${age}${limit}${s.running_nodes?.length ? `; ноды: ${s.running_nodes.join(', ')}` : ''}.`;
  return s.kind ? `Признак зависания: ${s.kind}, ${age}${limit}.` : null;
}

export function incidentFacts(i: IncidentView): string[] {
  const lines: string[] = [];
  if (i.rule) lines.push(`Правило, остановившее результат: ${i.rule}`);
  if (i.rule_diary) lines.push(...ruleDiaryLines(i.rule_diary));
  if (i.frames_source) lines.push(`Источник кадров: ${framesSourceRu(i.frames_source)}`);
  if (i.stall) { const l = stallLine(i.stall); if (l) lines.push(l); }
  return lines;
}

export function recommendationText(i: Pick<DiagnosticIncident, 'fact' | 'recommendation'>): string {
  const title = (i.recommendation?.title ?? '').trim();
  const reason = textRu(i.recommendation?.reason).trim();
  const fact = textRu(i.fact).trim();
  const same = !reason || reason === fact || fact.includes(reason) || reason.includes(fact);
  return same ? title : `${title}. ${reason}`;
}
