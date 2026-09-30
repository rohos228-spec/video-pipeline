"""Media modes of the existing group runner; only provider HTTP is a fixture."""
import asyncio
import hashlib
import io
import json
import os
import random
import sqlite3
import time
from collections import Counter
from pathlib import Path
from PIL import Image


def prepare(fixtures, root, mode):
    if mode=='media-decision-recovery':
        # Read a copy of the accepted genuine QC exhaustion checkpoint. Keep
        # every original attempt, frame, graph and allowance intact.
        with sqlite3.connect(fixtures.DATA/'state.db') as db:
            execution,keys,used,limit=db.execute('SELECT execution_id,node_keys,requests_used,request_limit FROM action_execution_controls').fetchone()
            assert used==22 and limit==32
            assert not db.execute('SELECT 1 FROM action_node_recoveries').fetchone()
        with sqlite3.connect(fixtures.DB) as db:
            frame_count=db.execute('SELECT count(*) FROM frames WHERE project_id=2').fetchone()[0]
            qc=db.execute('SELECT status,attempts FROM node_runs WHERE node_key=?',(fixtures.NODES[4],)).fetchone()
            assert qc==('failed',9)
        cfg={'version':1,'mode':'synthetic','online_enabled':True,
             'endpoint':'http://127.0.0.1:18766/codex/v1/responses',
             'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
             'profiles':{},'allowed_actions':['read_events','read_contract','retry_same','wait','human_review','finish'],
             'media':{'enabled':True,'provider':'kie','origin':'http://127.0.0.1:18766',
                 'download_origins':['http://127.0.0.1:18766'],'model_ids':['z-image'],
                 'max_bytes':1048576,'timeout_s':30,'poll_interval_s':0.1,'max_polls':4}}
        path=root/'synthetic-media-runtime.json';path.write_text(json.dumps(cfg,indent=2))
        os.environ['ORCHESTRATOR_RUNTIME_CONFIG']=str(path)
        os.environ['KIE_API_BASE_URL']='http://127.0.0.1:18766'
        evidence={'scenario':mode,'scope':json.loads(keys),'outside':'n_videos','frame_count':frame_count,
            'execution_id':execution,'historical_requests_used':used,'historical_request_limit':limit,
            'config_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'operator_interventions':['copy immutable genuine QC native exhaustion checkpoint',
                'enable bounded loopback Decision for the existing execution; no Actions/worker start',
                'new run_id and separate test DB instance; source execution UUID retained as lineage']}
        (root/'media-prepared.json').write_text(json.dumps(evidence,indent=2))
        return evidence
    mixed_input = mode in {"media-mixed", "media-recovery"}
    selected = list(fixtures.NODES) if mode == "media-mixed" else (
        list(fixtures.NODES[:-1]) if mode == "media-recovery" else [])
    selected.append("n_images")
    with sqlite3.connect(fixtures.DATA / "state.db") as db:
        original = json.loads(db.execute("SELECT nodes_snapshot FROM workflow_runs WHERE project_id=2 ORDER BY id DESC LIMIT 1").fetchone()[0])
    by_id = {n["id"]: n for n in original}
    keys = selected + ["n_videos"]
    nodes = [by_id[key] for key in keys]
    for node in nodes:
        node["data"] = {**node.get("data", {}), "groupId": "media-test" if node["id"] in selected else "outside"}
        if node["id"] == "n_images":
            node["data"].update(modelId="z-image", aspectRatio="9:16")
    edges = [{"id": "synthetic-media-edge-" + str(i), "source": a, "target": b,
              "data": {"kind": "after"}} for i, (a, b) in enumerate(zip(keys, keys[1:]))]
    seed_voiceover = "Он вошёл в комнату и открыл папку."
    # Initial state construction on fresh synthetic copies BEFORE app starts.
    # No successful node statuses are manufactured.
    for db_path in (fixtures.DATA / "state.db", fixtures.DB):
        with sqlite3.connect(db_path) as db:
            meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0] or "{}")
            meta["canvas_graph"]["nodes"], meta["canvas_graph"]["edges"] = nodes, edges
            meta.pop("active_excel_gpt_node_key", None)
            meta.update(img_streams=1, outsee_streams=1)
            db.execute("UPDATE projects SET meta=?,image_generator='z_image' WHERE id=2", (json.dumps(meta),))
            if mode=='launch-images':db.execute("UPDATE projects SET status='new' WHERE id=2")
            if mixed_input:
                # fw_script's real contract starts from one cell containing the
                # complete voiceover. Two unrelated seeded cells are rebuilt by
                # ensure_single_seed_vo_cell, correctly dropping their prompts.
                # Construct a consistent input BEFORE startup; never patch the
                # resulting frames or bypass the missing-input gate after start.
                assert db.execute("SELECT count(*) FROM frames WHERE project_id=2").fetchone()[0] == 0
                db.execute("UPDATE projects SET script_text=? WHERE id=2", (seed_voiceover,))
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE project_id=2",
                       (json.dumps(nodes), json.dumps(edges)))
            marks = ",".join("?" for _ in keys)
            db.execute("DELETE FROM node_runs WHERE workflow_run_id IN (SELECT id FROM workflow_runs WHERE project_id=2) AND node_key NOT IN ("+marks+")", keys)
            if not db.execute("SELECT 1 FROM frames WHERE project_id=2 LIMIT 1").fetchone():
                texts = (seed_voiceover,) if mixed_input else (
                    seed_voiceover, "Он положил лист на стол и указал на запись.")
                for number, text in enumerate(texts, 1):
                    db.execute("INSERT INTO frames(project_id,number,voiceover_text,status,attrs,uuid,sort_key,created_at,updated_at) VALUES(2,?,?,'planned','{}',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                               (number,text,"synthetic-media-frame-"+str(number),number))
            db.execute("UPDATE frames SET image_prompt='Synthetic geometric shapes on a plain background',animation_prompt=NULL WHERE project_id=2")
            frame_count = db.execute("SELECT count(*) FROM frames WHERE project_id=2").fetchone()[0]
    cfg = {"version":1,"mode":"synthetic","online_enabled":False,"endpoint":None,
           "decision":None,"profiles":{},"allowed_actions":[],
           "media":{"enabled":True,"provider":"kie","origin":"http://127.0.0.1:18766",
                    "download_origins":["http://127.0.0.1:18766"],"model_ids":["z-image"],
                    "max_bytes":1048576,"timeout_s":30,"poll_interval_s":0.1,"max_polls":4}}
    path = root / "synthetic-media-runtime.json"
    if mode=='launch-images':
        cfg.update(online_enabled=True,endpoint='http://127.0.0.1:18766/codex/v1/responses',
            decision={'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
            allowed_actions=['read_events','read_contract','wait','human_review','finish'])
    path.write_text(json.dumps(cfg, indent=2))
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(path)
    os.environ["KIE_API_BASE_URL"] = "http://127.0.0.1:18766"
    evidence = {"scope":selected,"outside":"n_videos","frame_count":frame_count,"scenario":mode,
                "operator_interventions":["fresh synthetic copy; existing registry nodes wired into small test graph",
                    "initial image prompts supplied explicitly; script output is not an image prompt quality test",
                    "explicit loopback-only z-image profile; one image stream"],
                "config_sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    if mixed_input:
        evidence["operator_interventions"].append(
            "initial single cell equals full synthetic script_text, per fw_script input contract; no input edits after start")
    if mode=='launch-images':evidence['operator_interventions'].append('initialize both fresh synthetic project replicas as new before startup')
    if mode == "media-recovery":
        evidence["operator_interventions"].extend([
            "bounded graph: five original text nodes ending in QC, then image; local HTML node omitted from this probe",
            "QC external fixture is malformed until the one authorized recovery attempt",
            "operator will request recovery once through Actions API after genuine native exhaustion; no Decision model"])
    if mode == "media-unverified":
        evidence["operator_interventions"].append(
            "inject synthetic disk files after managed enable, before worker start; no job/artifact proof or status changes")
    (root / "media-prepared.json").write_text(json.dumps(evidence, indent=2))
    return evidence


def handler_for(fixtures, root):
    stream = io.BytesIO()
    rng = random.Random(42)
    Image.frombytes("RGB", (256, 384), rng.randbytes(256*384*3)).save(stream, "PNG")
    image = stream.getvalue()
    class Handler(fixtures.FixtureHandler):
        media_counts = Counter()
        jobs = {}
        def response(self, body, content_type="application/json"):
            content = json.dumps(body).encode() if isinstance(body, dict) else body
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        def do_POST(self):
            if self.path == "/codex/v1/responses":
                return super().do_POST()
            if self.path != "/api/v1/jobs/createTask":
                return self.send_error(404)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["model"] == "z-image"
            self.media_counts["generation_submit"] += 1
            job = "fixture-image-" + str(self.media_counts["generation_submit"])
            self.jobs[job] = body
            (root / "fixture-media-requests.json").write_text(json.dumps(self.jobs, indent=2))
            self.response({"code":200,"data":{"taskId":job}})
        def do_GET(self):
            if self.path.startswith("/api/v1/jobs/recordInfo?"):
                from urllib.parse import parse_qs, urlsplit
                job = parse_qs(urlsplit(self.path).query)["taskId"][0]
                assert job in self.jobs
                self.media_counts["status"] += 1
                return self.response({"code":200,"data":{"state":"success","resultJson":json.dumps(
                    {"resultUrls":["http://127.0.0.1:18766/artifacts/"+job+".png"]})}})
            if self.path.startswith("/artifacts/fixture-image-"):
                self.media_counts["download"] += 1
                return self.response(image, "image/png")
            return super().do_GET()
    return Handler


async def run(client, fixtures, observer, root, result, prepared, handler, *, unified_launch=False):
    base = "http://127.0.0.1:18765/api/projects/2"
    async def post(suffix, body):
        response = await client.post(base+suffix, json=body)
        assert response.is_success, (suffix, response.status_code, response.text)
        return response.json()
    selected = prepared["scope"]
    if unified_launch:
        options=await client.get(base+'/observations/launch-options');options.raise_for_status()
        options=options.json()
        scope=await post('/observations/launch',{'workflow_run_id':options['workflow_run_id'],
            'graph_version':options['graph_version'],'node_keys':selected,'strategies':['wait','human_review'],
            'request_limit':32,'rationale':'one synthetic unified image launch; original executor and verifier',
            'idempotency_key':'unified-launch-images-01'})
        result['managed_launch']=scope
    elif prepared['scenario']=='media-decision-recovery':scope={'execution_id':prepared['execution_id']}
    else:scope = await post("/observations", {"node_keys":selected})
    execution = scope["execution_id"]
    result["execution_id"] = execution
    prefix = "/observations/"+execution
    checkpoint_decision=prepared['scenario']=='media-decision-recovery'
    if checkpoint_decision:
        result['operator_starts']=0
        result['interventions']=prepared['operator_interventions']
        await post(prefix+'/decisions/control',{'enabled':True,'rationale':'bounded synthetic Decision on immutable QC exhaustion copy'})
    if not unified_launch and not checkpoint_decision:
        await post(prefix+"/pin", {})
        await post(prefix+"/actions/enable", {"group_id":"media-test", "node_keys":selected,
            "rationale":"synthetic existing worker media acceptance", "request_limit":32})
    if prepared["scenario"] == "media-unverified":
        # After managed enable: unmanaged startup maintenance must not create
        # baseline artifacts before this managed-worker denial test begins.
        scenes = fixtures.DB.parent / "scenes"
        scenes.mkdir(parents=True, exist_ok=True)
        rng = random.Random(47)
        for number in range(1, prepared["frame_count"] + 1):
            Image.frombytes("RGB", (256, 384), rng.randbytes(256*384*3)).save(
                scenes / f"frame_{number:03d}_deadbeef.png", "PNG")
    first_type = "images" if selected[0]=="n_images" else "excel_gpt"
    if not unified_launch and not checkpoint_decision:
        result["managed_start"] = await post(prefix+"/actions/start", {"node_key":selected[0],
            "step_code":first_type,"rationale":"one start; following nodes belong to original planner",
            "idempotency_key":"synthetic-media-start-01"})
    if prepared["scenario"] == "media-recovery":
        await recover_exhausted_qc(client, fixtures, root, result, base, prefix)
    deadline = time.monotonic()+180
    state = {}
    while time.monotonic()<deadline:
        response = await client.get(base+"/run")
        response.raise_for_status()
        state = response.json()
        nodes = {n["node_key"]:n for n in state["node_runs"]}
        with sqlite3.connect(fixtures.DB) as db:
            meta=json.loads(db.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0] or "{}")
        if prepared["scenario"] in {"media-recovery",'media-decision-recovery'}:
            with sqlite3.connect(fixtures.DATA / "state.db") as db:
                row = db.execute("SELECT status FROM action_node_recoveries WHERE execution_id=?", (execution,)).fetchone()
            if row and row[0] == "human_review":
                result["recovery_stopped"] = "human_review"
                break
        elif (meta.get("step_failure") or {}).get("last_error"):
            result["native_failure"] = meta["step_failure"]
            break  # preserve first failure; no long native exhaustion for adapter debugging
        if nodes.get("n_images",{}).get("status") in {"done","failed","waiting_hitl"}:
            break
        await asyncio.sleep(.25)
    await observer.catch_up()
    result.update(state=state, media_counts=dict(handler.media_counts))
    # Save diagnostics even when the original pipeline stops or fails.
    context = await client.get(base+prefix+"/context")
    result["context"] = context.json()
    history = []; after = 0
    while True:
        page = (await client.get(base+prefix+"/events", params={"after":after,"limit":500})).json()
        history.extend(page["events"])
        if not page["has_more"]:break
        after=page["next_cursor"]
    (root/"api-history.json").write_text(json.dumps({"events":history},ensure_ascii=False,indent=2))
    if prepared["scenario"] in {"media-recovery",'media-decision-recovery'}:
        actions = await client.get(base+prefix+"/actions")
        actions.raise_for_status()
        result["actions"] = actions.json()
        diagnosis = await client.get(base+prefix+"/diagnoses")
        diagnosis.raise_for_status()
        result["diagnosis"] = diagnosis.json()
    with sqlite3.connect(fixtures.DB) as db:
        artifacts = db.execute("SELECT uuid,path,meta FROM artifacts WHERE project_id=2 AND kind='scene_image'").fetchall()
    result["artifacts"] = [{"uuid":u,"bytes":Path(p).stat().st_size,
        "sha256":hashlib.sha256(Path(p).read_bytes()).hexdigest(),"meta":json.loads(m)} for u,p,m in artifacts]
    if prepared["scenario"] == "media-unverified":
        assert nodes["n_images"]["status"] != "done", "unproven disk files must not complete managed node"
        assert nodes["n_videos"]["status"] == "pending"
        assert not artifacts, "unproven files cannot become business artifacts"
        assert not handler.media_counts, "no media HTTP operation authorized by disk recovery"
        with sqlite3.connect(fixtures.DATA / "state.db") as db:
            held = db.execute("SELECT held FROM action_execution_controls WHERE execution_id=?", (execution,)).fetchone()[0]
            assert held, "missing proof must hold execution, not enter native repeat loop"
            assert db.execute("SELECT status FROM projects WHERE id=1").fetchone()[0] == "new"
        assert any(e["kind"] == "media_policy_denied" for e in history)
        await asyncio.sleep(12)
        assert not handler.media_counts
        result.update(accepted=True, protective_stop=True, semantic_quality_verified=False)
        return
    assert all(nodes[key]["status"]=="done" for key in selected), {k:v["status"] for k,v in nodes.items()}
    assert nodes["n_videos"]["status"]=="pending"
    assert len(artifacts)==prepared["frame_count"]
    assert handler.media_counts["generation_submit"]==len(artifacts)
    assert all(a["meta"]["orchestrator"]["verification"]["status"]=="passed" for a in result["artifacts"])
    assert any(e["kind"]=="artifact_saved" for e in history)
    assert any(e["kind"]=="media_node_verified" and e["node_key"]=="n_images" for e in history)
    assert any(e["kind"]=="node_start" and e["node_key"]=="n_images" for e in history)
    if prepared["scenario"] in {"media-recovery",'media-decision-recovery'}:
        if checkpoint_decision:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                decisions=(await client.get(base+prefix+'/decisions')).json()
                if decisions['records'] and decisions['records'][0]['status'] in {'done','human_review'}:break
                await asyncio.sleep(.25)
            assert len(decisions['records'])==1 and decisions['records'][0]['status']=='done',decisions
            assert decisions['records'][0]['model_steps']==2,'verified recovery needs no third model summary POST'
            result.update(decisions=decisions,brain_posts=handler.counts['__decision__'],
                source_requests=prepared['historical_requests_used'],worker_posts=handler.counts[fixtures.NODES[4]])
        verify_recovery(fixtures, result, history)
    if prepared["scenario"] == "media-mixed":
        report = Path(fixtures.snapshot()["report_path"])
        assert report.is_file() and report.stat().st_size > 0
        result["html"] = {"bytes":report.stat().st_size, "sha256":hashlib.sha256(report.read_bytes()).hexdigest()}
    before = dict(handler.media_counts)
    await asyncio.sleep(12)
    assert before == dict(handler.media_counts), "no generation may escape group boundary"
    with sqlite3.connect(fixtures.DATA/"state.db") as db:
        result["pilot_project_1_status"] = db.execute("SELECT status FROM projects WHERE id=1").fetchone()[0]
        assert result["pilot_project_1_status"]=="new"
    if unified_launch:
        actions=(await client.get(base+prefix+'/actions')).json()
        decisions=(await client.get(base+prefix+'/decisions')).json()
        assert actions['requests_used']==sum(handler.media_counts.values()) and not actions['recoveries']
        assert decisions['enabled'] and not decisions['records'],'healthy images must not invoke Decision'
        result.update(one_user_launch=True,actions=actions)
    result.update(accepted=True,scope_boundary="original managed gate",
        semantic_quality_verified=False,full_media_chain_blocker="ffmpeg/ffprobe/ASR dependencies absent")


async def recover_exhausted_qc(client, fixtures, root, result, base, prefix):
    """Observe real native exhaustion, then one explicit operator Actions POST.

    No retry counters/status/input writes, shortened backoff, manual next-node
    start or direct reconcile call. Observer.loop owns verification/continuation.
    """
    import shutil
    from app.services.step_failure_policy import MAX_TOTAL_FAILS
    from app.services.step_cancel import is_generation_active
    target = fixtures.NODES[4]
    deadline = time.monotonic()+600
    incident = None
    while time.monotonic()<deadline:
        with sqlite3.connect(fixtures.DATA/"state.db") as db:
            meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0] or "{}")
        failure = meta.get("step_failure") or {}
        count = max((failure.get("total_fails") or {}).values(), default=0)
        (root/"recovery-progress.json").write_text(json.dumps({"phase":"native_exhaustion",
            "native_failures":count,"text_posts":dict(fixtures.FixtureHandler.counts)}))
        if failure.get("abandoned_at") and count >= MAX_TOTAL_FAILS and not is_generation_active(2):
            response = await client.get(base+prefix+"/diagnoses")
            response.raise_for_status()
            current = [i for i in response.json()["incidents"] if i["node_key"]==target
                and i["type"] in {"invalid_json","missing_payload"} and i.get("currently_relevant")]
            if len(current)==1:
                incident=current[0]
                break
        await asyncio.sleep(.5)
    assert incident is not None, "QC did not reach diagnosed native exhaustion within the existing bound"
    assert fixtures.FixtureHandler.counts[target] == 2*MAX_TOTAL_FAILS
    result["native_exhaustion"] = failure
    checkpoint=root/"exhausted-checkpoint"
    shutil.copytree(fixtures.DATA,checkpoint,ignore=shutil.ignore_patterns("*-wal","*-shm"))
    for path in fixtures.DATA.rglob("*.db"):
        with sqlite3.connect(path) as source, sqlite3.connect(checkpoint/path.relative_to(fixtures.DATA)) as dest:
            source.backup(dest)
    (root/"checkpoint-provenance.json").write_text(json.dumps({"execution_id":result["execution_id"],
        "node_key":target,"source_attempt_id":incident["attempt_id"],"synthetic_only":True,
        "status":"exhausted before supervisor action","fixture_counts":dict(fixtures.FixtureHandler.counts),
        "native_failures":failure["total_fails"]},indent=2))
    payload={"incident_id":incident["incident_id"],"node_key":target,
        "rationale":"one synthetic QC recovery followed by original managed image continuation",
        "idempotency_key":"media-qc-recovery-01"}
    response=await client.post(base+prefix+"/actions/retry",json=payload)
    result["recovery_response"]={"http_status":response.status_code,"body":response.json()}
    assert response.is_success, response.text
    result["recovery_action"]=response.json()
    replay=await client.post(base+prefix+"/actions/retry",json=payload)
    assert replay.is_success and replay.json()["idempotent_replay"], replay.text
    assert replay.json()["action_id"]==result["recovery_action"]["action_id"]
    result["recovery_replay"]=replay.json()
    (root/"recovery-progress.json").write_text(json.dumps({"phase":"recovery_reserved",
        "action_id":result["recovery_action"]["action_id"],"text_posts":dict(fixtures.FixtureHandler.counts)}))


