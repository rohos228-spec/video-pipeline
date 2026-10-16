import hashlib,json,sqlite3

async def approve(client,base,execution,fixtures,root):
    url=base+f'/observations/{execution}/profiles'
    state=(await client.get(url,params={'node_key':fixtures.NODES[0]})).json()
    (root/'profile-base-state.json').write_text(json.dumps(state,indent=2))
    response=await client.post(url+'/approve',json={'node_key':fixtures.NODES[0],
        'profile_id':'synthetic-json-v1','base_version':state['base_version'],'catalog_hash':state['catalog_hash'],
        'rationale':'explicit synthetic profile approval for exactly one next attempt'})
    (root/'profile-approval-response.json').write_text(json.dumps(response.json(),indent=2))
    response.raise_for_status()
    (root/'profile-approval.json').write_text(json.dumps(response.json(),indent=2))

async def verify(client,base,execution,fixtures,root,result):
    from app.db import SessionLocal
    from app.services.observation import ObservationEvent
    from sqlalchemy import select
    bodies=json.loads((root/'actual-worker-requests.json').read_text())
    target=[r for r in bodies if r['node_key']==fixtures.NODES[0]]
    assert len(target)==(2 if result['mode']=='profiles-bad' else 1)
    for row in target:
        body=row['body']
        assert body['model']=='gpt-synthetic-alt',body['model']
        assert body['reasoning']=={'effort':'medium'} and body['max_output_tokens']==2048
        assert '[Approved synthetic profile: require-apply-ops-v1]' in json.dumps(body['input'])
    others=[r for r in bodies if r['node_key']!=fixtures.NODES[0]]
    assert all(r['body']['model']=='gpt-5-6-sol' and '[Approved synthetic profile:' not in json.dumps(r['body']['input']) for r in others)
    async with SessionLocal() as s:
        facts=(await s.scalars(select(ObservationEvent).where(ObservationEvent.execution_id==execution,
            ObservationEvent.kind=='profile_effective_context'))).all()
        assert len(facts)==1 and facts[0].node_key==fixtures.NODES[0]
    url=base+f'/observations/{execution}/profiles'
    state=(await client.get(url,params={'node_key':fixtures.NODES[0]})).json()
    overlay=state['overlays'][0]
    assert overlay['attempt_id']==target[0]['attempt_id'] and overlay['status'] in {'applied','failed'}
    assert result['actions']['recoveries'][0]['preconditions']['action_type']=='retry_profile'
    assert result['actions']['recoveries'][0]['preconditions']['profile_overlay_id']==overlay['overlay_id']
    before=fixtures.snapshot();used=result['actions']['requests_used']
    response=await client.post(url+f"/{overlay['overlay_id']}/cancel",json={'rationale':'return configuration only after terminal attempt'})
    response.raise_for_status();cancel=response.json()
    assert cancel['status']=='cancelled' and cancel['data_rollback'] is False and cancel['budget_reset'] is False
    assert before==fixtures.snapshot()
    actions=(await client.get(base+f'/observations/{execution}/actions')).json()
    assert actions['requests_used']==used and len(actions['recoveries'])==1
    state=(await client.get(url,params={'node_key':fixtures.NODES[0]})).json()
    (root/'profile-state.json').write_text(json.dumps(state,indent=2))
    result.update(profile_proof={'target_model':'gpt-synthetic-alt','target_posts':len(target),
        'other_nodes_base_model':True,'effective_context_events':1,'scope_exact_attempt':True,
        'config_cancelled_without_data_or_budget_rollback':True,'profile':overlay,'cancel':cancel})
