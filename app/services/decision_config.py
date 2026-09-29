"""Explicit local policy for the existing text client. No secrets or new provider."""
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

READ_ACTIONS={'read_events','read_contract','human_review','wait','finish'}
ALL_ACTIONS=READ_ACTIONS|{'retry_same','retry_profile'}


def validate_model(model):
    from app.services.text_llm_catalog import CATALOG
    from app.services.vibecode_catalog import find_model
    if model.get('catalog_id')!='gpt-kie' or not any(m['id']=='gpt-kie' for m in CATALOG):
        raise ValueError('unapproved provider/catalog; this adapter supports existing Kie Responses only')
    mid=model.get('model_id','')
    # Existing model catalog supplies compatibility, never operator permission.
    alias=mid.replace('gpt-5-6-','gpt-5.6-')
    known=find_model(alias) or find_model(mid)
    if not known or known.get('kind')!='text' or 'synthetic' in mid:
        raise ValueError('unknown production text model')
    if model.get('reasoning_effort') not in {'low','medium','high','xhigh'}:raise ValueError('unsupported reasoning effort')
    n=model.get('max_output_tokens')
    if type(n)!=int or not 1<=n<=16384:raise ValueError('invalid output token cap')


def validate_config(raw):
    expected={'version','mode','online_enabled','endpoint','decision','profiles','allowed_actions'}
    optional={'model_transport','external_calls_enabled','retrieval_allowed','continuation_allowed','gateway_ack'}
    if not isinstance(raw,dict) or not expected<=set(raw) or set(raw)-expected-optional or raw['version']!=1 or raw['mode'] not in {'synthetic','production'} or type(raw['online_enabled'])!=bool:
        raise ValueError('invalid runtime configuration')
    for key in ('external_calls_enabled','retrieval_allowed','continuation_allowed','gateway_ack'):
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
    endpoint=urlsplit(raw['endpoint'] or '')
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path!='/codex/v1/responses':raise ValueError('invalid approved route')
    if endpoint.hostname not in {'127.0.0.1','localhost','::1'} and endpoint.scheme!='https':raise ValueError('external route requires HTTPS')
    if raw['mode']=='synthetic' and endpoint.hostname not in {'127.0.0.1','localhost','::1'}:raise ValueError('synthetic configuration loopback only')
    for key,c in raw['profiles'].items():
        if not key or not isinstance(c,dict) or set(c)!={'model_id','provider','kind','channel','reasoning_effort','max_output_tokens','prompt_variant','synthetic_only'}:raise ValueError('unknown profile parameters')
        if c['provider']!='kie' or c['kind']!='text' or c['channel']!='stable' or c['prompt_variant']!='require-apply-ops-v1':raise ValueError('incompatible profile')
        if raw['mode']=='production':
            if c['synthetic_only'] or 'synthetic' in key:raise ValueError('synthetic profile not production')
            validate_model({'catalog_id':'gpt-kie',**c})
    return json.loads(json.dumps(raw))


def runtime_config():
    from app.settings import settings
    path=os.environ.get('ORCHESTRATOR_RUNTIME_CONFIG','').strip()
    if path:return validate_config(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    # Backward compatible isolated acceptance only; never infers online approval.
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


def status():
    try:
        cfg=runtime_config()
        available=bool(cfg['decision'] and cfg['online_enabled'])
        return {'mode':cfg['mode'],'configured':available,'decision_model':cfg['decision'],
            'allowed_actions':cfg['allowed_actions'],'profile_count':len(cfg['profiles']),
            'route':cfg['endpoint'],'reason':None if available else 'Модель принятия решений не настроена или выключена'}
    except (ValueError,OSError,TypeError):return {'mode':'production','configured':False,'decision_model':None,'allowed_actions':[],
        'profile_count':0,'route':None,'reason':'Конфигурация модели недоступна или отклонена'}
