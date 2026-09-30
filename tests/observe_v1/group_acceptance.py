"""Actual API -> worker -> six-node group; only the external HTTP transport is synthetic.

The test runner stops the app at the group boundary. Observation itself does not.
Prepared answers are reused unchanged from the versioned group_runner.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from http.server import ThreadingHTTPServer

import httpx

ROOT = Path("/results")
DATA = ROOT / "free-run-data"
SOURCE = Path("/observe-source")
sys.path.insert(0, str(SOURCE))
import group_runner as fixtures
from coverage_capture import begin
finish_coverage = begin(SOURCE)


async def run():
    mode = os.environ.get("OBSERVE_GROUP_MODE", "observe")
    denial_mode = mode.startswith('deny-')
    decision_mode = mode.startswith('decision-') or mode.startswith('profiles-') or mode=='media-decision-recovery'
    live_decision = mode=='live-decision'
    shutil.copytree('/observe-denial-seed' if denial_mode or decision_mode else '/srv/seed', DATA)
    browser_mode = os.environ.get("OBSERVE_BROWSER", "").lower() == "true"
    os.environ.update({
        "DATA_DIR": str(DATA), "SQLITE_PATH": str(DATA / "state.db"),
        "TEXT_LLM_PROVIDER": "kie", "GPT_API_KEY": "synthetic-fixture-token",
        "GPT_BASE_URL": "http://127.0.0.1:18766", "GPT_CHAT_PATH": "/codex/v1/responses",
        "GPT_MODEL": "gpt-5-6-sol", "GPT_API_MODE": "responses", "GPT_MAX_RETRIES": "4",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false",
        "FLEET_AUTO_PULL": "false", "WEB_ENABLED": "true", "WEB_HOST": "127.0.0.1",
        "WEB_PORT": "18765", "HITL_AUTO_APPROVE": "false", "BROWSER_CDP_AUTO_RECOVER": "false",
        "OUTSEE_HTTP_FALLBACK_CDP": "false", "AUDIO_USE_ELEVENLABS_FALLBACK": "false",
        "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false",
    })
    if mode.startswith('decision-managed-'):
        plan=json.loads((SOURCE/'config/managed-text-pilot.example.json').read_text())
        provenance=json.loads(Path('/observe-checkpoint-provenance.json').read_text())
        assert provenance['synthetic_only'] and provenance['status']=='exhausted before supervisor action'
        assert plan['online_authorized'] is False and plan['runtime']['external_calls_enabled'] is False
        target=plan['target_node']
        with sqlite3.connect(DATA/'state.db') as db:
            execution=provenance['execution_id']
            control=db.execute('SELECT requests_used,graph_version,node_keys FROM action_execution_controls WHERE execution_id=?',(execution,)).fetchone()
            assert control and control[0]==plan['inherited_requests']==18
            assert not db.execute('SELECT 1 FROM action_node_recoveries').fetchone()
            attempt=db.execute('SELECT attempt_id FROM observation_events WHERE execution_id=? AND node_key=? AND kind=? ORDER BY id DESC LIMIT 1',(execution,target,'node_start')).fetchone()[0]
            incident=db.execute('SELECT incident_id FROM observation_incidents WHERE execution_id=? AND node_key=? AND attempt_id=?',(execution,target,attempt)).fetchone()[0]
            assert attempt==provenance['source_attempt_id']
            assert set(json.loads(control[2]))==set(fixtures.NODES)
            b=plan['budget'];assert b['new_request_limit']==b['decision_max_posts']+b['worker_base_posts']+b['worker_json_retry_slots']
            assert b['managed_request_limit']==18+b['new_request_limit']
            # Authorized service-budget setup on this fresh copy only; no attempts/business writes.
            db.execute('UPDATE action_execution_controls SET request_limit=? WHERE execution_id=?',(b['managed_request_limit'],execution))
        cfg=plan['runtime'];cfg['online_enabled']=True  # loopback fixture only
        cfg['incident_scope'].update(execution_id=execution,incident_id=incident,source_attempt_id=attempt)
        path=ROOT/'synthetic-managed-runtime.json';path.write_text(json.dumps(cfg,indent=2))
        os.environ['ORCHESTRATOR_RUNTIME_CONFIG']=str(path)
        plan.update(run_id='managed-synthetic-'+str(uuid.uuid4()),execution_id=execution,
            test_db_instance=str(DATA/'state.db'),graph_revision=control[1],
            runtime_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            operator_changes=['copy genuine exhausted seed','reduce total request_limit from 40 to 30',
                              'bind one incident/source attempt','enable loopback fixtures; preserve seed settings including transport retries'])
        (ROOT/'prepared-managed-pilot.json').write_text(json.dumps(plan,indent=2))
    if live_decision or mode=='decision-runtime-bad':
        config={'version':1,'mode':'production','online_enabled':True,
            'endpoint':'http://127.0.0.1:18766/codex/v1/responses',
            'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
            'profiles':{},'allowed_actions':['read_events','read_contract','retry_same','human_review','wait','finish']}
        path=ROOT/'synthetic-production-adapter.json';path.write_text(json.dumps(config,indent=2))
        os.environ['ORCHESTRATOR_RUNTIME_CONFIG']=str(path)
    recovery_mode = mode in {"recovery-a", "recovery-b"}
    native_module_name={'native-animation':'native_animation_acceptance',
        'native-animation-corrupt':'native_animation_acceptance','native-entity-observe':'native_entity_acceptance',
        'native-scene-camera':'native_scene_acceptance','launch-assembly':'native_assembly_acceptance',
        'native-sfx':'native_sfx_acceptance','native-video':'native_video_acceptance'}.get(mode)
    native_family_mode=native_module_name is not None
    if native_family_mode:
        import importlib
        native_family=importlib.import_module(native_module_name)
        native_prepared=native_family.prepare(fixtures,ROOT,mode)
    unified_launch = mode in {'launch-text','launch-images'}
    project_text_mode=mode in {'launch-plan','launch-script','launch-split','launch-split-loss','launch-image-prompts','launch-image-prompts-loss','launch-image-prompts-short'}
    if project_text_mode:
        from project_text_acceptance import prepare
        project_text_prepared=prepare(fixtures,ROOT,mode)
    if mode=='launch-text':
        (ROOT/'launch-prepared.json').write_text(json.dumps(fixtures.prepare_launch_graph(),indent=2))
    media_mode = mode in {"media-image", "media-mixed", "media-unverified", "media-recovery", 'launch-images','media-decision-recovery'}
    if media_mode:
        from media_acceptance import prepare
        media_prepared = prepare(fixtures, ROOT, mode)
    fixtures.MODE = "malformed" if mode in {"malformed","unrepaired"} or recovery_mode else "group"
    recovery_transport = {"active": False}
    noop_before = None
    if mode == 'qc-noop':
        original_answer = fixtures.synthetic_answer
        fixtures.synthetic_answer = lambda node, attempt: '{"ops":[]}' if node == fixtures.NODES[4] else original_answer(node, attempt)
    if recovery_mode:
        original_answer = fixtures.synthetic_answer
        fixtures.synthetic_answer = lambda node, attempt: original_answer(node,
            2 if mode == "recovery-a" and recovery_transport["active"] else 1
        ) if node == fixtures.NODES[0] else original_answer(node, attempt)
    if mode == "media-recovery":
        original_answer = fixtures.synthetic_answer
        # Same malformed JSON as the existing exhaustion fixture, but at the
        # last model node (QC). Only the external answer changes for recovery.
        fixtures.synthetic_answer = lambda node, attempt: '{"ops": }' if (
            node == fixtures.NODES[4] and not recovery_transport["active"]
        ) else original_answer(node, attempt)
    if mode == "unrepaired":
        original_answer=fixtures.synthetic_answer
        fixtures.synthetic_answer=lambda node,attempt: original_answer(node,1) if node==fixtures.NODES[0] else original_answer(node,attempt)
    sock = socket.socket()
    sock.settimeout(1)
    try:
        error = sock.connect_ex(("192.0.2.1", 9))
        assert error != 0, "Network none not enforced"
    finally:
        sock.close()
    handler=fixtures.FixtureHandler
    if media_mode:
        from media_acceptance import handler_for as media_handler
        handler = media_handler(fixtures, ROOT)
    if native_family_mode and hasattr(native_family,'handler_for'):
        handler=native_family.handler_for(fixtures,ROOT)
    if decision_mode or live_decision:
        from decision_acceptance import handler_for
        handler=handler_for(fixtures,ROOT,mode,base_handler=handler)
        if mode in {'decision-bad','profiles-bad','decision-runtime-bad'} or live_decision:
            fixtures.MODE='malformed'
            original_answer=fixtures.synthetic_answer
            fixtures.synthetic_answer=lambda node,attempt:original_answer(node,2 if live_decision and recovery_transport['active'] else 1) if node==fixtures.NODES[0] else original_answer(node,attempt)
    server = ThreadingHTTPServer(("127.0.0.1", 18766), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from app.services import observation
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        nonlocal noop_before
        if request.url.host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Test transport refuses non-loopback request")
        if request.url.port == 18766 and request.method == "POST":
            ctx = observation._attempt.get()
            if media_mode and request.url.path == "/api/v1/jobs/createTask":
                assert ctx and ctx.node_key == "n_images", "media call outside observed image worker"
                return await original_send(client, request, **kwargs)
            if native_family_mode and request.url.path!='/codex/v1/responses':
                # Original media clients may submit multipart uploads or jobs.
                # Keep their HTTP body untouched; do not label them model POST.
                assert ctx and ctx.node_key in fixtures.NODES[:-1], 'native media outside scoped worker'
                request.headers['x-diagnostic-node']=ctx.node_key
                return await original_send(client,request,**kwargs)
            from app.services.decision_transport import current
            if current.get() is not None:
                assert decision_mode or live_decision,'unexpected decision HTTP'
                request.headers['x-diagnostic-node']='__decision__'
                return await original_send(client,request,**kwargs)
            if ctx:
                if mode.startswith('profiles-') or os.environ.get('OBSERVE_APPROVED_PROFILE') or project_text_mode or native_family_mode:
                    payload=json.loads(request.content)
                    p=ROOT/'actual-worker-requests.json'
                    bodies=json.loads(p.read_text()) if p.exists() else []
                    bodies.append({'node_key':ctx.node_key,'attempt_id':ctx.attempt_id,'body':payload})
                    p.write_text(json.dumps(bodies,ensure_ascii=False,indent=2))
                node = ctx.node_key
                recovery_transport["active"] = ctx.session.info.get("recovery_attempt_id") == ctx.attempt_id
            else:
                with sqlite3.connect(fixtures.DB) as conn:
                    meta = json.loads(conn.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0])
                    node = meta.get("active_excel_gpt_node_key", "")
            assert node in fixtures.NODES[:-1], "Provider call outside selected text group"
            request.headers["x-diagnostic-node"] = node
            if mode == 'qc-noop' and node == fixtures.NODES[4]:
                assert noop_before is None, 'QC no-op must not retry'
                noop_before = fixtures.frames()
            if (recovery_mode or live_decision) and recovery_transport['active']:
                # Synthetic external transport latency only, allowing capture
                # of a real running action for later offline browser checks.
                await asyncio.sleep(10 if live_decision else 3)
        return await original_send(client, request, **kwargs)

    httpx.AsyncClient.send = send  # External transport only; no worker/planner/DB replacements.
    from app import main
    task = asyncio.create_task(main.main())
    result = {"run_id": "observe-20260928-" + str(uuid.uuid4()), "mode": mode, "paid_calls": 0,
              "network": "none", "operator_starts": 1, "scope_boundary": "test runner"}
    try:
        await fixtures.wait_api()
        # A busy synchronous node can outlive Uvicorn's idle keepalive window.
        # Fresh control-plane connections avoid reusing a server-closed socket;
        # there is no retry or exception suppression (including for POST).
        async with httpx.AsyncClient(timeout=30, trust_env=False,
                limits=httpx.Limits(max_keepalive_connections=0)) as client:
            base = "http://127.0.0.1:18765/api/projects/2"
            if native_family_mode:
                assert not browser_mode,'new native cases do not reuse previous browser acceptance'
                if mode=='native-entity-observe':
                    await native_family.run(client,fixtures,observation.get_observer(),ROOT,result,native_prepared,handler)
                else:
                    await native_family.run(client,fixtures,observation.get_observer(),ROOT,result,native_prepared)
                return
            if project_text_mode:
                from project_text_acceptance import run as run_project_text
                await run_project_text(client,fixtures,observation.get_observer(),ROOT,result,project_text_prepared,browser=browser_mode)
                if browser_mode:
                    (ROOT/'group-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
                    before=dict(fixtures.FixtureHandler.counts)
                    deadline=time.monotonic()+300
                    while not (ROOT/'browser-finish-requested').exists() and time.monotonic()<deadline:
                        await asyncio.sleep(.2)
                    assert (ROOT/'browser-finish-requested').exists(),'native UI capture did not finish'
                    assert before==dict(fixtures.FixtureHandler.counts),'browser must not approve HITL or generate again'
                return
            if media_mode:
                from media_acceptance import run as run_media
                await run_media(client, fixtures, observation.get_observer(), ROOT,
                                result, media_prepared, handler, unified_launch=unified_launch)
                if browser_mode:
                    (ROOT/'browser-ready.json').write_text(json.dumps({
                        'project_id':2, 'execution_id':result['execution_id'],
                        'application':'live', 'mode':mode}))
                    (ROOT/'group-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
                    before_media = dict(handler.media_counts)
                    before_text = dict(fixtures.FixtureHandler.counts)
                    deadline=time.monotonic()+600
                    while not (ROOT/'browser-finish-requested').exists() and time.monotonic()<deadline:
                        await asyncio.sleep(.2)
                    assert (ROOT/'browser-finish-requested').exists(),'capture live media UI and finish explicitly'
                    assert before_media == dict(handler.media_counts) and before_text == dict(fixtures.FixtureHandler.counts), 'browser must not trigger generation'
                return
            if live_decision:
                assert browser_mode,'live acceptance requires the browser'
                from live_decision_acceptance import run as run_live
                await run_live(client,fixtures,observation.get_observer(),ROOT,result)
                return
            if decision_mode:
                from decision_acceptance import run as run_decision
                await run_decision(mode,client,fixtures,observation.get_observer(),ROOT,result)
                if mode.startswith("profiles-"):
                    from profiles_acceptance import verify
                    await verify(client,base,result["execution_id"],fixtures,ROOT,result)
                if browser_mode and (mode=='decision-runtime-bad' or mode.startswith('decision-managed-')):
                    (ROOT/'browser-ready.json').write_text(json.dumps({'project_id':2,'application':'live','state':result['decisions']['records'][0]['status']}))
                    (ROOT/'group-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
                    deadline=time.monotonic()+300
                    while not (ROOT/'browser-finish-requested').exists() and time.monotonic()<deadline:await asyncio.sleep(.2)
                    assert (ROOT/'browser-finish-requested').exists(),'capture live UI and finish explicitly'
                return
            if denial_mode:
                from recovery_denials import run as run_denial
                await run_denial(mode,client,fixtures,observation.get_observer(),ROOT,result)
                return
            execution_id = None
            if browser_mode and not recovery_mode and not unified_launch:
                # User enables observation in the real panel. No node is started
                # until the explicit test-runner signal is written via docker exec.
                (ROOT / "browser-ready.json").write_text(json.dumps({"project_id":2,"nodes":list(fixtures.NODES),"state":fixtures.snapshot()}))
                while not (ROOT / "browser-run-requested").exists():
                    await asyncio.sleep(0.2)
                response = await client.get(base + "/observations")
                response.raise_for_status()
                active = [s for s in response.json() if s["observer_state"] != "stopped"]
                assert len(active) == 1, "UI must explicitly enable one scope"
                execution_id = active[0]["execution_id"]
            elif mode != "off" and not unified_launch:
                response = await client.post(base + "/observations", json={"node_keys": list(fixtures.NODES)})
                response.raise_for_status()
                execution_id = response.json()["execution_id"]
            if execution_id:
                response = await client.post(base + f"/observations/{execution_id}/pin")
                response.raise_for_status()
            if unified_launch:
                run_response=await client.get(base+'/run');run_response.raise_for_status()
                run_snapshot=run_response.json()
                options=await client.get(base+'/observations/launch-options');options.raise_for_status()
                options=options.json()
                if browser_mode:
                    (ROOT/'browser-ready.json').write_text(json.dumps({'project_id':2,'mode':mode,'state':'awaiting one UI launch'}))
                    deadline=time.monotonic()+300
                    # Launch permissions are persisted before native dispatch.
                    # Seeing `dispatching` is expected while the UI POST is in
                    # flight, not a failed acceptance or a reason to cancel it.
                    while (not options['latest_launch'] or options['latest_launch']['status']=='dispatching') and time.monotonic()<deadline:
                        await asyncio.sleep(.5)
                        response=await client.get(base+'/observations/launch-options');response.raise_for_status()
                        options=response.json()
                    launch=options['latest_launch']
                    assert launch and launch['status']=='submitted','one UI launch was not submitted'
                    assert set(launch['policy']['node_keys'])==set(fixtures.NODES),'UI must select the full text scope'
                    result['launch_origin']='browser UI; runner issued no launch POST'
                else:
                    response=await client.post(base+'/observations/launch',json={
                        'workflow_run_id':options['workflow_run_id'],'graph_version':options['graph_version'],
                        'node_keys':list(fixtures.NODES),'strategies':['wait','human_review','retry_same'],
                        'request_limit':12,'rationale':'one synthetic unified launch; original worker owns all transitions',
                        'idempotency_key':'unified-launch-text-01'})
                    assert response.is_success,response.text
                    launch=response.json()
                result['managed_launch']=launch;execution_id=launch['execution_id']
            elif mode in {"actions", "qc-noop"} or recovery_mode:
                run_response = await client.get(base + "/run")
                run_response.raise_for_status()
                run_snapshot = run_response.json()
                graph_nodes = {str(n.get("id")): n for n in run_snapshot.get("nodes_snapshot", [])}
                selected = [graph_nodes.get(key) for key in fixtures.NODES]
                assert all(selected), "managed test nodes missing from immutable workflow graph"
                group_ids = {str((n.get("data") or {}).get("groupId") or "") for n in selected}
                assert len(group_ids) == 1 and "" not in group_ids, group_ids
                scope_response = await client.post(base + f"/observations/{execution_id}/actions/enable", json={
                    "group_id": group_ids.pop(), "node_keys": list(fixtures.NODES),
                    "rationale": "synthetic isolated actions-v1 group acceptance", "request_limit": 40 if recovery_mode else 12,
                })
                scope_response.raise_for_status()
                start_response = await client.post(base + f"/observations/{execution_id}/actions/start", json={
                    "node_key": fixtures.NODES[0], "step_code": str(selected[0].get("type") or ""),
                    "rationale": "synthetic isolated acceptance start", "idempotency_key": "actions-test-start-0001",
                })
                assert start_response.is_success, start_response.text
                result["managed_start"] = start_response.json()
            else:
                response = await client.post(base + "/steps/excel_gpt/run", params={"node_key": fixtures.NODES[0]})
                response.raise_for_status()
            if mode == "stop":
                response = await client.post(base + f"/observations/{execution_id}/stop")
                response.raise_for_status()
                assert response.json()["business_action"] is None
                result["observer_stop_during_pipeline"] = True
            if recovery_mode:
                from app.db import SessionLocal
                from app.models import Project
                from app.services.step_failure_policy import MAX_TOTAL_FAILS
                from app.services.step_cancel import is_generation_active
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    async with SessionLocal() as session:
                        p = await session.get(Project, 2)
                        failure = (p.meta or {}).get("step_failure") or {}
                    if (failure.get("abandoned_at") and max((failure.get("total_fails") or {}).values(), default=0) >= MAX_TOTAL_FAILS
                            and not is_generation_active(2)):
                        break
                    await asyncio.sleep(.2)
                assert failure.get("abandoned_at"), "native retries did not reach terminal exhaustion"
                result["native_exhaustion"] = failure
                await observation.get_observer().catch_up()
                from app.services.diagnosis import get_diagnoser
                await get_diagnoser(observation.get_observer()).update(execution_id)
                response = await client.get(base + f"/observations/{execution_id}/diagnoses")
                incidents = [i for i in response.json()["incidents"] if i["node_key"] == fixtures.NODES[0]
                    and i["type"] in {"invalid_json", "missing_payload"} and i.get("currently_relevant")]
                assert len(incidents) == 1, "current exhausted attempt must have one incident"
                checkpoint = ROOT / 'exhausted-checkpoint'
                shutil.copytree(DATA,checkpoint,ignore=shutil.ignore_patterns('*-wal','*-shm'))
                for db in DATA.rglob('*.db'):
                    with sqlite3.connect(db) as source_db, sqlite3.connect(checkpoint/db.relative_to(DATA)) as target_db:
                        source_db.backup(target_db)
                (ROOT/'checkpoint-provenance.json').write_text(json.dumps({
                    'execution_id':execution_id,'native_failures':failure['total_fails'],
                    'source_attempt_id':incidents[0]['attempt_id'],'fixture_counts':dict(fixtures.FixtureHandler.counts),
                    'status':'exhausted before supervisor action','synthetic_only':True},indent=2))
                payload = {"incident_id": incidents[0]["incident_id"], "node_key": fixtures.NODES[0],
                    "rationale": "one authorized synthetic recovery after native exhaustion",
                    "idempotency_key": "recovery-worker-test-0001"}
                retry = await client.post(base + f"/observations/{execution_id}/actions/retry", json=payload)
                assert retry.is_success, retry.text
                result["recovery_action"] = retry.json()
                replay = await client.post(base + f"/observations/{execution_id}/actions/retry", json=payload)
                assert replay.is_success and replay.json()["idempotent_replay"]
                result["recovery_replay"] = replay.json()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if recovery_mode and not (ROOT/'running-phase').exists():
                    phase = (await client.get(base+f'/observations/{execution_id}/actions')).json()
                    if phase['recoveries'][0]['status'] == 'claimed':
                        target=ROOT/'running-phase'/'free-run-data'
                        shutil.copytree(DATA,target,ignore=shutil.ignore_patterns('*-wal','*-shm'))
                        for db in DATA.rglob('*.db'):
                            with sqlite3.connect(db) as src, sqlite3.connect(target/db.relative_to(DATA)) as dst:
                                src.backup(dst)
                        (ROOT/'running-phase'/'observed-actions.json').write_text(json.dumps(phase,default=str))
                state = fixtures.snapshot()
                if state["nodes"].get(fixtures.NODES[-1], {}).get("status") == "done":
                    break
                if any(n["status"] == "failed" for n in state["nodes"].values()) and mode != "recovery-a":
                    break
                await asyncio.sleep(0.1)
            result.update({"execution_id":execution_id,"state":state})
            if mode in {'actions','qc-noop','recovery-a','recovery-b','launch-text'}:
                # Do not manufacture a group boundary by stopping the runner
                # immediately after HTML/error. Four normal five-second cycles.
                before_posts = sum(fixtures.FixtureHandler.counts.values())
                samples = []
                for _ in range(4):
                    await asyncio.sleep(5)
                    live = (await client.get(base+'/run')).json()
                    states = {n['node_key']:n['status'] for n in live['node_runs']}
                    assert states.get('n_script') == 'pending', states
                    assert sum(fixtures.FixtureHandler.counts.values()) == before_posts
                    samples.append({'n_script':states['n_script'],'model_posts':before_posts})
                result['boundary_live_cycles'] = samples
                if mode != 'recovery-b' and not unified_launch:
                    denied = await client.post(base+f'/observations/{execution_id}/actions/start',json={
                        'node_key':'n_script','step_code':'script','rationale':'synthetic boundary scope denial',
                        'idempotency_key':'boundary-outside-scope-0001'})
                    assert denied.status_code == 409 and 'outside' in denied.text, denied.text
                    assert sum(fixtures.FixtureHandler.counts.values()) == before_posts
                    result['boundary_scope_denial'] = denied.json()
            if mode in {"unrepaired", "recovery-b"}:
                assert state['nodes'][fixtures.NODES[0]]['status']=='failed',state
                assert all(state['nodes'][key]['status']=='pending' for key in fixtures.NODES[1:]),state
                result['fault']='external synthetic transport always returns prepared malformed answer'
            else:
                assert all(state["nodes"].get(key, {}).get("status") == "done" for key in fixtures.NODES), state
                path = Path(state["report_path"])
                assert path.exists() and path.stat().st_size > 0
                result['html']={"path":str(path),"bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
            if execution_id:
                await observation.get_observer().catch_up()
                response = await client.get(base + f"/observations/{execution_id}/events", params={"limit": 500})
                response.raise_for_status()
                history = response.json()
                (ROOT / "api-history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2))
                kinds = {key: [e["kind"] for e in history["events"] if e["node_key"] == key] for key in fixtures.NODES}
                if mode not in {"stop","unrepaired", "recovery-b"}:
                    for key in fixtures.NODES:
                        assert "node_start" in kinds[key] and "node_end" in kinds[key], (key, kinds[key])
                    for key in fixtures.NODES[:-1]:
                        assert "model_start" in kinds[key] and "model_end" in kinds[key], (key, kinds[key])
                    assert "artifact_created" in kinds[fixtures.NODES[-1]]
                    assert "node_input" in kinds[fixtures.NODES[-1]]
                text_check = [e for e in history["events"] if e["node_key"] == fixtures.NODES[1] and e["kind"] == "parse_result"]
                assert all(not e["payload"]["payload_required"] for e in text_check), "Text verdict must not require apply-ops"
                if mode == "malformed":
                    assert "json_retry" in kinds[fixtures.NODES[0]]
                    assert "parse_result" in kinds[fixtures.NODES[0]]
                response = await client.get(base + f"/observations/{execution_id}/context")
                response.raise_for_status()
                result["context"] = response.json()
                assert "node_contexts" in result["context"], "Persistent per-node inputs/model/checks missing"
                assert result["context"]["business_group_outcome"] == ("failed" if mode in {'unrepaired','recovery-b'} else "done")
                from app.services.diagnosis import get_diagnoser
                await get_diagnoser(observation.get_observer()).update(execution_id)
                response=await client.get(base+f"/observations/{execution_id}/diagnoses")
                response.raise_for_status()
                result['diagnosis']=response.json()
                (ROOT/'diagnosis.json').write_text(json.dumps(result['diagnosis'],ensure_ascii=False,indent=2))
                formats=[i for i in result['diagnosis']['incidents'] if i['type'] in {'invalid_json','missing_payload'}]
                if mode=='malformed':
                    assert len(formats)==1,result['diagnosis']
                    assert formats[0]['status']=='resolved_confirmed',formats
                if mode=='unrepaired':
                    attempts={e['attempt_id'] for e in history['events'] if e['kind']=='node_start'}
                    assert {i['attempt_id'] for i in formats}==attempts
                    assert all(i['status']=='human_review' for i in formats)
                    assert sum(fixtures.FixtureHandler.counts.values())==2*len(attempts)
                elif recovery_mode:
                    assert sum(fixtures.FixtureHandler.counts.values()) == (23 if mode=='recovery-a' else 20)
                else:
                    assert sum(fixtures.FixtureHandler.counts.values()) == (6 if mode=='malformed' else 5)
                if recovery_mode:
                    managed = (await client.get(base + f"/observations/{execution_id}/actions")).json()
                    result["actions"] = managed
                    assert len(managed["recoveries"]) == 1
                    recovery = managed["recoveries"][0]
                    assert recovery["status"] == ("verified" if mode == "recovery-a" else "human_review"), recovery
                    assert managed["held"] == (mode == "recovery-b"), managed
                    assert fixtures.FixtureHandler.counts[fixtures.NODES[0]] == (19 if mode == "recovery-a" else 20)
                    if mode == "recovery-a":
                        assert any(i["incident_id"] == recovery["incident_id"] and i["status"] == "resolved_confirmed" for i in formats)
                        recovery_events = [e for e in history['events'] if e['attempt_id'] == recovery['new_attempt_id']]
                        assert sum(e['kind']=='node_start' for e in recovery_events)==1
                        assert sum(e['kind']=='data_applied' for e in recovery_events)==1
                        proof = next(e for e in recovery_events if e['kind']=='recovery_verification' and e['payload'].get('verified'))
                        next_start = next(e for e in history['events'] if e['node_key']==fixtures.NODES[1] and e['kind']=='node_start')
                        assert proof['id'] < next_start['id'], 'continuation must follow persisted verification'
                        assert not result['context']['current_nodes'], result['context']['current_nodes']
                    run_after = (await client.get(base + "/run")).json()
                    all_states = {str(n["node_key"]): n["status"] for n in run_after.get("node_runs", [])}
                    assert all_states.get("n_script") == "pending", all_states
                    result["group_boundary"] = {"n_script": all_states["n_script"]}
                if mode in {"actions", "qc-noop",'launch-text'}:
                    controls = await client.get(base + f"/observations/{execution_id}/actions")
                    controls.raise_for_status()
                    managed = controls.json()
                    assert managed["enabled"] and not managed["held"], managed
                    assert managed["requests_used"] == 5, managed
                    assert any(a["action_kind"] == "start_node" and a["status"] == "submitted"
                               for a in managed["records"]), managed
                    result["actions"] = managed
                    if unified_launch:
                        assert not managed['recoveries'],'healthy graph must not need recovery'
                        decisions=(await client.get(base+f'/observations/{execution_id}/decisions')).json()
                        assert decisions['enabled'] and not decisions['records'],'no Decision call on healthy nodes'
                        result['one_user_launch']=True
                    if not unified_launch:
                        verification = await client.post(base + f"/observations/{execution_id}/actions/verify", json={
                            "node_key": fixtures.NODES[-1],
                            "rationale": "verify synthetic terminal node runtime completion",
                            "idempotency_key": "actions-test-verify-0001",
                        })
                        assert verification.is_success, verification.text
                        result["verification"] = verification.json()
                        assert result["verification"]["status"] == "verified", result["verification"]
                    run_after = await client.get(base + "/run")
                    run_after.raise_for_status()
                    all_states = {str(n["node_key"]): n["status"] for n in run_after.json().get("node_runs", [])}
                    boundary_targets = {str(edge.get("target")) for edge in run_snapshot.get("edges_snapshot", [])
                        if str(edge.get("source")) in set(fixtures.NODES)
                        and str(edge.get("target")) not in set(fixtures.NODES)}
                    assert boundary_targets, "selected managed group must have an outgoing graph boundary"
                    assert all(all_states.get(key) == "pending" for key in boundary_targets), {
                        "boundary_targets": sorted(boundary_targets), "node_states": all_states,
                    }
                    result["group_boundary"] = {key: all_states[key] for key in sorted(boundary_targets)}
                if mode == 'qc-noop':
                    assert noop_before is not None and fixtures.frames() == noop_before, 'QC changed frame content'
                    qc_events = [e for e in history['events'] if e['node_key'] == fixtures.NODES[4]]
                    assert fixtures.FixtureHandler.counts[fixtures.NODES[4]] == 1
                    assert not any(e['kind'] in {'data_applied','json_retry','node_error'} for e in qc_events)
                    from app.services.observation import confirmed_qc_noop
                    assert any(confirmed_qc_noop(qc_events, e.get('request_id')) for e in qc_events if e['kind']=='parse_result')
                    assert not [i for i in result['diagnosis']['incidents'] if i['node_key']==fixtures.NODES[4]
                                and i['type'] in {'invalid_json','missing_payload'}]
                    assert not managed['recoveries']
                    result['qc_noop'] = {'model_posts':1,'frame_content_unchanged':True,
                        'data_applied_events':0,'semantic_quality_verified':False,
                        'frame_content_sha256':hashlib.sha256(json.dumps(noop_before,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
                if mode == "stop":
                    assert result["context"]["observer_state"] == "stopped"
                with sqlite3.connect(DATA / "state.db") as conn:
                    assert conn.execute("SELECT count(*) FROM observation_events WHERE project_id!=2").fetchone()[0] == 0
            else:
                with sqlite3.connect(DATA / "state.db") as conn:
                    assert conn.execute("SELECT count(*) FROM observation_events").fetchone()[0] == 0
            result["accepted"] = True
            if browser_mode:
                result["fixture_counts"] = dict(fixtures.FixtureHandler.counts)
                (ROOT / "group-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                (ROOT / "browser-acceptance-ready").write_text(mode)
                while not (ROOT / "browser-finish-requested").exists():
                    await asyncio.sleep(.2)
    except BaseException as exc:
        result["accepted"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = observation.redact(str(exc))
        result["error_traceback"] = observation.redact(traceback.format_exc())
        raise
    finally:
        finish_coverage(ROOT)
        result["fixture_counts"] = dict(fixtures.FixtureHandler.counts)
        # Persist the reached group boundary before cancellation. A shutdown
        # failure must not hide completed nodes or already-written evidence.
        (ROOT / "group-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        task.cancel()
        done, pending = await asyncio.wait({task}, timeout=10)
        if pending:
            result["shutdown_gap"] = "app.main cancellation did not finish within 10 seconds"
            (ROOT / "group-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif not task.cancelled():
            error = task.exception()
            if error:
                result["shutdown_error_type"] = type(error).__name__
        server.shutdown()
        server.server_close()
        httpx.AsyncClient.send = original_send
        manifest = json.loads((SOURCE / "source-manifest.json").read_text())
        loaded = []
        for name, module in sorted(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            if not name.startswith("app.") or not filename:
                continue
            path = Path(filename).resolve()
            assert path.is_relative_to(SOURCE), (name, str(path))
            relative = path.relative_to(SOURCE).as_posix()
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            assert manifest.get(relative) == sha, name
            loaded.append({"module": name, "path": relative, "sha256": sha})
        (ROOT / "loaded-modules.json").write_text(json.dumps(loaded, indent=2))
        (ROOT / "group-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if pending:
            # Only this synthetic runner process; preserve data, do not claim a
            # clean application shutdown. Prevent infinite cancellation loops.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(3)
        if browser_mode and result.get("accepted") and not live_decision and mode!='decision-runtime-bad' and not mode.startswith('decision-managed-'):
            # At the boundary app.main is cancelled. Replay saved history in
            # the dedicated history_replay.py process. Binding in this process
            # races the original HTTP socket's shutdown (captured Errno 98).
            (ROOT / "browser-history-mode.json").write_text(json.dumps({"worker":"stopped by test runner at group boundary", "lifespan":"off", "frontend_sha256":hashlib.sha256((SOURCE / "web/out/index.html").read_bytes()).hexdigest()}))
        if not pending:
            # Background executor threads may outlive the cancelled synthetic
            # app even after all test-owned artifacts and hashes are flushed.
            # Exit this disposable runner cleanly only after acceptance evidence
            # and loaded-module verification have been persisted.
            sys.stdout.flush()
            sys.stderr.flush()
            # A failed assertion must remain a failed container exit. Avoid
            # asyncio.run waiting indefinitely for cancelled app executor
            # threads after the complete failure evidence has been saved.
            os._exit(0 if result.get("accepted") else 1)


if __name__ == "__main__":
    asyncio.run(run())
