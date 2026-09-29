"use client";
import {useEffect,useState} from 'react';
import {observations,type ProfileState} from '@/lib/observations';
import {profileStatus} from '@/lib/profiles-view';
import {Button} from '@/components/ui/button';

export function ProfilesSection({projectId,execution,nodes,managed}:{projectId:number;execution:string;nodes:string[];managed:boolean}){
  const [node,setNode]=useState('');const [state,setState]=useState<ProfileState|null>(null);
  const [rationale,setRationale]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const selected=nodes.includes(node)?node:(nodes[0]??'');
  useEffect(()=>{
    let cancelled=false;let timer:ReturnType<typeof setTimeout>;setState(null);setError('');
    if(!selected)return;
    const poll=async()=>{
      try{const next=await observations.profiles(projectId,execution,selected);if(!cancelled)setState(next);}
      catch{if(!cancelled)setError('История профиля недоступна.');}
      if(!cancelled)timer=setTimeout(poll,2000);
    };void poll();return()=>{cancelled=true;clearTimeout(timer);};
  },[projectId,execution,selected]);
  async function change(overlay?:string){
    if(!state?.base_version&&!overlay)return;setBusy(true);
    try{
      if(overlay)await observations.cancelProfile(projectId,execution,overlay,rationale);
      else await observations.approveProfile(projectId,execution,{node_key:selected,profile_id:state!.catalog[0].profile_id,
        base_version:state!.base_version!,catalog_hash:state!.catalog_hash,rationale});
      setState(await observations.profiles(projectId,execution,selected));setError('');
    }catch{setError('Сервер отклонил изменение: проверьте scope, версию и разрешение.');}
    finally{setBusy(false);}
  }
  return <section data-testid="profiles-panel" className="rounded border border-white/10 p-2 space-y-2">
    <h4>Профиль конкретной попытки</h4>
    <p>Только одобренный синтетический вариант. Production allowlist пуст. Бюджет и одна recovery сохраняются.</p>
    <label>Нода <select aria-label="Нода профиля" value={selected} onChange={e=>setNode(e.target.value)} className="bg-zinc-900">{nodes.map(n=><option key={n}>{n}</option>)}</select></label>
    <p>Кандидат: {state?.catalog[0]?.model_id??'нет'} · reasoning medium · max output 2048 · require-apply-ops-v1</p>
    <label className="block">Основание одобрения <input aria-label="Основание профиля" value={rationale} onChange={e=>setRationale(e.target.value)} className="bg-transparent border rounded"/></label>
    <Button size="sm" variant="outline" disabled={busy||!managed||!state?.available||!state.catalog.length||Boolean(state.overlays.length)||rationale.trim().length<3} onClick={()=>void change()}>Одобрить профиль следующей попытки</Button>
    {!state?.available&&<p>{state?.reason??'Контекст ещё не прочитан'}</p>}
    {error&&<p role="alert">{error}</p>}
    {state?.overlays.map(o=><div key={o.overlay_id} data-testid="profile-record" className="border-t border-white/10 pt-2">
      <p>{profileStatus(o.status)} · {o.profile_id} · {o.candidate.model_id}</p>
      <p>Base {o.base_version.slice(0,12)} → candidate {o.candidate_hash.slice(0,12)} · attempt {o.attempt_id??'не начата'}</p>
      <p>Одобрение: {o.initiator} · {o.rationale}</p>
      <p>Фактический результат: {o.recovery?.status??'не выполнялся'} · verify {o.recovery?.verification?.verified?'подтверждён runtime':'не подтверждён'}</p>
      <Button size="sm" variant="outline" disabled={busy||o.status==='cancelled'||['claimed','await_verify'].includes(o.recovery?.status??'')||rationale.trim().length<3} onClick={()=>void change(o.overlay_id)}>Вернуть конфигурацию</Button>
      <p>Возврат конфигурации не откатывает данные или расход.</p>
      <details><summary>История профиля</summary><pre className="whitespace-pre-wrap">{JSON.stringify(o.history,null,2)}</pre></details>
    </div>)}
  </section>;
}
