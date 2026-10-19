import json,os
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime
from sqlalchemy import JSON,String
from sqlalchemy.orm import Mapped,mapped_column
from app.models import Base

class DecisionRuntimeBinding(Base):
    __tablename__='decision_runtime_bindings'
    execution_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    config_hash: Mapped[str]=mapped_column(String(64))
    config: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(default=datetime.utcnow)

VIBECODE_HOST='vibecode.moe'
LOOPBACK={'127.0.0.1','localhost','::1'}
APPROVED_DECISION_ROUTES={
    'gpt-kie':{'provider':'kie','route_kind':'kie_responses','path':'/codex/v1/responses'},
    'gpt-5.6-sol-vibecode':{'provider':'vibecode','route_kind':'vibecode_chat_completions','path':'/v1/chat/completions'},
}

READ_ACTIONS={'read_events','read_contract','human_review','wait','finish'}
ALL_ACTIONS=READ_ACTIONS|{'retry_same','retry_profile'}

def owner_budget_policy(config=None):
    cfg=runtime_config() if config is None else config
    policy=cfg.get('budget_policy',{'policy_id':'default-100','max_requests':100})
    if (not isinstance(policy,dict) or set(policy)!={'policy_id','max_requests'}
            or not isinstance(policy['policy_id'],str) or not 1<=len(policy['policy_id'])<=120
            or policy['policy_id'].strip()!=policy['policy_id']
            or type(policy['max_requests'])!=int or not 1<=policy['max_requests']<=100000):
        raise ValueError('invalid owner request budget policy')
    return dict(policy)

def incident_in_scope(config, *, execution_id, incident_id, node_key, source_attempt_id):
    scope=config.get('incident_scope')
    return scope is None or all(scope[k]==v for k,v in {
        'execution_id':execution_id,'incident_id':incident_id,'node_key':node_key,
        'source_attempt_id':source_attempt_id}.items())

def check_incident_scope(config, **identity):
    if not incident_in_scope(config, **identity):
        raise ValueError('incident scope does not authorize this execution/node/attempt')


def route_info(config):
    model=(config or {}).get('decision')
    return APPROVED_DECISION_ROUTES.get(model.get('catalog_id')) if model else None

def route_kind(config):
    info=route_info(config)
    return info['route_kind'] if info else None

def provider(config):
    info=route_info(config)
    return info['provider'] if info else None

def approved_vibecode_hosts():
    from app.settings import settings
    hosts={VIBECODE_HOST}|LOOPBACK
    relay=urlsplit(settings.vps_relay_base_url or '').hostname
    if relay:hosts.add(relay)
    return hosts


def validate_model(model):
    from app.services.text_llm_catalog import CATALOG,catalog_api_model
    from app.services.vibecode_catalog import find_model
    cid=model.get('catalog_id')
    route=APPROVED_DECISION_ROUTES.get(cid) if isinstance(cid,str) else None
    if route is None or not any(m['id']==cid for m in CATALOG):
        raise ValueError('unapproved provider/catalog; approved: Kie Responses, Vibecode chat/completions')
    mid=model.get('model_id','')
    if not isinstance(mid,str):raise ValueError('unknown production text model')
    if route['provider']=='vibecode':
        known=mid==catalog_api_model(cid,default='') and find_model(mid)
    else:
        alias=mid.replace('gpt-5-6-','gpt-5.6-')
        known=find_model(alias) or find_model(mid)
    if not known or known.get('kind')!='text' or 'synthetic' in mid:
        raise ValueError('unknown production text model')
    if route['provider']=='vibecode':
        if model.get('reasoning_effort') is not None:raise ValueError('vibecode decision requires reasoning_effort null')
    elif model.get('reasoning_effort') not in {'low','medium','high','xhigh'}:raise ValueError('unsupported reasoning effort')
    n=model.get('max_output_tokens')
    if type(n)!=int or not 1<=n<=16384:raise ValueError('invalid output token cap')


