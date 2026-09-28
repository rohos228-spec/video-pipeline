"""Rule-based projection of the existing journal. Never schedules business work.

The cursor and incidents commit together in master observation storage. Every
revision reads the complete delivered history in pages, including late project
outbox events. Correlation uses attempt/request links, never cross-DB clocks.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, String, select, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, WorkflowRun
from app.services.error_catalog import ERROR_CATALOG
from app.services.observation import ExecutionControl, ObservationEvent, digest, panel_safe, business_snapshot

RULE_VERSION = 'diagnose-v1.1'


class DiagnosisState(Base):
    __tablename__ = 'observation_diagnosis'
    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cursor: Mapped[int] = mapped_column(default=0)
    checked_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    rule_version: Mapped[str] = mapped_column(String(32), default=RULE_VERSION)
    snapshot_hash: Mapped[str] = mapped_column(String(64), default='')


class Incident(Base):
    __tablename__ = 'observation_incidents'
    incident_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int] = mapped_column(index=True)
    node_key: Mapped[str | None] = mapped_column(String(60), default=None)
    attempt_id: Mapped[str | None] = mapped_column(String(36), default=None)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24))
    detected_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    details: Mapped[dict] = mapped_column(JSON)


TITLES = {
    'invalid_json':'Ответ не разобран как обязательный JSON',
    'missing_payload':'Нет обязательных данных в ответе',
    'contract_rejected':'Ответ отклонён существующей проверкой',
    'api_error':'Зафиксирована ошибка API или транспорта',
    'uncertain_request':'Завершение внешнего запроса не подтверждено',
    'runtime_wait':'Ожидание очереди, блокировки или подтверждения',
    'user_stop':'Остановка пользователем',
    'db_disagreement':'Состояния master и project расходятся',
    'journal_gap':'Доказательства неполны: пробел журнала',
}
RECOMMENDATIONS = {
    'wait':'Ожидать', 'check_contract':'Проверить входные данные и контракт',
    'check_database':'Проверить состояние БД',
    'review_provider_response':'Сверить сохранённый ответ провайдера',
    'restore_journal':'Восстановить доступность журнала', 'human_review':'Передать человеку',
}


def derive_incidents(scope, events, snapshot):
    """Pure projection; all inputs are recorded facts, not provider body guesses."""
    rows = {e['event_id']: e for e in events if e.get('project_id') == scope.project_id
            and e.get('execution_id') == scope.execution_id
            and (e.get('node_key') is None or e['node_key'] in scope.node_keys)}
    rows = sorted(rows.values(), key=lambda e:e['id'])
    groups = defaultdict(list)
    for e in rows: groups[(e.get('node_key'), e.get('attempt_id'))].append(e)
    issues = {}

    def issue(kind, key, evidence, *, blocking=True, severity='error', fact=None, action='check_contract', incomplete=False):
        first=evidence[0] if evidence else {}
        identity=str(uuid.uuid5(uuid.NAMESPACE_URL, f'{scope.execution_id}|{kind if kind not in {"invalid_json","missing_payload"} else "format"}|{key}'))
        item=issues.get(identity)
        if item is None:
            item=dict(incident_id=identity,execution_id=scope.execution_id,project_id=scope.project_id,
                node_key=first.get('node_key'),attempt_id=first.get('attempt_id'),type=kind,title=TITLES[kind],
                severity=severity,status='open',blocking=blocking,currently_relevant=True,
                fact=fact or TITLES[kind],cause={'hypothesis':None,'certainty':'unknown'},
                evidence_incomplete=incomplete,event_ids=[],request_ids=[],evidence=[],recovery=[],resolution=None,
                recommendation={'action':action,'title':RECOMMENDATIONS[action],'reason':fact or TITLES[kind],
                    'automatic_action':False,'safety_conditions':['Не выполнять повтор без подтверждения результата прежнего запроса, идемпотентности записи и разрешённого бюджета.']})
            issues[identity]=item
        for e in evidence:
            if e['event_id'] not in item['event_ids']:
                item['event_ids'].append(e['event_id'])
                item['evidence'].append({'event_id':e['event_id'],'kind':e['kind'],'db_scope':e.get('db_scope'),
                    'attempt_id':e.get('attempt_id'),'request_id':e.get('request_id')})
            if e.get('request_id') and e['request_id'] not in item['request_ids']: item['request_ids'].append(e['request_id'])
        return item

    for (node,attempt), related in groups.items():
        starts={e['request_id']:e for e in related if e['kind']=='model_start' and e.get('request_id')}
        retries=[e for e in related if e['kind']=='json_retry']
        bads=[e for e in related if e['kind']=='parse_result' and e['payload'].get('payload_required') is True
              and (e['payload'].get('parsed') is False or e['payload'].get('payload_present') is False)]
        bad_requests={e.get('request_id') for e in bads if e.get('request_id')}

        def previous(request): return starts.get(request,{}).get('payload',{}).get('previous_request_id')
        def retry_for(request):
            explicit=[e for e in retries if e.get('request_id')==request or e['payload'].get('source_request_id')==request]
            if explicit: return explicit
            # Legacy JSON retry had no request_id. Accept only an unambiguous
            # single retry plus the actual recorded previous_request link.
            if len(retries)==1 and not retries[0].get('request_id') and not retries[0]['payload'].get('source_request_id'):
                children=[r for r in starts if previous(r)==request]
                if len(children)==1 and request in bad_requests: return retries
            return []

        def root(request):
            seen=set()
            while request and request not in seen:
                seen.add(request); parent=previous(request)
                if parent in bad_requests and retry_for(parent): request=parent
                else: break
            return request

        for bad in bads:
            request=bad.get('request_id'); origin=root(request) or bad['event_id']
            kind='invalid_json' if bad['payload'].get('parsed') is False else 'missing_payload'
            item=issue(kind,(node,attempt,origin),[bad],incomplete=not bool(attempt and request))
            linked_retries=retry_for(origin)
            descendants=set([origin]); changed=True
            while changed:
                changed=False
                for r in starts:
                    if previous(r) in descendants and retry_for(previous(r)) and r not in descendants:
                        descendants.add(r); changed=True
            accepted=[e for e in related if e['kind']=='parse_result' and e.get('request_id') in descendants
                      and e['payload'].get('parsed') is True and e['payload'].get('payload_present') is True
                      and e['payload'].get('payload_required') is True]
            all_accepted=[e for e in related if e['kind']=='parse_result' and e['payload'].get('parsed') is True
                          and e['payload'].get('payload_present') is True]
            done=[e for e in related if attempt and e['kind']=='node_change' and e['payload'].get('transactional') is True
                  and 'done' in e['payload'].get('changes',{}).get('status',{}).get('after',[])]
            commits=[e for e in related if attempt and e['kind']=='data_applied' and e['payload'].get('transactional') is True
                     and e['payload'].get('result',{}).get('ok') is True]
            proof=[]; correlation=None
            for parsed in accepted:
                linked=[e for e in commits if e.get('request_id')==parsed['request_id']]
                if not linked and len(all_accepted)==1:
                    linked=[e for e in commits if not e.get('request_id')]
                    if linked: correlation='legacy_same_attempt_single_accepted_payload'
                elif linked: correlation='explicit_request_link'
                if linked and done and linked_retries:
                    proof=[parsed,*linked,*done]; break
            issue(kind,(node,attempt,origin),[*linked_retries,*[starts[r] for r in descendants if r in starts],*proof])
            item['recovery']=[{'kind':'runtime_json_retry','event_id':e['event_id'],'source_request_id':origin,
                               'accepted_response_observed':bool(accepted)} for e in linked_retries]
            if proof and attempt:
                item.update(status='resolved_confirmed',blocking=False,currently_relevant=False,
                    resolution={'kind':'format_recovery','correlation':correlation,
                        'event_ids':[e['event_id'] for e in proof],'quality_verified':False})
                item['recommendation'].update(action='wait',title=RECOMMENDATIONS['wait'],
                    reason='Штатный повтор принят; применение и done подтверждены в этой попытке. Семантическое качество не проверено.')
            elif any(e['kind']=='node_error' for e in related): item['status']='human_review'

        for e in related:
            p=e['payload']; kind=e['kind']
            if kind=='runtime_check' and p.get('performed') is True and p.get('status') in {'failed','rejected'}:
                issue('contract_rejected',(node,attempt,p.get('check'),e.get('request_id') or e['event_id']),[e],fact=f"Проверка {p.get('check','unknown')} отклонила результат.")
            if kind in {'apply_error','node_error','model_error'}:
                if kind=='node_error' and bads:
                    for item in issues.values():
                        if item['node_key']==node and item['attempt_id']==attempt and item['type'] in {'invalid_json','missing_payload'}:
                            issue(item['type'],(node,attempt,root(bads[0].get('request_id')) or bads[0]['event_id']),[e])
                    continue
                uncertain=p.get('uncertain') is True or p.get('error_type') in {'CancelledError','ReadTimeout','ConnectTimeout'}
                if kind=='model_error' or uncertain:
                    request=e.get('request_id')
                    pending=[r for r in starts if not any(t.get('request_id')==r and t['kind'] in {'model_end','model_error'} for t in related)]
                    if request is None and uncertain and len(pending)==1: request=pending[0]
                    code=p.get('error_code','unknown'); title=ERROR_CATALOG.get(code,ERROR_CATALOG['unknown']).title
                    item=issue('uncertain_request' if uncertain else 'api_error',(node,attempt,request or e['event_id']),[e],
                        fact=title if code!='unknown' else 'Записано исключение транспорта; причина не установлена.',
                        action='review_provider_response' if uncertain else 'human_review',incomplete=uncertain)
                    item['catalog_code']=code; item['status']='human_review'
                else:
                    item=issue('contract_rejected',(node,attempt,e['event_id']),[e],fact='Исходный runtime записал ошибку применения/выполнения; точная причина неизвестна.',
                        action='check_database' if kind=='apply_error' else 'human_review')
                    item['status']='human_review'
            if kind=='model_start' and e.get('request_id') and not any(r.get('request_id')==e['request_id'] and r['kind'] in {'model_end','model_error'} for r in related):
                ended=any(r['kind'] in {'node_error','node_end'} for r in related)
                terminal=snapshot.get('nodes',{}).get(node,{}).get('status') in {'done','failed','cancelled'}
                if ended or (scope.observer_state=='stopped' and terminal):
                    item=issue('uncertain_request',(node,attempt,e['request_id']),[e],action='review_provider_response',incomplete=True)
                    item['status']='human_review'
            if kind=='runtime_wait':
                state=snapshot.get('nodes',{}).get(node,{}).get('status')
                later_start=any(r['kind']=='node_start' and r['id']>e['id'] for r in rows if r.get('node_key')==node)
                if state not in {'done','skipped'} and not later_start:
                    issue('runtime_wait',(node,attempt,e['event_id']),[e],blocking=False,severity='info',action='wait',fact=f"Runtime ожидает: {p.get('reason','unknown')}. Это не ошибка.")
            if kind=='project_control' and p.get('user_stop') is True and snapshot.get('user_stop') is not False:
                latest=next((r for r in reversed(rows) if r['kind']=='project_control'),e)
                if latest['event_id']==e['event_id']:
                    item=issue('user_stop','control',[e],blocking=False,severity='info',action='wait',fact='Зафиксирована остановка пользователем. Автоматически продолжать нельзя.')
                    item['recommendation']['safety_conditions']=['Продолжение только после явного снятия user_stop пользователем.']
            ref=p.get('body',{})
            if isinstance(ref,dict) and ref.get('gap'):
                issue('journal_gap',e['event_id'],[e],blocking=False,severity='warning',action='restore_journal',incomplete=True,fact='Полное тело не сохранено: '+str(ref['gap']))

    for node,state in snapshot.get('nodes',{}).items():
        if state.get('status')=='waiting_hitl' and not any(i['type']=='runtime_wait' and i['node_key']==node for i in issues.values()):
            item=issue('runtime_wait',('hitl',node),[],blocking=False,severity='info',action='wait',fact='В БД waiting_hitl; ожидается подтверждение. Это не ошибка.')
            item['node_key']=node
    if snapshot.get('user_stop') and not any(i['type']=='user_stop' for i in issues.values()):
        issue('user_stop','control',[],blocking=False,severity='info',action='wait',fact='В текущей БД установлен user_stop. Автоматическое продолжение запрещено.')
    if snapshot.get('disagreement'):
        item=issue('db_disagreement','snapshot',[],blocking=False,severity='warning',action='check_database',fact='Зафиксировано несовпадение master/project snapshot; причина неизвестна.')
        item['status']='human_review'; item['snapshot_evidence']=snapshot.get('snapshot_evidence')
    for gap in [*(scope.gaps or []),*snapshot.get('gaps',[])]:
        issue('journal_gap',digest(gap),[],blocking=False,severity='warning',action='restore_journal',incomplete=True,fact='Не хватает доказательств: '+str(gap.get('kind') or gap.get('error_type') or 'observer gap'))
    return list(issues.values())


class Diagnoser:
    def __init__(self,observer):
        self.observer=observer
        self.failures={}
        self.lock=asyncio.Lock()

    async def snapshot(self,scope):
        observer=self.observer
        async with observer.master() as s:
            master,project=await business_snapshot(s,scope)
            run=await s.get(WorkflowRun,scope.workflow_run_id)
            titles={n['id']:str((n.get('data') or {}).get('label') or (n.get('data') or {}).get('title') or n['id']) for n in (run.nodes_snapshot if run else [])}
        replica=None; gaps=[]
        if project and project.data_dir and (Path(project.data_dir)/'project.db').exists():
            try:
                from app.project_db import get_project_sessionmaker
                async with (await get_project_sessionmaker(project.data_dir))() as s:
                    replica,replica_project=await business_snapshot(s,scope)
                    project=replica_project
            except Exception as exc: gaps.append({'kind':'project_snapshot_unavailable','error_type':type(exc).__name__})
        else: gaps.append({'kind':'project_database_not_available'})
        return {'nodes':(replica or master)['nodes'],'user_stop':bool((project.meta or {}).get('user_stop')) if project else None,
                'disagreement':replica is not None and replica!=master,'snapshot_evidence':{'master':master,'project':replica},'gaps':gaps,'titles':titles}

    async def update(self,execution_id,*,snapshot=None):
        async with self.lock:
            async with self.observer.master() as s:
                scope=await s.get(ExecutionControl,execution_id)
                if scope is None: return
                snapshot=await self.snapshot(scope) if snapshot is None else snapshot
                rows=[]; cursor=0
                while True:
                    page=(await s.scalars(select(ObservationEvent).where(ObservationEvent.execution_id==execution_id,
                        ObservationEvent.project_id==scope.project_id,ObservationEvent.id>cursor).order_by(ObservationEvent.id).limit(500))).all()
                    if not page: break
                    rows.extend(page); cursor=page[-1].id
                facts=[{k:getattr(e,k) for k in ('id','event_id','execution_id','project_id','node_key','attempt_id','request_id','kind','db_scope','payload','created_at')} for e in rows]
                for e in rows:
                    ref=(e.payload or {}).get('body',{})
                    if isinstance(ref,dict) and ref.get('path'):
                        path=(self.observer.body_root/ref['path']).resolve()
                        if not path.is_relative_to(self.observer.body_root.resolve()) or not path.is_file():
                            snapshot.setdefault('gaps',[]).append({'kind':'body_not_available','event_id':e.event_id})
                if execution_id in self.observer.failures: snapshot.setdefault('gaps',[]).append({'kind':'observer_error'})
                if execution_id in self.observer.commit_uncertainty: snapshot.setdefault('gaps',[]).append({'kind':'business_commit_unconfirmed'})
                computed=derive_incidents(scope,facts,snapshot)
                stored=(await s.scalars(select(Incident).where(Incident.execution_id==execution_id))).all()
                lookup={i.incident_id:i for i in stored}
                computed_ids=set()
                for item in computed:
                    computed_ids.add(item['incident_id'])
                    item['node_title']=snapshot.get('titles',{}).get(item['node_key'],item['node_key'] or 'Выполнение группы')
                    row=lookup.get(item['incident_id'])
                    if row is None:
                        row=Incident(**{k:item[k] for k in ('incident_id','execution_id','project_id','node_key','attempt_id','type','severity','status')},details=panel_safe(item))
                        s.add(row)
                    elif digest(row.details)!=digest(item):
                        row.status=item['status'];row.details=panel_safe(item);row.updated_at=datetime.utcnow()
                # Keep historical notices and their proof when a wait/gap or
                # disagreement disappears. Do not falsely call this JSON recovery.
                for row in stored:
                    if row.incident_id not in computed_ids and row.type in {'runtime_wait','user_stop','db_disagreement','journal_gap'}:
                        row.status='resolved_confirmed'; row.details={**row.details,'status':'resolved_confirmed','currently_relevant':False,
                            'blocking':False,'resolution':{'kind':'condition_not_observed','quality_verified':False}}
                state=await s.get(DiagnosisState,execution_id)
                if state is None: state=DiagnosisState(execution_id=execution_id);s.add(state)
                state.cursor=cursor;state.checked_at=datetime.utcnow();state.rule_version=RULE_VERSION;state.snapshot_hash=digest(snapshot)
                await s.commit()
            self.failures.pop(execution_id,None)

    async def safe_update(self,execution_id):
        try: await self.update(execution_id)
        except Exception as exc:
            self.failures[execution_id]=type(exc).__name__

    async def catch_up(self):
        try:
            async with self.observer.master() as s: scopes=(await s.scalars(select(ExecutionControl))).all()
            for scope in scopes: await self.safe_update(scope.execution_id)
        except Exception as exc:
            self.failures['consumer']=type(exc).__name__
        else: self.failures.pop('consumer',None)


def get_diagnoser(observer):
    if not hasattr(observer,'diagnoser'): observer.diagnoser=Diagnoser(observer)
    return observer.diagnoser
