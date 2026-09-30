"""Synthetic brain transport, original actions executor/planner/worker/DB."""
import asyncio
import json
import sqlite3
import time
import re
from sqlalchemy import select


def handler_for(fixtures,root,mode,*,base_handler=None):
    class Handler(base_handler or fixtures.FixtureHandler):
        requests=[]
        def do_POST(self):
            if self.headers.get('x-diagnostic-node')!='__decision__':return super().do_POST()
            assert self.path=='/codex/v1/responses'
            body=json.loads(self.rfile.read(int(self.headers.get('content-length','0'))))
            type(self).requests.append(body)
            (root/'brain-fixture-requests.json').write_text(json.dumps(type(self).requests,ensure_ascii=False,indent=2))
            strings=[]
            def walk(x):
                if isinstance(x,str):strings.append(x)
                elif isinstance(x,dict):
                    for v in x.values():walk(v)
                elif isinstance(x,list):
                    for v in x:walk(v)
            walk(body)
            contexts=[]
            for text in strings:
                # Existing Kie client flattens role messages into one input
                # string. Read the supplied JSON context inside that envelope.
                for opening in re.finditer(r'\{',text):
                    try:x,_=json.JSONDecoder().raw_decode(text[opening.start():])
                    except ValueError:continue
                    if isinstance(x,dict) and 'allowed_actions' in x:contexts.append(x)
            assert len(contexts)==1
            ctx=contexts[0];turn=len(type(self).requests)
            if turn>1:assert any('tool_result' in text for text in strings),'actual tool result missing from next request'
            action=['read_events','retry_same','finish'][turn-1]
            if mode=='decision-managed-review':action='human_review'
            arguments={'event_ids':ctx['evidence_ids'][:1]} if action=='read_events' else {}
            if mode.startswith('profiles-') and action=='retry_same':
                action='retry_profile';arguments={'overlay_id':ctx['approved_profiles'][0]['overlay_id']}
            proposal=dict(action=action,arguments=arguments,evidence_ids=ctx['evidence_ids'][:1],
                context_version=ctx['context_version'],explanation='synthetic bounded proposal from recorded runtime facts')
            answer=json.dumps(proposal,ensure_ascii=False)
            type(self).counts['__decision__']+=1
            rid=f'resp_synthetic_decision_{turn}'
            response={'id':rid,'status':'completed','output':[{'content':[{'type':'output_text','text':answer}]}],
                'usage':{'input_tokens':0,'output_tokens':0,'fixture':True}}
            events=[{'type':'response.created','response':{'id':rid}},
                {'type':'response.output_text.delta','delta':answer},{'type':'response.output_text.done','text':answer},
                {'type':'response.completed','response':response}]
            data=''.join('data: '+json.dumps(e,ensure_ascii=False)+'\n\n' for e in events).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
    return Handler