def validate_profile(candidate,mode):
    if isinstance(candidate,dict) and candidate.get('provider')=='vibecode':
        from app.services.profiles_v1 import validate_vibecode_profile
        return validate_vibecode_profile(candidate,mode)
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS
    fields={'model_id','provider','kind','channel','reasoning_effort','max_output_tokens','prompt_variant','synthetic_only'}
    if not isinstance(candidate,dict) or set(candidate)!=fields:raise ValueError('unknown profile parameters')
    c=candidate
    if (c['provider']!='kie' or c['kind']!='text' or c['channel']!='stable'
            or c['prompt_variant'] not in APPROVED_PROFILE_PROMPTS or type(c['synthetic_only'])!=bool):
        raise ValueError('incompatible registered profile')
    if mode=='synthetic':
        if (c['synthetic_only'] is not True or c['model_id']!='gpt-synthetic-alt'
                or c['reasoning_effort'] not in {'low','medium','high','xhigh'}
                or type(c['max_output_tokens'])!=int or not 1<=c['max_output_tokens']<=16384):
            raise ValueError('invalid bounded synthetic profile')
    else:
        if c['synthetic_only']:raise ValueError('synthetic profile not production')
        validate_model({'catalog_id':'gpt-kie',**c})


def validate_vibecode_policy(raw,endpoint):
    if raw['mode']!='production' or raw.get('model_transport')!='real' or raw.get('external_calls_enabled') is not True:
        raise ValueError('vibecode decision requires production mode with explicit real external authorization')
    if raw.get('continuation_allowed') is not False or raw.get('retrieval_allowed') is not False or raw.get('gateway_ack',False):
        raise ValueError('vibecode decision requires continuation_allowed=false, retrieval_allowed=false and no gateway_ack')
    if any(not isinstance(item,dict) or item.get('provider')!='vibecode' for item in raw['profiles'].values()):
        raise ValueError('vibecode decision profiles must be vibecode profiles')
    if 'retry_profile' in raw['allowed_actions'] and not raw['profiles']:
        from app.services.profiles_v1 import builtin_production_profiles
        if not builtin_production_profiles(raw):
            raise ValueError('retry_profile requires at least one approved vibecode profile')
    if endpoint.scheme not in {'https','http'} or endpoint.hostname not in approved_vibecode_hosts():
        raise ValueError('vibecode endpoint host is not approved')


def validate_config(raw):
    expected={'version','mode','online_enabled','endpoint','decision','profiles','allowed_actions'}
    optional={'model_transport','external_calls_enabled','retrieval_allowed','continuation_allowed','gateway_ack','incident_scope','media','budget_policy','builtin_profiles'}
    if not isinstance(raw,dict) or not expected<=set(raw) or set(raw)-expected-optional or raw['version']!=1 or raw['mode'] not in {'synthetic','production'} or type(raw['online_enabled'])!=bool:
        raise ValueError('invalid runtime configuration')
    owner_budget_policy(raw)
    if 'incident_scope' in raw:
        scope=raw['incident_scope']
        ids={'execution_id','incident_id','node_key','source_attempt_id'}
        if (not isinstance(scope,dict) or set(scope)!=ids|{'recovery_limit'}
                or type(scope.get('recovery_limit'))!=int or scope['recovery_limit']!=1
                or any(not isinstance(scope.get(k),str) or not 1<=len(scope[k])<=120
                       or scope[k].strip()!=scope[k] or any(c in scope[k] for c in '*?[]') for k in ids)):
            raise ValueError('invalid explicit incident scope / single recovery limit')
    if 'media' in raw:
        from app.services.media_jobs import validate_media_config
        validate_media_config(raw['media'], mode=raw['mode'])
    for key in ('external_calls_enabled','retrieval_allowed','continuation_allowed','gateway_ack','builtin_profiles'):
        if key in raw and type(raw[key])!=bool:raise ValueError('invalid decision network policy')
    transport=raw.get('model_transport')
    if transport is not None and transport not in {'fixture','real'}:raise ValueError('invalid model transport')
    if transport=='real' and (raw['mode']!='production' or raw.get('external_calls_enabled') is not True):
        raise ValueError('real model transport requires explicit production external authorization')
    if raw['mode']=='synthetic' and raw.get('external_calls_enabled',False):
        raise ValueError('synthetic mode cannot authorize external calls')
    if transport=='fixture' and raw.get('external_calls_enabled',False):
        raise ValueError('fixture transport cannot authorize external calls')
    if not isinstance(raw['profiles'],dict) or not isinstance(raw['allowed_actions'],list) or any(a not in ALL_ACTIONS for a in raw['allowed_actions']):
        raise ValueError('unknown profile/action policy')
    model=raw['decision']
    if model is None:
        if raw['online_enabled'] or raw['profiles'] or raw['allowed_actions']:raise ValueError('decision model not configured')
        return raw
    if set(model)!={'catalog_id','model_id','reasoning_effort','max_output_tokens','timeout_s'} or type(model['timeout_s'])!=int or not 30<=model['timeout_s']<=90:
        raise ValueError('unknown decision parameters')
    if raw['mode']=='production':validate_model(model)
    else:
        if model['model_id']!='gpt-5-6-sol' or model['catalog_id']!='gpt-kie':raise ValueError('synthetic acceptance uses compatible original text model')
        validate_model(model)
    route=route_info(raw)
    endpoint=urlsplit(raw['endpoint'] or '')
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path!=route['path']:raise ValueError('invalid approved route')
    if endpoint.hostname not in LOOPBACK and endpoint.scheme!='https':raise ValueError('external route requires HTTPS')
    if raw['mode']=='synthetic' and endpoint.hostname not in LOOPBACK:raise ValueError('synthetic configuration loopback only')
    if route['provider']=='vibecode':validate_vibecode_policy(raw,endpoint)
    for key,c in raw['profiles'].items():
        if not key or not isinstance(key,str):raise ValueError('unknown profile parameters')
        validate_profile(c,raw['mode'])
        if raw['mode']=='production':
            if 'synthetic' in key:raise ValueError('synthetic profile not production')
    return json.loads(json.dumps(raw))


