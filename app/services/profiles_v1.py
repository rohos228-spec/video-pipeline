"""Fixed allowlisted node profiles on the original recovery executor.

Synthetic profiles serve isolated acceptance. Production has a built-in catalog of
text-recovery profiles (PRODUCTION_CATALOG): it becomes active only when the owner's
runtime config is production, has a decision model, lists ``retry_profile`` in
``allowed_actions``, carries no explicit ``profiles`` and no ``incident_scope`` (a scoped
pilot stays explicit). An explicit ``profiles`` object always wins; ``builtin_profiles:false``
switches the built-in catalog off. Media nodes never get a profile. Approval binds the base and exact first
request transformation; subsequent requests keep the same attempt overlay.
Reverting configuration cannot roll back business data or billed work.
"""
import copy,hashlib,json,uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from urllib.parse import urlsplit
from sqlalchemy import select
from app.services import revisions
from app.models import Project,WorkflowRun
from app.services.profiles_state import ProfileOverlay
from app.services.observation import ExecutionControl,ObservationEvent,digest,panel_safe

CATALOG={'synthetic-json-v1':{'model_id':'gpt-synthetic-alt','provider':'kie',
    'kind':'text','channel':'stable','reasoning_effort':'medium','max_output_tokens':2048,
    'prompt_variant':'require-apply-ops-v1','synthetic_only':True}}
CATALOG.update({name:{**CATALOG['synthetic-json-v1'],'reasoning_effort':'low','max_output_tokens':1024,
    'prompt_variant':variant} for name,variant in (
        ('synthetic-original-v1','original-contract-v1'),('synthetic-format-v1','repair-json-only-v1'))})
PROVIDERS=('kie','vibecode')
# Reference Vibecode repair profile (chat/completions). reasoning_effort / max_output_tokens stay None:
# whether Vibecode honours them for this model is unverified, so the body never carries them.
VIBECODE_REPAIR_PROFILE_ID='vibecode-json-repair-v1'
VIBECODE_REPAIR_PROFILE={'model_id':'gpt-5.6-sol','provider':'vibecode','kind':'text','channel':'stable',
    'reasoning_effort':None,'max_output_tokens':None,'prompt_variant':'repair-json-only-v1','synthetic_only':False}

# Built-in production recovery profiles for text nodes. Only real catalog models
# (text_llm_catalog / vibecode_catalog); only prompt variants from the approved library
# (original contract or formatting-only JSON repair). They never touch media, write
# permissions, graph or budget: a profile changes only the body of one recovery request.
# Kie (Responses) honours reasoning_effort / max_output_tokens; Vibecode chat/completions is
# not known to, so its profile keeps both None (see validate_vibecode_profile).
def _kie(effort,tokens,variant):
    return {'model_id':'gpt-5-6-sol','provider':'kie','kind':'text','channel':'stable',
        'reasoning_effort':effort,'max_output_tokens':tokens,'prompt_variant':variant,'synthetic_only':False}
PRODUCTION_CATALOG={
    'kie-original-v1':_kie('medium',4096,'original-contract-v1'),
    'kie-json-repair-v1':_kie('medium',4096,'repair-json-only-v1'),
    'kie-json-repair-roomy-v1':_kie('high',8192,'repair-json-only-v1'),
    VIBECODE_REPAIR_PROFILE_ID:dict(VIBECODE_REPAIR_PROFILE)}
# Managed text node types (capability must also be profile_compatible: that check stays in
# decision_v1/_base) and the incidents a built-in profile may answer.
BUILTIN_NODE_TYPES=frozenset({'excel_gpt','plan','script','split','image_prompts','sfx_plan',
    'animation_prompts','sd_agent','sd_assemble'})
BUILTIN_INCIDENTS=frozenset({'invalid_json','missing_payload'})
MEDIA_NODE_TYPES=frozenset({'images','videos','items','music','audio','sfx_gen','hero','hitl_images','hitl_videos','assemble'})
PROFILE_BINDINGS={pid:{'node_types':BUILTIN_NODE_TYPES,'incidents':BUILTIN_INCIDENTS} for pid in PRODUCTION_CATALOG}

