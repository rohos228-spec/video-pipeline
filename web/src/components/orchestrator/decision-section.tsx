"use client";
import {useEffect,useState} from 'react';
import {observations,type DecisionState} from '@/lib/observations';
import {Button} from '@/components/ui/button';

export function DecisionSection({projectId,execution,managed}:{projectId:number;execution:string;managed:boolean}) {
  const [state,setState]=useState<DecisionState|null>(null);
  const [error,setError]=useState('');
  const [rationale,setRationale]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    let cancelled=false;let timer:ReturnType<typeof setTimeout>;
    setState(null);setError('');
    const poll=async()=>{
      try {const next=await observations.decisions(projectId,execution);if(!cancelled){setState(next);setError('');}}
      catch {if(!cancelled)setError('История решений недоступна. Новое разрешение не подтверждено.');}
      if(!cancelled)timer=setTimeout(poll,2000);
    };
    void poll();return()=>{cancelled=true;clearTimeout(timer);};
  },[projectId,execution]);
  async function toggle(){
    if(!state)return;setBusy(true);
    try {await observations.controlDecisions(projectId,execution,!state.enabled,rationale);
      setState(await observations.decisions(projectId,execution));setError('');}
    catch {setError('Изменение режима отклонено сервером.');}
    finally {setBusy(false);}
  }
  return <section data-testid="decision-panel" className="rounded border border-white/10 p-2 space-y-2">
    <h4>Цикл решений · {state?.enabled?'включён':'выключен'}</h4>
    <p>Только синтетическая модель. Управление и scope задаёт Actions. До {state?.max_model_steps??3} модельных шагов, общий request budget.</p>
    <label className="block">Основание оператора<input aria-label="Основание режима решений" value={rationale} onChange={e=>setRationale(e.target.value)} className="ml-2 bg-transparent border rounded"/></label>
    <Button size="sm" variant="outline" disabled={busy||!state||!managed||rationale.trim().length<3} onClick={()=>void toggle()}>{state?.enabled?'Выключить цикл решений':'Включить цикл решений'}</Button>
    {error&&<p role="alert">{error}</p>}
    <ol>{state?.records.map(r=><li key={r.decision_id} data-testid="decision-record" className="border-t border-white/10 mt-2 pt-2">
      <p>{r.node_key} · {r.status} · шаги {r.model_steps}/{state.max_model_steps}</p>
      <p>Предложение: {r.proposal.action??'ещё не получено'} · {r.proposal.explanation}</p>
      <p>Policy: {r.policy_result.allowed===true?'разрешено':r.policy_result.reason??'ожидается проверка'}</p>
      <p>Action: {r.action_id??'не выполнялся'} · Verification: {r.verification.verified?'подтверждена runtime':'не подтверждена'}</p>
      <p>Incident: {r.incident_id} · Context: {r.context_version.slice(0,12)}</p>
      <p>Evidence: {r.proposal.evidence_ids?.map(id=><a key={id} href={`#observation-event-${id}`} className="mr-2 underline">{id.slice(0,8)}</a>)??'нет'}</p>
      <details><summary>История model/tool turns</summary><ol>{r.turns.map(t=><li key={t.id}><strong>{t.role}</strong><pre className="whitespace-pre-wrap break-all">{JSON.stringify(t.payload,null,2)}</pre></li>)}</ol></details>
      {r.status==='human_review'&&<p className="text-amber-200">Нужен человек. Execution удержан; ещё одна recovery не разрешена.</p>}
    </li>)}</ol>
  </section>;
}
