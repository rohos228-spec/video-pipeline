"use client";

import {useEffect, useRef, useState} from "react";
import {api} from "@/lib/api";
import {observations, type ObservationContext, type ObservationScope} from "@/lib/observations";
import {eventLabel, mergeEvents, usageLabel, type ObservationEvent} from "@/lib/observation-view";
import {Button} from "@/components/ui/button";

export function ObservationSection({projectId}: {projectId: number | null}) {
  const [groups,setGroups] = useState<{id:string;title:string;keys:string[]}[]>([]);
  const [group,setGroup] = useState("");
  const [scopes,setScopes] = useState<ObservationScope[]>([]);
  const [execution,setExecution] = useState("");
  const [context,setContext] = useState<ObservationContext | null>(null);
  const [events,setEvents] = useState<ObservationEvent[]>([]);
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const generation = useRef(0);
  const key = `studio-observation-execution-${projectId}`;

  useEffect(() => {
    const epoch = ++generation.current;
    setGroups([]); setGroup(""); setScopes([]); setExecution(""); setContext(null); setEvents([]); setError("");
    if (projectId == null) return;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const load = () => { void Promise.all([api.getProjectRun(projectId), observations.list(projectId)]).then(([run,list]) => {
      if (generation.current !== epoch) return;
      const grouped = new Map<string,{id:string;title:string;keys:string[]}>();
      for (const node of run.nodes_snapshot) {
        const id = typeof node.data?.groupId === "string" ? node.data.groupId : null;
        if (!id) continue;
        const existing = grouped.get(id) ?? {id,title:String(node.data.groupTitle ?? id),keys:[]};
        existing.keys.push(node.id); grouped.set(id,existing);
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
    setEvents([]); setContext(null);
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
        let more = true;
        while(more && !cancelled) {
          const page = await observations.events(projectId,execution,cursor);
          if(cancelled) return;
          setEvents(old => mergeEvents(old,page.events,projectId));
          if(page.has_more && page.next_cursor <= cursor) throw new Error("Invalid cursor");
          cursor = page.next_cursor; more = page.has_more;
        }
        setError("");
      } catch { if(!cancelled) setError("Связь с журналом потеряна. Показана последняя полученная история, сохранность новых событий неизвестна."); }
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

  if(projectId == null) return <p className="text-xs text-white/50">Выберите проект в студии для наблюдения.</p>;
  const active=scopes.some(s=>s.observer_state!=="stopped");
  const states=context?.project?.nodes ?? context?.master.nodes ?? {};
  const models=events.filter(e=>e.kind==="model_end");
  return <section data-testid="observation-panel" className="space-y-3 text-xs text-white/80">
    <div className="flex flex-wrap items-center gap-2">
      <strong>Наблюдение · проект #{projectId}</strong>
      <select aria-label="Группа наблюдения" value={group} onChange={e=>setGroup(e.target.value)} className="max-w-72 rounded border border-white/15 bg-[#161616] p-1.5">
        {!groups.length && <option value="">Нет групп в текущем графе</option>}
        {groups.map(g=><option key={g.id} value={g.id}>{g.title} ({g.keys.length})</option>)}
      </select>
      <Button size="sm" variant="outline" disabled={busy || !group || active} onClick={()=>void enable()}>Включить наблюдение</Button>
      <Button size="sm" variant="outline" disabled={busy || !context || context.observer_state==="stopped"} onClick={()=>void stop()}>Остановить наблюдение</Button>
    </div>
    <p className="text-white/45">Наблюдение читает выполнение. Эти кнопки не запускают и не останавливают задачу.</p>
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
        {context.latest_problem && <p className="text-amber-200">Последняя проблема: {context.latest_problem.node_key} · {context.latest_problem.kind} {context.latest_problem.payload.error_type ?? ""}; последующее применение БД: {context.latest_problem.later_apply_committed ? "записано" : "не подтверждено"}</p>}
        {context.commit_confirmation && <p className="text-red-300">{context.commit_confirmation}: бизнес-результат не подтверждён.</p>}
        <p>Неполнота журнала: {context.data_gaps.length ? context.data_gaps.map(g=>g.kind ?? g.error_type ?? "неизвестно").join(", ") : "зафиксированных gaps нет; это не гарантия полноты"}</p>
        {context.history_window_limited && <p className="text-amber-200">Контекст ограничен последними 500 событиями; история ниже загружается с курсором.</p>}
      </div>
      <div className="overflow-x-auto"><table className="w-full text-left"><thead><tr>{["Нода","Состояние / прогресс","Попытка","Модель / usage","Проверки / ожидание"].map(x=><th key={x} className="p-1 text-white/40">{x}</th>)}</tr></thead><tbody>
        {context.node_keys.map(node=>{
          const nc=context.node_contexts[node]; const state=states[node]; const latest=models.filter(e=>e.node_key===node).at(-1);
          return <tr key={node} className="border-t border-white/10"><td className="p-1">{node}</td><td className="p-1">{state?.status ?? "неизвестно"} · {state?.progress == null ? "прогресс неизвестен" : `${state.progress}%`}</td><td className="p-1">{nc?.latest_attempt_id?.slice(0,8) ?? "неизвестно"}</td><td className="p-1">{nc?.model ?? "модель неизвестна"}<br/>{usageLabel(latest?.payload.usage as Record<string,unknown> | null)}</td><td className="p-1">{nc?.checks.length ? nc.checks.slice(0,4).map((check,i)=><div key={i}>{eventLabel({kind:check.kind,payload:check.result})}</div>) : "not_checked"}<br/>{context.wait_reasons?.[node]?.reason ?? "причина ожидания неизвестна"}</td></tr>;
        })}
      </tbody></table></div>
      <div><strong>Хронология · {events.length} событий</strong><ol data-testid="observation-timeline" className="mt-2 max-h-64 overflow-y-auto space-y-1">
        {events.map(e=><li key={e.event_id} data-event-id={e.event_id} className="border-l border-white/15 pl-2 text-white/60">{e.created_at?.slice(11,19)} · {e.node_key ?? "scope"} · {eventLabel(e)} · {e.db_scope ?? "источник неизвестен"}</li>)}
      </ol></div>
    </>}
  </section>;
}