def builtin_production_profiles(cfg):
    """Built-in profiles of the decision provider, or {} unless the owner opted in (see module doc).
    A profile that no longer validates against the model catalogs is dropped (fail closed)."""
    from app.services import decision_config as dc
    if (not isinstance(cfg,dict) or cfg.get('mode')!='production' or cfg.get('decision') is None
            or cfg.get('profiles') or cfg.get('incident_scope') or cfg.get('builtin_profiles') is False
            or 'retry_profile' not in (cfg.get('allowed_actions') or [])):return {}
    prov=dc.provider(cfg);result={}
    for pid,c in PRODUCTION_CATALOG.items():
        if c['provider']!=prov:continue
        try:dc.validate_profile(c,'production')
        except ValueError:continue
        result[pid]=dict(c)
    return result

def effective_profiles(cfg):
    """Owner's explicit profiles, else the built-in production catalog (when enabled)."""
    return cfg['profiles'] if cfg.get('profiles') else builtin_production_profiles(cfg)

def binding_allows(profile_id,*,node_type,incident_type):
    """Built-in profiles are bound to node types and incidents; owner-defined ids are not restricted here."""
    b=PROFILE_BINDINGS.get(profile_id)
    return True if b is None else (node_type in b['node_types'] and incident_type in b['incidents']
        and node_type not in MEDIA_NODE_TYPES)
_current=ContextVar('attempt_profile_overlay',default=None)

def source_provider(payload):
    """Provider of a recorded model_start: the recorded field, else a legacy path/host fingerprint, else 'unknown'.

    A profile is only allowed for a known provider that equals its own (fail closed)."""
    payload=payload or {}
    if payload.get('provider') in PROVIDERS:return payload['provider']
    path=str(payload.get('path') or '')
    if path.endswith('/codex/v1/responses'):return 'kie'
    if payload.get('host')=='vibecode.moe' and path.endswith('/chat/completions'):return 'vibecode'
    return 'unknown'

def _provider_mismatch(candidate,source):
    return source not in PROVIDERS or candidate.get('provider')!=source

async def _source_events(s,execution_id,project_id,node_key):
    """Latest node_start of the node and the first model_start of that attempt (as recorded in the journal)."""
    source=await s.scalar(select(ObservationEvent).where(ObservationEvent.execution_id==execution_id,
        ObservationEvent.project_id==project_id,ObservationEvent.node_key==node_key,
        ObservationEvent.kind=='node_start').order_by(ObservationEvent.id.desc()).limit(1))
    event=await s.scalar(select(ObservationEvent).where(ObservationEvent.execution_id==execution_id,
        ObservationEvent.node_key==node_key,ObservationEvent.attempt_id==source.attempt_id,
        ObservationEvent.kind=='model_start').order_by(ObservationEvent.id).limit(1)) if source else None
    return source,event

def active_catalog():
    from app.services.decision_config import runtime_config
    cfg=runtime_config()
    return CATALOG if cfg['mode']=='synthetic' and not cfg.get('incident_scope') and not cfg['profiles'] else effective_profiles(cfg)

def catalog_hash():return digest(active_catalog())

async def eligible_profiles(session,row,base_version):
    """Read-only choices from the operator's launch snapshot; no overlay writes."""
    from app.services.managed_launch import ManagedLaunch
    launch=await session.scalar(select(ManagedLaunch).where(ManagedLaunch.execution_id==row.execution_id,
        ManagedLaunch.project_id==row.project_id))
    if not launch or launch.status!='submitted' or 'retry_profile' not in launch.policy['allowed_actions']:return []
    policy=launch.policy;ids=policy.get('profile_ids',[])
    if not ids:return []
    if policy.get('profile_catalog_hash')!=catalog_hash():raise ValueError('execution profile catalog changed')
    catalog=active_catalog()
    if any(k not in catalog or policy.get('profiles',{}).get(k)!=catalog[k] for k in ids):
        raise ValueError('execution profile catalog snapshot mismatch')
    event=await session.scalar(select(ObservationEvent).where(ObservationEvent.execution_id==row.execution_id,
        ObservationEvent.node_key==row.node_key,ObservationEvent.attempt_id==row.source_attempt_id,
        ObservationEvent.kind=='model_start').order_by(ObservationEvent.id).limit(1))
    src=source_provider(event.payload) if event else 'unknown'
    ids=[k for k in ids if not _provider_mismatch(catalog[k],src)]
    if any(k in PROFILE_BINDINGS for k in ids):
        from app.services.diagnosis import Incident
        from app.services.observation import ExecutionControl
        from app.services.excel_gpt_node import effective_node_type
        incident=await session.get(Incident,row.incident_id)
        scope=await session.get(ExecutionControl,row.execution_id)
        run=await session.get(WorkflowRun,scope.workflow_run_id) if scope else None
        node=next((n for n in (run.nodes_snapshot if run else []) if str(n.get('id'))==row.node_key),None)
        node_type=effective_node_type(node) if node else ''
        ids=[k for k in ids if binding_allows(k,node_type=node_type,incident_type=incident.type if incident else '')]
    return [{'profile_id':k,'candidate':dict(catalog[k]),'catalog_hash':policy['profile_catalog_hash'],
        'base_version':base_version} for k in ids]

