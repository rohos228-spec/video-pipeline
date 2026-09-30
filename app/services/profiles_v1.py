"""Fixed allowlisted synthetic node profiles on the original recovery executor.

No production profiles are enabled. Approval binds the base and exact first
request transformation; subsequent requests keep the same attempt overlay.
Reverting configuration cannot roll back business data or billed work.
"""
import copy,hashlib,json,uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from urllib.parse import urlsplit
from sqlalchemy import select
from app.models import Project,WorkflowRun
from app.services.profiles_state import ProfileOverlay
from app.services.observation import ExecutionControl,ObservationEvent,digest,panel_safe

CATALOG={'synthetic-json-v1':{'model_id':'gpt-synthetic-alt','provider':'kie',
    'kind':'text','channel':'stable','reasoning_effort':'medium','max_output_tokens':2048,
    'prompt_variant':'require-apply-ops-v1','synthetic_only':True}}
CATALOG.update({name:{**CATALOG['synthetic-json-v1'],'reasoning_effort':'low','max_output_tokens':1024,
    'prompt_variant':variant} for name,variant in (
        ('synthetic-original-v1','original-contract-v1'),('synthetic-format-v1','repair-json-only-v1'))})
PRODUCTION_CATALOG={}
_current=ContextVar('attempt_profile_overlay',default=None)

def active_catalog():
    from app.services.decision_config import runtime_config
    cfg=runtime_config()
    return CATALOG if cfg['mode']=='synthetic' and not cfg.get('incident_scope') and not cfg['profiles'] else cfg['profiles']

def catalog_hash():return digest(active_catalog())

def validate_candidate(candidate):
    if not isinstance(candidate,dict) or set(candidate)!=set(CATALOG['synthetic-json-v1']):
        raise ValueError('unknown profile parameters')
    if candidate not in active_catalog().values():raise ValueError('incompatible profile or forbidden provider')
    from app.services.decision_config import validate_profile,runtime_config
    validate_profile(candidate,runtime_config()['mode'])

def synthetic_transport(url=None,key=None):
    from app.settings import settings
    from app.services.decision_config import runtime_config,check_route
    cfg=runtime_config()
    if cfg['mode']=='production':
        from app.services.gpt_api import _chat_url
        from app.services.llm_override import NodeLlmOverride,use_override
        m=cfg['decision']
        if not m:raise ValueError('production profile not configured')
        with use_override(NodeLlmOverride(m['model_id'],'stable','text','kie',m['model_id'],m['reasoning_effort'])):
            check_route(cfg,url if url is not None else _chat_url(m['model_id']))
        return
    host=urlsplit(str(url if url is not None else settings.gpt_base_url)).hostname
    credential=str(key if key is not None else settings.gpt_api_key)
    if host not in {'localhost','127.0.0.1','::1'} or not credential.startswith('synthetic'):
        raise ValueError('Profiles-v1 synthetic loopback only; no production approval')

def effective_body(body,candidate):
    validate_candidate(candidate)
    from app.services.prompt_library import apply_approved_profile_prompt
    result=dict(body)
    if 'messages' in result:raise ValueError('profile requires compatible Responses text contract')
    result.update(model=candidate['model_id'],reasoning={'effort':candidate['reasoning_effort']},
                  max_output_tokens=candidate['max_output_tokens'])
    content=result.get('input')
    if isinstance(content,str):result['input']=apply_approved_profile_prompt(content,candidate['prompt_variant'])
    elif isinstance(content,list) and content and all(isinstance(m,dict) and set(m)=={'role','content'} and isinstance(m['content'],str) for m in content):
        content=copy.deepcopy(content)
        target=next((m for m in reversed(content) if m['role']=='user'),None)
        if target is None:raise ValueError('profile requires user text input')
        target['content']=apply_approved_profile_prompt(target['content'],candidate['prompt_variant']);result['input']=content
    else:raise ValueError('profile requires compatible Responses text contract')
    return result

