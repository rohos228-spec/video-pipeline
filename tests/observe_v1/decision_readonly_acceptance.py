"""Copied genuine exhaustion; original observer owns every Decision turn.

Only model HTTP is a fixture. No application/worker main, no replacement of
configure/begin/tick/context/live/tools. SQLite triggers forbid business writes.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx

ROOT=Path('/results');SOURCE=Path('/observe-source');DATA=ROOT/'free-run-data'
SERVICE={'observation_executions','observation_outbox','observation_events',
         'observation_incidents','observation_diagnosis','action_execution_controls',
         'action_v1_records','decision_execution_controls','decision_runtime_bindings',
         'decision_records','decision_turns'}
sys.path.insert(0,str(SOURCE))
from coverage_capture import begin
finish_coverage=begin(SOURCE)

def save(name,value):
    (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

def snapshot():
    result={}
    for path in DATA.rglob('*.db'):
        with sqlite3.connect(path) as db:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                if table in SERVICE:continue
                rows=db.execute('SELECT * FROM "'+table+'"').fetchall()
                result[str(path.relative_to(DATA))+':'+table]=sorted([list(r) for r in rows],key=str)
    return result

def prohibit_business_writes():
    for path in DATA.rglob('*.db'):
        with sqlite3.connect(path) as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                if table in SERVICE:continue
                for op in ('INSERT','UPDATE','DELETE'):
                    db.execute(f'''CREATE TRIGGER "readonly_{table}_{op}" BEFORE {op} ON "{table}"
                        BEGIN SELECT RAISE(ABORT,'diagnostic executor forbids business/profile/recovery write'); END''')
            if 'action_v1_records' in tables:
                db.execute('''CREATE TRIGGER readonly_action_audit BEFORE INSERT ON action_v1_records
                    WHEN NEW.action_kind NOT IN ('decision_mode','human_review')
                    BEGIN SELECT RAISE(ABORT,'diagnostic executor forbids modifying action'); END''')

def context_from(body):
    strings=[]
    def walk(x):
        if isinstance(x,str):strings.append(x)
        elif isinstance(x,dict):
            for v in x.values():walk(v)
        elif isinstance(x,list):
            for v in x:walk(v)
    walk(body);contexts=[]
    for text in strings:
        for pos in re.finditer(r'\{',text):
            try:value,_=json.JSONDecoder().raw_decode(text[pos.start():])
            except ValueError:continue
            if isinstance(value,dict) and 'allowed_actions' in value:contexts.append(value)
    assert len(contexts)==1
    return contexts[0],strings

class Fixture(BaseHTTPRequestHandler):
    bodies=[];responses=[];unexpected=[];sizes=[];request_hashes=[]
    def log_message(self,*args):pass
    def do_POST(self):
        try:
            assert self.path=='/codex/v1/responses'
            raw=self.rfile.read(int(self.headers['Content-Length']));body=json.loads(raw)
            type(self).request_hashes.append(hashlib.sha256(raw).hexdigest())
            type(self).sizes.append(len(raw))
            ctx,_=context_from(body)
            assert len(type(self).bodies)<3,'physical synthetic POST cap'
            assert set(ctx['allowed_actions'])=={'read_events','read_contract','wait','human_review'}
            turn=len(type(self).bodies)+1
            action=('read_events','read_contract','human_review')[turn-1]
            args={'event_ids':ctx['evidence_ids'][:2]} if action=='read_events' else {}
            proposal={'action':action,'arguments':args,'evidence_ids':ctx['evidence_ids'][:1],
                'context_version':ctx['context_version'],'explanation':'Synthetic read-only decision; request human assessment after reading actual tools.'}
            answer=json.dumps(proposal,ensure_ascii=False)
            type(self).bodies.append(body);type(self).responses.append(proposal)
            save('requests.json',type(self).bodies);save('fixture-responses.json',type(self).responses)
            response={'id':f'resp_readonly_{turn}','status':'completed',
                'output':[{'content':[{'type':'output_text','text':answer}]}],
                'usage':{'input_tokens':100+turn,'output_tokens':40+turn,'synthetic':True},
                'credits_consumed':0.01}
            events=[{'type':'response.created','response':{'id':response['id']}},
                {'type':'response.output_text.delta','delta':answer},
                {'type':'response.completed','response':response}]
            data=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except BaseException as exc:
            type(self).unexpected.append(type(exc).__name__+':'+str(exc));save('unexpected-http.json',type(self).unexpected)
            self.send_response(500);self.end_headers()
    def do_GET(self):
        type(self).unexpected.append('GET forbidden');self.send_response(403);self.end_headers()

def setup_gateway():
    spec=importlib.util.spec_from_file_location('diagnostic_gateway',SOURCE/'gateway.py')
    gateway=importlib.util.module_from_spec(spec);spec.loader.exec_module(gateway)
    gateway.KIE='http://127.0.0.1:18767'
    gateway.SOCKET=ROOT/'llm.sock';gateway.LEDGER=ROOT/'gateway.sqlite'
    gateway.MAX_REQUESTS=3;gateway.MAX_OUTPUT=1024;gateway.FIXTURE=False
    gateway.recover_pending()
    class Handler(gateway.Handler):
        def do_GET(self):self.reject(403,'test_no_retrieval')
    server=gateway.Server(str(gateway.SOCKET),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return gateway,server

async def run():
    assert Path('/.dockerenv').exists()
    ROOT.mkdir(exist_ok=True)
    shutil.copytree('/observe-denial-seed',DATA)
    provenance=json.loads(Path('/observe-checkpoint-provenance.json').read_text())
    save('checkpoint-provenance.json',provenance)
    assert provenance['synthetic_only'] and provenance['status']=='exhausted before supervisor action'
    cfg={'version':1,'mode':'synthetic','online_enabled':True,
         'endpoint':'http://127.0.0.1/codex/v1/responses',
         'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
         'profiles':{},'allowed_actions':['read_events','read_contract','wait','human_review'],
         'model_transport':'fixture','external_calls_enabled':False,'retrieval_allowed':False,
         'continuation_allowed':False,'gateway_ack':True}
    save('runtime-config.json',cfg)
    os.environ.update({'DATA_DIR':str(DATA),'SQLITE_PATH':str(DATA/'state.db'),
        'GPT_API_KEY':'synthetic-readonly-test-key','GPT_BASE_URL':'http://127.0.0.1',
        'GPT_API_MODE':'responses','GPT_CHAT_PATH':'/codex/v1/responses','GPT_MAX_RETRIES':'0',
        'ORCHESTRATOR_RUNTIME_CONFIG':str(ROOT/'runtime-config.json'),
        'TELEGRAM_ENABLED':'false','TELEGRAM_BOT_TOKEN':'','FLEET_ENABLED':'false'})
    sock=socket.socket();sock.settimeout(1)
    try:assert sock.connect_ex(('192.0.2.1',9))!=0
    finally:sock.close()
    from sqlalchemy import select
    from app.db import engine,SessionLocal
    from app.models import Project
    from app.services import observation,gpt_api
    from app.services.actions_v1 import ActionExecutionControl,ActionSupervisor
    from app.services.decision_v1 import get_decider,validate_proposal
    from app.services.decision_state import DecisionRecord,DecisionTurn
    from app.services.actions_recovery import RecoveryAction
    async with engine.begin() as connection:await observation.migrate_observation_schema(connection)
    observer=observation.get_observer();await observer.reload()
    execution=provenance['execution_id']
    async with SessionLocal() as s:
        control=await s.get(ActionExecutionControl,execution)
        assert control.requests_used==sum(provenance['fixture_counts'].values())==18
        assert not control.held and control.enabled
        assert not (await s.scalars(select(RecoveryAction))).all()
        initial_used=control.requests_used;initial_limit=control.request_limit
        control.request_limit=initial_used+3
        await s.commit()
    await observer.reload()
    decider=get_decider(observer)
    await decider.configure(project_id=2,execution_id=execution,enabled=True,
        initiator='synthetic-readonly-operator',rationale='Copied genuine exhausted checkpoint; only read tools and human review; three synthetic steps')
    prohibit_business_writes()
    before=snapshot();save('business-before.json',before)
    with sqlite3.connect(DATA/'state.db') as db:
        old_audit=db.execute('SELECT count(*) FROM action_v1_records').fetchone()[0]
        old_starts=db.execute("SELECT count(*) FROM observation_events WHERE kind='node_start'").fetchone()[0]
    calls=set()
    def profile(frame,event,arg):
        if event=='call' and frame.f_code.co_filename.endswith('/app/services/decision_v1.py'):
            calls.add(frame.f_code.co_name)
    sys.setprofile(profile)
    server=ThreadingHTTPServer(('127.0.0.1',18767),Fixture)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    gateway,gateway_server=setup_gateway()
    def socket_client(**kwargs):
        kwargs['transport']=httpx.AsyncHTTPTransport(uds=str(gateway.SOCKET),retries=0)
        kwargs['trust_env']=False
        return httpx.AsyncClient(**kwargs)
    transport_patch=patch.object(gpt_api,'_async_client',socket_client)
    transport_patch.start()
    task=asyncio.create_task(observer.loop())
    try:
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            async with SessionLocal() as s:
                records=(await s.scalars(select(DecisionRecord))).all()
                if records and records[0].status=='human_review':break
            await asyncio.sleep(.1)
        else:raise AssertionError('Original observer loop did not complete three read-only turns')
        await asyncio.sleep(2.2)
        assert len(Fixture.bodies)==3 and not Fixture.unexpected
        async with SessionLocal() as s:
            rows=(await s.scalars(select(DecisionRecord))).all();assert len(rows)==1
            record=rows[0];assert record.model_steps==3 and record.status=='human_review' and record.action_id is None
            turns=(await s.scalars(select(DecisionTurn).order_by(DecisionTurn.id))).all()
            control=await s.get(ActionExecutionControl,execution)
            assert control.requests_used==initial_used+3 and control.held
            assert not (await s.scalars(select(RecoveryAction))).all()
            context=await decider.context(record.decision_id)
        tools=[t.payload for t in turns if t.role=='tool' and t.payload.get('action') in {'read_events','read_contract'}]
        assert [t['action'] for t in tools]==['read_events','read_contract']
        starts=[t for t in turns if t.role=='http_event' and t.payload.get('kind')=='decision_model_start']
        ends=[t for t in turns if t.role=='http_event' and t.payload.get('kind')=='decision_model_end']
        assert len(starts)==len(ends)==3
        assert all(t.payload['metadata'].get('complete') and t.payload['metadata'].get('usage') is not None for t in ends)
        acks=[t.payload for t in turns if t.role=='http_event' and t.payload.get('kind')=='decision_gateway_ack']
        expected_ids=[f'resp_readonly_{n}' for n in range(1,4)]
        assert [(a['metadata']['response_id'],a['metadata']['stage']) for a in acks]==[
            (response_id,stage) for response_id in expected_ids for stage in ('received','accepted')]
        with sqlite3.connect(gateway.LEDGER) as db:
            gateway_rows=db.execute('''SELECT ordinal,state,method,body_sha256,response_id,
                upstream_complete,downstream_complete,application_received_at,application_accepted_at,
                terminal_body_path,accumulated_body_path,usage_json,event_count,credits
                FROM requests ORDER BY ordinal''').fetchall()
            batches=db.execute('''SELECT request_ordinal,body_path,sha256 FROM event_batches
                ORDER BY request_ordinal,batch_index''').fetchall()
        assert len(gateway_rows)==len(Fixture.request_hashes)==3 and len(batches)>=3
        assert [r[2] for r in gateway_rows]==['POST']*3
        assert [r[3] for r in gateway_rows]==Fixture.request_hashes==[
            s.payload['metadata']['request_body_sha256'] for s in starts]
        assert [r[4] for r in gateway_rows]==expected_ids
        assert all(r[1]=='complete' and r[5]==r[6]==1 and r[7] and r[8] and r[12]>=3 for r in gateway_rows)
        assert all(r[13]==0.01 for r in gateway_rows)
        for row in gateway_rows:
            terminal=json.loads((gateway.BODY_ROOT/row[9]).read_text())
            accumulated=json.loads((gateway.BODY_ROOT/row[10]).read_text())
            usage=json.loads(row[11])
            assert terminal['type']=='response.completed' and terminal['response']['id']==row[4]
            assert usage['output_tokens']==40+row[0]
            assert json.loads(accumulated['text'])['action']==('read_events','read_contract','human_review')[row[0]-1]
            assert ends[row[0]-1].payload['metadata']['usage']['output_tokens']==usage['output_tokens']
        assert all(hashlib.sha256((gateway.BODY_ROOT/path).read_bytes()).hexdigest()==sha
                   for _,path,sha in batches)
        for index,tool in enumerate(tools,1):
            _,strings=context_from(Fixture.bodies[index])
            decoded=[]
            for text in strings:
                for pos in re.finditer(r'\{',text):
                    try:value,_=json.JSONDecoder().raw_decode(text[pos.start():])
                    except ValueError:continue
                    if isinstance(value,dict) and value.get('tool_result')==tool:decoded.append(value)
            assert decoded,'Exact actual tool result absent from next HTTP model input'
        forbidden=[]
        for action in ('retry_same','retry_profile','start','resume','finish','shell'):
            proposal={**Fixture.responses[-1],'action':action,'arguments':{}}
            try:validate_proposal(json.dumps(proposal),context)
            except ValueError:forbidden.append(action)
            else:raise AssertionError('Forbidden action accepted '+action)
        try:
            await ActionSupervisor(SessionLocal).recover_node(project_id=2,execution_id=execution,
                incident_id=record.incident_id,node_key=record.node_key,idempotency_key='forbidden-direct-recovery',
                initiator='synthetic-readonly-operator',rationale='Verify executor refusal',decision_id=record.decision_id)
        except ValueError as exc:executor_denial=str(exc)
        else:raise AssertionError('Direct recovery executor was permitted')
        with sqlite3.connect(DATA/'state.db') as db:
            try:db.execute('UPDATE node_runs SET attempts=attempts+1')
            except sqlite3.IntegrityError as exc:database_denial=str(exc)
            else:raise AssertionError('Business write guard bypass')
            db.rollback()
        after=snapshot();save('business-after.json',after);assert before==after
        with sqlite3.connect(DATA/'state.db') as db:
            assert old_starts==db.execute("SELECT count(*) FROM observation_events WHERE kind='node_start'").fetchone()[0]
            assert db.execute('SELECT count(*) FROM action_v1_records').fetchone()[0]==old_audit+1
        assert {'begin','tick','step','read_events','read_contract','_live','_context'}<=calls,calls
        from app.services.actions_v1 import ActionRecord
        async with SessionLocal() as s:
            audit=(await s.scalars(select(ActionRecord))).all()
            final_control=await s.get(ActionExecutionControl,execution)
            assert final_control.requests_used==21 and final_control.held
        save('turns.json',[{'id':t.id,'role':t.role,'turn_key':t.turn_key,'payload':t.payload} for t in turns])
        save('result.json',{'accepted':True,'real_api_calls':0,'network':'none','execution_id':execution,
            'case':'copied genuine synthetic exhaustion, not historical QC','source_attempt_id':record.source_attempt_id,
            'source_counters_preserved':True,'requests_before':initial_used,'requests_after':21,'original_limit':initial_limit,
            'model_steps':3,'synthetic_posts':3,'status':record.status,'held':True,
            'actual_tools_in_next_requests':True,'business_tables_unchanged':True,
            'worker_started':False,'actions_dispatched':0,'recovery_records':0,
            'forbidden_proposals_denied':forbidden,'executor_denial':executor_denial,'database_denial':database_denial,
            'runtime_calls':sorted(calls),'request_bytes':Fixture.sizes,'persisted_model_reservations':len(starts),'persisted_model_responses_with_usage':len(ends),
            'gateway_response_ids':expected_ids,'gateway_request_hashes_match':True,
            'gateway_terminal_usage_and_ack':True,'gateway_event_batches':len(batches),
            'request_limit_remaining':0,'next_turn_authorized':False,
            'audit':[{'kind':a.action_kind,'status':a.status,'idempotency_key':a.idempotency_key} for a in audit]})
    finally:
        sys.setprofile(None);task.cancel();await asyncio.gather(task,return_exceptions=True)
        transport_patch.stop()
        gateway_server.shutdown();gateway_server.server_close()
        server.shutdown();server.server_close();await engine.dispose()
    manifest=json.loads((SOURCE/'source-manifest.json').read_text())
    loaded={'gateway.py':hashlib.sha256((SOURCE/'gateway.py').read_bytes()).hexdigest(),
        'app/services/gpt_api.py':hashlib.sha256(Path(gpt_api.__file__).read_bytes()).hexdigest(),
        'app/services/decision_v1.py':hashlib.sha256(Path(sys.modules['app.services.decision_v1'].__file__).read_bytes()).hexdigest()}
    assert all(manifest[path]==sha for path,sha in loaded.items())
    save('loaded-source-hashes.json',loaded)

if __name__=='__main__':
    code=0
    try:asyncio.run(run())
    except BaseException as exc:
        code=1;save('failure.json',{'type':type(exc).__name__,'reason':str(exc)})
        import traceback;traceback.print_exc()
    finally:
        finish_coverage(ROOT)
        manifest=json.loads((SOURCE/'source-manifest.json').read_text());loaded=[]
        for name,module in list(sys.modules.items()):
            filename=getattr(module,'__file__',None)
            if not filename:continue
            path=Path(filename)
            if not path.is_relative_to(SOURCE) or path.suffix!='.py':continue
            rel=path.relative_to(SOURCE).as_posix();sha=hashlib.sha256(path.read_bytes()).hexdigest()
            assert manifest[rel]==sha
            loaded.append({'module':name,'path':rel,'sha256':sha})
        save('loaded-modules.json',loaded)
    raise SystemExit(code)