def validate_candidate(candidate):
    if not isinstance(candidate,dict) or set(candidate)!=set(CATALOG['synthetic-json-v1']):
        raise ValueError('unknown profile parameters')
    if candidate not in active_catalog().values():raise ValueError('incompatible profile or forbidden provider')
    from app.services.decision_config import validate_profile,runtime_config
    if candidate.get('provider')=='vibecode':return validate_vibecode_profile(candidate,runtime_config()['mode'])
    validate_profile(candidate,runtime_config()['mode'])

def validate_vibecode_profile(candidate,mode):
    """Vibecode chat/completions profile. decision_config.validate_profile is Kie-only, so this lives here."""
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS
    c=candidate
    if (not isinstance(c,dict) or set(c)!=set(CATALOG['synthetic-json-v1']) or c['provider']!='vibecode'
            or c['kind']!='text' or c['channel']!='stable' or c['prompt_variant'] not in APPROVED_PROFILE_PROMPTS
            or type(c['synthetic_only'])!=bool or c['reasoning_effort'] is not None or c['max_output_tokens'] is not None
            or not isinstance(c['model_id'],str)):
        raise ValueError('incompatible registered profile')
    if mode=='synthetic':
        if c['synthetic_only'] is not True or c['model_id']!='gpt-synthetic-alt':raise ValueError('invalid bounded synthetic profile')
        return
    if c['synthetic_only'] or 'synthetic' in c['model_id']:raise ValueError('synthetic profile not production')
    from app.services.vibecode_catalog import find_model
    known=find_model(c['model_id'])
    if not known or known.get('kind')!='text' or known.get('provider')!='vibecode':raise ValueError('unknown production text model')

def _check_vibecode_route(cfg,url):
    """Route of a Vibecode profile request: an approved Vibecode host (direct or the owner's relay),
    HTTPS (loopback excepted) and the chat/completions path. The Kie-only decision endpoint is not used."""
    from app.services import decision_config as dc
    if not cfg['online_enabled'] or cfg['decision'] is None:raise ValueError('decision provider disabled / not configured')
    if url is None:
        from app.services.gpt_api import _chat_url
        from app.services.llm_override import NodeLlmOverride,use_override
        m=cfg['decision']
        with use_override(NodeLlmOverride(m['model_id'],'stable','text','vibecode',m['model_id'],None)):url=_chat_url(m['model_id'])
    u=urlsplit(str(url))
    loopback={'127.0.0.1','localhost','::1'}
    hosts=dc.approved_vibecode_hosts() if hasattr(dc,'approved_vibecode_hosts') else {'vibecode.moe'}|loopback
    if (u.username or u.password or u.query or u.fragment or u.hostname not in hosts
            or (u.scheme!='https' and u.hostname not in loopback) or not u.path.endswith('/chat/completions')):
        raise ValueError('route differs from approved configuration')

def synthetic_transport(url=None,key=None,provider='kie'):
    from app.settings import settings
    from app.services.decision_config import runtime_config,check_route
    cfg=runtime_config()
    if cfg['mode']=='production' and provider=='vibecode':return _check_vibecode_route(cfg,url)
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

