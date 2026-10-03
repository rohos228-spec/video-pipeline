"use client";
import {useEffect,useState} from 'react';
import {observations,type ProfileState} from '@/lib/observations';
import {profileStatus} from '@/lib/profiles-view';
import {Button} from '@/components/ui/button';

// Server sends provider and nullable knobs (Vibecode profiles have no reasoning/token cap).
type Entry=Omit<ProfileState['catalog'][number],'reasoning_effort'|'max_output_tokens'>&{provider?:string;
  reasoning_effort?:string|null;max_output_tokens?:number|null;matches_source_provider?:boolean};
type View=Omit<ProfileState,'catalog'>&{catalog:Entry[];source_provider?:string};

export function ProfilesSection({projectId,execution,nodes,managed}:{projectId:number;execution:string;nodes:string[];managed:boolean}){
  const [node,setNode]=useState('');const [state,setState]=useState<View|null>(null);const [pid,setPid]=useState('');
  const [rationale,setRationale]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const selected=nodes.includes(node)?node:(nodes[0]??'');
  // Only profiles of the failed attempt's provider can be approved; the server enforces it too.
  const options=state?.catalog.filter(c=>c.matches_source_provider!==false)??[];
  const chosen=options.find(c=>c.profile_id===pid)??options[0];
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
    if((!state?.base_version||!chosen)&&!overlay)return;setBusy(true);
    try{
      if(overlay)await observations.cancelProfile(projectId,execution,overlay,rationale);
      else await observations.approveProfile(projectId,execution,{node_key:selected,profile_id:chosen!.profile_id,
        base_version:state!.base_version!,catalog_hash:state!.catalog_hash,rationale});
      setState(await observations.profiles(projectId,execution,selected));setError('');
    }catch{setError('Сервер отклонил изменение: проверьте scope, версию и разрешение.');}
    finally{setBusy(false);}
  }
  return <section data-testid="profiles-panel" className="rounded border border-white/10 p-2 space-y-2">
    <h4>Профиль конкретной попытки</h4>
    <p>{state?.synthetic_only?'Тестовые профили':'Рабочие профили'} · {state?.catalog.length?'только явно разрешённые варианты':'не настроено'}. Общий бюджет и одна дополнительная попытка сохраняются.</p>
    <label>Нода <select aria-label="Нода профиля" value={selected} onChange={e=>setNode(e.target.value)} className="bg-zinc-900">{nodes.map(n=><option key={n}>{n}</option>)}</select></label>
    {options.length>1&&<label>Профиль <select aria-label="Профиль попытки" value={chosen?.profile_id} onChange={e=>setPid(e.target.value)} className="bg-zinc-900">{options.map(c=><option key={c.profile_id}>{c.profile_id}</option>)}</select></label>}
    <p>Кандидат: {chosen?`${chosen.model_id} · ${chosen.provider??'kie'}`:'не настроен'} · глубина рассуждений {chosen?.reasoning_effort??'не передаётся'} · лимит выходных токенов {chosen?.max_output_tokens??'не передаётся'}.</p>
    <p>Провайдер профиля должен совпадать с провайдером упавшей попытки ({state?.source_provider??'неизвестен'}).{state&&state.catalog.length>0&&!options.length?' Подходящего профиля нет.':''}</p>
    <label className="block">Основание одобрения <input aria-label="Основание профиля" value={rationale} onChange={e=>setRationale(e.target.value)} className="bg-transparent border rounded"/></label>
    <Button size="sm" variant="outline" disabled={busy||!managed||!state?.available||!chosen||Boolean(state.overlays.length)||rationale.trim().length<3} onClick={()=>void change()}>Одобрить профиль следующей попытки</Button>
    {!state?.available&&<p>{state?.reason??'Контекст ещё не прочитан'}</p>}
    {error&&<p role="alert">{error}</p>}
    {state?.overlays.map(o=><div key={o.overlay_id} data-testid="profile-record" className="border-t border-white/10 pt-2">
      <p>{profileStatus(o.status)} · {o.profile_id} · {o.candidate.model_id}</p>
      <p>Одобрение: {o.initiator} · {o.rationale}</p>
      <p>Фактический результат: {o.recovery?.status??'не выполнялся'} · verify {o.recovery?.verification?.verified?'подтверждён runtime':'не подтверждён'}</p>
      <Button size="sm" variant="outline" disabled={busy||o.status==='cancelled'||['claimed','await_verify'].includes(o.recovery?.status??'')||rationale.trim().length<3} onClick={()=>void change(o.overlay_id)}>Вернуть конфигурацию</Button>
      <p>Возврат конфигурации не откатывает данные или расход.</p>
      <details><summary>Технические подробности и история профиля</summary><p>Base {o.base_version} → candidate {o.candidate_hash} · attempt {o.attempt_id??'не начата'}</p><pre className="whitespace-pre-wrap">{JSON.stringify(o.history,null,2)}</pre></details>
    </div>)}
  </section>;
}