def overlay_json(row):
    return panel_safe({k:getattr(row,k) for k in ('overlay_id','execution_id','node_key','source_attempt_id',
        'attempt_id','profile_id','base_version','graph_version','catalog_hash','candidate_hash','candidate',
        'source_body_hash','expected_body_hash','initiator','rationale','status','history','created_at')})

class ProfilesSupervisor:
    def __init__(self,observer):self.observer=observer;self.master=observer.master

    async def _base(self,s,project_id,execution_id,node_key):
        from app.services.actions_v1 import ActionExecutionControl
        from app.services.actions_recovery import input_revision
        scope=await s.get(ExecutionControl,execution_id);control=await s.get(ActionExecutionControl,execution_id)
        if not scope or scope.project_id!=project_id or not control or control.project_id!=project_id:
            raise ValueError('execution outside project scope')
        if node_key not in scope.node_keys or node_key not in control.node_keys:raise ValueError('node outside scope')
        run=await s.get(WorkflowRun,control.workflow_run_id)
        node=next((n for n in (run.nodes_snapshot if run else []) if n.get('id')==node_key),None)
        from app.services.node_capabilities import capability_for
        capability=capability_for(str(node.get('type') or '')) if node else None
        if not capability or not capability.managed or capability.result_kind!='structured' or node.get('type')!='excel_gpt':
            raise ValueError('incompatible accepted text node contract')
        project=await s.get(Project,project_id)
        if not control.enabled or control.held or scope.observer_state!='active' or scope.gaps or (project.meta or {}).get('user_stop'):
            raise ValueError('stop, permission or audit blocker')
        graph=digest({'nodes':run.nodes_snapshot,'edges':run.edges_snapshot})
        if graph!=control.graph_version or graph!=scope.graph_version:raise ValueError('stale graph revision')
        if project.data_dir:
            from app.project_db import project_db_session_scope
            async with project_db_session_scope(project_id) as ps:
                pp=await ps.get(Project,project_id)
                if not pp or (pp.meta or {}).get('user_stop'):raise ValueError('user_stop or project unavailable')
                revision=await input_revision(ps,pp,node_key)
        else:revision=await input_revision(s,project,node_key)
        return revision,graph

    async def state(self,project_id,execution_id,node_key):
        async with self.master() as s:
            # Viewing a terminal/held execution still shows its immutable history.
            scope=await s.get(ExecutionControl,execution_id)
            if not scope or scope.project_id!=project_id or node_key not in scope.node_keys:raise ValueError('scope unavailable')
            rows=(await s.scalars(select(ProfileOverlay).where(ProfileOverlay.execution_id==execution_id,
                ProfileOverlay.node_key==node_key,ProfileOverlay.project_id==project_id))).all()
            try:version,_=await self._base(s,project_id,execution_id,node_key);available=True;reason=None
            except ValueError as exc:version=None;available=False;reason=str(exc)
            from app.services.actions_recovery import RecoveryAction
            views=[]
            for row in rows:
                ticket=await s.scalar(select(RecoveryAction).where(RecoveryAction.execution_id==execution_id,RecoveryAction.new_attempt_id==row.attempt_id)) if row.attempt_id else None
                views.append({**overlay_json(row),'recovery':{'status':ticket.status,'verification':ticket.verification_result} if ticket else None})
            return {'base_version':version,'catalog_hash':catalog_hash(),'synthetic_only':__import__('app.services.decision_config',fromlist=['runtime_config']).runtime_config()['mode']=='synthetic',
                'catalog':[{'profile_id':k,**v} for k,v in active_catalog().items()], 'available':available,'reason':reason,
                'overlays':views}

    async def approve(self,*,project_id,execution_id,node_key,profile_id,base_version,catalog_hash,initiator,rationale):
        synthetic_transport()
        if not initiator or len(rationale.strip())<3:raise ValueError('explicit approval rationale required')
        if catalog_hash!=globals()['catalog_hash']():raise ValueError('stale catalog hash')
        candidate=active_catalog().get(profile_id)
        if not candidate:raise ValueError('unknown or incompatible profile / provider')
        validate_candidate(candidate)
        async with self.master() as s:
            revision,graph=await self._base(s,project_id,execution_id,node_key)
            if revision!=base_version:raise ValueError('stale config base version')
            prior=await s.scalar(select(ProfileOverlay).where(ProfileOverlay.execution_id==execution_id,ProfileOverlay.node_key==node_key))
            if prior:
                if prior.profile_id==profile_id and prior.base_version==base_version and prior.status=='approved':return overlay_json(prior)
                raise ValueError('one immutable profile version per node/execution; no replacement or extra recovery')
            source=await s.scalar(select(ObservationEvent).where(ObservationEvent.execution_id==execution_id,
                ObservationEvent.project_id==project_id,ObservationEvent.node_key==node_key,
                ObservationEvent.kind=='node_start').order_by(ObservationEvent.id.desc()).limit(1))
            event=await s.scalar(select(ObservationEvent).where(ObservationEvent.execution_id==execution_id,
                ObservationEvent.node_key==node_key,ObservationEvent.attempt_id==source.attempt_id,
                ObservationEvent.kind=='model_start').order_by(ObservationEvent.id).limit(1)) if source else None
            if not event:raise ValueError('recorded source request required')
            if candidate['prompt_variant']!='original-contract-v1':
                parsed=await s.scalar(select(ObservationEvent).where(ObservationEvent.execution_id==execution_id,
                    ObservationEvent.project_id==project_id,ObservationEvent.node_key==node_key,
                    ObservationEvent.attempt_id==source.attempt_id,ObservationEvent.kind=='parse_result')
                    .order_by(ObservationEvent.id.desc()).limit(1))
                if not parsed or parsed.payload.get('payload_required') is not True:
                    raise ValueError('registered JSON prompt variant requires recorded structured payload contract')
            ref=event.payload.get('body') or {};path=(self.observer.body_root/ref.get('path','')).resolve()
            if not path.is_relative_to(self.observer.body_root.resolve()) or not path.is_file():raise ValueError('source body audit unavailable')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=ref.get('sha256'):raise ValueError('source body hash mismatch')
            body=json.loads(raw)
            if digest(body)!=event.payload.get('input_revision'):raise ValueError('source effective body hash mismatch')
            expected=effective_body(body,candidate)
            row=ProfileOverlay(overlay_id=str(uuid.uuid4()),execution_id=execution_id,project_id=project_id,
                node_key=node_key,source_attempt_id=source.attempt_id,profile_id=profile_id,base_version=revision,
                graph_version=graph,catalog_hash=catalog_hash,candidate_hash=digest(candidate),candidate=dict(candidate),
                source_body_hash=digest(body),expected_body_hash=digest(expected),initiator=initiator,rationale=rationale,
                status='approved',history=[{'kind':'operator_approved','initiator':initiator,'at':datetime.utcnow().isoformat()}])
            s.add(row);await s.commit();return overlay_json(row)

    async def recover(self,*,overlay_id,**kwargs):
        from app.services.actions_v1 import ActionSupervisor
        # Executor repeats profile checks inside its durable reservation transaction.
        return await ActionSupervisor(self.master).recover_node(profile_overlay_id=overlay_id,**kwargs)

    async def cancel(self,*,project_id,execution_id,overlay_id,initiator,rationale):
        from app.services.actions_recovery import RecoveryAction
        from app.services.actions_v1 import ActionExecutionControl,update_permission
        if not initiator or len(rationale.strip())<3:raise ValueError('explicit cancellation rationale required')
        async with self.master() as s:
            row=await s.get(ProfileOverlay,overlay_id)
            if not row or row.project_id!=project_id or row.execution_id!=execution_id:raise ValueError('overlay outside scope')
            ticket=await s.scalar(select(RecoveryAction).where(RecoveryAction.execution_id==execution_id,
                RecoveryAction.new_attempt_id==row.attempt_id)) if row.attempt_id else None
            if ticket and ticket.status in {'claimed','await_verify'}:raise ValueError('attempt in progress; hold and review, no config mutation')
            if ticket and ticket.status in {'reserved','queued'}:
                control=await s.get(ActionExecutionControl,execution_id);control.held=True
                ticket.status='human_review';ticket.verification_result={'verified':False,'reason':'profile cancelled before claim'}
                update_permission(project_id,held=True)
            if row.status!='cancelled':
                row.status='cancelled';row.history=[*row.history,{'kind':'config_cancelled','initiator':initiator,
                    'rationale':rationale,'at':datetime.utcnow().isoformat(),'data_rollback':False}]
            await s.commit();return {**overlay_json(row),'data_rollback':False,'budget_reset':False}