def _effective_chat_body(body,candidate):
    """Vibecode chat/completions: only text `messages`; no Responses `input`, no reasoning/token knobs."""
    from app.services.prompt_library import apply_approved_profile_prompt
    result=dict(body);messages=result.get('messages')
    if 'input' in result or not (isinstance(messages,list) and messages and all(isinstance(m,dict)
            and set(m)=={'role','content'} and isinstance(m['content'],str) for m in messages)):
        raise ValueError('profile requires compatible chat/completions text contract')
    messages=copy.deepcopy(messages)
    target=next((m for m in reversed(messages) if m['role']=='user'),None)
    if target is None:raise ValueError('profile requires user text input')
    target['content']=apply_approved_profile_prompt(target['content'],candidate['prompt_variant'])
    result.update(model=candidate['model_id'],messages=messages)
    return result

def effective_body(body,candidate):
    validate_candidate(candidate)
    if candidate['provider']=='vibecode':return _effective_chat_body(body,candidate)
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
        from app.services.excel_gpt_node import effective_node_type
        capability=capability_for(effective_node_type(node)) if node else None
        # Eligibility is capability data, not a hardcoded node-type string: a managed
        # node qualifies only if its capability declares profile_compatible (excel_gpt
        # and the native plan/script text nodes). effective_body() below is the real
        # safety net: it raises on any request shape it does not recognize
        # (e.g. vision/multimodal input), so widening eligibility here cannot by
        # itself let an incompatible node start using a profile.
        if not capability or not capability.managed or not capability.profile_compatible:
            raise ValueError('incompatible accepted text node contract')
        project=await s.get(Project,project_id)
        if not control.enabled or control.held or scope.observer_state!='active' or scope.gaps or (project.meta or {}).get('user_stop'):
            raise ValueError('stop, permission or audit blocker')
        graph=revisions.graph_version(run.nodes_snapshot,run.edges_snapshot)
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
            _,start=await _source_events(s,project_id=project_id,execution_id=execution_id,node_key=node_key)
            src=source_provider(start.payload) if start else 'unknown'
            return {'base_version':version,'catalog_hash':catalog_hash(),'synthetic_only':__import__('app.services.decision_config',fromlist=['runtime_config']).runtime_config()['mode']=='synthetic',
                'catalog_source':'synthetic' if active_catalog() is CATALOG else ('owner' if __import__('app.services.decision_config',fromlist=['runtime_config']).runtime_config()['profiles'] else 'builtin'),
                'catalog':[{'profile_id':k,**v,'matches_source_provider':not _provider_mismatch(v,src)} for k,v in active_catalog().items()],
                'source_provider':src,'available':available,'reason':reason,
                'overlays':views}

    async def select_for_decision(self,decision_id,profile_id):
        from app.services.decision_state import DecisionRecord
        async with self.master() as s:
            row=await s.get(DecisionRecord,decision_id)
            if (not row or row.status!='acting' or not row.lease_token or not row.lease_until
                    or row.lease_until<=datetime.utcnow()):raise ValueError('claimed acting decision required')
            from app.services.decision_v1 import DecisionSupervisor,validate_proposal
            context=await DecisionSupervisor(self.observer)._live(s,row,row.lease_token)
            if row.proposal['action']!='retry_profile' or row.proposal['arguments']!={'profile_id':profile_id}:
                raise ValueError('profile selection outside persisted proposal')
            choices={p['profile_id']:p for p in context['eligible_profiles']}
            if profile_id not in choices:raise ValueError('profile outside execution-approved catalog')
            choice=choices[profile_id]
            prior=await s.scalar(select(ProfileOverlay).where(ProfileOverlay.execution_id==row.execution_id,
                ProfileOverlay.node_key==row.node_key))
            if (prior and prior.profile_id==profile_id and prior.status=='approved' and prior.base_version==choice['base_version']
                    and prior.catalog_hash==choice['catalog_hash'] and any(h.get('decision_id')==decision_id
                        and h.get('proposal_hash')==digest(row.proposal) for h in prior.history)):
                return overlay_json(prior)
            validate_proposal(json.dumps(row.proposal),context)
            identity=dict(project_id=row.project_id,execution_id=row.execution_id,node_key=row.node_key,
                profile_id=profile_id,base_version=choice['base_version'],catalog_hash=choice['catalog_hash'],
                initiator='decision_supervisor',rationale=row.proposal['explanation'],_decision_id=decision_id)
        return await self.approve(**identity)

    async def approve(self,*,project_id,execution_id,node_key,profile_id,base_version,catalog_hash,initiator,rationale,_decision_id=None):
        synthetic_transport(provider=(active_catalog().get(profile_id) or {}).get('provider','kie'))
        if not initiator or len(rationale.strip())<3:raise ValueError('explicit approval rationale required')
        if catalog_hash!=globals()['catalog_hash']():raise ValueError('stale catalog hash')
        candidate=active_catalog().get(profile_id)
        if not candidate:raise ValueError('unknown or incompatible profile / provider')
        validate_candidate(candidate)
        async with self.master() as s:
            revision,graph=await self._base(s,project_id,execution_id,node_key)
            if revision!=base_version:raise ValueError('stale config base version')
            if _decision_id:
                from app.services.decision_state import DecisionRecord
                from app.services.decision_v1 import DecisionSupervisor,validate_proposal
                decision=await s.get(DecisionRecord,_decision_id)
                if (not decision or decision.status!='acting' or not decision.lease_token or not decision.lease_until
                        or decision.lease_until<=datetime.utcnow()
                        or (decision.project_id,decision.execution_id,decision.node_key)!=(project_id,execution_id,node_key)):
                    raise ValueError('claimed acting decision identity required')
                context=await DecisionSupervisor(self.observer)._live(s,decision,decision.lease_token,version=decision.context_version)
                validate_proposal(json.dumps(decision.proposal),context)
                if decision.proposal['action']!='retry_profile' or decision.proposal['arguments']!={'profile_id':profile_id}:
                    raise ValueError('persisted proposal does not select this profile')
                if profile_id not in [p['profile_id'] for p in context['eligible_profiles']]:
                    raise ValueError('profile outside execution-approved catalog')
            prior=await s.scalar(select(ProfileOverlay).where(ProfileOverlay.execution_id==execution_id,ProfileOverlay.node_key==node_key))
            if prior:
                if prior.profile_id==profile_id and prior.base_version==base_version and prior.status=='approved':return overlay_json(prior)
                raise ValueError('one immutable profile version per node/execution; no replacement or extra recovery')
            source,event=await _source_events(s,execution_id,project_id,node_key)
            if not event:raise ValueError('recorded source request required')
            # A profile only changes the request body; the node (not the profile) decides the provider.
            if _provider_mismatch(candidate,source_provider(event.payload)):
                raise ValueError('profile provider differs from failed attempt provider')
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
                status='approved',history=[{'kind':'decision_selected' if _decision_id else 'operator_approved',
                    'decision_id':_decision_id,'proposal_hash':digest(decision.proposal) if _decision_id else None,
                    'initiator':initiator,'at':datetime.utcnow().isoformat()}])
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

async def reservation(s,overlay_id,*,execution_id,project_id,node_key,source_attempt_id,revision,graph,body_hash,source_provider=None):
    row=await s.get(ProfileOverlay,overlay_id)
    synthetic_transport(provider=((row.candidate if row else None) or {}).get('provider','kie'))
    if not row or (row.execution_id,row.project_id,row.node_key,row.source_attempt_id)!=(execution_id,project_id,node_key,source_attempt_id):
        raise ValueError('profile outside execution/node/attempt scope')
    validate_candidate(row.candidate)
    if _provider_mismatch(row.candidate,source_provider):raise ValueError('profile provider differs from failed attempt provider')
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
        validate_candidate(row.candidate);synthetic_transport(provider=row.candidate['provider'])
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
    from app.services.observation import effective_text_provider
    if overlay['candidate']['provider']!=effective_text_provider():
        raise RuntimeError('profile provider differs from node provider; blocked before transport')
    synthetic_transport(kwargs.get('url'),(kwargs.get('headers') or {}).get('Authorization','').removeprefix('Bearer '),overlay['candidate']['provider'])
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
