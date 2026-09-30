"use client";
import {actionStatus, actionsModeLabel, actionReviewMessage} from "@/lib/actions-view";

import {useEffect, useRef, useState} from "react";
import {api} from "@/lib/api";
import {observations, type ObservationContext, type ObservationScope, type Diagnosis, type ActionState, type DiagnosticIncident} from "@/lib/observations";
import {DiagnosisSection} from './diagnosis-section';
import {DecisionSection} from './decision-section';
import {ProfilesSection} from './profiles-section';
import {eventLabel, mergeEvents, usageLabel, groupForScope, type ObservationEvent} from "@/lib/observation-view";
import {Button} from "@/components/ui/button";
import {mediaPhase, mediaVerification, artifactLink, imagePreviewLink, publicationSummary} from "@/lib/media-view";
import type {MediaContext} from "@/lib/observations";

function VerifiedImagePreview({href}:{href:string}) {
  const [failed,setFailed]=useState(false);
  if(failed) return <p role="status" className="text-amber-200">Превью недоступно: файл нужно проверить повторно.</p>;
  // Original application endpoint rechecks ownership, containment and file hash.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={href} alt="Проверенный файл изображения; качество содержания не оценивалось"
    loading="lazy" onError={()=>setFailed(true)} className="mt-2 max-h-64 max-w-full rounded object-contain"
    data-testid="verified-image-preview"/>;
}

function MediaDetails({media,projectId}:{media:MediaContext;projectId:number|null}) {
  return <div className="space-y-2" data-testid="media-node-details">
    <p>{media.limited_management ? "Ограниченное управление" : "Только наблюдение"} · {media.result_kind}</p>
    <p>Проверено только синтетически · рабочий профиль не настроен</p>
    {media.blockers.length>0 && <p className="text-amber-200">{media.blockers.join("; ")}</p>}
    {media.publication && <div data-testid="publication-dry-run">
      <p>{publicationSummary(media.publication.validation.status)}</p>
      <p>Подпись: {media.publication.caption_characters} символов.</p>
      <p>Площадки: {media.publication.platforms.map(p=>`${p.platform}${p.already_recorded ? " (есть прежняя запись)" : ""}`).join(", ")}</p>
      <p>Отправка требует отдельного разрешения. Dry-run не меняет статус проекта.</p>
    </div>}
    {media.jobs.length===0 && !media.publication && <p>Job ещё не зарегистрирован; результат не подтверждён.</p>}
    {media.jobs.map(job=>{
      const href=job.phase==="verified" && job.verification.status==="passed" && job.artifact_uuid
        ? artifactLink(job.artifact_uuid,projectId) : null;
      const preview=imagePreviewLink(media.result_kind,job,projectId);
      return <details key={job.job_id} className="rounded border border-white/15 p-2">
        <summary className="cursor-pointer">{mediaPhase(job.phase)} · {job.model_id}</summary>
        <p>Прогресс: {job.progress==null ? "провайдер не сообщил" : `${job.progress}%`}</p>
        <p>Job провайдера: {job.provider_job_id ?? "не подтверждён"}</p>
        <p>Попытка: {job.attempt_id}</p>
        <p>{mediaVerification(job.verification.status)}</p>
        {job.error && <p className="text-amber-200">Проблема: {job.error}</p>}
        {job.verification.reason && <p>Причина проверки: {job.verification.reason}</p>}
        {href && <a href={href} target="_blank" rel="noreferrer" className="underline">Открыть проверенный локальный файл</a>}
        {preview && <VerifiedImagePreview key={preview} href={preview}/>}
      </details>;
    })}
    <p>Отправки генерации: {media.requests.generation_submit??0} · статусы: {media.requests.status??0} · загрузки: {media.requests.download??0}</p>
    <p>Тариф операций неизвестен. Лимит запросов не является денежным потолком.</p>
    <p>Разрешено: чтение событий и контракта, ожидание, обращение к человеку. Автоматический повтор генерации недоступен.</p>
  </div>;
}

