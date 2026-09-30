"""Existing group harness: native plan/script -> DB -> preserved HITL boundary."""
import asyncio
import hashlib
import json
import os
import sqlite3
import time

TEXT=('Синтетический текст: человек находит папку, читает записку и возвращает её на стол. '*6).strip()

def prepare(fixtures,root,mode):
    typ='split' if mode.startswith('launch-split') else ('plan' if mode=='launch-plan' else 'script')
    key='native-'+typ+'-A';outside='outside-native-text-Z'
    nodes=[{'id':key,'type':typ,'position':{'x':100,'y':100},
            'data':{'label':'Synthetic native '+typ,'modelId':'gpt-kie'}},
        {'id':outside,'type':'script' if typ=='plan' else 'plan','position':{'x':500,'y':100},
            'data':{'label':'Outside scope'}}]
    edges=[{'id':'native-text-edge','source':key,'target':outside,'data':{'kind':'after'}}]
    for path in (fixtures.DATA/'state.db',fixtures.DB):
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT count(*) FROM frames WHERE project_id=2').fetchone()[0]==0
            meta=json.loads(db.execute('SELECT meta FROM projects WHERE id=2').fetchone()[0])
            meta['canvas_graph'].update(nodes=nodes,edges=edges)
            meta.pop('active_excel_gpt_node_key',None);meta.pop('step_failure',None)
            db.execute("UPDATE projects SET status='new',general_plan=?,script_text=?,meta=? WHERE id=2",
                (None if typ=='plan' else TEXT,TEXT if typ=='split' else None,json.dumps(meta)))
            run=db.execute('SELECT id FROM workflow_runs WHERE project_id=2 ORDER BY id DESC LIMIT 1').fetchone()[0]
            db.execute('UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?',(json.dumps(nodes),json.dumps(edges),run))
            db.execute('DELETE FROM node_runs WHERE workflow_run_id=?',(run,))
            for node in nodes:
                db.execute("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts,meta,created_at,updated_at) VALUES(?,?,?,'pending',0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (run,node['id'],node['type']))
    cfg={'version':1,'mode':'synthetic','online_enabled':True,
        'endpoint':'http://127.0.0.1:18766/codex/v1/responses',
        'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
        'profiles':{},'allowed_actions':['read_events','read_contract','wait','human_review']}
    path=root/'synthetic-project-text-runtime.json';path.write_text(json.dumps(cfg))
    os.environ['ORCHESTRATOR_RUNTIME_CONFIG']=str(path)
    if typ=='split':
        # The original native executor intentionally prefers an existing usable
        # file. Initialize the copied synthetic file consistently with its DB.
        (fixtures.DB.parent/'voiceover.txt').write_text(TEXT,encoding='utf-8')
    field='общий_план' if typ=='plan' else 'закадровый_текст'
    words=TEXT.split();mid=len(words)//2
    vo_parts=[' '.join(words[:mid]),' '.join(words[mid:])]
    if mode=='launch-split-loss':vo_parts[1]='Потерянный хвост.'
    answer=json.dumps({'ops':[{'target':'replace_frames','frames':[{'закадр':vo} for vo in vo_parts]}]}
        if typ=='split' else {'ops':[{'target':'project','fields':{field:TEXT}}]},ensure_ascii=False)
    fixtures.NODES=(key,outside)
    fixtures.synthetic_answer=lambda node,attempt:answer
    prepared={'node_type':typ,'node_key':key,'outside':outside,
        'output_field':'general_plan' if typ=='plan' else 'script_text','expect_split_loss':mode=='launch-split-loss',
        'interventions':['fresh pending graph before startup; no successful status manufactured',
            'script scenario has a synthetic initial general_plan, empty script_text and no frames',
            'explicit compatible gpt-kie choice before startup; loopback transport only, native prompts unchanged',
            'no HITL approval, next node start or manual recovery']}
    (root/'project-text-prepared.json').write_text(json.dumps(prepared,indent=2))
    return prepared

async def run(client,fixtures,observer,root,result,prepared,*,browser=False):
    base='http://127.0.0.1:18765/api/projects/2'
    options=(await client.get(base+'/observations/launch-options')).json()
    if browser:
        (root/'browser-ready.json').write_text(json.dumps({'project_id':2,'state':'awaiting one native UI launch'}))
        deadline=time.monotonic()+300
        while (not options['latest_launch'] or options['latest_launch']['status']=='dispatching') and time.monotonic()<deadline:
            await asyncio.sleep(.5)
            response=await client.get(base+'/observations/launch-options');response.raise_for_status()
            options=response.json()
        launch=options['latest_launch']
        assert launch and launch['status']=='submitted','native UI launch not submitted'
        assert launch['policy']['node_keys']==[prepared['node_key']]
        assert set(launch['policy']['allowed_actions'])=={'read_events','read_contract','wait','human_review'},launch['policy']
        result['launch_origin']='browser UI; runner issued no launch POST'
    else:
        response=await client.post(base+'/observations/launch',json={
            'workflow_run_id':options['workflow_run_id'],'graph_version':options['graph_version'],
            'node_keys':[prepared['node_key']],'strategies':['wait','human_review'],
            'request_limit':8,'rationale':'native project text with original HITL; no approval/recovery',
            'idempotency_key':'native-project-text-one-launch'})
        assert response.status_code==201,(response.status_code,response.text)
        launch=response.json()
    execution=launch['execution_id']
    result.update(execution_id=execution,managed_launch=launch,operator_starts=0,
        interventions=prepared['interventions'],scope_boundary='original managed gate')
    if prepared['node_type']=='split':
        assert not browser,'existing browser case is native plan only'
        await run_split(client,fixtures,observer,root,result,prepared,prefix=base+'/observations/'+execution)
        return
    deadline=time.monotonic()+120
    state={}
    while time.monotonic()<deadline:
        with sqlite3.connect(fixtures.DB) as db:
            states=dict(db.execute('SELECT node_key,status FROM node_runs').fetchall())
            output=db.execute('SELECT '+prepared['output_field']+' FROM projects WHERE id=2').fetchone()[0]
            hitl=db.execute('SELECT kind,decision FROM hitl_requests WHERE project_id=2').fetchall()
            state={'nodes':states,'output_characters':len(output or ''),'hitl':hitl}
        if states.get(prepared['node_key'])=='failed' or (output==TEXT and hitl and states.get(prepared['node_key']) in {'done','waiting_hitl'}):break
        await asyncio.sleep(.25)
    result['native_state']=state
    await observer.catch_up()
    prefix=base+'/observations/'+execution
    history=[];after=0
    while True:
        page=(await client.get(prefix+'/events',params={'after':after,'limit':500})).json()
        history.extend(page['events'])
        if not page['has_more']:break
        after=page['next_cursor']
    (root/'api-history.json').write_text(json.dumps({'events':history},ensure_ascii=False,indent=2))
    assert output==TEXT,'native executor did not apply fixture output'
    requests=json.loads((root/'actual-worker-requests.json').read_text())
    assert len(requests)==1 and requests[0]['node_key']==prepared['node_key']
    assert requests[0]['body']['model']=='gpt-5-6-sol','registered native Kie choice must use exact API model'
    assert len(hitl)==1 and hitl[0][1]=='pending','mandatory HITL must remain pending'
    assert states[prepared['outside']]=='pending'
    assert states[prepared['node_key']] in {'done','waiting_hitl'},state
    facts=[e for e in history if e['node_key']==prepared['node_key']]
    assert len([e for e in facts if e['kind']=='node_start'])==1
    proofs=[e for e in facts if e['kind']=='node_result_verification']
    assert len(proofs)==1 and proofs[0]['payload']['status']=='passed'
    inputs=[e for e in facts if e['kind']=='node_input']
    parsed=[e for e in facts if e['kind']=='parse_result']
    assert len(inputs)==1 and inputs[0]['payload'].get('body',{}).get('sha256')
    assert len(parsed)==1 and parsed[0]['request_id']
    assert parsed[0]['payload']['schema_valid'] is True
    assert parsed[0]['payload']['sha256']==proofs[0]['payload']['sha256']
    assert any(e['kind']=='runtime_check' and e['payload']['check']=='native_'+prepared['node_type']+'_text_requirement'
        and e['payload']['status']=='passed' for e in facts)
    # Current Observe stores these facts in the same business transaction:
    # delivery from its outbox is commit proof (rollback produces no delivery).
    applied=[e for e in facts if e['kind']=='data_applied' and e.get('db_scope')=='project'
        and e['payload'].get('transactional') is True and e['payload'].get('result',{}).get('ok') is True]
    assert len(applied)==1,'one committed native apply required'
    # Native project.db is the content source of truth. The original master
    # projection deliberately copies runtime, not general_plan/script_text.
    detail=(await client.get(base)).json()
    assert detail[prepared['output_field']]==output,'API must expose the authoritative output'
    assert proofs[0]['payload']['sha256']==hashlib.sha256(output.encode()).hexdigest()
    with sqlite3.connect(fixtures.DATA/'state.db') as db:
        master=db.execute('SELECT status,'+prepared['output_field']+' FROM projects WHERE id=2').fetchone()
        master_nodes=dict(db.execute('SELECT node_key,status FROM node_runs').fetchall())
    assert master_nodes[prepared['node_key']]==states[prepared['node_key']]
    assert master_nodes[prepared['outside']]=='pending'
    result['db_authority']={'content':'project.db','api_content_matches':True,
        'master_projection':'status/auto_mode/runtime meta/NodeRuns only',
        'master_status':master[0],'master_content_characters':len(master[1] or ''),
        'master_content_matches':master[1]==output}
    context=(await client.get(prefix+'/context')).json()
    wait=context['wait_reasons'][prepared['node_key']]
    assert wait['decision']=='pending' and wait['kind']=='approve_'+prepared['node_type']
    assert wait['event_id'] in {e['event_id'] for e in facts if e['kind']=='human_approval_pending'}
    result['approval_context']=wait
    actions=(await client.get(prefix+'/actions')).json()
    assert actions['requests_used']==1 and not actions['recoveries']
    decisions=(await client.get(prefix+'/decisions')).json()
    assert not decisions['records'],'healthy output/HITL needs no model decision'
    before=dict(fixtures.FixtureHandler.counts)
    await asyncio.sleep(12)
    assert before==dict(fixtures.FixtureHandler.counts)=={prepared['node_key']:1}
    with sqlite3.connect(fixtures.DATA/'state.db') as db:
        assert db.execute('SELECT status FROM projects WHERE id=1').fetchone()[0]=='new'
    result.update(accepted=True,one_user_launch=True,actions=actions,
        verification=proofs[0]['payload'],mandatory_human_approval=True,
        business_output_sha256=hashlib.sha256(output.encode()).hexdigest(),
        semantic_quality_verified=False)


async def run_split(client,fixtures,observer,root,result,prepared,*,prefix):
    key=prepared['node_key'];bad=prepared['expect_split_loss']
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        with sqlite3.connect(fixtures.DB) as db:
            states=dict(db.execute('SELECT node_key,status FROM node_runs').fetchall())
            rows=db.execute('SELECT uuid,number,voiceover_text FROM frames WHERE project_id=2 ORDER BY number').fetchall()
        actions=(await client.get(prefix+'/actions')).json()
        if (bad and actions['held']) or (not bad and states[key]=='done'):break
        await asyncio.sleep(.25)
    result['split_state']={'nodes':states,'frame_count':len(rows),'held':actions['held']}
    await observer.catch_up()
    await observer.diagnoser.update(result['execution_id'])
    history=[];after=0
    while True:
        page=(await client.get(prefix+'/events',params={'after':after,'limit':500})).json()
        history.extend(page['events'])
        if not page['has_more']:break
        after=page['next_cursor']
    (root/'api-history.json').write_text(json.dumps({'events':history},ensure_ascii=False,indent=2))
    requests=json.loads((root/'actual-worker-requests.json').read_text())
    assert len(requests)==1 and requests[0]['node_key']==key
    assert requests[0]['body']['model']=='gpt-5-6-sol'
    assert len(rows)==2 and len({r[0] for r in rows})==2
    assert [r[1] for r in rows]==[1,2]
    preserved=' '.join(' '.join(r[2] for r in rows).split())==' '.join(TEXT.split())
    assert preserved is (not bad)
    assert states[prepared['outside']]=='pending'
    facts=[e for e in history if e['node_key']==key]
    assert len([e for e in facts if e['kind']=='node_start'])==1
    assert len([e for e in facts if e['kind']=='node_input'])==1
    parsed=[e for e in facts if e['kind']=='parse_result']
    assert len(parsed)==1 and parsed[0]['payload']['syntax_valid'] is True
    assert parsed[0]['payload']['schema_valid'] is (not bad)
    proof=[e for e in facts if e['kind']=='node_result_verification']
    assert len(proof)==1 and proof[0]['payload']['status']==('failed' if bad else 'passed')
    applied=[e for e in facts if e['kind']=='data_applied' and e.get('db_scope')=='project'
        and e['payload'].get('transactional') is True and e['payload'].get('result',{}).get('ok') is True]
    assert len(applied)==1,'original replace_frames applied once; no rollback claim'
    assert actions['requests_used']==1 and not actions['recoveries']
    assert actions['held'] is bad
    assert not (await client.get(prefix+'/decisions')).json()['records']
    diagnoses=(await client.get(prefix+'/diagnoses')).json()
    if bad:
        assert any(i['type']=='contract_rejected' and i['currently_relevant'] for i in diagnoses['incidents'])
        assert any(a['initiator']=='native_split_verifier' for a in actions['records'])
    else:
        assert states[key]=='done'
        assert not any(i['blocking'] and i['currently_relevant'] for i in diagnoses['incidents'])
    before=dict(fixtures.FixtureHandler.counts)
    await asyncio.sleep(12)
    assert before==dict(fixtures.FixtureHandler.counts)=={key:1}
    await observer.catch_up()
    with sqlite3.connect(fixtures.DATA/'state.db') as db:
        assert db.execute('SELECT status FROM projects WHERE id=1').fetchone()[0]=='new'
        assert db.execute("SELECT count(*) FROM observation_events WHERE execution_id=? AND node_key=? AND kind='node_start'",
            (result['execution_id'],key)).fetchone()[0]==1,'held execution must not create another worker attempt'
    with sqlite3.connect(fixtures.DB) as db:
        final_states=dict(db.execute('SELECT node_key,status FROM node_runs').fetchall())
    assert final_states[prepared['outside']]=='pending'
    result['split_state']['nodes']=final_states
    result.update(accepted=True,one_user_launch=True,actions=actions,verification=proof[0]['payload'],
        diagnoses=diagnoses,voiceover_preserved=preserved,business_apply_count=1,
        partial_apply_recorded=bad,semantic_quality_verified=False)