def runtime_config():
    from app.settings import settings
    path=os.environ.get('ORCHESTRATOR_RUNTIME_CONFIG','').strip()
    if path:return validate_config(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    host=urlsplit(settings.gpt_base_url or '').hostname
    if host in {'127.0.0.1','localhost','::1'} and str(settings.gpt_api_key).startswith('synthetic'):
        return validate_config({'version':1,'mode':'synthetic','online_enabled':True,
            'endpoint':(settings.gpt_base_url or '').rstrip('/')+'/codex/v1/responses',
            'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
            'profiles':{},'allowed_actions':sorted(ALL_ACTIONS)})
    return {'version':1,'mode':'production','online_enabled':False,'endpoint':None,'decision':None,'profiles':{},'allowed_actions':[]}


def check_route(config,url):
    if not config['online_enabled'] or config['decision'] is None:raise ValueError('decision provider disabled / not configured')
    if url!=config['endpoint']:raise ValueError('route differs from approved configuration')
    if config['mode']=='synthetic':
        from app.settings import settings
        if not str(settings.gpt_api_key).startswith('synthetic'):raise ValueError('synthetic credential required')
    if provider(config)=='vibecode':
        from app.settings import settings
        if not (settings.vibecode_api_key or '').strip():raise ValueError('vibecode credential missing')


def effective_profiles(cfg):
    from app.services.profiles_v1 import effective_profiles as _effective
    return _effective(cfg)


def status():
    try:
        cfg=runtime_config()
        available=bool(cfg['decision'] and cfg['online_enabled'])
        return {'mode':cfg['mode'],'configured':available,'decision_model':cfg['decision'],
            'budget_policy':owner_budget_policy(cfg),
            'allowed_actions':cfg['allowed_actions'],'profile_count':len(effective_profiles(cfg)),
            'profile_source':'owner' if cfg['profiles'] else ('builtin' if effective_profiles(cfg) else 'none'),
            'route':cfg['endpoint'],'provider':provider(cfg),'route_kind':route_kind(cfg),'reason':None if available else 'Модель принятия решений не настроена или выключена'}
    except (ValueError,OSError,TypeError):return {'mode':'production','configured':False,'decision_model':None,'allowed_actions':[],
        'profile_count':0,'profile_source':'none','route':None,'provider':None,'route_kind':None,'reason':'Конфигурация модели недоступна или отклонена'}
