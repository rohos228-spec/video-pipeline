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
    selected = list(fixtures.NODES) if mode == "media-mixed" else []
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
    # Initial state construction on fresh synthetic copies BEFORE app starts.
    # No successful node statuses are manufactured.
    for db_path in (fixtures.DATA / "state.db", fixtures.DB):
        with sqlite3.connect(db_path) as db:
            meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0] or "{}")
            meta["canvas_graph"]["nodes"], meta["canvas_graph"]["edges"] = nodes, edges
            meta.pop("active_excel_gpt_node_key", None)
            meta.update(img_streams=1, outsee_streams=1)
            db.execute("UPDATE projects SET meta=?,image_generator='z_image' WHERE id=2", (json.dumps(meta),))
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE project_id=2",
                       (json.dumps(nodes), json.dumps(edges)))
            marks = ",".join("?" for _ in keys)
            db.execute("DELETE FROM node_runs WHERE workflow_run_id IN (SELECT id FROM workflow_runs WHERE project_id=2) AND node_key NOT IN ("+marks+")", keys)
            if not db.execute("SELECT 1 FROM frames WHERE project_id=2 LIMIT 1").fetchone():
                for number, text in enumerate(("Он вошёл в комнату и открыл папку.", "Он положил лист на стол и указал на запись."), 1):
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
    path.write_text(json.dumps(cfg, indent=2))
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(path)
    os.environ["KIE_API_BASE_URL"] = "http://127.0.0.1:18766"
    evidence = {"scope":selected,"outside":"n_videos","frame_count":frame_count,"scenario":mode,
                "operator_interventions":["fresh synthetic copy; existing registry nodes wired into small test graph",
                    "initial image prompts supplied explicitly; script output is not an image prompt quality test",
                    "explicit loopback-only z-image profile; one image stream"],
                "config_sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
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


async def run(client, fixtures, observer, root, result, prepared, handler):
    base = "http://127.0.0.1:18765/api/projects/2"
    async def post(suffix, body):
        response = await client.post(base+suffix, json=body)
        assert response.is_success, (suffix, response.status_code, response.text)
        return response.json()
    selected = prepared["scope"]
    scope = await post("/observations", {"node_keys":selected})
    execution = scope["execution_id"]
    result["execution_id"] = execution
    prefix = "/observations/"+execution
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
    result["managed_start"] = await post(prefix+"/actions/start", {"node_key":selected[0],
        "step_code":first_type,"rationale":"one start; following nodes belong to original planner",
        "idempotency_key":"synthetic-media-start-01"})
    deadline = time.monotonic()+180
    state = {}
    while time.monotonic()<deadline:
        response = await client.get(base+"/run")
        response.raise_for_status()
        state = response.json()
        nodes = {n["node_key"]:n for n in state["node_runs"]}
        with sqlite3.connect(fixtures.DB) as db:
            meta=json.loads(db.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0] or "{}")
        if (meta.get("step_failure") or {}).get("last_error"):
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
    result.update(accepted=True,scope_boundary="original managed gate",
        semantic_quality_verified=False,full_media_chain_blocker="ffmpeg/ffprobe/ASR dependencies absent")
