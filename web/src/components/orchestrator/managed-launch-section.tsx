"use client";

import {useEffect,useRef,useState} from 'react';
import {Button} from '@/components/ui/button';
import {managedLaunchError,actionStatus,scopedLaunchStrategies} from '@/lib/actions-view';
import {observations,type ManagedLaunchOptions,type ManagedLaunchRequest,type ManagedLaunch,
  type ObservationContext,type ActionState,type Diagnosis} from '@/lib/observations';

const strategyLabels:Record<string,string>={wait:'Ожидать штатное восстановление',human_review:'Обращаться к человеку',
  retry_same:'Один повтор после исчерпания штатных попыток',retry_profile:'Выбирать разрешённый профиль восстановления'};
const promptLabels:Record<string,string>={'original-contract-v1':'Исходный контракт',
  'repair-json-only-v1':'Исправить только формат JSON','require-apply-ops-v1':'Обязательные операции записи'};

export function ManagedLaunchSection({projectId,context,actions,diagnosis,onExecution}:{projectId:number;
  context:ObservationContext|null;actions:ActionState|null;diagnosis:Diagnosis|null;
  onExecution:(execution:string)=>void}) {
  const [options,setOptions]=useState<ManagedLaunchOptions|null>(null);
  const [nodes,setNodes]=useState<string[]>([]);
  const [strategies,setStrategies]=useState<string[]>([]);
  const [profiles,setProfiles]=useState<string[]>([]);
  const [limit,setLimit]=useState(12);
  const [launch,setLaunch]=useState<ManagedLaunch|null>(null);
  const [pending,setPending]=useState<ManagedLaunchRequest|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [storageReady,setStorageReady]=useState(false);
  const generation=useRef(0);
  const onExecutionRef=useRef(onExecution);onExecutionRef.current=onExecution;
  const storageKey=`studio-managed-launch-${projectId}`;

  useEffect(()=>{
    const epoch=++generation.current;
    setOptions(null);setLaunch(null);setNodes([]);setStrategies([]);setProfiles([]);setPending(null);setMessage('');setBusy(false);setStorageReady(false);
    let cancelled=false;
    void observations.launchOptions(projectId).then(value=>{
      if(cancelled || generation.current!==epoch) return;
      setOptions(value);setLimit(value.request_limit_default);setStrategies(value.strategies.filter(s=>s!=='retry_profile'));
      const eligible=value.nodes.filter(node=>node.supported && node.status==='pending');
      const firstGroup=eligible.find(node=>node.group_id)?.group_id;
      setNodes(firstGroup ? eligible.filter(node=>node.group_id===firstGroup).map(node=>node.node_key) : []);
      if(value.latest_launch) {setLaunch(value.latest_launch);onExecutionRef.current(value.latest_launch.execution_id);}
      try {
        const stored=localStorage.getItem(storageKey);
        if(stored && !value.latest_launch) {
          const request=JSON.parse(stored) as ManagedLaunchRequest;
          if(typeof request.idempotency_key==='string') {
            setPending(request);setMessage('Предыдущая отправка не подтверждена. Проверьте сохранённый запуск; новая отправка заблокирована.');
          }
        }
        setStorageReady(true);
      } catch {setMessage('Локальная история отправок недоступна. Запуск запрещён до восстановления сохранения.');}
    }).catch(()=>{if(!cancelled)setMessage('Не удалось прочитать текущий граф и разрешения. Запуск закрыт.');});
    return ()=>{cancelled=true;generation.current++;};
  },[projectId,storageKey]);

  useEffect(()=>{
    if(!options || !nodes.length) return;
    const allowed=scopedLaunchStrategies(options,nodes);
    setStrategies(previous=>{
      const next=previous.filter(strategy=>allowed.includes(strategy));
      return next.length===previous.length ? previous : next;
    });
    if(!allowed.includes('retry_profile')) setProfiles(previous=>previous.length ? [] : previous);
  },[options,nodes]);

  async function refresh() {
    const epoch=generation.current;setBusy(true);
    try {
      const value=await observations.launchOptions(projectId);
      if(generation.current!==epoch) return;
      setOptions(value);
      if(value.latest_launch) {
        setLaunch(value.latest_launch);onExecutionRef.current(value.latest_launch.execution_id);
        setMessage('Показано сохранённое состояние запуска.');
      } else setMessage(pending ? 'Сохранённый запуск ещё не найден. Повторная отправка автоматически не выполняется.' : 'Состояние прочитано.');
    } catch {if(generation.current===epoch)setMessage('Состояние запуска не подтверждено. Новая отправка запрещена.');}
    finally {if(generation.current===epoch)setBusy(false);}
  }

  async function start() {
    if(!options || busy || launch || pending || !storageReady) return;
    const error=managedLaunchError(options,{node_keys:nodes,strategies,request_limit:limit,
      profile_ids:profiles,profile_catalog_hash:strategies.includes('retry_profile') ? options.profile_catalog_hash : undefined});
    if(error) {setMessage(error);return;}
    const epoch=generation.current;
    const request:ManagedLaunchRequest={workflow_run_id:options.workflow_run_id,graph_version:options.graph_version,
      node_keys:[...nodes],strategies:[...strategies],request_limit:limit,
      profile_ids:[...profiles],profile_catalog_hash:strategies.includes('retry_profile') ? options.profile_catalog_hash : undefined,
      rationale:'Пользователь разрешил выбранную область, стратегии и общий лимит через единый запуск.',
      idempotency_key:crypto.randomUUID()};
    try {localStorage.setItem(storageKey,JSON.stringify(request));}
    catch {setMessage('Не удалось сохранить идентификатор отправки. Запуск не отправлен.');return;}
    setPending(request);setBusy(true);setMessage('Сохраняются разрешения и передаётся первая нода runtime…');
    try {
      const result=await observations.launch(projectId,request);
      if(generation.current!==epoch) return;
      setLaunch(result);onExecutionRef.current(result.execution_id);
      setMessage(result.status==='submitted' ? 'Запуск принят. Следующие переходы выполняет конвейер.' : 'Запуск удержан; требуется проверить причину.');
    } catch {
      if(generation.current===epoch)setMessage('Ответ запуска не подтверждён. Проверьте сохранённое состояние; повторный запуск заблокирован.');
    } finally {if(generation.current===epoch)setBusy(false);}
  }

  const matches=Boolean(launch && context?.execution_id===launch.execution_id);
  const current=matches ? context : null;
  const actionState=matches ? actions : null;
  const incidents=matches ? diagnosis?.incidents.filter(incident=>incident.currently_relevant) ?? [] : [];
  const states=current?.project?.nodes ?? current?.master.nodes ?? {};
  const selected=launch?.policy.node_keys ?? nodes;
  const done=selected.filter(key=>states[key]?.status==='done').length;
  const failure=managedLaunchError(options,{node_keys:nodes,strategies,request_limit:limit,
    profile_ids:profiles,profile_catalog_hash:strategies.includes('retry_profile') ? options?.profile_catalog_hash : undefined});
  return <section data-testid="managed-launch-panel" className="space-y-3 rounded border border-emerald-400/25 bg-emerald-400/5 p-3">
    <strong>Автономный запуск выбранной области</strong>
    <p className="text-white/60">Выберите ноды, разрешённые стратегии и общий лимит. Система запускает цепочку и проверяет разрешения перед дальнейшими действиями.</p>
    {!launch && <>
      <fieldset disabled={busy || !!pending} className="space-y-2">
        <legend>Область текущего графа</legend>
        <div className="max-h-44 space-y-1 overflow-y-auto">
          {options?.nodes.map(node=><label key={node.node_key} className="flex items-start gap-2">
            <input type="checkbox" checked={nodes.includes(node.node_key)} disabled={!node.supported || node.status!=='pending'}
              onChange={event=>setNodes(previous=>event.target.checked ? [...previous,node.node_key] : previous.filter(key=>key!==node.node_key))}/>
            <span>{node.title} · {node.status}{!node.supported && <span className="text-amber-200"> · {node.reason}</span>}</span>
          </label>)}
        </div>
        <div className="space-y-1">{(nodes.length ? scopedLaunchStrategies(options,nodes) : options?.strategies ?? []).map(strategy=><label key={strategy} className="flex items-center gap-2">
          <input type="checkbox" checked={strategies.includes(strategy)} onChange={event=>{
            setStrategies(previous=>event.target.checked ? [...previous,strategy] : previous.filter(value=>value!==strategy));
            if(strategy==='retry_profile' && !event.target.checked)setProfiles([]);
          }}/>
          {strategyLabels[strategy] ?? strategy}
        </label>)}</div>
        {strategies.includes('retry_profile') && <fieldset className="space-y-1">
          <legend>Заранее разрешённые профили</legend>
          {options?.profiles.map(profile=><label key={profile.profile_id} className="flex items-start gap-2">
            <input type="checkbox" aria-label={`Разрешить профиль ${profile.profile_id}`} checked={profiles.includes(profile.profile_id)}
              onChange={event=>setProfiles(previous=>event.target.checked ? [...previous,profile.profile_id] : previous.filter(id=>id!==profile.profile_id))}/>
            <span>{promptLabels[profile.prompt_variant ?? ''] ?? profile.profile_id} · {profile.model_id} · {profile.reasoning_effort} · до {profile.max_output_tokens} выходных токенов</span>
          </label>)}
          <p className="text-white/50">Система выбирает только из отмеченного списка. Повтор с другим профилем использует ту же единственную recovery-попытку.</p>
        </fieldset>}
        <label className="flex items-center gap-2">Общий лимит запросов
          <input aria-label="Лимит единого запуска" type="number" min={1} max={options?.request_limit_max ?? 100}
            value={Number.isNaN(limit) ? '' : limit} onChange={event=>setLimit(Number(event.target.value))}
            className="w-20 rounded border border-white/15 bg-[#161616] p-1.5"/>
        </label>
      </fieldset>
      <p className="text-white/50">В лимит входят цикл решений, исполнитель и медиа-операции. Цена неизвестна; лимит запросов не гарантирует денежный потолок.</p>
      {failure && <p className="text-amber-200">{failure}</p>}
      <Button size="sm" disabled={busy || !!pending || !!failure || !storageReady} onClick={()=>void start()}>Запустить выбранную область</Button>
    </>}
    {launch && <div data-testid="managed-launch-progress" className="space-y-1">
      <p>Разрешённая область: {launch.policy.node_keys.map(key=>options?.nodes.find(node=>node.node_key===key)?.title ?? key).join(' → ')}</p>
      {!!launch.policy.profile_ids?.length && <p>Разрешённые профили: {launch.policy.profile_ids.join(', ')}</p>}
      <p>Запуск: {launch.status==='submitted' ? 'передан конвейеру' : 'удержан · нужен человек'} · выполнено {current ? `${done}/${selected.length}` : 'не подтверждено'}</p>
      <p>Сейчас: {current?.current_nodes.map(key=>options?.nodes.find(node=>node.node_key===key)?.title ?? key).join(', ') || 'нет подтверждённой активной ноды'}</p>
      {actionState && <p>{!actionState.enabled ? 'Управление завершено' : actionState.held ? 'Управление удержано' : 'Управление включено'} · запросы {actionState.requests_used}/{actionState.request_limit}</p>}
      {current?.wait_reasons && Object.entries(current.wait_reasons).map(([node,wait])=><p key={node}>Ожидание: {wait.reason}</p>)}
      {incidents.map(incident=><p key={incident.incident_id} className="text-amber-200">{incident.node_title}: {incident.fact} · {incident.recommendation.title}</p>)}
      {actionState?.records.slice(0,3).map(action=><p key={action.action_id}>{actionStatus(action.status)} · {action.rationale}</p>)}
      {launch.status!=='submitted' && <p className="text-amber-200">Подготовка или передача запуска не завершилась. Обычное возобновление запрещено; подробности сохранены в истории.</p>}
      {current?.business_group_outcome==='done' && <p>Выбранная область выполнена. Это не означает завершение всего проекта или проверку художественного качества.</p>}
    </div>}
    <Button size="sm" variant="outline" disabled={busy} onClick={()=>void refresh()}>Проверить сохранённое состояние</Button>
    {message && <p role="status" className="text-amber-100">{message}</p>}
  </section>;
}