def verify_recovery(fixtures, result, history):
    from app.services.observation import digest
    from app.services.decision_config import runtime_config
    actions=result["actions"]
    assert len(actions["recoveries"])==1
    recovery=actions["recoveries"][0]
    assert recovery["status"]=="verified" and not actions["held"], recovery
    assert recovery["result"]["runtime_config_hash"]==digest(runtime_config())
    copied_decision=result.get('mode')=='media-decision-recovery'
    if copied_decision:
        assert fixtures.FixtureHandler.counts[fixtures.NODES[4]]==1
        assert fixtures.FixtureHandler.counts['__decision__']==2
        assert sum(fixtures.FixtureHandler.counts.values())==3
        assert actions['requests_used']==result['source_requests']+3+sum(result['media_counts'].values())==28
    else:
        assert fixtures.FixtureHandler.counts[fixtures.NODES[4]]==19
        assert sum(fixtures.FixtureHandler.counts.values())==23
        assert actions["requests_used"]==23+sum(result["media_counts"].values())==26
    events=[e for e in history if e["attempt_id"]==recovery["new_attempt_id"]]
    assert sum(e["kind"]=="node_start" for e in events)==1
    assert sum(e["kind"]=="data_applied" for e in events)==1
    verify=next(e for e in events if e["kind"]=="recovery_verification" and e["payload"]["verified"])
    successors=[e for e in events if e["kind"]=="managed_successor"]
    assert len(successors)==1 and successors[0]["payload"]["to"]=="n_images"
    assert successors[0]["payload"]["runtime_config_hash"]==recovery["result"]["runtime_config_hash"]
    starts=[e for e in history if e["kind"]=="node_start" and e["node_key"]=="n_images"]
    assert len(starts)==1 and verify["id"]<successors[0]["id"]<starts[0]["id"]
    assert any(i["incident_id"]==recovery["incident_id"] and i["status"]=="resolved_confirmed"
               for i in result["diagnosis"]["incidents"])
    result["recovery_verification_proof"]={"action_id":recovery["action_id"],
        "native_model_posts":18,"recovery_model_posts":1,"preceding_text_posts":4,
        "verification_event":verify["id"],"successor_event":successors[0]["id"],
        "image_start_event":starts[0]["id"],"runtime_config_hash":recovery["result"]["runtime_config_hash"],
        "process_restart_tested":False,"operator_recovery_api_requests":0 if copied_decision else 2,
        'decision_model_posts':2 if copied_decision else 0,"distinct_recovery_actions":1}