async def reservation(s,overlay_id,*,execution_id,project_id,node_key,source_attempt_id,revision,graph,body_hash):
    synthetic_transport()
    row=await s.get(ProfileOverlay,overlay_id)
    if not row or (row.execution_id,row.project_id,row.node_key,row.source_attempt_id)!=(execution_id,project_id,node_key,source_attempt_id):
        raise ValueError('profile outside execution/node/attempt scope')
    validate_candidate(row.candidate)
    if row.status!='approved' or row.attempt_id or row.base_version!=revision or row.graph_version!=graph or row.catalog_hash!=catalog_hash() or row.candidate_hash!=digest(row.candidate) or row.source_body_hash!=body_hash:
        raise ValueError('stale or consumed approved profile/config version')
    return row

async def load_attempt(ctx):
    async with ctx.observer.master() as s:
        row=await s.scalar(select(ProfileOverlay).where(ProfileOverlay.execution_id==ctx.scope.execution_id,
            ProfileOverlay.project_id==ctx.scope.project_id,ProfileOverlay.node_key==ctx.node_key,
            ProfileOverlay.attempt_id==ctx.attempt_id))
        if not row:return None
        if row.status not in {'reserved','active'}:raise RuntimeError('profile is not active for this attempt')
        validate_candidate(row.candidate);synthetic_transport()
        if row.candidate_hash!=digest(row.candidate) or row.catalog_hash!=catalog_hash():raise RuntimeError('profile catalog changed')
        row.status='active';row.history=[*row.history,{'kind':'attempt_activated','attempt_id':ctx.attempt_id,'at':datetime.utcnow().isoformat()}]
        await s.commit();return overlay_json(row)