export function ObservationSection({projectId}: {projectId: number | null}) {
  const [groups,setGroups] = useState<{id:string;title:string;keys:string[];steps:Record<string,string>}[]>([]);
  const [group,setGroup] = useState("");
  const [scopes,setScopes] = useState<ObservationScope[]>([]);
  const [execution,setExecution] = useState("");
  const [context,setContext] = useState<ObservationContext | null>(null);
  const [events,setEvents] = useState<ObservationEvent[]>([]);
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const [diagnosis,setDiagnosis]=useState<Diagnosis|null>(null);
  const [diagnosisError,setDiagnosisError]=useState('');
  const [actionState,setActionState]=useState<ActionState|null>(null);
  const [actionRationale,setActionRationale]=useState('');
  const [actionRequestLimit,setActionRequestLimit]=useState(12);
  const [actionError,setActionError]=useState('');
  const [controlsError,setControlsError]=useState('');
  const generation = useRef(0);
  const key = `studio-observation-execution-${projectId}`;

  useEffect(() => {
    const epoch = ++generation.current;
    setGroups([]); setGroup(""); setScopes([]); setExecution(""); setContext(null); setEvents([]); setError("");
    if (projectId == null) return;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const load = () => { void Promise.all([api.getProjectRun(projectId), observations.list(projectId)]).then(([run,list]) => {
      if (generation.current !== epoch) return;
      const grouped = new Map<string,{id:string;title:string;keys:string[];steps:Record<string,string>}>();
      for (const node of run.nodes_snapshot) {
        const id = typeof node.data?.groupId === "string" ? node.data.groupId : null;
        if (!id) continue;
        const existing = grouped.get(id) ?? {id,title:String(node.data.groupTitle ?? id),keys:[],steps:{}};
        existing.keys.push(node.id); existing.steps[node.id]=String(node.type ?? ""); grouped.set(id,existing);
      }
      setGroups([...grouped.values()]); setGroup([...grouped.keys()][0] ?? ""); setScopes(list);
      let saved = "";
      try { saved = localStorage.getItem(key) ?? ""; } catch { /* History still available from API. */ }
      setExecution(list.some(s => s.execution_id === saved) ? saved : list[0]?.execution_id ?? "");
      setError("");
    }).catch(() => {
      if (generation.current !== epoch) return;
      setError("Не удалось загрузить наблюдение. История не подтверждена; переподключение повторит чтение.");
      retry = setTimeout(load,2000);
    }); };
    load();
    return () => {generation.current++; clearTimeout(retry);};
  }, [projectId,key]);

  useEffect(() => {
    const savedScope=scopes.find(s=>s.execution_id===execution);
    if(savedScope) setGroup(groupForScope(groups,savedScope.node_keys));
  }, [execution,scopes,groups]);

  useEffect(() => {
    setEvents([]); setContext(null); setDiagnosis(null); setDiagnosisError(''); setActionState(null); setActionError(''); setControlsError('');
    if (!execution || projectId == null) return;
    try { localStorage.setItem(key,execution); } catch { /* Server remains source of history. */ }
    let cancelled = false;
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await observations.context(projectId,execution);
        if (cancelled) return;
        setContext(next);
        try { const actions=await observations.actions(projectId,execution); if(!cancelled) {setActionState(actions);setControlsError('');} }
        catch { if(!cancelled) {setActionState(null);setControlsError('Состояние управления недоступно. Кнопки управления отключены до подтверждённого чтения.');} }
        try {
          const diagnostic=await observations.diagnoses(projectId,execution);
          if(cancelled) return;
          setDiagnosis(diagnostic);setDiagnosisError('');
        } catch {if(!cancelled) setDiagnosisError('Диагностика недоступна; сохранённые выводы могут быть устаревшими.');}
        let more = true;
        while(more && !cancelled) {
          const page = await observations.events(projectId,execution,cursor);
          if(cancelled) return;
          setEvents(old => mergeEvents(old,page.events,projectId));
          if(page.has_more && page.next_cursor <= cursor) throw new Error("Invalid cursor");
          cursor = page.next_cursor; more = page.has_more;
        }
        setError("");
      } catch { if(!cancelled) {setActionState(null);setControlsError('Состояние управления не подтверждено: чтение журнала недоступно.');setError("Связь с журналом потеряна. Показана последняя полученная история, сохранность новых событий неизвестна.");setDiagnosisError('Текущее состояние диагностики не подтверждено: чтение журнала недоступно.');} }
      if(!cancelled) timer=setTimeout(poll,2000);
    };
    void poll();
    return () => {cancelled=true; clearTimeout(timer);};
  }, [projectId,execution,key]);

  async function enable() {
    if(projectId == null) return;
    const selected=groups.find(g=>g.id===group);
    if(!selected) return;
    const epoch=generation.current;
    setBusy(true);
    try {
      const scope=await observations.enable(projectId,selected.keys);
      if(generation.current!==epoch) return;
      setScopes(old=>[scope,...old]); setExecution(scope.execution_id); setError("");
    } catch { if(generation.current===epoch) setError("Не удалось включить наблюдение. Проверьте существующий активный scope; запуск задачи не выполнялся."); }
    finally {setBusy(false);}
  }
  async function stop() {
    if(projectId == null || !execution) return;
    const epoch=generation.current;
    setBusy(true);
    try {
      await observations.stop(projectId,execution);
      if(generation.current!==epoch) return;
      setContext(old=>old ? {...old,observer_state:"stopped"} : old);
      setScopes(old=>old.map(s=>s.execution_id===execution ? {...s,observer_state:"stopped"} : s));
    } catch {if(generation.current===epoch) setError("Остановка наблюдения не подтверждена.");}
    finally {setBusy(false);}
  }

  async function enableActions() {
    if(projectId==null || !execution) return;
    const selected=groups.find(g=>g.id===group);
    const scope=scopes.find(s=>s.execution_id===execution);
    if(!selected || !scope || selected.keys.some(k=>!scope.node_keys.includes(k)) || actionRationale.trim().length<3) {
      setActionError('Нужны активное наблюдение с совпадающим scope и явное обоснование.'); return;
    }
    setBusy(true); setActionError('');
    try {
      await observations.enableActions(projectId,execution,{group_id:selected.id,node_keys:selected.keys,rationale:actionRationale.trim(),request_limit:actionRequestLimit});
      setActionState(await observations.actions(projectId,execution));
    } catch { setActionError('Разрешение выполнения не подтверждено; запуск остаётся закрыт.'); }
    finally {setBusy(false);}
  }

  async function setActionsHeld(held:boolean) {
    if(projectId==null || !execution || actionRationale.trim().length<3) return;
    setBusy(true); setActionError('');
    try {
      await observations.controlActions(projectId,execution,held,actionRationale.trim(),crypto.randomUUID());
      setActionState(await observations.actions(projectId,execution));
    } catch { setActionError(held ? 'Пауза выполнения не подтверждена.' : 'Возобновление не подтверждено; выполнение остаётся закрытым.'); }
    finally {setBusy(false);}
  }

  async function closeActions() {
    if(projectId==null || !execution || !actionRationale.trim()) return;
    setBusy(true); setActionError('');
    try {
      await observations.closeActions(projectId,execution,actionRationale.trim(),crypto.randomUUID());
      setActionState(await observations.actions(projectId,execution));
    } catch {setActionError('Закрытие не подтверждено; разрешение остаётся в текущем состоянии.');}
    finally {setBusy(false);}
  }

  async function runManagedGroup() {
    if(projectId==null || !execution || !context || !actionState?.enabled || actionState.held) return;
    const selected=groups.find(g=>g.id===actionState.group_id);
    const states=context.project?.nodes ?? context.master.nodes;
    const node=selected?.keys.find(k=>actionState.node_keys.includes(k) && states[k]?.status==='pending');
    const step=node ? selected?.steps[node] : undefined;
    if(!node || !step || actionRationale.trim().length<3) {
      setActionError('Нет доступной pending-ноды в разрешённой группе или отсутствует обоснование.'); return;
    }
    setBusy(true); setActionError('');
    try {
      await observations.startManagedNode(projectId,execution,{node_key:node,step_code:step,
        rationale:actionRationale.trim(),idempotency_key:crypto.randomUUID()});
      setActionState(await observations.actions(projectId,execution));
    } catch { setActionError('Запуск не подтверждён; повторно не отправляйте без проверки action status.'); }
    finally {setBusy(false);}
  }

  async function verifyManagedNode(node_key:string) {
    if(projectId==null || !execution || !actionState?.enabled || actionRationale.trim().length<3) return;
    setBusy(true); setActionError('');
    try {
      const result=await observations.verifyNode(projectId,execution,{node_key,rationale:actionRationale.trim(),idempotency_key:crypto.randomUUID()});
      setActionError(result.status==='verified' ? 'Runtime-проверка подтверждена; семантическое качество не оценивалось.' : 'Доказательств недостаточно — действие передано на ручную проверку.');
      setActionState(await observations.actions(projectId,execution));
    } catch {setActionError('Результат не удалось подтвердить; сохранённое состояние не изменено.');}
    finally {setBusy(false);}
  }

  async function markIncidentForReview(incident:DiagnosticIncident) {
    if(projectId==null || !execution || actionRationale.trim().length<3) {
      setActionError('Для передачи человеку укажите обоснование.'); return;
    }
    setBusy(true); setActionError('');
    try {
      await observations.reviewIncident(projectId,execution,{incident_id:incident.incident_id,rationale:actionRationale.trim(),idempotency_key:crypto.randomUUID()});
      setActionState(await observations.actions(projectId,execution));
    } catch {setActionError('Передача на ручную проверку не подтверждена.');}
    finally {setBusy(false);}
  }

  async function retryIncident(incident:DiagnosticIncident) {
    if(projectId==null || !execution || !incident.node_key || !actionRationale.trim()) return;
    setBusy(true);setActionError('');
    try {
      await observations.retryNode(projectId,execution,{incident_id:incident.incident_id,node_key:incident.node_key,
        rationale:actionRationale.trim(),idempotency_key:crypto.randomUUID()});
      setActionState(await observations.actions(projectId,execution));
    } catch { setActionError('Сервер отклонил действие: проверьте область, разрешение, остановку и лимиты. Подробности доступны в истории действий.'); }
    finally { setBusy(false); }
  }

  if(projectId == null) return <p className="text-xs text-white/50">Выберите проект в студии для наблюдения.</p>;
  const active=scopes.some(s=>s.observer_state!=="stopped");
  const states=context?.project?.nodes ?? context?.master.nodes ?? {};
  const models=events.filter(e=>e.kind==="model_end");
  return <section data-testid="observation-panel" className="space-y-3 text-xs text-white/80">
    <div className="flex flex-wrap items-center gap-2">
      <strong>Наблюдение · проект #{projectId}</strong>
      <select aria-label="Группа наблюдения" value={group} disabled={busy || active} onChange={e=>setGroup(e.target.value)} className="max-w-72 rounded border border-white/15 bg-[#161616] p-1.5">
        {!groups.length && <option value="">Нет групп в текущем графе</option>}
        {!!groups.length && !group && <option value="">Сохранённая область не совпадает с группами графа</option>}
        {groups.map(g=><option key={g.id} value={g.id}>{g.title} ({g.keys.length})</option>)}
      </select>
      <Button size="sm" variant="outline" disabled={busy || !group || active} onClick={()=>void enable()}>Включить наблюдение</Button>
      <Button size="sm" variant="outline" disabled={busy || !context || context.observer_state==="stopped"} onClick={()=>void stop()}>Остановить наблюдение</Button>
    </div>
    <p className="text-white/45">Наблюдение читает выполнение. Эти кнопки не запускают и не останавливают задачу.</p>
    {context && <div className="space-y-2 rounded border border-sky-400/20 p-2">
      <strong>Управление · Actions-v1</strong>
      <label className="flex items-center gap-2">Общий лимит запросов на execution
        <input type="number" min={1} max={100} aria-label="Лимит запросов Actions" disabled={!!actionState?.configured}
          value={actionState?.request_limit ?? actionRequestLimit} onChange={e=>setActionRequestLimit(Number(e.target.value))}
          className="w-20 rounded border border-white/15 bg-[#161616] p-1.5"/>
      </label>
      <p className="text-white/50">Общий счётчик включает запросы Decision и worker, статусы внешних задач и загрузки файлов. Это не денежный потолок.</p>
      <p data-testid="actions-mode">{actionsModeLabel(actionState)}</p>
      <p className="text-white/50">Закрытие execution не означает done всего WorkflowRun. User_stop supervisor не снимает.</p>
      <p className="text-white/50">По умолчанию выключено. Разрешение ограничено одним execution, выбранной группой и снимком графа; общий бюджет задаётся выше до включения. Остановка наблюдения и пауза выполнения — разные действия.</p>
      <label className="block">Обоснование оператора
        <input value={actionRationale} onChange={e=>setActionRationale(e.target.value)} maxLength={1000} className="ml-2 w-72 rounded border border-white/15 bg-[#161616] p-1.5" placeholder="Почему разрешается это выполнение"/>
      </label>
      {!actionState?.configured ? <Button size="sm" variant="outline" disabled={busy || !actionState || !execution || !group || context.observer_state==='stopped' || actionRationale.trim().length<3} onClick={()=>void enableActions()}>Разрешить выполнение выбранной группы</Button> : !actionState.enabled ? <p className="text-white/50">Managed execution закрыт. Для нового разрешения создайте новое наблюдение.</p> : <>
        <span>Статус: {actionState.held ? '⏸ на паузе' : '🟢 разрешено'} · запросы: {actionState.requests_used}/{actionState.request_limit}</span>
        <Button size="sm" variant="outline" disabled={busy || actionRationale.trim().length<3} onClick={()=>void setActionsHeld(!actionState.held)}>{actionState.held ? 'Возобновить управление' : 'Приостановить управление'}</Button>
        <p className="text-white/50">Пауза управления запрещает новые управляемые запуски и запросы, но не отменяет уже отправленный запрос. Чтобы остановить задачу, используйте «Остановить» над графом: это user_stop проекта, который supervisor не снимает.</p>
        <Button size="sm" variant="outline" disabled={busy || actionState.held || !actionRationale.trim()} onClick={()=>void runManagedGroup()}>Запустить следующую ноду группы</Button>
        <Button size="sm" variant="outline" disabled={busy || !actionRationale.trim()} onClick={()=>void closeActions()}>Закрыть execution</Button>
        <p className="text-white/50">Разрешённые ноды: {actionState.node_keys.join(', ')} · graph: {actionState.graph_version?.slice(0,12)}</p>
        {diagnosis?.incidents.filter(i=>i.currently_relevant && ['invalid_json','missing_payload'].includes(i.type)).map(i=><div key={i.incident_id} className="space-y-1">
          <p>Текущий incident: {i.node_title} · {i.fact}</p>
          <p>Действие предложено: один безопасный повтор ноды после всех штатных retry. Backend проверит условия до запуска.</p>
          <Button size="sm" variant="outline" disabled={busy || actionState.held || !actionRationale.trim() || actionState.recoveries?.some(r=>r.node_key===i.node_key)} onClick={()=>void retryIncident(i)}>Разрешить один recovery</Button>
        </div>)}
        <ol data-testid="actions-history" className="space-y-1">{actionState.records.slice(0,10).map(a=><li key={a.action_id}>
          {actionStatus(a.status)} · {a.node_key ?? 'execution'} · {a.created_at?.slice(0,19)} · {a.initiator} · {a.rationale}
          {a.preconditions.reason != null && <p>Причина: {String(a.preconditions.reason)}</p>}
          {a.status==='human_review' && <p>{actionReviewMessage(a.action_kind)}</p>}
          <details><summary>Доказательства и условия действия</summary><pre className="whitespace-pre-wrap">{JSON.stringify({action_id:a.action_id,preconditions:a.preconditions,verification:a.verification},null,2)}</pre></details>
        </li>)}</ol>
      </>}
      {actionError && <p role="alert" className="text-amber-200">{actionError}</p>}
      {controlsError && <p role="alert" className="text-amber-200">{controlsError}</p>}
      {actionState?.configured && !actionState.enabled && <ol className="space-y-1">{actionState.records.slice(0,10).map(a=><li key={a.action_id}>{actionStatus(a.status)} · {a.node_key ?? 'execution'} · {a.rationale}</li>)}</ol>}
    </div>}
    <label className="flex items-center gap-2">История запуска
      <select aria-label="История наблюдения" value={execution} onChange={e=>setExecution(e.target.value)} className="rounded border border-white/15 bg-[#161616] p-1.5">
        {!scopes.length && <option value="">Наблюдение ещё не включено</option>}
        {scopes.map(s=><option key={s.execution_id} value={s.execution_id}>{s.created_at.slice(0,19)} · {s.execution_id.slice(0,8)} · {s.observer_state}</option>)}
      </select>
    </label>
    {error && <p role="alert" className="rounded border border-amber-500/30 p-2 text-amber-200">{error}</p>}
    {context && <>
      <div data-testid="observation-context" className="rounded border border-white/10 p-2 space-y-1">
        <p>Execution: <span data-testid="observation-execution">{execution}</span> · observer: {context.observer_state} · группа: {context.business_group_outcome}</p>
        <p>Scope: {context.node_keys.join(" → ")}</p>
        <p>Текущая нода: {context.current_nodes.join(", ") || "нет активной по снимку БД"}</p>
        {context.disagreement && <p className="text-amber-200">Расхождение master/project: состояния отличаются; показана проектная БД.</p>}
        {context.commit_confirmation && <p className="text-red-300">{context.commit_confirmation}: бизнес-результат не подтверждён.</p>}
        <p>Неполнота журнала: {context.data_gaps.length ? context.data_gaps.map(g=>g.kind ?? g.error_type ?? "неизвестно").join(", ") : "зафиксированных gaps нет; это не гарантия полноты"}</p>
        {context.history_window_limited && <p className="text-amber-200">Контекст ограничен последними 500 событиями; история ниже загружается с курсором.</p>}
      </div>
      <DiagnosisSection diagnosis={diagnosis} error={diagnosisError} onReview={actionState?.enabled ? markIncidentForReview : undefined}/>
      <div className="overflow-x-auto"><table className="w-full text-left"><thead><tr>{["Нода","Состояние / прогресс","Попытка","Модель / usage","Проверки / ожидание"].map(x=><th key={x} className="p-1 text-white/40">{x}</th>)}</tr></thead><tbody>
        {context.node_keys.map(node=>{
          const nc=context.node_contexts[node]; const state=states[node]; const latest=models.filter(e=>e.node_key===node).at(-1);
          return <tr key={node} className="border-t border-white/10"><td className="p-1">{node}</td><td className="p-1">{state?.status ?? "неизвестно"} · {nc?.media || state?.progress == null ? "прогресс неизвестен" : `${state.progress}%`}</td><td className="p-1">{nc?.latest_attempt_id?.slice(0,8) ?? "неизвестно"}</td><td className="p-1">{nc?.model ?? "модель неизвестна"}<br/>{usageLabel(latest?.payload.usage as Record<string,unknown> | null)}</td><td className="p-1">{nc?.media ? <MediaDetails media={nc.media} projectId={projectId}/> : <>{nc?.checks.length ? nc.checks.slice(0,4).map((check,i)=><div key={i}>{eventLabel({kind:check.kind,payload:check.result})}</div>) : "not_checked"}<br/>{context.wait_reasons?.[node]?.reason ?? "причина ожидания неизвестна"}{actionState?.enabled && state?.status==='done' && <div><Button size="sm" variant="outline" disabled={busy || !actionRationale.trim()} onClick={()=>void verifyManagedNode(node)}>Проверить результат</Button></div>}</>}</td></tr>;
        })}
      </tbody></table></div>
      <div><strong>Хронология · {events.length} событий</strong><ol data-testid="observation-timeline" className="mt-2 max-h-64 overflow-y-auto space-y-1">
        {events.map(e=><li key={e.event_id} id={`observation-event-${e.event_id}`} data-event-id={e.event_id} className="border-l border-white/15 pl-2 text-white/60">{e.created_at?.slice(11,19)} · {e.node_key ?? "scope"} · {eventLabel(e)} · {e.db_scope ?? "источник неизвестен"}</li>)}
      </ol></div>
    </>}
    {projectId!=null && execution && <ProfilesSection projectId={projectId} execution={execution} nodes={context?.node_keys??groups.find(g=>g.id===group)?.keys??[]} managed={Boolean(actionState?.enabled)}/>}
    {projectId!=null && execution && <DecisionSection projectId={projectId} execution={execution} managed={Boolean(actionState?.enabled)}/>}
  </section>;
}
