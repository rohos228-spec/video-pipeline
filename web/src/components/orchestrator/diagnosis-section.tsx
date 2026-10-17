"use client";
import type {Diagnosis,DiagnosticIncident} from '@/lib/observations';
import {criterionRu,diagnosisSummary,groupIncidents,incidentFacts,incidentTitle,nodeTitles,recommendationText,textRu,type IncidentView} from '@/lib/diagnosis-view';
import {cn} from '@/lib/utils';

function statusOf(i:DiagnosticIncident):{label:string;cls:string} {
  if(i.status==='resolved_confirmed') return {label:'Исправлена с подтверждением',cls:'border-emerald-400/30 text-emerald-200'};
  if(i.status==='human_review') return {label:'Требует разбора человеком',cls:'border-amber-400/40 text-amber-200'};
  return {label:'Открыта',cls:'border-white/25 text-white/80'};
}

function HoldFrames({hold}:{hold:NonNullable<IncidentView['hold_explanation']>}) {
  const frames=hold.failing_frames ?? [];
  if(!frames.length) return null;
  const total=hold.failures_total ?? frames.length;
  return <div className="space-y-1">
    <p className="text-white/60">Кадры, не прошедшие проверку{total>frames.length ? ` (показано ${frames.length} из ${total})` : ` (${frames.length})`}:</p>
    <div className="overflow-x-auto">
      <table className="w-full text-left" aria-label="Кадры, не прошедшие проверку">
        <thead><tr>{['Кадр','Что не так','Длина основного','Длина второго','Минимум'].map(x=><th key={x} className="pr-3 font-normal text-white/50">{x}</th>)}</tr></thead>
        <tbody>{frames.map((f,k)=><tr key={`${f.frame_uuid ?? 'f'}-${f.criterion ?? ''}-${k}`} className="border-t border-white/10">
          <td className="pr-3 py-0.5">{f.number ?? '—'}</td><td className="pr-3">{criterionRu(f.criterion)}</td>
          <td className="pr-3">{f.primary_len ?? '—'}</td><td className="pr-3">{f.second_len ?? '—'}</td><td>{f.required_min ?? '—'}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}

function IncidentCard({incident,onReview,titles}:{incident:DiagnosticIncident;onReview?:(incident:DiagnosticIncident)=>void;titles:Record<string,string>}) {
  const i=incident as IncidentView;
  const st=statusOf(i);
  const node=i.node_key && (!i.node_title || i.node_title===i.node_key) ? (titles[i.node_key] ?? i.node_title) : i.node_title;
  const facts=incidentFacts(i);
  const hold=i.hold_explanation;
  const summary=i.human_review_summary;
  const fact=textRu(i.fact);
  const holdText=hold?.summary && textRu(hold.summary)!==fact ? textRu(hold.summary) : null;
  const rec=recommendationText(i);
  const border=i.status==='human_review' ? 'border-amber-400/25' : i.status==='resolved_confirmed' ? 'border-emerald-400/20' : 'border-white/10';
  return <article data-incident-id={i.incident_id} data-incident-type={i.type} className={cn('rounded border p-2 space-y-1.5',border)}>
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
      <strong className="min-w-0 break-words">{node} · {incidentTitle(i)}</strong>
    </div>
    <p className="flex flex-wrap items-center gap-1.5">
      <span className={cn('rounded border px-1.5 py-0.5',st.cls)}>{st.label}</span>
      {i.severity==='info' && <span className="text-white/60">ожидание/ограничение, не ошибка</span>}
      {i.severity==='warning' && i.status!=='resolved_confirmed' && <span className="text-amber-200/90">предупреждение</span>}
      {i.blocking && i.currently_relevant && <span className="text-red-300">мешает выполнению</span>}
    </p>
    {!i.currently_relevant && i.status!=='resolved_confirmed' && <p className="text-white/55">Предыдущая попытка: сохранена для разбора, текущей блокировкой не является. Исправление этой попытки не доказано.</p>}
    <p className="break-words">Факт: {fact}</p>
    {(facts.length>0 || holdText || (hold?.failing_frames?.length ?? 0)>0) && <div data-testid="incident-facts" className="space-y-1 rounded bg-white/[0.04] p-1.5">
      <p className="text-white/50">Что известно</p>
      {facts.map((l,k)=><p key={k} className="break-words">{l}</p>)}
      {holdText && <p className="break-words">Причина удержания: {holdText}</p>}
      {hold && <HoldFrames hold={hold}/>}
      {hold?.hold_release==='human_only' && <p className="text-white/60">Снять удержание может только человек; автоматических действий нет.</p>}
    </div>}
    {summary && <div data-testid="incident-review-summary" className="space-y-0.5 rounded bg-white/[0.04] p-1.5">
      <p className="text-white/50">Сводка для оператора</p>
      <p>Нужен человек: дальнейших автоматических действий по этой ноде не будет, пока оператор не разберёт случай и не возобновит работу.</p>
      {typeof summary.model_steps_used==='number' && typeof summary.max_model_steps==='number' && <p>Шагов модели решений: {summary.model_steps_used} из {summary.max_model_steps}</p>}
      {typeof summary.evidence_event_count==='number' && <p>Записанных событий-доказательств: {summary.evidence_event_count}</p>}
      {summary.provider_error_title && <p>Причина от провайдера: {summary.provider_error_title}</p>}
    </div>}
    <p className="text-white/55">
      Причина: {i.cause.hypothesis ?? 'не установлена'} · восстановление: {i.recovery.length ? `штатный JSON retry (${i.recovery.length}); ${i.recovery.some(r=>r.accepted_response_observed) ? 'принятый ответ записан' : 'принятый ответ не подтверждён'}` : 'не зафиксировано'}
    </p>
    {i.resolution?.kind==='format_recovery' && <p className="text-emerald-200">В этой попытке подтверждены принятие ответа, применение данных и done. Это исправление формата; качество не проверено.</p>}
    {i.evidence_incomplete && <p className="text-amber-200">Доказательства неполны.</p>}
    {rec && <p className="break-words">Рекомендация: {rec}</p>}
    {onReview && i.currently_relevant && i.status!=='resolved_confirmed' && <button type="button" className="rounded border border-white/15 px-2 py-1" onClick={()=>onReview(i)}>Передать человеку</button>}
    <details><summary className="cursor-pointer text-white/55">Доказательства и технические ID</summary>
      <div className="mt-1 space-y-0.5 break-all">
        <p>incident: {i.incident_id} · node: {i.node_key ?? 'группа'} · attempt: {i.attempt_id ?? 'неизвестно'}</p>
        <p>requests: {i.request_ids.join(', ') || 'неизвестно'}</p>
        <ul>{i.evidence.map(e=><li key={e.event_id}><a className="underline" href={`#observation-event-${encodeURIComponent(e.event_id)}`}>{e.kind} · {e.db_scope ?? 'источник неизвестен'} · {e.event_id}</a></li>)}</ul>
        {!i.evidence.length && <p>Основание: текущий снимок БД/метаданные доступности журнала, а не событие выполнения.</p>}
        {i.snapshot_evidence && <pre className="whitespace-pre-wrap break-all text-white/50">{JSON.stringify(i.snapshot_evidence,null,2)}</pre>}
        <p>Условия потенциального повторного запуска: {i.recommendation.safety_conditions.join(' ')}</p>
      </div>
    </details>
  </article>;
}
export function DiagnosisSection({diagnosis,error,onReview}:{diagnosis:Diagnosis|null;error:string;onReview?:(incident:DiagnosticIncident)=>void}) {
  const all=diagnosis?.incidents ?? [];
  const groups=groupIncidents(all);
  const titles=nodeTitles(all);
  return <section data-testid="diagnosis-panel" className="space-y-2 border-t border-white/10 pt-3">
    <p className={error || !diagnosis?.evidence_complete ? 'text-amber-200' : 'text-white/70'}>{error || diagnosisSummary(diagnosis)}</p>
    <p className="text-white/50">Диагностика по правилам. Рекомендации не выполняются; промпты, модели и задачи не изменяются.</p>
    <h3 className="font-semibold">Требует внимания{groups.attention.length ? ` · ${groups.attention.length}` : ''}</h3>
    <div data-testid="diagnosis-attention" className="space-y-2">{groups.attention.map(i=><IncidentCard key={i.incident_id} incident={i} onReview={onReview} titles={titles}/>)}
      {!groups.attention.length && <p>Активных инцидентов в сохранённом диагнозе нет{!diagnosis?.evidence_complete ? '; полнота не подтверждена' : ''}.</p>}</div>
    <h3 className="font-semibold">Исправлено автоматически</h3>
    <div data-testid="diagnosis-repaired" className="space-y-2">{groups.repaired.map(i=><IncidentCard key={i.incident_id} incident={i} onReview={onReview} titles={titles}/>)}
      {!groups.repaired.length && <p>Подтверждённого автоматического исправления формата не зафиксировано.</p>}</div>
    {!!groups.other.length && <details><summary>Другие снятые наблюдения · {groups.other.length}</summary><div className="mt-2 space-y-2">{groups.other.map(i=><IncidentCard key={i.incident_id} incident={i} onReview={onReview} titles={titles}/>)}</div></details>}
    {diagnosis && <details><summary className="text-white/50">Состояние диагностики</summary><p>{diagnosis.rule_version ?? 'правила неизвестны'} · cursor {diagnosis.cursor}/{diagnosis.journal_head} · {diagnosis.checked_at ?? 'чтение не подтверждено'}</p></details>}
  </section>;
}