async def run(mode,client,fixtures,observer,root,result):
    from app.db import SessionLocal
    from app.services.observation import ExecutionControl,ObservationEvent
    from app.services.decision_state import DecisionRecord,DecisionTurn
    from app.services.actions_recovery import RecoveryAction
    base='http://127.0.0.1:18765/api/projects/2'
    async with SessionLocal() as s:
        scope=await s.scalar(select(ExecutionControl).where(ExecutionControl.project_id==2))
        execution=scope.execution_id
        assert not (await s.scalars(select(RecoveryAction))).all(),'checkpoint must precede supervisor recovery'
    if mode.startswith('profiles-'):
        from profiles_acceptance import approve
        await approve(client,base,execution,fixtures,root)
    response=await client.post(base+f'/observations/{execution}/decisions/control',json={
        'enabled':True,'rationale':'synthetic decision-v1 acceptance on copied genuine exhausted checkpoint'})
    response.raise_for_status()
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        response=await client.get(base+f'/observations/{execution}/decisions');response.raise_for_status()
        decisions=response.json()
        state=fixtures.snapshot()
        if decisions['records'] and decisions['records'][0]['status'] in {'human_review','done'}:
            if decisions['records'][0]['status']=='human_review' or all(n['status']=='done' for n in state['nodes'].values()):break
        await asyncio.sleep(.25)
    (root/'decision-state.json').write_text(json.dumps(decisions,ensure_ascii=False,indent=2))
    result.update(execution_id=execution,decisions=decisions,state=state,
        source_checkpoint='immutable genuine native exhaustion; no repeated native backoff',operator_starts=0,
        interventions=['copy historical synthetic checkpoint','enable synthetic decision mode through original API'])
    assert len(decisions['records'])==1,decisions
    decision=decisions['records'][0]
    if mode=='decision-managed-review':
        actions=(await client.get(base+f'/observations/{execution}/actions')).json()
        assert decision['status']=='human_review' and decision['model_steps']==1
        assert actions['held'] and actions['requests_used']==19 and actions['request_limit']==30
        assert not actions['recoveries'] and sum(fixtures.FixtureHandler.counts.values())==1
        result.update(accepted=True,actions=actions,brain_posts=1,worker_posts=0,real_api_calls=0,
            outcome='fixture chose human review without forced retry; no worker dispatched')
        return
    if mode in {'decision-bad','profiles-bad','decision-runtime-bad'}:
        assert decision['status']=='human_review' and decision['model_steps']==2,decision
        actions=(await client.get(base+f'/observations/{execution}/actions')).json()
        assert actions['held'] and actions['requests_used']==22,actions
        assert len(actions['recoveries'])==1 and actions['recoveries'][0]['status']=='human_review',actions
        before=dict(fixtures.FixtureHandler.counts)
        await asyncio.sleep(5)
        assert before==dict(fixtures.FixtureHandler.counts),'must not repeat failed recovery'
        result.update(accepted=True,actions=actions,brain_posts=2,worker_posts=2,real_api_calls=0,
            outcome='one recovery failed; original worker verification escalated and execution held')
        return
    assert decision['status']=='done',decision
    assert decision['model_steps']==3 and decision['verification'].get('verified'),decision
    assert all(n['status']=='done' for n in state['nodes'].values()),state
    assert sum(fixtures.FixtureHandler.counts.values())==8,fixtures.FixtureHandler.counts
    assert fixtures.FixtureHandler.counts['__decision__']==3
    actions=(await client.get(base+f'/observations/{execution}/actions')).json()
    assert actions['requests_used']==26,actions
    if mode=='decision-managed-pilot':
        assert actions['request_limit']==30
        from app.services.decision_config import DecisionRuntimeBinding
        async with SessionLocal() as s:
            binding=await s.get(DecisionRuntimeBinding,execution)
            assert binding.config['incident_scope']['incident_id']==decision['incident_id']
            result['bound_runtime_hash']=binding.config_hash
    assert len(actions['recoveries'])==1 and actions['recoveries'][0]['status']=='verified',actions
    run=(await client.get(base+'/run')).json()
    assert next(n for n in run['node_runs'] if n['node_key']=='n_script')['status']=='pending'
    async with SessionLocal() as s:
        rows=(await s.scalars(select(DecisionTurn).where(DecisionTurn.decision_id==decision['decision_id']))).all()
        assert any(t.role=='http_event' and t.payload.get('kind')=='model_stream_event' for t in rows),'brain SSE must persist'
        ticket=await s.get(RecoveryAction,decision['action_id'])
        events=(await s.scalars(select(ObservationEvent).where(ObservationEvent.execution_id==execution).order_by(ObservationEvent.id))).all()
        attempt=[e for e in events if e.attempt_id==ticket.new_attempt_id]
        assert sum(e.kind=='node_start' for e in attempt)==1
        assert sum(e.kind=='data_applied' for e in attempt)==1
        proof=next(e for e in attempt if e.kind=='recovery_verification' and e.payload.get('verified'))
        nxt=next(e for e in events if e.node_key==fixtures.NODES[1] and e.kind=='node_start')
        assert proof.id<nxt.id
    html=list((root/'free-run-data').rglob('*.html'))
    assert any(p.stat().st_size>100 for p in html),'original HTML report missing'
    result.update(accepted=True,actions=actions,html_files=[str(p.relative_to(root)) for p in html],
        new_worker_attempts=5,brain_posts=3,worker_posts=5,group_boundary={'n_script':'pending'},
        real_api_calls=0,synthetic_only=True)