@contextmanager
def bind_attempt(overlay):
    if not overlay:yield;return
    from app.services.llm_override import NodeLlmOverride,use_override
    c=overlay['candidate'];token=_current.set(overlay)
    try:
        with use_override(NodeLlmOverride(c['model_id'],c['channel'],c['kind'],c['provider'],c['model_id'],c['reasoning_effort'])):yield
    finally:_current.reset(token)

async def transform_request(ctx,kwargs):
    overlay=_current.get()
    if not overlay:return kwargs
    if overlay['attempt_id']!=ctx.attempt_id:raise RuntimeError('profile attempt context mismatch')
    synthetic_transport(kwargs.get('url'),(kwargs.get('headers') or {}).get('Authorization','').removeprefix('Bearer '))
    async with ctx.observer.master() as s:
        row=await s.get(ProfileOverlay,overlay['overlay_id'])
        if not row or row.status!='active' or row.candidate_hash!=digest(row.candidate) or row.catalog_hash!=catalog_hash():raise RuntimeError('profile permission/config changed before send')
    body=effective_body(kwargs.get('body') or {},overlay['candidate'])
    return {**kwargs,'body':body,'use_model':overlay['candidate']['model_id']}

async def finish_attempt(ctx,error):
    async with ctx.observer.master() as s:
        row=await s.scalar(select(ProfileOverlay).where(ProfileOverlay.execution_id==ctx.scope.execution_id,ProfileOverlay.attempt_id==ctx.attempt_id))
        if row:
            row.status='failed' if error else 'applied';row.history=[*row.history,{'kind':'worker_returned','error':error,
                'at':datetime.utcnow().isoformat(),'verification_authority':'existing Actions executor'}];await s.commit()
