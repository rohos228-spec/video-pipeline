"use client";
import type {Diagnosis,DiagnosticIncident} from '@/lib/observations';
import {diagnosisSummary,groupIncidents} from '@/lib/diagnosis-view';

function IncidentCard({incident:i,onReview}:{incident:DiagnosticIncident;onReview?:(incident:DiagnosticIncident)=>void}) {
  const status=i.status==='resolved_confirmed' ? 'Исправлена с подтверждением' : i.status==='human_review' ? 'Требует разбора человеком' : 'Открыта';
  return <article data-incident-id={i.incident_id} className="rounded border border-white/10 p-2 space-y-1">
    <strong>{i.node_title} · {i.title}</strong>
    <p>{status}{i.severity==='info' ? ' · ожидание/ограничение, не ошибка' : ''}{i.blocking && i.currently_relevant ? ' · мешает выполнению' : ''}</p>
    {!i.currently_relevant && i.status!=='resolved_confirmed' && <p className="text-white/50">Предыдущая попытка: сохранена для разбора, текущей блокировкой не является. Исправление этой попытки не доказано.</p>}
    <p>Факт: {i.fact}</p>
    <p>Возможная причина: {i.cause.hypothesis ?? 'не установлена'}</p>
    <p>Наблюдавшееся восстановление: {i.recovery.length ? `штатный JSON retry (${i.recovery.length}); ${i.recovery.some(r=>r.accepted_response_observed) ? 'принятый ответ записан' : 'принятый ответ не подтверждён'}` : 'не зафиксировано'}</p>
    {i.resolution?.kind==='format_recovery' && <p className="text-emerald-200">В этой попытке подтверждены принятие ответа, применение данных и done. Это исправление формата; качество не проверено.</p>}
    {i.evidence_incomplete && <p className="text-amber-200">Доказательства неполны.</p>}
    <p>Рекомендация: {i.recommendation.title}. {i.recommendation.reason}</p>
    {onReview && i.currently_relevant && i.status!=='resolved_confirmed' && <button type="button" className="rounded border border-white/15 px-2 py-1" onClick={()=>onReview(i)}>Передать человеку</button>}
    <details><summary className="cursor-pointer text-white/50">Доказательства и технические ID</summary>
      <p>incident: {i.incident_id} · node: {i.node_key ?? 'группа'} · attempt: {i.attempt_id ?? 'неизвестно'}</p>
      <p>requests: {i.request_ids.join(', ') || 'неизвестно'}</p>
      <ul>{i.evidence.map(e=><li key={e.event_id}><a className="underline" href={`#observation-event-${encodeURIComponent(e.event_id)}`}>{e.kind} · {e.db_scope ?? 'источник неизвестен'} · {e.event_id}</a></li>)}</ul>
      {!i.evidence.length && <p>Основание: текущий снимок БД/метаданные доступности журнала, а не событие выполнения.</p>}
      {i.snapshot_evidence && <pre className="whitespace-pre-wrap break-all text-white/50">{JSON.stringify(i.snapshot_evidence,null,2)}</pre>}
      <p>Условия потенциального повторного запуска: {i.recommendation.safety_conditions.join(' ')}</p>
    </details>
  </article>;
}
export function DiagnosisSection({diagnosis,error,onReview}:{diagnosis:Diagnosis|null;error:string;onReview?:(incident:DiagnosticIncident)=>void}) {
  const groups=groupIncidents(diagnosis?.incidents ?? []);
  return <section data-testid="diagnosis-panel" className="space-y-2 border-t border-white/10 pt-3">
    <p className={error || !diagnosis?.evidence_complete ? 'text-amber-200' : 'text-white/70'}>{error || diagnosisSummary(diagnosis)}</p>
    <p className="text-white/40">Диагностика по правилам. Рекомендации не выполняются; промпты, модели и задачи не изменяются.</p>
    <h3 className="font-semibold">Требует внимания</h3>
    <div data-testid="diagnosis-attention" className="space-y-2">{groups.attention.map(i=><IncidentCard key={i.incident_id} incident={i} onReview={onReview}/>)}
      {!groups.attention.length && <p>Активных инцидентов в сохранённом диагнозе нет{!diagnosis?.evidence_complete ? '; полнота не подтверждена' : ''}.</p>}</div>
    <h3 className="font-semibold">Исправлено автоматически</h3>
    <div data-testid="diagnosis-repaired" className="space-y-2">{groups.repaired.map(i=><IncidentCard key={i.incident_id} incident={i} onReview={onReview}/>)}
      {!groups.repaired.length && <p>Подтверждённого автоматического исправления формата не зафиксировано.</p>}</div>
    {!!groups.other.length && <details><summary>Другие снятые наблюдения · {groups.other.length}</summary>{groups.other.map(i=><IncidentCard key={i.incident_id} incident={i} onReview={onReview}/>)}</details>}
    {diagnosis && <details><summary className="text-white/40">Состояние диагностики</summary><p>{diagnosis.rule_version ?? 'правила неизвестны'} · cursor {diagnosis.cursor}/{diagnosis.journal_head} · {diagnosis.checked_at ?? 'чтение не подтверждено'}</p></details>}
  </section>;
}
