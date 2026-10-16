import asyncio,hashlib,json,re,time
from sqlalchemy import select

async def run(client,fixtures,observer,root,result):
    from app.db import SessionLocal
    from app.services.observation import ObservationEvent
    from app.services.actions_recovery import RecoveryAction
    from app.services.decision_state import DecisionTurn
    base='http://127.0.0.1:18765/api/projects/2'
    page=await client.get('http://127.0.0.1:18765/');page.raise_for_status()
    assets=[{'path':'web/out/index.html','sha256':hashlib.sha256(page.content).hexdigest()}]
    for path in sorted(set(re.findall(r'<script[^>]+src="([^"]+)"',page.text))):
        response=await client.get('http://127.0.0.1:18765'+path);response.raise_for_status()
        assets.append({'path':'web/out/'+path.lstrip('/'),'sha256':hashlib.sha256(response.content).hexdigest()})
    (root/'live-served-frontend.json').write_text(json.dumps(assets,indent=2))
    (root/'browser-ready.json').write_text(json.dumps({'project_id':2,'nodes':list(fixtures.NODES),'control':'browser only','application':'live'}))
    execution=None;timeline=[];last=None;deadline=time.monotonic()+850
    while time.monotonic()<deadline:
        scopes=(await client.get(base+'/observations')).json()
        active=[s for s in scopes if s['observer_state']=='active']
        if active:
            assert len(active)==1,'one browser execution only'
            execution=active[0]['execution_id']
            actions=(await client.get(base+f'/observations/{execution}/actions')).json()
            decisions=(await client.get(base+f'/observations/{execution}/decisions')).json()
            diagnosis=(await client.get(base+f'/observations/{execution}/diagnoses')).json()
            state=fixtures.snapshot()
            current={'nodes':state['nodes'],'requests_used':actions['requests_used'],'held':actions['held'],
                'recoveries':[{k:r.get(k) for k in ('action_id','new_attempt_id','status')} for r in actions['recoveries']],
                'decisions':[{k:d.get(k) for k in ('decision_id','status','model_steps')} for d in decisions['records']]}
            if current!=last:
                timeline.append({'time':time.time(),**current});last=current
                (root/'live-timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2))
            if actions['recoveries'] and actions['recoveries'][0]['status']=='claimed':
                (root/'browser-recovery-running.json').write_text(json.dumps(current,indent=2))
            if decisions['records'] and decisions['records'][0]['status']=='done' and all(n['status']=='done' for n in state['nodes'].values()):break
        await asyncio.sleep(.5)
    assert execution,'browser did not enable execution'
    assert len(decisions['records'])==1 and decisions['records'][0]['status']=='done',decisions
    decision=decisions['records'][0]
    assert decision['model_steps']==3 and decision['verification'].get('verified'),decision
    assert len(actions['recoveries'])==1 and actions['recoveries'][0]['status']=='verified',actions
    assert actions['requests_used']==26,actions
    assert len([r for r in actions['records'] if r['action_kind']=='start_node'])==1
    assert all(n['status']=='done' for n in state['nodes'].values()),state
    assert sum(fixtures.FixtureHandler.counts.values())==26,fixtures.FixtureHandler.counts
    assert fixtures.FixtureHandler.counts['__decision__']==3
    run=(await client.get(base+'/run')).json()
    assert next(n for n in run['node_runs'] if n['node_key']=='n_script')['status']=='pending'
    async with SessionLocal() as s:
        turns=(await s.scalars(select(DecisionTurn).where(DecisionTurn.decision_id==decision['decision_id']))).all()
        assert any(t.role=='http_event' and t.payload.get('kind')=='model_stream_event' for t in turns)
        ticket=await s.get(RecoveryAction,decision['action_id'])
        events=(await s.scalars(select(ObservationEvent).where(ObservationEvent.execution_id==execution).order_by(ObservationEvent.id))).all()
        attempt=[e for e in events if e.attempt_id==ticket.new_attempt_id]
        assert sum(e.kind=='node_start' for e in attempt)==1
        assert sum(e.kind=='data_applied' for e in attempt)==1
        proof=next(e for e in attempt if e.kind=='recovery_verification' and e.payload.get('verified'))
        nxt=next(e for e in events if e.node_key==fixtures.NODES[1] and e.kind=='node_start')
        assert proof.id<nxt.id
        assert not [e for e in events if e.project_id!=2]
        (root/'live-events.json').write_text(json.dumps([{'id':e.id,'kind':e.kind,'node_key':e.node_key,
            'attempt_id':e.attempt_id,'request_id':e.request_id,'payload':e.payload} for e in events],ensure_ascii=False,indent=2,default=str))
    html=list((root/'free-run-data').rglob('*.html'))
    assert any(p.stat().st_size>100 for p in html)
    result.update(execution_id=execution,accepted=True,decisions=decisions,actions=actions,state=state,
        diagnoses=diagnosis,html_files=[str(p.relative_to(root)) for p in html],brain_posts=3,worker_posts=23,
        group_boundary={'n_script':'pending'},real_api_calls=0,synthetic_only=True,
        control='browser/API start only; original observer automatic decision/actions/worker/verify',
        interventions=['clean existing synthetic seed','replace external HTTP transport only','browser enables scope/permission/decision and starts first node'])
    (root/'group-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    (root/'browser-acceptance-ready').write_text('live application still running')
    deadline=time.monotonic()+300
    while not (root/'browser-finish-requested').exists() and time.monotonic()<deadline:await asyncio.sleep(.2)
    assert (root/'browser-finish-requested').exists(),'capture live UI and finish explicitly'
